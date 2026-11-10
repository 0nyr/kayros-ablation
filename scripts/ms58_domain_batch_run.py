"""18-cell batch screen after the pinned 17-case native gate.

Run on a reserved compute node with --mode run, --job-id, --cores (3 or 6 physical CPUs), --native-gate complete.json, --plan artifacts/plan.json, --output (new), --latest-start-utc and --stop-before. This helper does not reserve nodes or launch remotely.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

from checker_contract import checker_identity, sha256
from ms58_dabia_screen import (BEST, BEST_PATCH, LERA, HORIZON, MEMORY_MB, audit_cell,
    expected_identity, freeze_variant, immutable_json, launcher_command, monitor, now)
from ms58_dabia_validate import terminate_tree
from ms58_dabia_wave import checked_file, input_size
from ms58_domain_batch_native import (BATCH, BATCH_PATCH, BATCH_BINARY, BEST_BINARY,
    FIXTURE_SOURCE, FIXTURE_SHA, allocation, source_and_build, validate_fixture)
from ms58_proof_consistency import apply_feasible_consistency, proof_evidence, validate_late_event_identity
from probe_runner import digest, load_completed

INSTANCES = ('C201', 'RC201')
SOURCES = {'best': BEST, 'cplex-domain-batch': BATCH, 'minimal-lera': LERA}
REFERENCE_SHA = 'ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'
ORDERS = [('best', 'cplex-domain-batch', 'minimal-lera'),
          ('cplex-domain-batch', 'minimal-lera', 'best'),
          ('minimal-lera', 'best', 'cplex-domain-batch'),
          ('best', 'minimal-lera', 'cplex-domain-batch'),
          ('minimal-lera', 'cplex-domain-batch', 'best'),
          ('cplex-domain-batch', 'best', 'minimal-lera')]
CELL_TIMEOUT = HORIZON + 90
AUDIT_MARGIN = 180
PREPARE_BUDGET = 6 * CELL_TIMEOUT + AUDIT_MARGIN


def comparison_triplets(cores):
    """Keep all six balanced triplets on 3 or 6 distinct physical lanes."""
    if len(cores) not in (3, 6) or len(set(cores)) != len(cores):
        raise ValueError('Declare 3 or 6 distinct physical cores')
    return [{'instance': name, 'replicate': replicate, 'lane': index % len(cores),
             'cpu_core': cores[index % len(cores)], 'order': ORDERS[index],
             'triplet': index, 'lane_round': index // len(cores)}
            for index, (name, replicate) in enumerate((n, r) for n in INSTANCES for r in range(1, 4))]


def remaining_seconds(deadline, monotonic_deadline):
    return min((deadline - datetime.now(timezone.utc)).total_seconds(),
               monotonic_deadline - time.monotonic())


def require_prepare_budget(deadline, monotonic_deadline):
    if remaining_seconds(deadline, monotonic_deadline) < PREPARE_BUDGET:
        raise ValueError('Stop-before must leave 6 * (600 + 90) + 180 seconds before preparation')


def require_cell_budget(deadline, monotonic_deadline):
    if remaining_seconds(deadline, monotonic_deadline) < CELL_TIMEOUT + AUDIT_MARGIN:
        raise ValueError('Stop-before leaves insufficient time for a full cell and final audit')


def hardware_snapshot(allocated, gate):
    cpu = Path('/proc/cpuinfo').read_text()
    fields = {}
    for key in ('model name', 'vendor_id', 'cpu family', 'model', 'stepping', 'microcode'):
        fields[key] = sorted({line.split(':', 1)[1].strip() for line in cpu.splitlines()
                              if ':' in line and line.split(':', 1)[0].strip() == key})
    return {'host': allocated['host'], 'kernel': platform.uname()._asdict(), 'cpu': fields,
        'physical_slots': allocated['physical_slots'], 'python_version': sys.version,
        'libc': platform.libc_ver(), 'runtime': gate['identity']['runtime'],
        'cplex_library_sha256': gate['identity']['cplex_library_sha256'],
        'compiler_sha256': gate['identity']['compiler_sha256']}


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


def validate_plan(plan):
    if not isinstance(plan, list) or len(plan) != 3 or {v.get('name') for v in plan} != set(SOURCES):
        raise ValueError('Require exactly best, cplex-domain-batch and minimal-lera')
    for variant in plan:
        required = {'name', 'source_id', 'manifest_path', 'worker_python', 'diagnostic_environment'}
        if (required - variant.keys() or variant.keys() - required - {'worker_path'}
                or variant['source_id'] != SOURCES[variant['name']] or variant['diagnostic_environment'] != {}):
            raise ValueError('Batch screen requires exact source pins and no diagnostic overrides')


def native_gate(args):
    path = args.native_gate.resolve()
    gate = json.loads(path.read_bytes())
    if (gate.get('source_id') != BATCH or gate.get('all_checks_passed') is not True
            or gate.get('fixture_source_id') != FIXTURE_SOURCE or gate.get('fixture_sha256') != FIXTURE_SHA
            or gate.get('case_count') != 17 or len(gate.get('results', [])) != 3
            or [row.get('name') for row in gate['results']] != ['compile', 'link', 'fixture']
            or not all(row.get('passed') is True for row in gate['results'])):
        raise ValueError('The exact 17-case native gate must pass before preparation')
    checked_file(path.parent / 'fixture.stdout', gate['fixture_stdout_sha256'])
    checked_file(path.parent / 'fixture.stderr', gate['fixture_stderr_sha256'])
    validate_fixture(json.loads((path.parent / 'fixture.stdout').read_bytes()))
    if source_and_build(args.root.resolve(), path.parent / 'source-identity.json') != gate['identity']:
        raise ValueError('Source or runtime identity differs from the completed native gate')
    checked_file(Path(gate['identity']['source_directory']) / 'build/cplex/cpp/lera/ms58_cplex_domain_fixture',
                 gate['executable_sha256'])
    seal = json.loads((args.plan.parent / 'complete.json').read_bytes())
    if seal['native_gate_sha256'] != sha256(path):
        raise ValueError('Prepared artifacts are bound to another native gate')
    for relative, expected in seal['files'].items():
        checked_file(args.plan.parent / relative, expected)
    if 'plan.json' not in seal['files'] or args.plan.name != 'plan.json':
        raise ValueError('Plan must be the sealed post-native-gate artifact plan')
    return gate


def frozen_inputs(inputs, output):
    root = output / 'frozen-inputs/benchmarks/TDVRPTW/Dabia2013/n=100'
    root.mkdir(parents=True)
    rows = {}
    for name in INSTANCES:
        path = (inputs / f'{name}.vrp.json').resolve()
        data = json.loads(path.read_bytes())
        if input_size(path) != 100 or data['num_customers'] != 100 or data['instance_name'] != name:
            raise ValueError('Only declared Dabia2013 n100 C201 and RC201 inputs are allowed')
        relative = Path(data['td']['atf_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('ATF must stay within the input directory')
        atf, target, target_atf = path.parent / relative, root / path.name, root / relative
        target_atf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        shutil.copyfile(atf, target_atf)
        rows[name] = {'instance_name': name, 'num_customers': 100, 'dataset': 'Dabia2013',
            'instance_key': f'Dabia2013/n=100/{name}', 'instance_path': str(target),
            'instance_sha256': sha256(target), 'atf_path': str(target_atf), 'atf_sha256': sha256(target_atf),
            'source_instance_path': str(path), 'source_atf_path': str(atf)}
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


def prepare(args, allocated, gate):
    plan = json.loads(args.plan.read_bytes())
    validate_plan(plan)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for directory in ('manifests', 'rows', 'started', 'launch-logs', 'native-gate'):
        (output / directory).mkdir()
    shutil.copyfile(args.plan, output / 'plan.json')
    for filename in ('complete.json', 'plan.json', 'source-identity.json', 'fixture.stdout', 'fixture.stderr'):
        shutil.copyfile(args.native_gate.parent / filename, output / 'native-gate' / filename)
    shutil.copytree(Path(__file__).resolve().parent, output / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    checker = checker_identity()
    variants = {v['name']: freeze_variant(v, output, checker) for v in plan}
    for name, patch, binary in [('best', BEST_PATCH, BEST_BINARY), ('cplex-domain-batch', BATCH_PATCH, BATCH_BINARY)]:
        manifest = variants[name][0]
        if (patch not in manifest['source_patch_files'].values()
                or manifest.get('native_gate_sha256') != sha256(args.native_gate)):
            raise ValueError('Kayros manifest source or native gate binding changed')
        lera = [p for p in manifest['runtime_files'] if Path(p).name.startswith('_lera.')]
        if len(lera) != 1 or manifest['runtime_files'][lera[0]] != binary:
            raise ValueError('Runtime does not match the explicitly pinned CPLEX extension')
    inputs = frozen_inputs(args.inputs, output)
    freeze_references(args.references, output, inputs, checker)
    tasks = []
    triplets = comparison_triplets([slot['cpu_core'] for slot in allocated['physical_slots']])
    for triplet in triplets:
        name, replicate, lane = triplet['instance'], triplet['replicate'], triplet['lane']
        for position, variant in enumerate(triplet['order'], 1):
            manifest, python, worker = variants[variant]
            manifest = json.loads(json.dumps(manifest))
            native = variant == 'minimal-lera'
            task = dict(inputs[name], task_id=f'{name}--rep{replicate}--{variant}', lane=lane,
                variant=variant, source_id=manifest['declared_source_commit'], position=position,
                replicate=replicate, cpu_core=triplet['cpu_core'], host=allocated['host'],
                triplet=triplet['triplet'], lane_round=triplet['lane_round'],
                lane_position=triplet['lane_round'] * 3 + position,
                arm='lera-exact' if native else 'kayros-bpca-cplex', runtime_key='upstream' if native else 'cplex',
                seed=None, prefix_s=None, horizon_s=HORIZON, memory_mb=MEMORY_MB, diagnostic_environment={})
            if native:
                environment = manifest['worker_environment']
                native_input = Path(environment['UPSTREAM_SOURCE_ROOT']) / f'instances/dabia_et_al_2013/{name}_100.json'
                task['native_instance_sha256'] = sha256(native_input)
                environment['UPSTREAM_NATIVE_INPUT_SHA256'] = task['native_instance_sha256']
                filename = str(native_input.resolve())
                if filename in manifest['runtime_files'] and manifest['runtime_files'][filename] != task['native_instance_sha256']:
                    raise ValueError('Frozen native Lera input changed')
                manifest['runtime_files'][filename] = task['native_instance_sha256']
            manifest['screen_cell'] = task.copy()
            path = output / 'manifests' / f"{task['task_id']}.json"
            immutable_json(path, manifest)
            task.update(manifest_path=str(path), manifest_sha256=sha256(path), worker_python=python,
                worker_python_sha256=sha256(Path(python)), worker_path=worker, worker_sha256=sha256(Path(worker)),
                output_directory=str(output / 'cells'), launcher_stdout=str(output / 'launch-logs' / f"{task['task_id']}.json"),
                launcher_stderr=str(output / 'launch-logs' / f"{task['task_id']}.stderr.log"))
            tasks.append(task)
    if len(tasks) != 18:
        raise ValueError('Batch screen must freeze all 18 cells')
    spec = {'schema_version': 1, 'screen_kind': 'cplex-domain-batch-18', 'created_utc': now(),
        'allocation': allocated, 'checker': checker, 'horizon_s': HORIZON, 'memory_mb': MEMORY_MB,
        'clock_origin': 'solver_process_launch', 'comparison_triplets': triplets, 'scheduled_count': 18,
        'physical_lane_count': len(allocated['physical_slots']),
        'memory_required_gib': len(allocated['physical_slots']) * 16 + 16,
        'hardware': hardware_snapshot(allocated, gate),
        'cohort': {'node_count': 1, 'hosts': [allocated['host']],
                   'scope': 'All batch comparisons run on this single node. Other cohorts are separate.'},
        'scheduled_cells': tasks, 'checker_python': sys.executable, 'checker_python_sha256': sha256(Path(sys.executable)),
        'native_gate_sha256': sha256(args.native_gate), 'native_gate_source_id': gate['source_id'],
        'latest_start_utc': args.latest_start_utc,
        'stop_before': args.stop_before, 'launcher_timeout_s': CELL_TIMEOUT,
        'prepare_budget_s': PREPARE_BUDGET, 'final_audit_margin_s': AUDIT_MARGIN,
        'stop_before_enforcement': 'Check wall and monotonic time before every launch. Reserve full cell timeout and final audit margin. Retain skipped cells.',
        'purpose': 'Next-pass CPLEX batching comparison. Work changes qualify timing interpretation.',
        'frozen_files': {str(p.relative_to(output)): sha256(p) for directory in
            ('frozen-scripts', 'frozen-patches', 'frozen-inputs', 'frozen-references', 'manifests', 'native-gate')
            for p in sorted((output / directory).rglob('*')) if p.is_file()},
        'plan_sha256': sha256(output / 'plan.json')}
    immutable_json(output / 'schedule.json', spec)
    immutable_json(output / 'schedule-hash.json', {'sha256': sha256(output / 'schedule.json')})
    return spec


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


def work_comparisons(output, spec, audited):
    results = []
    all_rows = {row['task_id']: row for row in audited}
    for name in INSTANCES:
        for replicate in range(1, 4):
            rows = {variant: all_rows[f'{name}--rep{replicate}--{variant}']
                    for variant in SOURCES}
            reference = rows['best']
            for variant in ('cplex-domain-batch', 'minimal-lera'):
                other = rows[variant]
                signature = reference.get('native_work', {}).get('whole_work_signature')
                results.append({'instance': name, 'replicate': replicate, 'comparator': variant,
                    'both_observations_usable': reference.get('observation_usable') is True and other.get('observation_usable') is True,
                    'same_recorded_whole_work': bool(signature) and signature == other.get('native_work', {}).get('whole_work_signature'),
                    'best_status': reference['status'], 'comparator_status': other['status'],
                    'best_closure_s': reference.get('reported_closure_s'), 'comparator_closure_s': other.get('reported_closure_s'),
                    'best_native_work': reference.get('native_work'), 'comparator_native_work': other.get('native_work'),
                    'qualification': 'Controller observation with pooled feasible-witness consistency. Independent analysis remains required.'})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('run', 'monitor'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path.home() / 'ms58-bpca')
    parser.add_argument('--job-id')
    parser.add_argument('--cores', nargs='+', type=int)
    parser.add_argument('--native-gate', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--latest-start-utc')
    parser.add_argument('--stop-before')
    parser.add_argument('--inputs', type=Path, default=Path.home() / 'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100')
    parser.add_argument('--references', type=Path, default=Path(__file__).resolve().parents[1] /
                        'references/dabia2013-n100-v011-2026-11-09/manifest.json')
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == 'monitor':
        print(json.dumps(monitor(output), indent=2))
        return 0
    if (not args.native_gate or not args.plan or not args.latest_start_utc or not args.stop_before
            or not args.cores or len(args.cores) not in (3, 6)):
        parser.error('run requires native-gate, plan, latest-start-utc, stop-before and 3 or 6 physical cores')
    comparison_triplets(args.cores)
    latest = datetime.fromisoformat(args.latest_start_utc)
    if latest.tzinfo is None or datetime.now(timezone.utc) >= latest:
        raise ValueError('Declared latest start has passed or lacks a timezone')
    deadline = datetime.fromisoformat(args.stop_before)
    if deadline.tzinfo is None:
        raise ValueError('stop-before requires an explicit UTC offset')
    monotonic_deadline = time.monotonic() + (deadline - datetime.now(timezone.utc)).total_seconds()
    require_prepare_budget(deadline, monotonic_deadline)
    allocated = allocation(args.job_id, args.cores, len(args.cores) * 16 + 16)
    gate = native_gate(args)
    require_prepare_budget(deadline, monotonic_deadline)
    spec = prepare(args, allocated, gate)
    for filename, expected in spec['frozen_files'].items():
        checked_file(output / filename, expected)
    checked_file(spec['checker_python'], spec['checker_python_sha256'])
    if datetime.now(timezone.utc) >= latest:
        raise ValueError('Latest start passed during preparation; frozen schedule retained without launching')
    immutable_json(output / 'launch.json', {'created_utc': now(), 'schedule_sha256': sha256(output / 'schedule.json'),
        'oar_job_id': args.job_id, 'controller_pid': os.getpid(), 'stop_before': args.stop_before,
        'monotonic_deadline': monotonic_deadline})
    def lane(index):
        return [execute_cell(task, output, spec, deadline, monotonic_deadline)
                for task in spec['scheduled_cells'] if task['lane'] == index]
    with ThreadPoolExecutor(max_workers=len(args.cores)) as pool:
        list(pool.map(lane, range(len(args.cores))))
    summary = monitor(output)
    audited = consistency_audit(output, spec)
    immutable_json(output / 'consistency-audit.json', audited)
    immutable_json(output / 'work-comparisons.json', {'pairs': work_comparisons(output, spec, audited['cells'])})
    summary.update(consistency_failure_count=audited['consistency_failure_count'],
                   unsupported_closure_count=audited['unsupported_closure_count'],
                   cohort=spec['cohort'], physical_lane_count=spec['physical_lane_count'])
    immutable_json(output / 'complete.json', dict(summary, finished_utc=now(),
        row_sha256={p.name: sha256(p) for p in sorted((output / 'rows').glob('*.json'))}))
    print(json.dumps(summary, indent=2))
    return int(bool(summary['failed_or_unusable_cells'] or summary['pending_cells']
                    or audited['consistency_failure_count'] or audited['unsupported_closure_count']))


if __name__ == '__main__':
    raise SystemExit(main())
