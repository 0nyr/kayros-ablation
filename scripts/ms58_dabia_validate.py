"""Bounded CPLEX validation of the integrated repair on Dabia only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_paired_probe import candidate_manifest, frozen_existing_manifest
from probe_runner import atomic_json, digest, load_completed

SOURCE = '327e481713e1b9a3187fda2fc9f10f8d9d75b0c5'
PATCH_SHA = 'db4c10703514c458052022a1b85c8a1ccfdb404db83c158143dbfc13fc3e05e2'
LERA_SHA = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
CASES = [(25, 'C201', 60), (50, 'R201', 120), (100, 'C101', 60), (100, 'RC201', 300)]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def require_compute(job_id):
    if os.environ.get('OAR_JOB_ID') != job_id or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise RuntimeError('Validation requires its declared reserved compute job')


def input_identity(path):
    path = path.resolve()
    raw = json.loads(path.read_bytes())
    if (path.parent.parent.name != 'Dabia2013' or path.parent.parent.parent.name != 'TDVRPTW'
            or path.parent.name not in {'n=25', 'n=50', 'n=100'}
            or raw['num_customers'] != int(path.parent.name[2:])):
        raise ValueError('Only TDVRPTW/Dabia2013 at n25/n50/n100 is allowed')
    atf = (path.parent / raw['td']['atf_path']).resolve()
    witness = path.with_name(path.name.replace('.vrp.json', '.bks.Duration.json'))
    return {'instance': raw['instance_name'], 'size': raw['num_customers'],
            'input_path': str(path), 'input_sha256': sha256(path),
            'atf_path': str(atf), 'atf_sha256': sha256(atf),
            'witness_path': str(witness), 'witness_sha256': sha256(witness)}


def check_witness(loaded, routes):
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import check_td_solution
    result = check_td_solution(loaded, BenchmarkSolution(
        instance_name=loaded.instance.instance_name, routes=routes))
    return {'valid': result.is_valid(), 'checked_cost': result.routing_cost,
            'routes': routes, 'checker_evidence': result.model_dump(mode='json')}


def worker(args):
    require_compute(args.job_id)
    if args.instance is None or not 0 < args.horizon <= 300:
        raise ValueError('Workers require an allowed instance and a bounded horizon')
    identity = input_identity(args.instance)
    identity['checker'] = checker_identity()
    identity['script_sha256'] = sha256(Path(__file__))
    args.output.mkdir(parents=True, exist_ok=False)
    atomic_json(args.output / 'identity.json', identity)
    from mamut_routing_lib.td import load_td_instance
    loaded = load_td_instance(args.instance, verify_sha256=True, validate_complete=True)
    stored = json.loads(Path(identity['witness_path']).read_bytes())
    witness = check_witness(loaded, stored['routes'])
    atomic_json(args.output / 'witness.json', witness)
    if not witness['valid']:
        raise ValueError('Stored witness failed the pinned checker')
    if args.worker == 'witness':
        return 0
    from kayros import Params, _core, _lera, solve
    from kayros.lera import routes_to_mamut, solve_duration
    if _lera.LP_BACKEND != 'CPLEX':
        raise ValueError('Coupling requires the CPLEX extension')
    identity.update(source_id=SOURCE, backend=_lera.LP_BACKEND,
                    runtime_files={str(Path(m.__file__).resolve()): sha256(Path(m.__file__))
                                   for m in [_core, _lera]})
    atomic_json(args.output / 'identity.json', identity)
    params = Params(strategy='ils', ils_max_iterations=200)
    before = solve(loaded, params, seed=58)
    before_checked = check_witness(loaded, [list(r) for r in before.routes])
    atomic_json(args.output / 'ils-before.json', before_checked)
    if not before_checked['valid']:
        raise ValueError('ILS handoff witness failed the pinned checker')
    with (args.output / 'events.jsonl').open('x') as events:
        def callback(payload):
            events.write(json.dumps(payload, allow_nan=False) + '\n')
            events.flush()
        exact = solve_duration(loaded, initial_routes=before.routes, cut_limit=500,
                               time_limit_s=args.horizon, memory_limit_mb=16384, on_event=callback)
    atomic_json(args.output / 'native-result.json', exact)
    after = solve(loaded, params, seed=58)
    after_checked = check_witness(loaded, [list(r) for r in after.routes])
    atomic_json(args.output / 'ils-after.json', after_checked)
    returned_routes = ([r[1:-1] for r in routes_to_mamut(exact['routes'], loaded.instance.num_customers)]
                       if exact['routes'] else before_checked['routes'])
    returned = check_witness(loaded, returned_routes)
    log = exact['exact_log']
    costs = [witness['checked_cost'], before_checked['checked_cost']]
    if after_checked['valid']:
        costs.append(after_checked['checked_cost'])
    if returned['valid']:
        costs.append(returned['checked_cost'])
    feasible_cost = min(costs)
    bound = log.get('best_bound')
    events = [json.loads(line) for line in (args.output / 'events.jsonl').read_text().splitlines()]
    checks = {
        'backend_is_cplex': exact['effective_backend_settings']['lp']['backend'] == 'CPLEX',
        'cut_limit_is_500': exact['effective_algorithm_settings']['cut_limit'] == 500,
        'ils_state_unchanged': before.duration == after.duration and before.routes == after.routes,
        'ils_after_checker_valid': after_checked['valid'], 'returned_checker_valid': returned['valid'],
        'returned_cost_does_not_worsen_handoff': returned['valid']
            and returned['checked_cost'] <= before_checked['checked_cost'] + 1e-6,
        'bound_does_not_exceed_checked_witness': bound is None or bound <= feasible_cost + 1e-6,
        'all_event_bounds_compatible_with_checked_witnesses': all(
            event['global_lb'] <= feasible_cost + 1e-6 for event in events if 'global_lb' in event),
        'closed_value_matches_checked_solution': log['status'] != 'Optimum'
            or (returned['valid'] and abs(exact['value'] - returned['checked_cost']) <= 1e-6
                and exact['value'] <= feasible_cost + 1e-6),
        'no_rejected_infeasible_columns': exact.get('checker_infeasible_columns', 0) == 0,
    }
    summary = {'checks': checks, 'all_checks_passed': all(checks.values()),
               'status': log['status'], 'best_bound': bound, 'returned': returned,
               'interpretation': 'Deterministic ILS state and checked handoff. No speed or independent proof-completeness claim.'}
    atomic_json(args.output / 'summary.json', summary)
    return int(not summary['all_checks_passed'])


def terminate_tree(pid):
    """Stop only this runner's descendants, including separately sessioned workers."""
    try:
        os.kill(pid, signal.SIGSTOP)
    except ProcessLookupError:
        return
    children = {}
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            parent = next(int(line.split()[1]) for line in path.read_text().splitlines()
                          if line.startswith('PPid:'))
            children.setdefault(parent, []).append(int(path.parent.name))
        except (OSError, StopIteration, ValueError):
            pass
    order = [pid]
    for parent in order:
        order.extend(children.get(parent, []))
    for child in reversed(order):
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_command(output, name, command, core, timeout, cwd, expected=0, expected_text=None):
    launcher = ('import os,resource,sys; os.sched_setaffinity(0,{int(sys.argv[1])}); '
                'resource.setrlimit(resource.RLIMIT_AS,(16*1024**3,16*1024**3)); '
                'os.execvp(sys.argv[2],sys.argv[2:])')
    row = {'name': name, 'command': command, 'cpu': core, 'outer_timeout_s': timeout,
           'expected_returncode': expected, 'started_utc': stamp(), 'passed': False}
    atomic_json(output / f'{name}.started.json', row)
    try:
        with (output / f'{name}.stdout').open('x') as out, (output / f'{name}.stderr').open('x') as err:
            process = subprocess.Popen([sys.executable, '-c', launcher, str(core), *command],
                cwd=cwd, stdout=out, stderr=err, start_new_session=True)
            try:
                row.update(returncode=process.wait(timeout=timeout), outer_timeout=False)
            except subprocess.TimeoutExpired:
                terminate_tree(process.pid)
                row.update(returncode=process.wait(), outer_timeout=True)
        row['passed'] = row['returncode'] == expected and not row['outer_timeout']
        if expected_text:
            text = ''.join((output / f'{name}.{suffix}').read_text(errors='replace')
                           for suffix in ['stdout', 'stderr'])
            row['expected_diagnostic_present'] = expected_text in text
            row['passed'] &= row['expected_diagnostic_present']
    except Exception as error:
        row['error'] = f'{type(error).__name__}: {error}'
    row['finished_utc'] = stamp()
    atomic_json(output / f'{name}.result.json', row)
    return row


