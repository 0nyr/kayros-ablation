"""Fixed-f2 versus fe8 EPS pairs on Dabia n100 C201, RC201 and C104."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sys
import time

from checker_contract import checker_identity, sha256
from ms58_analyze_pairs import closure_and_resource, native_work
from ms58_dabia_screen import freeze_inputs
from ms58_dabia_validate import check_witness, run_command
from ms58_dabia_wave import no_overrides, topology_snapshot
from ms58_lera_eps_build import CONTROL, SOURCE, CPLEX_SHA, COMPILER
from ms58_proof_consistency import apply_feasible_consistency, proof_evidence, validate_late_event_identity
from probe_runner import atomic_json, digest, load_completed

CONTROL_BINARY = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
CONTROL_PATCH = 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf'
EPS_PATCH = '23b36405d306a839cdcbe5c5893057bdb45aee5e87b30d7790d2aa5c0d59f061'
INSTANCES = ('C201', 'RC201', 'C104')
MEMORY_MB = 16384


def now():
    return datetime.now(timezone.utc).isoformat()


def launch_timeout(deadline, monotonic_deadline, horizon):
    """Reserve the full cell timeout and final audit margin before launching."""
    remaining = min((deadline - datetime.now(timezone.utc)).total_seconds(),
                    monotonic_deadline - time.monotonic())
    timeout = horizon + 90
    if remaining < timeout + 30:
        raise ValueError('Stop-before leaves insufficient time for this cell and audit margin')
    return timeout


def paired_lanes(cores):
    """Two repetitions reverse pair order. Every pair uses one physical core."""
    if len(cores) not in (3, 6) or len(set(cores)) != len(cores):
        raise ValueError('Declare 3 or 6 distinct physical cores')
    lanes = [[] for _ in cores]
    for index, name in enumerate(INSTANCES):
        for rep in (1, 2):
            lane = index if len(cores) == 3 else 2 * index + rep - 1
            order = ['minimal-lera', 'eps-parity'] if rep == 1 else ['eps-parity', 'minimal-lera']
            for position, variant in enumerate(order, 1):
                lanes[lane].append({'instance_name': name, 'replicate': rep, 'position': position,
                    'variant': variant, 'cpu_core': cores[lane], 'lane': lane,
                    'task_id': f'{name}--rep{rep}--{variant}'})
    return lanes


def expected_identity(task, manifest, scripts, output):
    worker = scripts / 'upstream_event_worker.py'
    parameters = {k: task[k] for k in ['instance_name', 'instance_sha256', 'atf_sha256']}
    parameters.update(memory_mb=MEMORY_MB, memory_cap_bytes=MEMORY_MB * 1024**2,
        cpu_affinity=[task['cpu_core']], arm='lera-exact', search_seed=None,
        repetition=task['replicate'], prefix_seconds=None, worker_sha256=sha256(worker),
        worker_python_sha256=sha256(Path(sys.executable)), orchestration_files={name: sha256(scripts / name)
            for name in ['run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py']})
    environment = dict(os.environ, **manifest['worker_environment'], OMP_NUM_THREADS='1',
        OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', PROBE_CPU_AFFINITY=str(task['cpu_core']))
    command = [sys.executable, str(worker), '--instance', task['instance_path'],
               '--arm', 'lera-exact', '--memory-mb', str(MEMORY_MB)]
    common = {'horizon_s': float(task['horizon_s']), 'command': command,
        'environment_hash': digest(environment), 'cwd': str(scripts.parent)}
    native_hash = digest({'manifest': manifest, 'parameters': parameters, **common})
    command = command + ['--native-result', str(output / 'cells/native' / f'{native_hash}.json')]
    return {'schema_version': 1, 'manifest_hash': digest(manifest), 'parameters': parameters,
            **common, 'command': command}


def freeze_variant(name, path, output, checker, build):
    manifest = json.loads(path.read_bytes())
    source = CONTROL if name == 'minimal-lera' else SOURCE
    declared = manifest.get('source_id', '')
    if not (declared == source or (name == 'minimal-lera' and declared.startswith('f2c9e20'))):
        raise ValueError('Manifest source differs from the declared diagnostic arm')
    if (manifest.get('checker') != checker or manifest.get('runtime_kind') != 'native'
            or manifest.get('backend', '').upper() != 'CPLEX' or manifest.get('diagnostic_environment', {})):
        raise ValueError('Both arms require native CPLEX, the pinned checker and no overrides')
    env = manifest['worker_environment']
    if set(env) - {'UPSTREAM_PROBE_BINARY', 'UPSTREAM_SOURCE_ROOT', 'UPSTREAM_NATIVE_INPUT_SHA256'}:
        raise ValueError('Native algorithm overrides are forbidden')
    if manifest.get('compiler') != COMPILER or manifest.get('cplex_static_library', {}).get('sha256') != CPLEX_SHA:
        raise ValueError('Compiler or CPLEX differs from the fixed control build')
    expected_binary = CONTROL_BINARY if name == 'minimal-lera' else build['binary_sha256']
    binary = str(Path(env['UPSTREAM_PROBE_BINARY']).resolve())
    if manifest['runtime_files'].get(binary) != expected_binary or sha256(Path(binary)) != expected_binary:
        raise ValueError('The native executable differs from its frozen build')
    required_patch = CONTROL_PATCH if name == 'minimal-lera' else EPS_PATCH
    if required_patch not in manifest['source_patch_files'].values():
        raise ValueError('The exact control or EPS patch is missing')
    runtime = {}
    worker = output / 'frozen-scripts/upstream_event_worker.py'
    for filename, expected in manifest['runtime_files'].items():
        old = Path(filename)
        target = worker if old.name == 'upstream_event_worker.py' else old
        if sha256(target) != expected:
            raise ValueError(f'Frozen runtime mismatch: {target}')
        runtime[str(target)] = expected
    runtime[str(worker)] = sha256(worker)
    target = output / 'patches' / name
    target.mkdir(parents=True)
    patches = {}
    for index, (filename, expected) in enumerate(manifest['source_patch_files'].items()):
        if sha256(Path(filename)) != expected:
            raise ValueError('Source patch changed')
        copy = target / f'{index:03d}-{Path(filename).name}'
        shutil.copyfile(filename, copy)
        patches[str(copy)] = expected
    native_root = Path(env['UPSTREAM_SOURCE_ROOT']).resolve()
    for name in ['experiments/bp.json', *[f'instances/dabia_et_al_2013/{n}_100.json' for n in INSTANCES]]:
        file = native_root / name
        actual = sha256(file)
        if str(file) in runtime and runtime[str(file)] != actual:
            raise ValueError('Native input differs from the archived digest')
        runtime[str(file)] = actual
    manifest.update(runtime_files=runtime, source_patch_files=patches,
        parent_manifest_sha256=sha256(path), diagnostic_role='isolated_algorithm_eps_comparison')
    return manifest


def audit(task, output, result, expected):
    cell = output / 'cells' / result['run_id']
    if result['run_id'] != digest(expected) or load_completed(cell, expected_run_id=digest(expected)) != result:
        raise ValueError('Completed artifact differs from the independently frozen run identity')
    identity = json.loads((cell / 'identity.json').read_bytes())
    if any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('Persisted identity differs from launch contract')
    events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
    late = [json.loads(line) for line in (cell / 'late-events.jsonl').read_text().splitlines()]
    validate_late_event_identity(late, identity)
    evidence = proof_evidence(result['validation'], events, task['horizon_s'], late, expected_inputs=task)
    launches = [e for e in events if e.get('phase') == 'native_launch']
    metadata_ok = bool(launches) and all(e.get('native_instance_sha256') == task['native_instance_sha256']
        and e.get('metadata_equivalence') and all(v is True for v in e['metadata_equivalence'].values())
        and e.get('cpu_affinity') == [task['cpu_core']]
        and e.get('address_space_limit_bytes') == MEMORY_MB * 1024**2 for e in launches)
    settings = [e for e in events if e.get('phase') == 'backend_settings']
    backend_ok = bool(settings) and all(e.get('backend') == 'CPLEX' and e.get('version') == '12.10.0.0'
        and e.get('effective', {}).get('threads') == 1
        and e['effective'].get('primal_feasibility') == 1e-7
        and e['effective'].get('dual_feasibility') == 1e-7 for e in settings)
    parameters_ok = bool(launches) and all({k: v for k, v in e.get('parameters', {}).items()
        if k != 'time_limit'} == task['native_parameters'] for e in launches)
    candidates = result['validation'].get('candidates', [])
    candidates_ok = all(c.get('valid') is True and isinstance(c.get('native_minus_checked'), (int, float))
        and abs(c['native_minus_checked']) <= 1e-6 for c in candidates)
    closure, resource = closure_and_resource(result, events, (cell / 'stderr.log').read_text(errors='replace'))
    native = result['validation'].get('native_result', {})
    work = native_work(json.loads((cell / native['relative_path']).read_bytes())) if native.get('sha256') else {'available': False}
    correction_counts = [v.get('corrected_count') for v in work.get('root_labeling', {}).values()]
    zero_correction = all(c in (None, 0) for c in correction_counts)
    checks = {'metadata_and_allocation_match': metadata_ok, 'backend_cplex_single_thread': backend_ok,
        'native_parameters_match': parameters_ok,
        'checked_candidates_match_native_cost': candidates_ok, 'no_positive_correction_count': zero_correction,
        'probe_integrity_clean': not result['issues'], 'normal_or_deadline': result['status'] in {'completed', 'deadline'}}
    raw_closure = closure
    if not all(checks.values()):
        closure = None
    return {**evidence, 'run_id': result['run_id'], 'audit_ok': all(checks.values()), 'checks': checks,
        'finished_task': True, 'raw_result_status': result['status'], 'resource_failure_kind': resource,
        'raw_reported_closure_s': raw_closure, 'reported_closure_s': closure,
        'capped_reported_closure_s': closure if closure is not None else task['horizon_s'],
        'native_work': work, 'correction_counts_observed': correction_counts,
        'native_result': native, 'result_sha256': sha256(cell / 'result.json'),
        'events_sha256': result['events_sha256'], 'result_issues': result['issues']}


def main(args):
    no_overrides(os.environ)
    if os.environ.get('OAR_JOB_ID') != args.job_id or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise ValueError('Pairs require their declared OAR compute allocation')
    nodefile = Path(os.environ['OAR_NODEFILE'])
    if socket.gethostname().split('.')[0] not in {n.split('.')[0] for n in nodefile.read_text().split()}:
        raise ValueError('Compute host is absent from the real OAR nodefile')
    lanes = paired_lanes(args.cores)
    topology = topology_snapshot(args.cores)
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if available < (16 * len(lanes) + 16) * 1024**2:
        raise ValueError('Insufficient RAM for 16 GiB per lane plus 16 GiB reserve')
    deadline = datetime.fromisoformat(args.stop_before)
    if deadline.tzinfo is None:
        raise ValueError('stop-before requires an explicit UTC offset')
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    monotonic_deadline = time.monotonic() + remaining
    maximum_seconds = max(map(len, lanes)) * (args.horizon + 90) + 180
    if remaining < maximum_seconds:
        raise ValueError('Insufficient declared reservation time for every scheduled cell and audit margin')
    for key in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
        os.environ[key] = '1'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ['manifests', 'rows', 'launch-logs']:
        (output / name).mkdir()
    scripts = output / 'frozen-scripts'
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns('__pycache__'))
    build = json.loads(args.candidate_build.read_bytes())
    if not build.get('build_passed') or build.get('error') or build['source_id'] != SOURCE:
        raise ValueError('EPS candidate has no successful identified build')
    candidate_manifest = args.candidate_build.resolve().with_name('manifest.json')
    if sha256(candidate_manifest) != build['manifest_sha256']:
        raise ValueError('EPS candidate manifest differs from its build record')
    checker = checker_identity()
    variants = {name: freeze_variant(name, path, output, checker, build) for name, path in
        [('minimal-lera', args.control_manifest.resolve()), ('eps-parity', candidate_manifest)]}
    inputs = freeze_inputs(args.inputs.resolve(), output)
    from mamut_routing_lib.td import load_td_instance
    for name, row in inputs.items():
        witness_path = args.inputs / f'{name}.bks.Duration.json'
        witness = check_witness(load_td_instance(Path(row['instance_path']), verify_sha256=True, validate_complete=True),
                                json.loads(witness_path.read_bytes())['routes'])
        if not witness['valid'] or not math.isfinite(witness['checked_cost']):
            raise ValueError('Stored comparison witness failed the pinned checker')
        atomic_json(output / f'{name}-witness.json', {'checker': checker, **row,
            'source_witness_sha256': sha256(witness_path), **witness})
        row.update(reference_cost=witness['checked_cost'],
                   reference_manifest_sha256=sha256(output / f'{name}-witness.json'))
    tasks = []
    for lane in lanes:
        for task in lane:
            task.update(inputs[task['instance_name']], horizon_s=float(args.horizon),
                source_id=CONTROL if task['variant'] == 'minimal-lera' else SOURCE)
            manifest = json.loads(json.dumps(variants[task['variant']]))
            native_path = Path(manifest['worker_environment']['UPSTREAM_SOURCE_ROOT']) / f"instances/dabia_et_al_2013/{task['instance_name']}_100.json"
            task['native_instance_sha256'] = sha256(native_path)
            experiment_path = Path(manifest['worker_environment']['UPSTREAM_SOURCE_ROOT']) / 'experiments/bp.json'
            parameters = next(e for e in json.loads(experiment_path.read_bytes())['experiments']
                              if e['name'] == 'BP-CUTS')
            task['native_parameters'] = {k: v for k, v in parameters.items() if k != 'time_limit'}
            manifest['worker_environment']['UPSTREAM_NATIVE_INPUT_SHA256'] = task['native_instance_sha256']
            manifest['diagnostic_cell'] = task.copy()
            path = output / 'manifests' / f"{task['task_id']}.json"
            atomic_json(path, manifest)
            task.update(manifest_path=str(path), manifest_sha256=sha256(path))
            tasks.append(task)
    # Both arms must receive identical native bytes and the unchanged BP-CUTS settings.
    roots = [Path(v['worker_environment']['UPSTREAM_SOURCE_ROOT']) for v in variants.values()]
    for relative in ['experiments/bp.json', *[f'instances/dabia_et_al_2013/{n}_100.json' for n in INSTANCES]]:
        if sha256(roots[0] / relative) != sha256(roots[1] / relative):
            raise ValueError('Native input or experiment bytes differ across the pair')
    schedule = {'created_utc': now(), 'host': socket.gethostname(), 'job_id': args.job_id,
        'checker': checker, 'physical_slots': topology, 'stop_before': args.stop_before,
        'worst_case_seconds_with_margin': maximum_seconds, 'memory_available_kib': available,
        'stop_before_enforcement': 'Recheck wall and monotonic time before every cell. Require full launcher timeout plus 30 s audit margin. Retain skipped cells.',
        'scheduled_count': len(tasks), 'scheduled_cells': tasks, 'lanes': lanes,
        'candidate_build_sha256': sha256(args.candidate_build),
        'driver_sha256': sha256(Path(__file__)), 'worker_sha256': sha256(scripts / 'upstream_event_worker.py'),
        'proof_consistency_helper_sha256': sha256(scripts / 'ms58_proof_consistency.py'),
        'frozen_files': {str(p.relative_to(output)): sha256(p) for directory in
            ['frozen-scripts', 'patches', 'frozen-inputs', 'manifests']
            for p in (output / directory).rglob('*') if p.is_file()},
        'scope': 'Isolated algorithm EPS sensitivity. Fixed f2 control and final five-arm comparison remain unchanged. Reported closure passes necessary consistency checks only.'}
    atomic_json(output / 'schedule.json', schedule)

    def execute_lane(lane):
        rows = []
        for task in lane:
            row = dict(task, audit_ok=False, finished_task=True, raw_result_status='launch_or_audit_failure',
                reported_closure_s=None, capped_reported_closure_s=args.horizon,
                native_work={'available': False}, started_utc=now())
            try:
                manifest_path = Path(task['manifest_path'])
                if sha256(manifest_path) != task['manifest_sha256']:
                    raise ValueError('Cell manifest changed after schedule freeze')
                for path, key in [('instance_path', 'instance_sha256'), ('atf_path', 'atf_sha256')]:
                    if sha256(Path(task[path])) != task[key]:
                        raise ValueError('Frozen input changed before cell launch')
                manifest = json.loads(manifest_path.read_bytes())
                expected = expected_identity(task, manifest, scripts, output)
                atomic_json(output / 'rows' / f"{task['task_id']}-expected-identity.json", expected)
                command = [sys.executable, str(scripts / 'run_solver_probe.py'), '--manifest', str(manifest_path),
                    '--worker-python', sys.executable, '--worker', str(scripts / 'upstream_event_worker.py'),
                    '--instance', task['instance_path'], '--output', str(output / 'cells'), '--arm', 'lera-exact',
                    '--horizon', str(args.horizon), '--memory-mb', str(MEMORY_MB), '--cpu-core', str(task['cpu_core']),
                    '--repetition', str(task['replicate'])]
                try:
                    timeout = launch_timeout(deadline, monotonic_deadline, args.horizon)
                except ValueError:
                    row['raw_result_status'] = 'skipped_stop_before'
                    raise
                process = run_command(output / 'launch-logs', task['task_id'], command, task['cpu_core'],
                    timeout, scripts.parent)
                row['process'] = process
                if not process['passed']:
                    raise ValueError('Probe launcher failed or exceeded its outer deadline')
                result = json.loads((output / 'launch-logs' / f"{task['task_id']}.stdout").read_bytes())
                row.update(audit(task, output, result, expected))
            except Exception as error:
                row['error'] = f'{type(error).__name__}: {error}'
            row['finished_utc'] = now()
            atomic_json(output / 'rows' / f"{task['task_id']}.json", row)
            rows.append(row)
            print(json.dumps({'task_id': task['task_id'], 'audit_ok': row['audit_ok'], 'error': row.get('error')}), flush=True)
        return rows

    with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        rows = [row for future in [pool.submit(execute_lane, lane) for lane in lanes] for row in future.result()]
    ceilings = apply_feasible_consistency(rows)
    complete = {'finished_utc': now(), 'schedule_sha256': sha256(output / 'schedule.json'),
        'scheduled_count': len(tasks), 'completed_row_count': len(rows), 'rows': rows,
        'feasible_ceilings': ceilings, 'all_observations_usable': all(r['proof_observation_usable'] for r in rows),
        'row_sha256': {p.name: sha256(p) for p in (output / 'rows').glob('*.json')},
        'scope': schedule['scope']}
    atomic_json(output / 'complete.json', complete)
    return int(not complete['all_observations_usable'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--control-manifest', type=Path, required=True)
    parser.add_argument('--candidate-build', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--horizon', type=int, choices=[600, 1200], required=True)
    parser.add_argument('--cores', type=int, nargs='+', required=True)
    parser.add_argument('--stop-before', required=True)
    raise SystemExit(main(parser.parse_args()))
