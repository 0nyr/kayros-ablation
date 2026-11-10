"""Prepare six frozen cells, stop, then explicitly release their real budget.

No SSH, reservation or solver launch occurs in prepare/monitor modes. Launch
requires the exact schedule SHA, a declared compute job and stop-before time.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from checker_contract import checker_identity, sha256
from ms58_dabia_screen import (HORIZON, MEMORY_MB, immutable_json, now, freeze_variant,
    expected_identity, launcher_command, monitor as screen_monitor)
from ms58_dabia_validate import terminate_tree
from ms58_dabia_wave import checked_file, input_size, backend_audit
from ms58_analyze_pairs import closure_and_resource, native_work
from ms58_dominance_profile_native import BASE, PROFILE, allocation, validate_gate
from ms58_dominance_profile_prepare import validate_patch_stack
from ms58_dominance_profile_audit import compare_rows, summarize_profile
from ms58_proof_consistency import apply_feasible_consistency, proof_evidence, validate_late_event_identity
from probe_runner import digest, load_completed

INSTANCES = ('C104', 'RC201')
SOURCES = {'correction-on': BASE, 'dominance-profile': PROFILE, 'lera-minimal': 'f2c9e20'}
REFERENCE_SHA = 'ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'
CELL_TIMEOUT = HORIZON + 90
AUDIT_MARGIN = 180
PREPARE_BUDGET = 3 * CELL_TIMEOUT + AUDIT_MARGIN


def validate_plan(plan):
    if not isinstance(plan, list) or len(plan) != 3 or {v.get('name') for v in plan} != set(SOURCES):
        raise ValueError('Exactly correction-on, dominance-profile and lera-minimal are required')
    for variant in plan:
        required = {'name', 'source_id', 'manifest_path', 'worker_python', 'diagnostic_environment'}
        if (required - variant.keys() or variant.keys() - required - {'worker_path'}
                or variant['source_id'] != SOURCES[variant['name']] or variant['diagnostic_environment'] != {}):
            raise ValueError('Require exact source pins and no inherited diagnostic overrides')


def algorithm_setting_audit(task, native):
    expected = task['variant'] != 'lera-minimal'
    exact = (native.get('exact', native.get('native_result', {})) or {}) if native is not None else {}
    settings = exact.get('effective_algorithm_settings', {}) or {}
    observed = settings.get('label_correction')
    correction_status = ('passed' if observed is expected else 'failed') if 'label_correction' in settings else 'unknown'
    lazy = {key: settings[key] for key in ('lazy_extension', 'label_lazy_extension') if key in settings}
    lazy_status = ('passed' if all(value is True for value in lazy.values()) else 'failed') if lazy else 'unknown'
    # Source expectations remain explicit when a deadline leaves no sidecar.
    # Available Kayros sidecars must expose correction, as the pinned bridge does.
    if native is not None and expected:
        if 'label_correction' not in settings:
            correction_status = 'failed'
        if not lazy:
            lazy_status = 'failed'
    statuses = [correction_status, lazy_status]
    return {'status': 'failed' if 'failed' in statuses else 'unknown' if 'unknown' in statuses else 'passed',
        'native_sidecar_present': native is not None, 'effective_algorithm_settings': settings,
        'label_correction': {'expected': expected, 'observed': observed, 'status': correction_status,
            'expectation_basis': 'Pinned source default; no algorithm override',
            'required_in_available_kayros_sidecar': expected},
        'lazy_extension': {'expected': True, 'observed_fields': lazy, 'status': lazy_status},
        'missing_sidecar_meaning': 'Unknown observed mode, including outer-deadline termination; source expectation is not observed metadata.'}

def audit_cell(task, output, checker, result, expected):
    cell = output / 'cells' / result['run_id']
    manifest = json.loads(checked_file(task['manifest_path'], task['manifest_sha256']).read_bytes())
    identity = json.loads((cell / 'identity.json').read_bytes())
    if result['run_id'] != digest(expected) or any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('Persisted run identity differs from frozen screen cell')
    if load_completed(cell, expected_run_id=result['run_id']) != result:
        raise ValueError('Launcher result differs from audited stored result')
    validation = result['validation']
    if validation.get('checker') != checker:
        raise ValueError('Stored validation checker differs from pinned checker')
    events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
    backend = backend_audit(task, manifest, events, result)
    closure, resource = closure_and_resource(result, events, (cell / 'stderr.log').read_text(errors='replace'))
    native = validation.get('native_result', {})
    native_payload = json.loads((cell / native['relative_path']).read_bytes()) if native.get('sha256') else None
    work = native_work(native_payload) if native_payload is not None else {'available': False}
    algorithm = algorithm_setting_audit(task, native_payload)
    invalid = sum(c.get('valid') is not True for c in validation.get('candidates', []))
    cost_mismatches = sum(c.get('native_minus_checked') is not None and abs(c['native_minus_checked']) > 1e-6
                          for c in validation.get('candidates', []))
    checked_cost = validation.get('best_checked_cost')
    contradictory_bounds = [e for e in events if checked_cost is not None
        and isinstance(e.get('global_lb'), (int, float)) and e['global_lb'] > checked_cost + 1e-6]
    guards = [e['checker_infeasible_columns'] for e in events if e.get('checker_infeasible_columns') is not None]
    usable = (backend['passed'] and result['status'] in {'completed', 'deadline'} and not result['issues']
              and not invalid and not cost_mismatches and not contradictory_bounds and not resource
              and not any(guards) and algorithm['status'] != 'failed')
    return {'run_id': result['run_id'], 'status': result['status'], 'returncode': result['returncode'],
        'audit_ok': True, 'observation_usable': usable, 'backend_audit': backend,
        'algorithm_settings_audit': algorithm,
        'checked_cost': checked_cost, 'invalid_candidate_count': invalid,
        'native_cost_mismatch_count': cost_mismatches, 'contradictory_bound_count': len(contradictory_bounds),
        'native_guard_count': max(guards) if guards else None,
        'reported_closure_s': closure if usable else None, 'resource_failure_kind': resource,
        'proof_status': validation.get('proof', {}).get('status'), 'issues': result['issues'],
        'sampled_solver_peak_rss_kib': result.get('sampled_solver_peak_rss_kib'),
        'rss_scope': 'python_adapter_only_native_child_excluded' if task['runtime_key'] == 'upstream' else 'kayros_worker_including_native_extension',
        'native_work': work, 'result_sha256': sha256(cell / 'result.json'),
        'events_sha256': result['events_sha256'], 'native_result': native}

def remaining_seconds(deadline, monotonic_deadline):
    return min((deadline - datetime.now(timezone.utc)).total_seconds(),
               monotonic_deadline - time.monotonic())

def require_cell_budget(deadline, monotonic_deadline):
    if remaining_seconds(deadline, monotonic_deadline) < CELL_TIMEOUT + AUDIT_MARGIN:
        raise ValueError('Stop-before leaves insufficient time for a full cell and final audit')

def execute_cell(task, output, spec, deadline, monotonic_deadline):
    row = dict(task, started_utc=now(), audit_ok=False, observation_usable=False,
               native_work={'available': False}, run_id=None)
    try:
        require_cell_budget(deadline, monotonic_deadline)
    except ValueError as error:
        row.update(status='skipped_stop_before', reason=str(error), finished_utc=now(), launched=False)
        immutable_json(output / 'rows' / f"{task['task_id']}.json", row)
        return row
    immutable_json(output / 'started' / f"{task['task_id']}.json", row)
    try:
        for path, expected in [(task['instance_path'], task['instance_sha256']), (task['atf_path'], task['atf_sha256']),
                (task['worker_python'], task['worker_python_sha256']), (task['worker_path'], task['worker_sha256']),
                (task['manifest_path'], task['manifest_sha256'])]:
            checked_file(path, expected)
        taskset = shutil.which('taskset')
        if not taskset:
            raise RuntimeError('taskset is required')
        command = launcher_command(task, output, spec['checker_python'], taskset)
        row['command'] = command
        environment = dict(os.environ, **task['diagnostic_environment'], OMP_NUM_THREADS='1',
                           OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
        manifest = json.loads(Path(task['manifest_path']).read_bytes())
        expected = expected_identity(task, output, manifest, environment)
        row['expected_run_id'] = digest(expected)
        immutable_json(output / 'started' / f"{task['task_id']}-expected-identity.json", expected)
        with Path(task['launcher_stdout']).open('x') as out, Path(task['launcher_stderr']).open('x') as err:
            try:
                require_cell_budget(deadline, monotonic_deadline)
            except ValueError:
                row['status'] = 'skipped_stop_before'
                raise
            with subprocess.Popen(command, stdout=out, stderr=err, env=environment, start_new_session=True) as process:
                row.update(launched=True, launcher_timeout_s=CELL_TIMEOUT)
                try:
                    code = process.wait(timeout=CELL_TIMEOUT)
                except subprocess.TimeoutExpired:
                    terminate_tree(process.pid)
                    process.wait()
                    row['launcher_outer_timeout'] = True
                    raise RuntimeError('Probe launcher exceeded the bounded cell timeout')
        row['launcher_returncode'] = code
        if code:
            raise RuntimeError(f'Probe launcher failed with exit {code}')
        result = json.loads(Path(task['launcher_stdout']).read_bytes())
        row['run_id'] = result.get('run_id')
        row.update(audit_cell(task, output, spec['checker'], result, expected))
    except Exception as error:
        row.update(status=row.get('status', 'launch_or_audit_failure'), reason=f'{type(error).__name__}: {error}')
        row.setdefault('launched', False)
    row['finished_utc'] = now()
    immutable_json(output / 'rows' / f"{task['task_id']}.json", row)
    print(json.dumps({k: row.get(k) for k in ('task_id', 'status', 'run_id', 'reason')}), flush=True)
    return row

def frozen_inputs(inputs, output):
    root = output / 'frozen-inputs/benchmarks/TDVRPTW/Dabia2013/n=100'
    root.mkdir(parents=True)
    rows = {}
    for name in INSTANCES:
        source = (inputs / f'{name}.vrp.json').resolve()
        raw = json.loads(source.read_bytes())
        if input_size(source) != 100 or raw['num_customers'] != 100 or raw['instance_name'] != name:
            raise ValueError('Only Dabia2013 n100 C104 and RC201 are allowed')
        relative = Path(raw['td']['atf_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('ATF must be relative and contained within the input directory')
        atf = source.parent / relative
        target, target_atf = root / source.name, root / relative
        target_atf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        shutil.copyfile(atf, target_atf)
        rows[name] = {'instance_name': name, 'num_customers': 100, 'dataset': 'Dabia2013',
            'instance_key': f'Dabia2013/n=100/{name}', 'instance_path': str(target),
            'instance_sha256': sha256(target), 'atf_path': str(target_atf), 'atf_sha256': sha256(target_atf),
            'source_instance_path': str(source), 'source_atf_path': str(atf)}
    return rows

def freeze_references(path, output, inputs, checker):
    checked_file(path, REFERENCE_SHA)
    reference = json.loads(path.read_bytes())
    if reference['checker'] != checker:
        raise ValueError('Frozen reference checker mismatch')
    root = output / 'frozen-references'
    root.mkdir()
    shutil.copyfile(path, root / 'manifest.json')
    by_name = {row['instance_name']: row for row in reference['instances']}
    for name, entry in inputs.items():
        row = by_name[name]
        cost = float.fromhex(row['reference_cost_hex'])
        if (not math.isfinite(cost) or cost <= 0 or cost != row['reference_cost']
                or entry['instance_sha256'] != row['instance_sha256'] or entry['atf_sha256'] != row['atf_file_sha256']):
            raise ValueError('Screen input or cost differs from the frozen checked reference')
        copied = {}
        for kind in ('solution', 'check'):
            relative = Path(row[f'{kind}_path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Frozen reference artifact must stay inside its snapshot')
            source = checked_file(path.parent / relative, row[f'{kind}_sha256'])
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied[kind] = json.loads(target.read_bytes())
        if (copied['check'].get('status') != 'valid' or copied['check'].get('routing_cost') != cost
                or copied['solution'].get('instance_name') != name
                or copied['solution'].get('objective_function') != 'Duration' or copied['solution'].get('cost') != cost):
            raise ValueError('Frozen reference lacks its matching checked witness')
        entry.update(reference_cost=cost, reference_manifest_sha256=REFERENCE_SHA)

def consistency_audit(output, spec):
    rows = []
    for task in spec['scheduled_cells']:
        row = json.loads((output / 'rows' / f"{task['task_id']}.json").read_bytes())
        row.update(finished_task=True, capped_reported_closure_s=row.get('reported_closure_s') or HORIZON)
        if row.get('run_id'):
            try:
                directory = output / 'cells' / row['run_id']
                expected = json.loads((output / 'started' / f"{task['task_id']}-expected-identity.json").read_bytes())
                identity = json.loads((directory / 'identity.json').read_bytes())
                if digest(expected) != row['run_id'] or any(identity.get(k) != v for k, v in expected.items()):
                    raise ValueError('Stored run differs from expected identity')
                result = load_completed(directory, expected_run_id=row['run_id'])
                events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
                late = [json.loads(line) for line in (directory / 'late-events.jsonl').read_text().splitlines()]
                validate_late_event_identity(late, identity)
                row.update(proof_evidence(result['validation'], events, HORIZON, late, expected_inputs=task))
            except Exception as error:
                row.update(consistency_identity_error=f'{type(error).__name__}: {error}', audit_ok=False,
                           observation_usable=False, reported_closure_s=None, capped_reported_closure_s=HORIZON)
        rows.append(row)
    ceilings = apply_feasible_consistency(rows)
    return {'cells': rows, 'feasible_ceilings': ceilings, 'scheduled_cells': len(rows),
        'consistency_failure_count': sum(row['consistency_audit']['status'] == 'contradicted' for row in rows),
        'unsupported_closure_count': sum(row['consistency_audit']['status'] == 'unsupported' for row in rows),
        'qualification': 'Necessary feasible-witness consistency check, not independent proof certification.'}


def prepared_artifacts(plan):
    seal_path = plan.parent / 'complete.json'
    seal = json.loads(seal_path.read_bytes())
    if seal.get('source_id') != PROFILE or seal.get('base_source_id') != BASE or seal.get('solver_cells_started') != 0:
        raise ValueError('Plan is not sealed post-native dominance preparation')
    for relative, expected in seal['files'].items():
        checked_file(plan.parent / relative, expected)
    if plan.name != 'plan.json' or 'plan.json' not in seal['files']:
        raise ValueError('Use the sealed prepared plan')
    return seal


def prepare(args):
    allocated = allocation(args.job_id, args.cores)
    seal = prepared_artifacts(args.plan)
    gate = validate_gate(args.native_gate, args.profile_source, args.control_source, args.source_pins)
    if sha256(args.native_gate) != seal['native_gate_sha256'] or gate['identity'] != seal['native_identity']:
        raise ValueError('Prepared plan is bound to another native gate')
    checker = checker_identity()
    if seal['checker'] != checker:
        raise ValueError('Prepared artifact checker differs from the current pinned checker')
    plan = json.loads(args.plan.read_bytes())
    validate_plan(plan)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ('manifests', 'rows', 'started', 'launch-logs', 'profiles', 'profile-audits', 'native-gate'):
        (output / name).mkdir()
    shutil.copyfile(args.plan, output / 'plan.json')
    shutil.copytree(args.plan.parent, output / 'prepared-artifacts')
    shutil.copyfile(args.source_pins, output / 'source-pins.json')
    shutil.copyfile(args.native_gate, output / 'native-gate/complete.json')
    shutil.copytree(Path(__file__).resolve().parent, output / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    variants = {variant['name']: freeze_variant(variant, output, checker) for variant in plan}
    pins = json.loads(args.source_pins.read_bytes())
    for name in ('correction-on', 'dominance-profile'):
        validate_patch_stack(variants[name][0], pins, profile=name == 'dominance-profile')
    inputs = frozen_inputs(args.inputs, output)
    freeze_references(args.references, output, inputs, checker)
    orders = [('correction-on', 'dominance-profile', 'lera-minimal'),
              ('lera-minimal', 'dominance-profile', 'correction-on')]
    tasks = []
    for lane, (name, order, slot) in enumerate(zip(INSTANCES, orders, allocated['physical_slots'])):
        for position, variant in enumerate(order, 1):
            manifest, python, worker = variants[variant]
            manifest = json.loads(json.dumps(manifest))
            native = variant == 'lera-minimal'
            task_id = f'{name}--rep1--{variant}'
            environment = {} if variant != 'dominance-profile' else {
                'KAYROS_DOMINANCE_PROFILE': str(output / 'profiles' / f'{task_id}.jsonl'),
                'KAYROS_DOMINANCE_PROFILE_STRIDE': str(args.sample_stride),
                'KAYROS_DOMINANCE_PROFILE_OFFSET': str(args.sample_offset)}
            manifest['diagnostic_environment'] = environment
            task = dict(inputs[name], task_id=task_id, lane=lane, variant=variant,
                source_id=manifest['declared_source_commit'], position=position, replicate=1,
                cpu_core=slot['cpu_core'], host=allocated['host'],
                arm='lera-exact' if native else 'kayros-bpca-cplex', runtime_key='upstream' if native else 'cplex',
                seed=None, prefix_s=None, horizon_s=HORIZON, memory_mb=MEMORY_MB,
                diagnostic_environment=environment)
            if native:
                environment = manifest['worker_environment']
                native_input = Path(environment['UPSTREAM_SOURCE_ROOT']) / f'instances/dabia_et_al_2013/{name}_100.json'
                task['native_instance_sha256'] = sha256(native_input)
                environment['UPSTREAM_NATIVE_INPUT_SHA256'] = task['native_instance_sha256']
                filename = str(native_input.resolve())
                if filename in manifest['runtime_files'] and manifest['runtime_files'][filename] != task['native_instance_sha256']:
                    raise ValueError('Minimal Lera native input changed')
                manifest['runtime_files'][filename] = task['native_instance_sha256']
            manifest['screen_cell'] = task.copy()
            path = output / 'manifests' / f'{task_id}.json'
            immutable_json(path, manifest)
            task.update(manifest_path=str(path), manifest_sha256=sha256(path), worker_python=python,
                worker_python_sha256=sha256(Path(python)), worker_path=worker, worker_sha256=sha256(Path(worker)),
                output_directory=str(output / 'cells'), launcher_stdout=str(output / 'launch-logs' / f'{task_id}.json'),
                launcher_stderr=str(output / 'launch-logs' / f'{task_id}.stderr.log'))
            tasks.append(task)
    spec = {'schema_version': 1, 'purpose': 'Dominance mechanism diagnostic, no throughput claim',
        'created_utc': now(), 'allocation': allocated, 'checker': checker, 'scheduled_count': 6,
        'horizon_s': HORIZON, 'memory_mb': MEMORY_MB, 'physical_lane_count': 2,
        'clock_origin': 'solver_process_launch', 'variant_order_by_lane': orders, 'scheduled_cells': tasks,
        'checker_python': sys.executable, 'checker_python_sha256': sha256(Path(sys.executable)),
        'profile_source': str(args.profile_source.resolve()), 'control_source': str(args.control_source.resolve()),
        'source_pins': str((output / 'source-pins.json').resolve()),
        'native_gate': str(args.native_gate.resolve()), 'native_gate_sha256': sha256(args.native_gate),
        'sample_stride': args.sample_stride, 'sample_offset': args.sample_offset,
        'throughput_claim': False, 'solver_cells_started': 0,
        'real_budget_gate': 'A separate launch requires --release-real-budget equal to this exact schedule SHA',
        'frozen_files': {p.relative_to(output).as_posix(): sha256(p) for directory in
            ('frozen-scripts', 'frozen-patches', 'frozen-inputs', 'frozen-references', 'manifests', 'native-gate', 'prepared-artifacts')
            for p in sorted((output / directory).rglob('*')) if p.is_file()},
        'source_pins_sha256': sha256(output / 'source-pins.json'), 'plan_sha256': sha256(output / 'plan.json')}
    immutable_json(output / 'schedule.json', spec)
    schedule_hash = sha256(output / 'schedule.json')
    immutable_json(output / 'schedule-hash.json', {'sha256': schedule_hash})
    immutable_json(output / 'stop-before-real-budget.json', {'solver_cells_started': 0,
        'schedule_sha256': schedule_hash, 'next_action': 'Review this concrete schedule, then explicitly release its real budget.'})
    print(json.dumps({'output': str(output), 'schedule_sha256': schedule_hash, 'solver_cells_started': 0}))
    return 0


def verify_schedule(output):
    seal = json.loads((output / 'schedule-hash.json').read_bytes())
    spec = json.loads(checked_file(output / 'schedule.json', seal['sha256']).read_bytes())
    if spec.get('scheduled_count') != 6 or spec.get('physical_lane_count') != 2 or spec.get('throughput_claim') is not False:
        raise ValueError('Schedule denominator or diagnostic scope changed')
    for filename, expected in spec['frozen_files'].items():
        checked_file(output / filename, expected)
    checked_file(spec['checker_python'], spec['checker_python_sha256'])
    checked_file(spec['source_pins'], spec['source_pins_sha256'])
    return spec, seal['sha256']


def monitor(output):
    report = screen_monitor(output)
    spec, _ = verify_schedule(output)
    report['throughput_claim'] = False
    report['profiles'] = {}
    for name in INSTANCES:
        task_id = f'{name}--rep1--dominance-profile'
        summary = output / 'profile-audits' / f'{task_id}.json'
        report['profiles'][task_id] = json.loads(summary.read_bytes()) if summary.is_file() else summarize_profile(
            output / 'profiles' / f'{task_id}.jsonl')
    consistency = output / 'consistency-audit.json'
    if consistency.is_file():
        audited = json.loads(consistency.read_bytes())
        report['consistency_failure_count'] = audited['consistency_failure_count']
        report['unsupported_closure_count'] = audited['unsupported_closure_count']
        report['work_comparisons'] = compare_rows(audited['cells'])
    complete = output / 'complete.json'
    if complete.is_file():
        for relative, expected in json.loads(complete.read_bytes())['audit_sha256'].items():
            checked_file(output / relative, expected)
    return report


def launch(args):
    output = args.output.resolve()
    spec, schedule_hash = verify_schedule(output)
    if args.release_real_budget != schedule_hash:
        raise ValueError('Stop before real budget. Launch requires the exact reviewed schedule SHA')
    allocated = allocation(args.job_id, [slot['cpu_core'] for slot in spec['allocation']['physical_slots']])
    if (allocated['host'] != spec['allocation']['host']
            or allocated['oar_job_id'] != spec['allocation']['oar_job_id']):
        raise ValueError('Launch allocation differs from frozen preparation')
    if checker_identity() != spec['checker']:
        raise ValueError('Pinned checker changed after preparation')
    checked_file(spec['native_gate'], spec['native_gate_sha256'])
    validate_gate(Path(spec['native_gate']), Path(spec['profile_source']), Path(spec['control_source']), Path(spec['source_pins']))
    deadline = datetime.fromisoformat(args.stop_before)
    if deadline.tzinfo is None:
        raise ValueError('stop-before requires an explicit timezone')
    monotonic_deadline = time.monotonic() + (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining_seconds(deadline, monotonic_deadline) < PREPARE_BUDGET:
        raise ValueError('Need 3*(600+90)+180 seconds before the declared stop-before')
    immutable_json(output / 'launch.json', {'created_utc': now(), 'schedule_sha256': schedule_hash,
        'oar_job_id': args.job_id, 'controller_pid': os.getpid(), 'stop_before': args.stop_before,
        'monotonic_deadline': monotonic_deadline})
    def lane(index):
        for task in spec['scheduled_cells']:
            if task['lane'] != index:
                continue
            row = execute_cell(task, output, spec, deadline, monotonic_deadline)
            if task['variant'] == 'dominance-profile':
                try:
                    summary = summarize_profile(task['diagnostic_environment']['KAYROS_DOMINANCE_PROFILE'])
                    if summary.get('available') and (summary['sample_stride'] != spec['sample_stride'] or summary['sample_offset'] != spec['sample_offset']):
                        raise ValueError('Observed sample rule differs from frozen schedule')
                    summary.update(task_id=task['task_id'], source_id=task['source_id'], cell_audit_ok=row.get('audit_ok', False))
                except Exception as error:
                    summary = {'available': False, 'status': 'audit_failure', 'error': str(error), 'throughput_claim': False}
                immutable_json(output / 'profile-audits' / f"{task['task_id']}.json", summary)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lane, range(2)))
    audited = consistency_audit(output, spec)
    immutable_json(output / 'consistency-audit.json', audited)
    immutable_json(output / 'work-comparisons.json', {'comparisons': compare_rows(audited['cells']), 'throughput_claim': False})
    report = monitor(output)
    immutable_json(output / 'complete.json', dict(report, finished_utc=now(),
        row_sha256={p.name: sha256(p) for p in sorted((output / 'rows').glob('*.json'))},
        audit_sha256={p.relative_to(output).as_posix(): sha256(p) for p in sorted(output.rglob('*'))
                      if p.is_file() and (p.parent.name in ('profiles', 'profile-audits')
                                         or p.name in ('consistency-audit.json', 'work-comparisons.json'))}))
    print(json.dumps(report, indent=2))
    return int(bool(report['failed_or_unusable_cells'] or report['pending_cells']
                    or audited['consistency_failure_count'] or audited['unsupported_closure_count']
                    or any(value['status'] != 'parsed' for value in report['profiles'].values())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('prepare', 'launch', 'monitor'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--native-gate', type=Path)
    parser.add_argument('--profile-source', type=Path)
    parser.add_argument('--control-source', type=Path)
    parser.add_argument('--source-pins', type=Path)
    parser.add_argument('--job-id')
    parser.add_argument('--cores', type=int, nargs=2)
    parser.add_argument('--inputs', type=Path, default=Path.home() / 'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100')
    parser.add_argument('--references', type=Path, default=Path(__file__).resolve().parents[1] / 'references/dabia2013-n100-v011-2026-11-09/manifest.json')
    parser.add_argument('--sample-stride', type=int, default=1009)
    parser.add_argument('--sample-offset', type=int, default=0)
    parser.add_argument('--release-real-budget')
    parser.add_argument('--stop-before')
    args = parser.parse_args()
    if args.mode == 'monitor':
        print(json.dumps(monitor(args.output.resolve()), indent=2))
        return 0
    if args.mode == 'launch':
        if not all((args.job_id, args.release_real_budget, args.stop_before)):
            parser.error('launch requires job-id, exact release-real-budget schedule SHA and stop-before')
        return launch(args)
    if not all((args.plan, args.native_gate, args.profile_source, args.control_source, args.source_pins, args.job_id, args.cores)):
        parser.error('prepare requires plan, native-gate, profile-source, control-source, source-pins, job-id and two cores')
    if args.sample_stride <= 0 or not 0 <= args.sample_offset < args.sample_stride:
        parser.error('Invalid sample stride or offset')
    return prepare(args)


if __name__ == '__main__':
    raise SystemExit(main())