def audit_probe(result, cell, witness_cost, variant):
    events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
    candidates = result['validation']['candidates']
    checked = [row['checked_cost'] for row in candidates if row['valid']]
    best = min([witness_cost, *checked])
    backends = [event[key] for event in events for key in ['backend', 'lp_backend'] if key in event]
    bounds = [event['global_lb'] for event in events if isinstance(event.get('global_lb'), (int, float))]
    closures = [event for event in events if event.get('event') == 'closure']
    native_statuses = sorted({event['status'] for event in events if isinstance(event.get('status'), str)})
    checks = {'backend_observed_as_cplex': bool(backends) and set(backends) == {'CPLEX'},
        'probe_ended_normally_or_at_deadline': result['status'] in {'completed', 'deadline'},
        'no_probe_integrity_issues': not result['issues'],
        'all_returned_candidates_checker_valid': all(row['valid'] for row in candidates),
        'all_native_costs_match_checker': all(row.get('native_minus_checked') is not None
            and abs(row['native_minus_checked']) <= 1e-6 for row in candidates),
        'all_bounds_compatible_with_checked_witnesses': all(math.isfinite(value) and value <= best + 1e-6 for value in bounds),
        'closure_has_checked_incumbent': not closures or bool(checked),
        'closure_bound_matches_checked_incumbent': not closures or (bool(checked)
            and all(isinstance(event.get('global_lb'), (int, float))
                    and abs(event['global_lb'] - min(checked)) <= 1e-6 for event in closures)),
        'no_rejected_infeasible_columns': all(event.get('checker_infeasible_columns', 0) == 0
            for event in events if event.get('checker_infeasible_columns') is not None)}
    settings = [event.get('effective_backend_settings') for event in events
                if event.get('effective_backend_settings')]
    if settings:
        checks['effective_lp_backend_is_cplex'] = all(item['lp']['backend'] == 'CPLEX' for item in settings)
    if variant == 'minimal-lera':
        metadata = [event['metadata_equivalence'] for event in events if 'metadata_equivalence' in event]
        checks['native_materialized_metadata_matches'] = bool(metadata) and all(all(row.values()) for row in metadata)
    return {'checks': checks, 'all_checks_passed': all(checks.values()), 'probe_status': result['status'],
            'candidate_count': len(candidates), 'best_checked_cost': min(checked) if checked else None,
            'reported_closure': bool(closures), 'backend_events': backends,
            'native_statuses': native_statuses,
            'proof_status': result['validation']['proof']['status'],
            'interpretation': 'Bounded contradiction checks. A reported closure is not an independently accepted proof.'}


