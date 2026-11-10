"""600 s cold CPLEX screen, 3 Dabia n100 instances x 3 repetitions.

Run with the pinned checker Python directly on one reserved compute node:
  --mode run --plan variants.json --job-id ID [--cores CPU ... CPU]
The plan is a list of {name, source_id, manifest_path, worker_python,
worker_path (optional), diagnostic_environment}. Minimal Lera f2c9e20 and
Kayros best 327e481 are mandatory. At most 2 optional Kayros variants may be
declared. Exactly 9 physical cores and 160 GiB available memory are required.
The read-only --mode monitor --output DIRECTORY reports the full denominator.
The existing probe owns the 600 s solver-process clock and 16 GiB limit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from development_wave import launcher_command
from ms58_analyze_pairs import closure_and_resource, native_work
from ms58_dabia_wave import backend_audit, checked_file, input_size, no_overrides, source_matches, topology_snapshot
from probe_runner import atomic_json, digest, load_completed

HORIZON = 600.0
MEMORY_MB = 16384
INSTANCES = ('C201', 'RC201', 'C104')
BEST = '327e481713e1b9a3187fda2fc9f10f8d9d75b0c5'
BEST_PATCH = 'db4c10703514c458052022a1b85c8a1ccfdb404db83c158143dbfc13fc3e05e2'
LERA = 'f2c9e20'
LERA_BINARY = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
LERA_PATCH = 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf'
SAFE_CORRECTION = 'a63cf65d3f7e90e4c310ffcedd7a6dabfae0ce3d'


def now():
    return datetime.now(timezone.utc).isoformat()


def immutable_json(path, value):
    """Atomic publication without replacing a prior cell observation."""
    path = Path(path)
    temporary = path.with_name(path.name + f'.tmp-{os.getpid()}')
    with temporary.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()


def validate_plan(plan):
    if not isinstance(plan, list) or not 2 <= len(plan) <= 4:
        raise ValueError('Declare 2 through 4 variants including minimal Lera and best')
    names, sources = set(), []
    for variant in plan:
        required = {'name', 'source_id', 'manifest_path', 'worker_python', 'diagnostic_environment'}
        if not isinstance(variant, dict) or required - variant.keys() or variant.keys() - required - {'worker_path'}:
            raise ValueError('Variant fields differ from the documented plan schema')
        name = variant['name']
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]*', name) or name in names:
            raise ValueError('Variant names must be distinct simple names')
        names.add(name)
        source = variant['source_id']
        if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{7,40}', source):
            raise ValueError('Each variant requires its exact source commit')
        sources.append(source)
        environment = variant['diagnostic_environment']
        if not isinstance(environment, dict) or any(
                not isinstance(key, str) or not re.fullmatch(r'KAYROS_[A-Z0-9_]+', key) or not isinstance(value, str)
                or '\0' in value for key, value in environment.items()):
            raise ValueError('Only explicit string-valued KAYROS_ switches may be declared')
        if environment and (source_matches(source, LERA) or source_matches(source, BEST)):
            raise ValueError('Minimal Lera and integrated best must run without diagnostic overrides')
        correction = environment.get('KAYROS_DIAGNOSTIC_CORRECTING')
        if source_matches(source, SAFE_CORRECTION):
            if correction != '1':
                raise ValueError('The safe-correction screen source requires KAYROS_DIAGNOSTIC_CORRECTING=1')
        elif correction is not None:
            raise ValueError('The correction override is restricted to the pinned safe-correction source')
    if sum(source_matches(s, LERA) for s in sources) != 1 or sum(source_matches(s, BEST) for s in sources) != 1:
        raise ValueError('Exactly one minimal Lera f2c9e20 and one integrated best 327e481 are required')


def balanced_orders(names, seed=20261110):
    """Nine lanes, balanced positions and pair order to within one observation."""
    names = list(names)
    random.Random(seed).shuffle(names)
    templates = {2: [(0, 1), (1, 0)],
        3: [(0, 1, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0), (0, 2, 1), (1, 0, 2)],
        4: [(0, 1, 3, 2), (1, 2, 0, 3), (2, 3, 1, 0), (3, 0, 2, 1)]}[len(names)]
    return [[names[i] for i in templates[lane % len(templates)]] for lane in range(9)]


def selected_cores(requested):
    if requested is not None:
        if len(requested) != 9:
            raise ValueError('Exactly 9 physical cores are required')
        return topology_snapshot(requested)
    records, physical = [], set()
    for cpu in sorted(os.sched_getaffinity(0)):
        record = topology_snapshot([cpu])[0]
        key = (record['physical_package_id'], record['physical_core_id'])
        if key not in physical:
            physical.add(key)
            records.append(record)
        if len(records) == 9:
            return records
    raise ValueError('The actual process affinity contains fewer than 9 physical cores')


def compute_preflight(job_id, requested):
    no_overrides(os.environ)
    host = socket.gethostname().split('.')[0]
    nodefile = os.environ.get('OAR_NODEFILE')
    if not job_id or os.environ.get('OAR_JOB_ID') != job_id or host.startswith(('fnancy', 'flyon')):
        raise ValueError('Run requires the declared real OAR compute job')
    if not nodefile or not Path(nodefile).is_file():
        raise ValueError('The real OAR nodefile is required')
    hosts = {h.split('.')[0] for h in Path(nodefile).read_text().split()}
    if host not in hosts:
        raise ValueError('Current compute host is absent from the allocation')
    topology = selected_cores(requested)
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if available < 160 * 1024 ** 2:
        raise ValueError('Nine 16 GiB lanes require 160 GiB available RAM including reserve')
    return {'host': socket.gethostname(), 'oar_job_id': job_id, 'created_utc': now(),
        'nodefile': Path(nodefile).read_text(), 'allowed_cores': sorted(os.sched_getaffinity(0)),
        'physical_slots': topology, 'memory_available_kib': available}


def freeze_variant(variant, output, checker):
    original = Path(variant['manifest_path']).resolve()
    manifest = json.loads(original.read_bytes())
    if manifest.get('schema_version') != 1 or manifest.get('checker') != checker:
        raise ValueError('Manifest schema or pinned checker mismatch')
    if not source_matches(manifest.get('source_id'), variant['source_id']):
        raise ValueError('Manifest source differs from declared source')
    native = source_matches(variant['source_id'], LERA)
    if manifest.get('runtime_kind', 'kayros') != ('native' if native else 'kayros'):
        raise ValueError('Manifest runtime kind differs from declared source')
    if manifest.get('backend', '').lower() != 'cplex':
        raise ValueError('Every screen variant must explicitly declare CPLEX')
    if manifest.get('diagnostic_environment', {}) != variant['diagnostic_environment']:
        raise ValueError('Manifest and plan diagnostic environment differ')
    runtime, patches = manifest.get('runtime_files', {}), manifest.get('source_patch_files', {})
    if not runtime or not patches:
        raise ValueError('Runtime and complete source patch inventory are required')
    for filename, expected in {**runtime, **patches}.items():
        checked_file(filename, expected)
    environment = manifest.get('worker_environment', {})
    if native:
        if set(environment) - {'UPSTREAM_PROBE_BINARY', 'UPSTREAM_SOURCE_ROOT', 'UPSTREAM_NATIVE_INPUT_SHA256'}:
            raise ValueError('Minimal Lera algorithm overrides are forbidden')
        binary = checked_file(environment['UPSTREAM_PROBE_BINARY'], LERA_BINARY)
        if str(binary) not in {str(Path(p).resolve()) for p in runtime} or LERA_PATCH not in patches.values():
            raise ValueError('Minimal Lera must pin its exact executable and correcting=false source patch')
        experiment = Path(environment['UPSTREAM_SOURCE_ROOT']).resolve() / 'experiments/bp.json'
        experiment_hash = sha256(experiment)
        if str(experiment) in runtime and runtime[str(experiment)] != experiment_hash:
            raise ValueError('Archived native experiment digest differs from current bytes')
        runtime[str(experiment)] = experiment_hash
    elif environment:
        raise ValueError('Kayros variants cannot set upstream worker environment')
    if source_matches(variant['source_id'], BEST) and BEST_PATCH not in patches.values():
        raise ValueError('Integrated best must pin its exact canonical and point patch')
    target = output / 'frozen-patches' / variant['name']
    target.mkdir(parents=True)
    copies = {}
    for index, (filename, expected) in enumerate(patches.items()):
        copy = target / f'{index:03d}-{Path(filename).name}'
        shutil.copyfile(filename, copy)
        copies[str(copy)] = expected
    scripts = output / 'frozen-scripts'
    source_worker = Path(variant.get('worker_path', str(Path(__file__).with_name(
        'upstream_event_worker.py' if native else 'kayros_worker.py')))).resolve()
    worker = scripts / f"screen-{variant['name']}-worker.py"
    shutil.copyfile(source_worker, worker)
    for filename in list(runtime):
        if Path(filename).resolve() == source_worker:
            del runtime[filename]
    runtime[str(worker)] = sha256(worker)
    python = Path(variant['worker_python']).absolute()
    if not python.is_file():
        raise ValueError('Worker Python is missing')
    if not native:
        observed = subprocess.check_output([str(python), '-c', 'from kayros import _lera; print(_lera.LP_BACKEND)'],
                                          text=True, timeout=30).strip()
        if observed != 'CPLEX':
            raise ValueError(f'Installed runtime is {observed}, expected CPLEX')
    shutil.copyfile(original, output / 'manifests' / f"{variant['name']}-original.json")
    manifest.update(runtime_files=runtime, source_patch_files=copies, parent_manifest_sha256=sha256(original),
        diagnostic_environment=variant['diagnostic_environment'], declared_source_commit=variant['source_id'],
        purpose='Dabia n100 bounded CPLEX development screen')
    return manifest, str(python), str(worker)


def freeze_inputs(inputs, output):
    frozen = output / 'frozen-inputs/benchmarks/TDVRPTW/Dabia2013/n=100'
    frozen.mkdir(parents=True)
    rows = {}
    for name in INSTANCES:
        path = (inputs / f'{name}.vrp.json').resolve()
        data = json.loads(path.read_bytes())
        if input_size(path) != 100 or data['num_customers'] != 100 or data['instance_name'] != name:
            raise ValueError('Only the declared Dabia n100 screen inputs are allowed')
        relative = Path(data['td']['atf_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('ATF must be relative and contained in the instance directory')
        atf = (path.parent / relative).resolve()
        target, target_atf = frozen / path.name, frozen / relative
        target_atf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        shutil.copyfile(atf, target_atf)
        rows[name] = {'instance_name': name, 'num_customers': 100, 'dataset': 'Dabia2013',
            'instance_key': f'Dabia2013/n=100/{name}', 'instance_path': str(target),
            'instance_sha256': sha256(target), 'atf_path': str(target_atf), 'atf_sha256': sha256(target_atf),
            'source_instance_path': str(path), 'source_atf_path': str(atf)}
    return rows


def prepare(args, allocation, checker):
    plan = json.loads(args.plan.read_bytes())
    validate_plan(plan)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for directory in ('manifests', 'rows', 'started', 'launch-logs'):
        (output / directory).mkdir()
    shutil.copyfile(args.plan, output / 'plan.json')
    shutil.copytree(Path(__file__).resolve().parent, output / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    variants = {v['name']: freeze_variant(v, output, checker) for v in plan}
    inputs = freeze_inputs(args.inputs, output)
    orders = balanced_orders(variants, args.seed)
    tasks = []
    for lane, ((name, replicate), slot, order) in enumerate(zip(
            [(n, r) for n in INSTANCES for r in range(1, 4)], allocation['physical_slots'], orders)):
        for position, variant in enumerate(order, 1):
            manifest, python, worker = variants[variant]
            manifest = json.loads(json.dumps(manifest))
            native = manifest['runtime_kind'] == 'native' if 'runtime_kind' in manifest else False
            task = dict(inputs[name], task_id=f'{name}--rep{replicate}--{variant}', lane=lane,
                variant=variant, source_id=manifest['declared_source_commit'], position=position,
                replicate=replicate, cpu_core=slot['cpu_core'], host=allocation['host'],
                arm='lera-exact' if native else 'kayros-bpca-cplex', runtime_key='upstream' if native else 'cplex',
                seed=None, prefix_s=None, horizon_s=HORIZON, memory_mb=MEMORY_MB,
                diagnostic_environment=manifest['diagnostic_environment'])
            if native:
                environment = manifest['worker_environment']
                native_input = Path(environment['UPSTREAM_SOURCE_ROOT']) / f'instances/dabia_et_al_2013/{name}_100.json'
                task['native_instance_sha256'] = sha256(native_input)
                environment['UPSTREAM_NATIVE_INPUT_SHA256'] = task['native_instance_sha256']
                filename = str(native_input.resolve())
                if filename in manifest['runtime_files'] and manifest['runtime_files'][filename] != task['native_instance_sha256']:
                    raise ValueError('Archived native input digest differs from current bytes')
                manifest['runtime_files'][filename] = task['native_instance_sha256']
            manifest['screen_cell'] = task.copy()
            manifest_path = output / 'manifests' / f"{task['task_id']}.json"
            atomic_json(manifest_path, manifest)
            task.update(manifest_path=str(manifest_path), manifest_sha256=sha256(manifest_path),
                worker_python=python, worker_python_sha256=sha256(Path(python)), worker_path=worker,
                worker_sha256=sha256(Path(worker)), output_directory=str(output / 'cells'),
                launcher_stdout=str(output / 'launch-logs' / f"{task['task_id']}.json"),
                launcher_stderr=str(output / 'launch-logs' / f"{task['task_id']}.stderr.log"))
            tasks.append(task)
    spec = {'schema_version': 1, 'created_utc': now(), 'allocation': allocation, 'checker': checker,
        'horizon_s': HORIZON, 'memory_mb': MEMORY_MB, 'clock_origin': 'solver_process_launch',
        'purpose': 'Development screen. Timing interpretation requires a separate audited analysis.',
        'seed': args.seed, 'variant_order_by_lane': orders, 'scheduled_count': len(tasks),
        'scheduled_cells': tasks, 'checker_python': sys.executable, 'checker_python_sha256': sha256(Path(sys.executable)),
        'frozen_files': {str(p.relative_to(output)): sha256(p) for base in
            ['frozen-scripts', 'frozen-patches', 'frozen-inputs', 'manifests'] for p in sorted((output / base).rglob('*')) if p.is_file()},
        'plan_sha256': sha256(output / 'plan.json')}
    immutable_json(output / 'schedule.json', spec)
    immutable_json(output / 'schedule-hash.json', {'sha256': sha256(output / 'schedule.json')})
    return spec


def expected_identity(task, output, manifest, launcher_environment):
    """Derive the expected run ID before reading anything emitted by the probe."""
    worker = Path(task['worker_path']).resolve()
    parameters = {'instance_name': task['instance_name'], 'instance_sha256': task['instance_sha256'],
        'atf_sha256': task['atf_sha256'], 'memory_mb': MEMORY_MB, 'memory_cap_bytes': MEMORY_MB * 1024 ** 2,
        'cpu_affinity': [task['cpu_core']], 'repetition': task['replicate'], 'search_seed': None,
        'prefix_seconds': None, 'arm': task['arm'], 'worker_sha256': task['worker_sha256'],
        'worker_python_sha256': task['worker_python_sha256'], 'orchestration_files': {
            name: sha256(worker.with_name(name)) for name in
            ['run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py']}}
    environment = dict(launcher_environment, **manifest.get('worker_environment', {}),
        OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', PROBE_CPU_AFFINITY=str(task['cpu_core']))
    command = [task['worker_python'], str(worker), '--instance', task['instance_path'],
               '--arm', task['arm'], '--memory-mb', str(MEMORY_MB)]
    cwd = str(worker.parent.parent)
    native_digest = digest({'manifest': manifest, 'parameters': parameters, 'horizon_s': HORIZON,
        'command': command, 'environment_hash': digest(environment), 'cwd': cwd})
    command += ['--native-result', str(output / 'cells/native' / (native_digest + '.json'))]
    return {'schema_version': 1, 'manifest_hash': digest(manifest), 'parameters': parameters,
        'command': command, 'horizon_s': HORIZON, 'cwd': cwd, 'environment_hash': digest(environment)}


def algorithm_setting_audit(task, native):
    """Keep configured expectations separate from observed native settings."""
    required = source_matches(task['source_id'], SAFE_CORRECTION)
    exact = (native.get('exact', native.get('native_result', {})) or {}) if native is not None else {}
    settings = exact.get('effective_algorithm_settings', {}) or {}
    expected_correction = required
    correction_observed = settings.get('label_correction')
    correction_status = ('passed' if correction_observed is expected_correction else 'failed') \
        if 'label_correction' in settings else ('failed' if required and native is not None else 'unknown')
    lazy = {key: settings[key] for key in ('lazy_extension', 'label_lazy_extension') if key in settings}
    lazy_status = ('passed' if all(value is True for value in lazy.values()) else 'failed') if lazy else 'unknown'
    statuses = [correction_status, lazy_status]
    return {'status': 'failed' if 'failed' in statuses else ('unknown' if 'unknown' in statuses else 'passed'),
        'native_sidecar_present': native is not None, 'effective_algorithm_settings': settings,
        'label_correction': {'expected': expected_correction, 'observed': correction_observed,
            'status': correction_status, 'required_in_native_sidecar': required,
            'expectation_basis': 'Pinned source and declared diagnostic environment',
            'reason': ('Native sidecar is absent, including outer-deadline termination' if native is None
                       else 'Native label_correction setting is absent' if 'label_correction' not in settings else None)},
        'lazy_extension': {'expected': True, 'observed_fields': lazy, 'status': lazy_status,
            'reason': 'Native setting is not exposed' if not lazy else None}}


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


def execute(task, output, spec):
    row = dict(task, started_utc=now(), audit_ok=False, observation_usable=False,
               native_work={'available': False}, run_id=None)
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
            code = subprocess.run(command, stdout=out, stderr=err, env=environment).returncode
        row['launcher_returncode'] = code
        if code:
            raise RuntimeError(f'Probe launcher failed with exit {code}')
        result = json.loads(Path(task['launcher_stdout']).read_bytes())
        row['run_id'] = result.get('run_id')
        row.update(audit_cell(task, output, spec['checker'], result, expected))
    except Exception as error:
        row.update(status='launch_or_audit_failure', reason=f'{type(error).__name__}: {error}')
    row['finished_utc'] = now()
    immutable_json(output / 'rows' / f"{task['task_id']}.json", row)
    print(json.dumps({k: row.get(k) for k in ('task_id', 'status', 'run_id', 'reason')}), flush=True)
    return row


def monitor(output):
    seal = json.loads((output / 'schedule-hash.json').read_bytes())
    spec = json.loads(checked_file(output / 'schedule.json', seal['sha256']).read_bytes())
    complete = output / 'complete.json'
    if complete.exists():
        for filename, expected in json.loads(complete.read_bytes())['row_sha256'].items():
            checked_file(output / 'rows' / filename, expected)
    rows, pending = [], []
    for task in spec['scheduled_cells']:
        path = output / 'rows' / f"{task['task_id']}.json"
        if not path.exists():
            pending.append({'task_id': task['task_id'], 'state': 'started' if
                (output / 'started' / f"{task['task_id']}.json").exists() else 'queued'})
            continue
        row = json.loads(path.read_bytes())
        if any(row.get(k) != v for k, v in task.items()):
            raise ValueError('Immutable row identity differs from schedule')
        rows.append(row)
    if spec['scheduled_count'] != len(rows) + len(pending):
        raise ValueError('Scheduled denominator differs from observed plus pending rows')
    return {'scheduled_cells': spec['scheduled_count'], 'completed_rows': len(rows),
        'statuses': dict(Counter(r['status'] for r in rows)),
        'audited_rows': sum(r.get('audit_ok') is True for r in rows),
        'failed_or_unusable_cells': [r['task_id'] for r in rows if not r.get('observation_usable')],
        'pending_cells': pending, 'complete_record_present': (output / 'complete.json').exists()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=('run', 'monitor'), required=True)
    parser.add_argument('--output', type=Path, default=Path.home() / 'ms58-bpca/dabia-20261110/screen-v1')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--job-id')
    parser.add_argument('--cores', type=int, nargs='+')
    parser.add_argument('--inputs', type=Path, default=Path.home() / 'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100')
    parser.add_argument('--seed', type=int, default=20261110)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == 'monitor':
        print(json.dumps(monitor(output), indent=2))
        return 0
    if not args.plan or not args.job_id:
        parser.error('run requires --plan and --job-id')
    allocation = compute_preflight(args.job_id, args.cores)
    spec = prepare(args, allocation, checker_identity())
    for path, expected in spec['frozen_files'].items():
        checked_file(output / path, expected)
    checked_file(spec['checker_python'], spec['checker_python_sha256'])
    immutable_json(output / 'launch.json', {'created_utc': now(), 'schedule_sha256': sha256(output / 'schedule.json'),
                                          'oar_job_id': args.job_id, 'controller_pid': os.getpid()})
    def lane(index):
        return [execute(task, output, spec) for task in spec['scheduled_cells'] if task['lane'] == index]
    with ThreadPoolExecutor(max_workers=9) as pool:
        list(pool.map(lane, range(9)))
    summary = monitor(output)
    immutable_json(output / 'complete.json', dict(summary, finished_utc=now(),
        row_sha256={p.name: sha256(p) for p in sorted((output / 'rows').glob('*.json'))}))
    print(json.dumps(summary, indent=2))
    return int(bool(summary['failed_or_unusable_cells'] or summary['pending_cells']))


if __name__ == '__main__':
    raise SystemExit(main())