def expected_probe_identity(case, core, manifest, worker_python, worker, cells):
    """Reconstruct the launch contract independently of persisted cell contents."""
    arm = 'lera-exact' if manifest['runtime_kind'] == 'native' else 'kayros-bpca-cplex'
    worker = worker.resolve()
    worker_python = Path(os.path.abspath(worker_python))
    parameters = {'instance_name': case['instance'], 'instance_sha256': case['input_sha256'],
        'atf_sha256': case['atf_sha256'], 'orchestration_files': {
            name: sha256(worker.with_name(name)) for name in
            ['run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py']},
        'cpu_affinity': [core], 'memory_cap_bytes': 16384 * 1024 ** 2,
        'arm': arm, 'search_seed': None, 'repetition': 0, 'prefix_seconds': None,
        'memory_mb': 16384, 'worker_sha256': sha256(worker),
        'worker_python_sha256': sha256(worker_python)}
    environment = dict(os.environ, **manifest.get('worker_environment', {}),
        OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', PROBE_CPU_AFFINITY=str(core))
    command = [str(worker_python), str(worker), '--instance', case['input_path'],
               '--arm', arm, '--memory-mb', '16384']
    horizon = float(case['horizon_s'])
    cwd = str(worker.parent.parent)
    native_digest = digest({'manifest': manifest, 'parameters': parameters, 'horizon_s': horizon,
        'command': command, 'environment_hash': digest(environment), 'cwd': cwd})
    command += ['--native-result', str(cells.resolve() / 'native' / (native_digest + '.json'))]
    return {'schema_version': 1, 'manifest_hash': digest(manifest), 'parameters': parameters,
            'command': command, 'horizon_s': horizon, 'cwd': cwd, 'environment_hash': digest(environment)}


def main(args):
    require_compute(args.job_id)
    if any(name.startswith('KAYROS_') for name in os.environ):
        raise ValueError('Validation requires no inherited Kayros diagnostic or algorithm switches')
    for name in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
        os.environ[name] = '1'
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    checker = checker_identity()
    cores, seen = [], set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = tuple((topology / name).read_text().strip() for name in ['physical_package_id', 'core_id'])
        if key not in seen:
            seen.add(key)
            cores.append(cpu)
    if any(index < 0 or index >= len(cores) for index in args.core_indices):
        raise ValueError('Physical-core indices must be within the allocated topology')
    selected = [cores[index] for index in args.core_indices]
    if len(selected) != 4 or len(set(selected)) != 4:
        raise ValueError('Select 4 distinct allocated physical cores')
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if available < 80 * 1024 ** 2:
        raise ValueError('Four validation lanes need 64 GiB plus 16 GiB reserve')
    scripts = output / 'frozen-scripts'
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns('__pycache__'))
    source = root / 'sources/best'
    python = root / 'venv-best-cplex/bin/python'
    patchdir = root / 'patches-best'
    patch_hashes = {p.name: sha256(p) for p in patchdir.glob('*.patch')}
    if len(patch_hashes) != 5 or PATCH_SHA not in patch_hashes.values():
        raise ValueError('Integrated candidate requires its exact patch plus all 4 baseline repairs')
    baseline_hashes = {p.name: sha256(p) for p in (root / 'patches-reference').glob('*.patch')}
    if len(baseline_hashes) != 4 or any(patch_hashes.get(k) != v for k, v in baseline_hashes.items()):
        raise ValueError('Candidate baseline repair inventory differs from the frozen reference')
    for name in ['candidate-patches', 'upstream-patches', 'manifests']:
        (output / name).mkdir()
    candidate = candidate_manifest(python, patchdir, output / 'candidate-patches', checker, SOURCE)
    candidate['purpose'] = 'Dabia-only integrated correctness validation, CPLEX'
    atomic_json(output / 'manifests/candidate.json', candidate)
    upstream = frozen_existing_manifest(root / 'archived-manifests/upstream.json',
        output / 'upstream-patches', scripts, root / 'experiment/patches', checker)
    binary = root / 'upstream-minimal-build/bp'
    if sha256(binary) != LERA_SHA:
        raise ValueError('Minimal Lera binary differs from f2c9e20 validated build')
    old_binary = upstream['worker_environment']['UPSTREAM_PROBE_BINARY']
    del upstream['runtime_files'][old_binary]
    upstream['runtime_files'][str(binary)] = LERA_SHA
    upstream['worker_environment']['UPSTREAM_PROBE_BINARY'] = str(binary)
    upstream_patch = root / 'scripts/upstream-minimal-reference.patch'
    frozen_patch = output / 'upstream-patches' / upstream_patch.name
    shutil.copyfile(upstream_patch, frozen_patch)
    if sha256(frozen_patch) != 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf':
        raise ValueError('Minimal Lera source patch differs from the one-line repair')
    upstream['source_patch_files'][str(frozen_patch)] = sha256(frozen_patch)
    upstream.update(source_id='f2c9e20', backend='CPLEX', diagnostic_environment={},
                    purpose='Dabia-only validation with explicitly initialized minimal Lera reference')
    atomic_json(output / 'manifests/minimal-lera.json', upstream)
    cases = []
    for size, name, horizon in CASES:
        identity = input_identity(args.benchmarks / 'TDVRPTW/Dabia2013' / f'n={size}' / f'{name}.vrp.json')
        native_root = Path(upstream['worker_environment']['UPSTREAM_SOURCE_ROOT'])
        native_path = native_root / 'instances/dabia_et_al_2013' / f'{name}_{size}.json'
        experiment_path = native_root / 'experiments/bp.json'
        selected_upstream = json.loads(json.dumps(upstream))
        for path in [native_path, experiment_path]:
            actual = sha256(path)
            recorded = selected_upstream['runtime_files'].get(str(path))
            if recorded is not None and recorded != actual:
                raise ValueError('Upstream native input or experiment changed after archive freeze')
            selected_upstream['runtime_files'][str(path)] = actual
        selected_upstream['worker_environment']['UPSTREAM_NATIVE_INPUT_SHA256'] = sha256(native_path)
        selected_upstream['selected_native_input'] = {'path': str(native_path), 'sha256': sha256(native_path)}
        atomic_json(output / 'manifests' / f'n{size}-{name}-minimal-lera.json', selected_upstream)
        cases.append(dict(identity, horizon_s=horizon, key=f'n{size}-{name}', coupling=size < 100,
                          native_input_path=str(native_path), native_input_sha256=sha256(native_path),
                          upstream_experiment_sha256=sha256(experiment_path)))
    query = ("from pathlib import Path;import hashlib,json;from kayros import _core,_lera;"
             "print(json.dumps({'backend':_lera.LP_BACKEND,'core_sha256':hashlib.sha256(Path(_core.__file__).read_bytes()).hexdigest(),"
             "'lera_sha256':hashlib.sha256(Path(_lera.__file__).read_bytes()).hexdigest()}))")
    runtime = json.loads(subprocess.check_output([str(python), '-c', query], text=True, timeout=30))
    reference_runtime = json.loads(subprocess.check_output([str(root / 'venv-reference/bin/python'), '-c', query], text=True, timeout=30))
    if runtime['backend'] != 'CPLEX' or reference_runtime['backend'] != 'CPLEX':
        raise ValueError('Only CPLEX candidate and reference extensions are allowed here')
    source_files = [source / name for name in ['tests/cpp/test_point_addition.cpp',
        'tests/cpp/test_ordered_valuation.cpp', 'cpp/lera/goc/src/math/pwl_function.cpp',
        'cpp/lera/main/include/bcp/ordered_valuation.h', 'cpp/lera/main/src/bcp/bcp.cpp',
        'cpp/lera/main/src/bcp/spf.cpp']]
    plan = {'source_id': SOURCE, 'minimal_lera_source_id': 'f2c9e20', 'backend': 'CPLEX',
            'checker': checker, 'host': socket.gethostname(), 'oar_job_id': args.job_id,
            'created_utc': stamp(), 'cores': selected, 'memory_available_kib': available,
            'cases': cases, 'candidate_runtime': runtime, 'reference_runtime': reference_runtime,
            'core_binary_matches_reference': runtime['core_sha256'] == reference_runtime['core_sha256'],
            'source_files': {str(p): sha256(p) for p in source_files},
            'native_library_sha256': sha256(source / 'build/cplex/cpp/lera/liblera.a'),
            'driver_sha256': sha256(Path(__file__)),
            'scope': 'Synthetic native fixtures and Dabia-only witness, cold CPLEX and deterministic coupling checks. n25/n50 are correctness/startup checks only. No speed conclusion or accepted proof-completeness claim.'}
    atomic_json(output / 'plan.json', plan)
    native_rows = []
    for name, fixture, variant in [('point-reference', 'test_point_addition', 'reference'),
                                  ('point-candidate', 'test_point_addition', 'best'),
                                  ('ordered-candidate', 'test_ordered_valuation', 'best')]:
        tree = root / 'sources' / variant
        executable = output / name
        command = ['c++', '-std=c++23', '-O3', '-DNDEBUG', '-ffp-contract=off',
                   '-I' + str(tree / 'cpp/lera/goc/include'), '-I' + str(tree / 'cpp/lera/main/include'),
                   str(source / 'tests/cpp' / f'{fixture}.cpp')]
        if fixture == 'test_ordered_valuation':
            command.append(str(tree / 'cpp/lera/goc/src/linear_programming/highs/matrix_formulation.cpp'))
        command += [str(tree / 'build/cplex/cpp/lera/liblera.a'), '-ldl', '-lm', '-o', str(executable)]
        compilation = run_command(output, 'compile-' + name, command, selected[0], 120, source)
        native_rows.append(compilation)
        if compilation['passed']:
            native_rows.append(run_command(output, name, [str(executable)], selected[0], 30, source,
                expected=1 if variant == 'reference' else 0,
                expected_text='Adding a scalar point lost an attainable departure choice' if variant == 'reference' else None))
    native_passed = len(native_rows) == 6 and all(row['passed'] for row in native_rows)
    atomic_json(output / 'native-complete.json', {'results': native_rows, 'all_checks_passed': native_passed})
    if not native_passed:
        atomic_json(output / 'complete.json', {'native_checks_passed': False,
                    'solver_cases_skipped': cases, 'reason': 'Native gate failed', 'finished_utc': stamp()})
        return 1

    def lane(case, core):
        key = case['key']
        row = {'case': case, 'cpu': core, 'started_utc': stamp(), 'checks': {}, 'processes': []}
        try:
            def verify_inputs():
                for filename, digest in [('input_path', 'input_sha256'), ('atf_path', 'atf_sha256'),
                                         ('witness_path', 'witness_sha256')]:
                    if sha256(Path(case[filename])) != case[digest]:
                        raise ValueError('Dabia input or witness changed after schedule freeze')
            verify_inputs()
            for variant in ['candidate', 'minimal-lera']:
                row[variant] = None
            witness_command = [str(python), str(scripts / Path(__file__).name), '--worker', 'witness',
                '--job-id', args.job_id, '--instance', case['input_path'], '--output', str(output / f'{key}-witness')]
            witness_process = run_command(output, key + '-witness', witness_command, core, 90, scripts)
            row['processes'].append(witness_process)
            if not witness_process['passed']:
                raise ValueError('Witness validation failed or reached its outer deadline')
            witness = json.loads((output / f'{key}-witness/witness.json').read_bytes())
            row['witness_checked_cost'] = witness['checked_cost']
            for variant in ['candidate', 'minimal-lera']:
                # A pair runs sequentially on one core. No timing claim is made by this validation.
                verify_inputs()
                manifest_path = output / 'manifests' / (f'{key}-minimal-lera.json'
                    if variant == 'minimal-lera' else 'candidate.json')
                cell_manifest = json.loads(manifest_path.read_bytes())
                worker_python = python if variant == 'candidate' else Path(sys.executable)
                worker_path = scripts / ('kayros_worker.py' if variant == 'candidate' else 'upstream_event_worker.py')
                cells = output / f'{key}-{variant}-cells'
                expected_identity = expected_probe_identity(case, core, cell_manifest, worker_python, worker_path, cells)
                expected_run_id = digest(expected_identity)
                atomic_json(output / f'{key}-{variant}-expected-identity.json', expected_identity)
                command = [sys.executable, str(scripts / 'run_solver_probe.py'),
                    '--manifest', str(manifest_path),
                    '--worker-python', str(worker_python),
                    '--instance', case['input_path'], '--output', str(cells),
                    '--arm', 'kayros-bpca-cplex' if variant == 'candidate' else 'lera-exact',
                    '--horizon', str(case['horizon_s']), '--memory-mb', '16384', '--cpu-core', str(core)]
                if variant == 'minimal-lera':
                    command += ['--worker', str(scripts / 'upstream_event_worker.py')]
                process = run_command(output, key + '-' + variant, command, core, case['horizon_s'] + 90, scripts)
                row['processes'].append(process)
                if process['passed']:
                    result = json.loads((output / f'{key}-{variant}.stdout').read_bytes())
                    if result['run_id'] != expected_run_id:
                        raise ValueError('Probe output does not match the independently frozen launch identity')
                    cell = cells / expected_run_id
                    if load_completed(cell, expected_run_id=expected_run_id) != result:
                        raise ValueError('Probe stdout differs from its verified completed artifact')
                    row[variant] = audit_probe(result, cell, witness['checked_cost'], variant)
                    row[variant]['completed_artifact_verified'] = True
                    row[variant]['expected_run_id'] = expected_run_id
            if case['coupling']:
                verify_inputs()
                if any(sha256(Path(path)) != digest for path, digest in candidate['runtime_files'].items()):
                    raise ValueError('Candidate runtime changed before coupling')
                command = [str(python), str(scripts / Path(__file__).name), '--worker', 'coupling',
                    '--job-id', args.job_id, '--instance', case['input_path'], '--horizon', str(case['horizon_s']),
                    '--output', str(output / f'{key}-coupling')]
                process = run_command(output, key + '-coupling', command, core, case['horizon_s'] + 120, scripts)
                row['processes'].append(process)
                summary = output / f'{key}-coupling/summary.json'
                row['coupling'] = json.loads(summary.read_bytes()) if summary.exists() else None
            a, b = row['candidate'], row['minimal-lera']
            row['checks']['all_processes_passed'] = all(p['passed'] for p in row['processes'])
            row['checks']['both_probe_audits_passed'] = bool(a and b and a['all_checks_passed'] and b['all_checks_passed'])
            row['checks']['closed_values_agree_if_both_closed'] = not (a and b and a['reported_closure'] and b['reported_closure']) or abs(a['best_checked_cost'] - b['best_checked_cost']) <= 1e-6
            if case['coupling']:
                row['checks']['coupling_passed'] = bool(row.get('coupling') and row['coupling']['all_checks_passed'])
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        row['all_checks_passed'] = bool(row['checks']) and all(row['checks'].values()) and 'error' not in row
        row['finished_utc'] = stamp()
        atomic_json(output / f'{key}-result.json', row)
        print(json.dumps({'case': key, 'passed': row['all_checks_passed'], 'error': row.get('error')}), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(lane, case, core) for case, core in zip(cases, selected)]
        rows = [future.result() for future in futures]
    passed = native_passed and all(row['all_checks_passed'] for row in rows)
    closure_coverage = {variant: {
        'reported_closures': sum(bool(row.get(variant) and row[variant]['reported_closure']) for row in rows),
        'cases_without_observed_closure': [row['case']['key'] for row in rows
            if not row.get(variant) or not row[variant]['reported_closure']]}
        for variant in ['candidate', 'minimal-lera']}
    atomic_json(output / 'complete.json', {'source_id': SOURCE, 'minimal_lera_source_id': 'f2c9e20',
        'backend': 'CPLEX', 'native_checks_passed': native_passed, 'all_checks_passed': passed,
        'cases': rows, 'closure_coverage': closure_coverage, 'finished_utc': stamp(), 'scope': plan['scope']})
    return int(not passed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--root', type=Path, default=Path.home() / 'ms58-bpca')
    parser.add_argument('--output', type=Path, default=Path.home() / 'ms58-bpca/dabia-20261110/validation')
    parser.add_argument('--benchmarks', type=Path, default=Path.home() / 'kayros-sweeps/MAMUT-routing/benchmarks')
    parser.add_argument('--core-indices', type=int, nargs=4, default=[0, 1, 2, 3])
    parser.add_argument('--worker', choices=['witness', 'coupling'])
    parser.add_argument('--instance', type=Path)
    parser.add_argument('--horizon', type=float, default=120)
    args = parser.parse_args()
    raise SystemExit(worker(args) if args.worker else main(args))
