"""Freeze and run the 20-cell CPLEX fixed-witness exact-stage diagnostic.

prepare requires --design JSON --output NEW_DIRECTORY. The design has
schema_version=1, source_wave={path,sha256}, inputs (Dabia n100 directory),
checker_python and manifests keyed current, ub-only, dedup-only and lera.
Each manifest spec has path, sha256 and worker_python. Kayros specs also have
gate={path,sha256}; gates bind source_id, all_checks_passed and either
runtime_files_sha256=digest(manifest.runtime_files) or manifest_sha256.
launch requires --output, --job-id, --cores (4 physical CPUs), --stop-before
(timezone-qualified). No ILS is run. Preparation and checking precede the
600 s exact stage. The 660 s process cap and 680 s launcher cap are separate.
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
import signal
import socket
import subprocess
import sys
import time

from checker_contract import checker_identity, sha256
from ms58_analyze_pairs import EXPECTED_CHECKER, closure_and_resource, native_work
from ms58_dabia_preflight import observed_hardware
from ms58_dabia_validate import run_command
from ms58_dabia_wave import checked_file, input_size, no_overrides, source_matches, topology_snapshot
from ms58_proof_consistency import apply_feasible_consistency, proof_evidence, validate_late_event_identity
from probe_runner import atomic_json, digest, load_completed, run_probe
from validate_probe import validate_candidates

BASE = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
UB_ONLY = '74ed81486d2cd365061bdbce02264f6845d8ad30'
DEDUP = '391954aef4c131ad759d2862afc28458c3144026'
LERA = 'f2c9e205bd1ecc9afb45d5efe7fcb0df881f2b33'
LERA_BINARY = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
LERA_PATCH = 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf'
INSTANCES = ('C201', 'R201', 'RC201', 'RC105')
ARMS = ('current-warm', 'ub-only', 'dedup-only', 'current-cold', 'lera-minimal')
RUNTIMES = {'current-warm': 'current', 'ub-only': 'ub-only', 'dedup-only': 'dedup-only',
            'current-cold': 'current', 'lera-minimal': 'lera'}
SOURCES = {'current': BASE, 'ub-only': UB_ONLY, 'dedup-only': DEDUP, 'lera': LERA}
EXACT_SECONDS = 600.0
PROCESS_SECONDS = 660.0
LAUNCH_SECONDS = 680.0
MEMORY_MB = 16384


def now():
    return datetime.now(timezone.utc).isoformat()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_original_routes(raw, routes):
    """Check every original ID before coercion, native indexing or demand lookup."""
    n = raw['num_customers']
    if type(n) is not int or n <= 0 or not isinstance(routes, list) or not routes:
        raise ValueError('A nonempty list of customer routes is required')
    if any(not isinstance(route, list) or not route for route in routes):
        raise ValueError('Each witness route must be a nonempty list')
    vertices = [v for route in routes for v in route]
    if any(type(v) is not int or not 1 <= v <= n for v in vertices):
        raise ValueError('Original customer IDs must be integers in 1..n, without depots')
    if len(vertices) != n or set(vertices) != set(range(1, n + 1)):
        raise ValueError('Witness routes must cover every customer exactly once')
    demands, capacity = raw['demands'], raw['vehicle_capacity']
    if len(demands) != n + 1 or not finite(capacity) or capacity < 0:
        raise ValueError('Invalid materialized capacity or demands')
    if any(not finite(v) or v < 0 for v in demands):
        raise ValueError('Demands must be finite and nonnegative')
    if any(sum(demands[v] for v in route) > capacity for route in routes):
        raise ValueError('Witness exceeds vehicle capacity')
    if type(raw.get('num_vehicles')) is int and len(routes) > raw['num_vehicles']:
        raise ValueError('Witness exceeds the declared vehicle count')


def checked_witness(instance, routes, expected_cost=None):
    raw = json.loads(Path(instance).read_bytes())
    validate_original_routes(raw, routes)
    identity = checker_identity()
    if identity != EXPECTED_CHECKER:
        raise ValueError('Fixed-witness checking requires the pinned v0.11 checker')
    from mamut_routing_lib.enums import ObjectiveFunction
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import load_td_instance, check_td_solution
    loaded = load_td_instance(Path(instance), verify_sha256=True, validate_complete=True)
    result = check_td_solution(loaded, BenchmarkSolution(instance_name=raw['instance_name'], routes=routes),
                               ObjectiveFunction.DURATION)
    if not result.is_valid() or not finite(result.routing_cost) or result.routing_cost < 0:
        raise ValueError('Independent full-model witness check failed')
    evaluations = result.route_evaluations
    if len(evaluations) != len(routes) or any(not v.feasible or not finite(v.duration) or v.duration < 0
                                               or not finite(v.departure_time) for v in evaluations):
        raise ValueError('Every witness route needs finite independently checked costs and departure times')
    total = 0.
    for evaluation in sorted(evaluations, key=lambda v: v.route[0]):
        total += evaluation.duration
    if total != result.routing_cost or (expected_cost is not None and result.routing_cost != expected_cost):
        raise ValueError('Independent route total differs from the frozen witness cost')
    return {'checker': identity, 'checked_cost': result.routing_cost, 'routes': routes,
            'routes_sha256': digest(routes), 'route_multiset_sha256': digest(sorted(routes)),
            'checker_evidence': result.model_dump(mode='json')}, loaded


def make_design(args):
    variants = json.loads(args.variants.read_bytes())
    if isinstance(variants, dict):
        variants = variants['variants']
    names = {'correction-on': 'current', 'warm-incumbent-only': 'ub-only',
             'warm-dedup': 'dedup-only', 'lera-minimal': 'lera'}
    if len(variants) != 4 or {v['name'] for v in variants} != set(names):
        raise ValueError('Use the 4-arm successful warm-handoff gate variants.json')
    if any(v.get('all_checks_passed') is not True for v in variants):
        raise ValueError('All runtime/native/integration gates must pass before design creation')
    design = {'schema_version': 1, 'checker_python': sys.executable,
        'source_wave': {'path': str(args.source_wave.resolve()), 'sha256': sha256(args.source_wave / 'wave.json')},
        'inputs': str(args.inputs.resolve()), 'gate_variants_sha256': sha256(args.variants),
        'manifests': {names[v['name']]: {k: v[k] for k in ['path', 'sha256', 'worker_python', 'gate']}
                      for v in variants}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        stream.write(json.dumps(design, indent=2) + '\n')
    print(json.dumps({'design': str(args.output), 'scheduled_cells': 20, 'launched': False}))


def freeze_inputs_and_witnesses(design, output, checker):
    source = Path(design['source_wave']['path']).resolve()
    original = json.loads(checked_file(source / 'wave.json', design['source_wave']['sha256']).read_bytes())
    if original.get('kayros_source_id') != BASE or original.get('checker') != checker:
        raise ValueError('Witnesses must come from the declared 9d main panel and checker')
    rows = {}
    for name in INSTANCES:
        matches = [t for t in original['scheduled_cells'] if t['num_customers'] == 100
            and t['instance_name'] == name and t['arm_id'] == 'kayros-full-cplex-prefix5s'
            and t['seed'] == 123 and t['replicate'] == 2]
        if len(matches) != 1:
            raise ValueError('Exactly one original Full5 seed123 witness source is required')
        task = matches[0]
        stored_row = json.loads((source / 'rows' / f"{task['task_id']}.json").read_bytes())
        if any(stored_row.get(k) != v for k, v in task.items()):
            raise ValueError('Original Full5 row differs from its frozen schedule')
        cell = source / 'cells' / stored_row['run_id']
        result = load_completed(cell, expected_run_id=stored_row['run_id'])
        original_events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
        proof_evidence(result['validation'], original_events, task['horizon_s'], expected_inputs=task)
        identity = json.loads((cell / 'identity.json').read_bytes())
        manifest = json.loads(checked_file(source / 'manifests' / Path(task['manifest_path']).name,
                                           task['manifest_sha256']).read_bytes())
        if identity['manifest_hash'] != digest(manifest):
            raise ValueError('Original Full5 manifest differs from its run identity')
        native_path = (cell / result['validation']['native_result']['relative_path']).resolve()
        handoff = json.loads(native_path.read_bytes()).get('handoff_witness')
        if not handoff or not finite(handoff.get('native_cost')):
            raise ValueError('Original Full5 native artifact has no finite handoff witness')
        candidates = [c for c in result['validation'].get('candidates', []) if c.get('valid') is True
            and c.get('routes') == handoff['routes'] and c.get('elapsed_s', 6) <= 5]
        if not candidates or result['validation'].get('checker') != checker:
            raise ValueError('Original handoff lacks checked in-prefix candidate evidence')
        path = Path(design['inputs']) / f'{name}.vrp.json'
        raw = json.loads(checked_file(path, task['instance_sha256']).read_bytes())
        if input_size(path) != 100 or raw['instance_name'] != name or raw['num_customers'] != 100:
            raise ValueError('Only the 4 declared Dabia n100 instances are allowed')
        relative = Path(raw['td']['atf_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('ATF path must remain inside the frozen instance directory')
        atf = checked_file(path.parent / relative, task['atf_sha256'])
        target = output / 'frozen-inputs/benchmarks/TDVRPTW/Dabia2013/n=100' / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target_atf = target.parent / relative
        target_atf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        shutil.copyfile(atf, target_atf)
        witness, _ = checked_witness(target, handoff['routes'], candidates[0]['checked_cost'])
        if abs(witness['checked_cost'] - handoff['native_cost']) > 1e-6:
            raise ValueError('Original native handoff cost differs from the independent checker')
        provenance = {str(p.relative_to(source)): sha256(p) for p in
            [source / 'wave.json', cell / 'identity.json', cell / 'result.json', cell / 'events.jsonl', native_path]}
        witness.update(instance_name=name, num_customers=100, instance_sha256=task['instance_sha256'],
            atf_sha256=task['atf_sha256'], source_task_id=task['task_id'], source_run_id=stored_row['run_id'],
            source_seed=123, source_repetition=2, source_prefix_s=5, source_artifacts_sha256=provenance,
            source_checked_candidate_sequences=[c['sequence'] for c in candidates])
        witness_path = output / 'witnesses' / f'{name}.json'
        atomic_json(witness_path, witness)
        rows[name] = {'instance_name': name, 'instance_key': f'Dabia2013/n=100/{name}',
            'num_customers': 100, 'instance_path': str(target), 'instance_sha256': sha256(target),
            'atf_path': str(target_atf), 'atf_sha256': sha256(target_atf),
            'witness_path': str(witness_path), 'witness_sha256': sha256(witness_path),
            'reference_cost': witness['checked_cost'], 'reference_manifest_sha256': sha256(witness_path)}
    return rows


def freeze_runtime(key, spec, output, checker):
    path = checked_file(spec['path'], spec['sha256'])
    manifest = json.loads(path.read_bytes())
    if (manifest.get('schema_version') != 1 or manifest.get('checker') != checker
            or not source_matches(manifest.get('source_id'), SOURCES[key])
            or manifest.get('backend', '').lower() != 'cplex' or manifest.get('diagnostic_environment', {})):
        raise ValueError('Runtime source, backend, checker or diagnostic settings differ from the design')
    if manifest.get('runtime_kind', 'kayros') != ('native' if key == 'lera' else 'kayros'):
        raise ValueError('Runtime kind differs from its diagnostic arm')
    for filename, expected in manifest['runtime_files'].items():
        checked_file(filename, expected)
    gate_path = checked_file(spec['gate']['path'], spec['gate']['sha256'])
    gate = json.loads(gate_path.read_bytes())
    if (gate.get('source_id') != SOURCES[key] or gate.get('all_checks_passed') is not True
            or gate.get('checker', checker) != checker
            or (gate.get('runtime_files_sha256') != digest(manifest['runtime_files'])
                and gate.get('manifest_sha256') != spec['sha256'])):
        raise ValueError('Native/integration gate does not bind the successful runtime build')
    shutil.copyfile(gate_path, output / 'gates' / f'{key}.json')
    worker_python = Path(spec['worker_python']).absolute()
    checked_file(worker_python, sha256(worker_python))
    if key == 'lera':
        environment = manifest['worker_environment']
        if set(environment) - {'UPSTREAM_PROBE_BINARY', 'UPSTREAM_SOURCE_ROOT', 'UPSTREAM_NATIVE_INPUT_SHA256'}:
            raise ValueError('Fixed Lera algorithm overrides are forbidden')
        binary = checked_file(environment['UPSTREAM_PROBE_BINARY'], LERA_BINARY)
        if manifest['runtime_files'].get(str(binary)) != LERA_BINARY or LERA_PATCH not in manifest['source_patch_files'].values():
            raise ValueError('Fixed minimal Lera binary or one-line correction patch differs')
        root = Path(environment['UPSTREAM_SOURCE_ROOT']).resolve()
        for relative in ['experiments/bp.json', *[f'instances/dabia_et_al_2013/{name}_100.json' for name in INSTANCES]]:
            native = root / relative
            if str(native) in manifest['runtime_files'] and manifest['runtime_files'][str(native)] != sha256(native):
                raise ValueError('Fixed native input or parameter bytes changed')
            manifest['runtime_files'][str(native)] = sha256(native)
    else:
        if manifest.get('worker_environment', {}):
            raise ValueError('Kayros variants cannot inherit worker overrides')
        query = ("import importlib.metadata as m,json,hashlib;from pathlib import Path;"
            "from kayros import _lera;import kayros.lera as wrapper;d=m.distribution('kayros');"
            "print(json.dumps({'backend':_lera.LP_BACKEND,'files':[str(d.locate_file(p).resolve()) for p in d.files "
            "if str(p).endswith(('.py','.so'))],'imports':{str(Path(v.__file__).resolve()):"
            "hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest() for v in (_lera,wrapper)}}))")
        imported = json.loads(subprocess.check_output([str(worker_python), '-c', query], text=True, timeout=30))
        if imported['backend'] != 'CPLEX' or not imported['files'] or not set(imported['files']) <= set(manifest['runtime_files']):
            raise ValueError('Installed Kayros runtime is not completely pinned CPLEX')
        if any(manifest['runtime_files'].get(p) != value for p, value in imported['imports'].items()):
            raise ValueError('Imported exact extension or Python adapter shadows the pinned runtime')
    patches = {}
    target = output / 'patches' / key
    target.mkdir(parents=True)
    for index, (filename, expected) in enumerate(manifest['source_patch_files'].items()):
        original = checked_file(filename, expected)
        copied = target / f'{index:03d}-{original.name}'
        shutil.copyfile(original, copied)
        patches[str(copied)] = expected
    manifest.update(source_patch_files=patches, parent_manifest_sha256=spec['sha256'],
                    declared_source_commit=SOURCES[key], diagnostic_role='fixed_witness_exact_stage')
    target = output / 'manifests' / f'{key}.json'
    atomic_json(target, manifest)
    return {'path': str(target), 'sha256': sha256(target), 'worker_python': str(worker_python),
            'worker_python_sha256': sha256(worker_python)}


def prepare(args):
    no_overrides(os.environ)
    design = json.loads(args.design.read_bytes())
    if design.get('schema_version') != 1 or set(design['manifests']) != set(SOURCES):
        raise ValueError('The fixed-witness design requires the 4 declared runtime manifests')
    checker = checker_identity()
    if checker != EXPECTED_CHECKER or Path(design['checker_python']).absolute() != Path(sys.executable).absolute():
        raise ValueError('Prepare with the design checker Python and pinned checker')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ['manifests', 'gates', 'witnesses', 'rows', 'launch-logs']:
        (output / name).mkdir()
    shutil.copyfile(args.design, output / 'design.json')
    shutil.copytree(Path(__file__).resolve().parent, output / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    inputs = freeze_inputs_and_witnesses(design, output, checker)
    runtimes = {key: freeze_runtime(key, spec, output, checker) for key, spec in design['manifests'].items()}
    tasks = [{'task_id': f'{name}--{arm}', **inputs[name], 'arm_id': arm, 'runtime_key': RUNTIMES[arm],
        'source_id': SOURCES[RUNTIMES[arm]], 'horizon_s': EXACT_SECONDS,
        'process_horizon_s': PROCESS_SECONDS, 'memory_mb': MEMORY_MB, 'lane': lane,
        'warm': arm in ('current-warm', 'ub-only', 'dedup-only')}
        for lane, name in enumerate(INSTANCES) for arm in ARMS[lane:] + ARMS[:lane]]
    lera_manifest = json.loads(Path(runtimes['lera']['path']).read_bytes())
    lera_root = Path(lera_manifest['worker_environment']['UPSTREAM_SOURCE_ROOT']).resolve()
    native_parameters = next(e for e in json.loads((lera_root / 'experiments/bp.json').read_bytes())['experiments'] if e['name'] == 'BP-CUTS')
    for task in tasks:
        if task['runtime_key'] == 'lera':
            task.update(native_instance_sha256=sha256(lera_root / f"instances/dabia_et_al_2013/{task['instance_name']}_100.json"),
                        native_parameters={k: v for k, v in native_parameters.items() if k != 'time_limit'})
    plan = {'schema_version': 1, 'created_utc': now(), 'checker': checker, 'checker_python': sys.executable,
        'checker_python_sha256': sha256(Path(sys.executable)), 'runtimes': runtimes,
        'scheduled_count': 20, 'scheduled_cells': tasks,
        'clock': '600 s exact stage after witness/input preparation, separate from 660 s outer process clock',
        'qualification': 'Fixed-witness diagnostic. No ILS or full-process timing comparison. No replacement of the main panel or fixed Lera reference.'}
    plan['frozen_files_sha256'] = {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}
    atomic_json(output / 'plan.json', plan)
    print(json.dumps({'plan': str(output / 'plan.json'), 'scheduled_cells': 20, 'launched': False}))


def verify_plan(output):
    plan = json.loads((output / 'plan.json').read_bytes())
    if plan['checker'] != EXPECTED_CHECKER or plan['scheduled_count'] != 20 or len(plan['scheduled_cells']) != 20:
        raise ValueError('Fixed diagnostic checker or denominator differs')
    for relative, expected in plan['frozen_files_sha256'].items():
        checked_file(output / relative, expected)
    checked_file(plan['checker_python'], plan['checker_python_sha256'])
    for spec in plan['runtimes'].values():
        manifest = json.loads(checked_file(spec['path'], spec['sha256']).read_bytes())
        checked_file(spec['worker_python'], spec['worker_python_sha256'])
        for filename, expected in {**manifest['runtime_files'], **manifest['source_patch_files']}.items():
            checked_file(filename, expected)
    if {(t['instance_name'], t['arm_id']) for t in plan['scheduled_cells']} != {(n, a) for n in INSTANCES for a in ARMS}:
        raise ValueError('The 20 fixed-witness cells differ from the declared design')
    for task in plan['scheduled_cells']:
        key = RUNTIMES[task['arm_id']]
        if (task['runtime_key'] != key or task['source_id'] != SOURCES[key]
                or task['horizon_s'] != EXACT_SECONDS or task['process_horizon_s'] != PROCESS_SECONDS
                or task['memory_mb'] != MEMORY_MB or task['warm'] is not (task['arm_id'] in ARMS[:3])
                or task['lane'] != INSTANCES.index(task['instance_name'])):
            raise ValueError('A fixed diagnostic arm, budget, source or lane drifted')
    return plan


def cell_contract(task, spec, manifest, scripts, output):
    worker = scripts / 'ms58_fixed_witness_worker.py'
    environment = dict(os.environ, **manifest.get('worker_environment', {}), OMP_NUM_THREADS='1',
        OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', PROBE_CPU_AFFINITY=str(task['cpu_core']))
    parameters = {**task, 'worker_python_sha256': spec['worker_python_sha256'],
                  'worker_sha256': sha256(worker), 'manifest_sha256': spec['sha256']}
    command = [spec['worker_python'], str(worker), '--instance', task['instance_path'],
        '--witness', task['witness_path'], '--witness-sha256', task['witness_sha256'], '--arm', task['arm_id'],
        '--runtime-manifest', spec['path'], '--runtime-manifest-sha256', spec['sha256']]
    common = {'schema_version': 1, 'manifest_hash': digest(manifest), 'parameters': parameters,
              'horizon_s': PROCESS_SECONDS, 'cwd': str(output), 'environment_hash': digest(environment)}
    artifact_id = digest({**common, 'command': command})
    native = output / 'cells/native' / f'{artifact_id}.json'
    command += ['--native-result', str(native)]
    identity = {**common, 'command': command}
    return identity, environment, native


def cell(args):
    output = args.output.resolve()
    task = json.loads(args.task.read_bytes())
    plan = verify_plan(output)
    if ({k: v for k, v in task.items() if k != 'cpu_core'} not in plan['scheduled_cells']
            or task['cpu_core'] not in os.sched_getaffinity(0)):
        raise ValueError('Cell task or CPU differs from the frozen plan and actual affinity')
    launch_record = json.loads((output / 'launch.json').read_bytes())
    if (launch_record['plan_sha256'] != sha256(output / 'plan.json')
            or task not in launch_record['scheduled_cells']
            or launch_record['job_id'] != os.environ.get('OAR_JOB_ID')):
        raise ValueError('Cell is not bound to the frozen launched plan and actual OAR job')
    if checker_identity() != plan['checker']:
        raise ValueError('Cell checker drift')
    spec = plan['runtimes'][task['runtime_key']]
    manifest = json.loads(Path(spec['path']).read_bytes())
    identity, environment, native = cell_contract(task, spec, manifest, output / 'frozen-scripts', output)
    def validate(events):
        validation = validate_candidates(events, instance_path=Path(task['instance_path']),
            instance_sha256=task['instance_sha256'], atf_path=Path(task['atf_path']),
            atf_sha256=task['atf_sha256'], instance_name=task['instance_name'], expected_checker=plan['checker'])
        validation['native_result'] = {'relative_path': '../native/' + native.name,
                                      'sha256': sha256(native) if native.exists() else None}
        return validation
    result = run_probe(output=output / 'cells', manifest=manifest, parameters=identity['parameters'],
        command=identity['command'], horizon_s=PROCESS_SECONDS, cwd=output, environment=environment, validate=validate)
    print(json.dumps(result, allow_nan=False))


def stage_evidence(result, events, late, task):
    starts = [e for e in events if e.get('phase') == 'exact_stage_start']
    if len(starts) != 1:
        raise ValueError('Exactly one exact-stage clock origin is required')
    start = starts[0]['elapsed_s']
    accepted = [e for e in events if e['elapsed_s'] <= start + EXACT_SECONDS]
    excluded = [e for e in events if e['elapsed_s'] > start + EXACT_SECONDS] + late
    validation = dict(result['validation'], candidates=[c for c in result['validation'].get('candidates', [])
                                                      if c['elapsed_s'] <= start + EXACT_SECONDS])
    evidence = proof_evidence(validation, accepted, PROCESS_SECONDS, excluded, expected_inputs=task)
    for key in ['checked_candidates', 'global_bound_claims', 'raw_closure_claims', 'unavailable_global_bound_events']:
        for item in evidence.get(key, []):
            item['process_elapsed_s'] = item['elapsed_s']
            item['elapsed_s'] = max(0., item['elapsed_s'] - start)
            preceding = item.get('preceding_checked_candidate')
            if preceding and 'process_elapsed_s' not in preceding:
                preceding['process_elapsed_s'] = preceding['elapsed_s']
                preceding['elapsed_s'] = max(0., preceding['elapsed_s'] - start)
    return start, accepted, evidence


def audit(task, output, result, expected):
    root = output / 'cells' / result['run_id']
    if result['run_id'] != digest(expected) or load_completed(root, expected_run_id=digest(expected)) != result:
        raise ValueError('Completed cell differs from its independently derived identity')
    identity = json.loads((root / 'identity.json').read_bytes())
    if any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('Stored cell parameters differ from the scheduled contract')
    events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
    late = [json.loads(line) for line in (root / 'late-events.jsonl').read_text().splitlines()]
    validate_late_event_identity(late, identity)
    start, accepted, evidence = stage_evidence(result, events, late, task)
    alarm = result['returncode'] == -signal.SIGALRM and result['solver_elapsed_s'] >= start + EXACT_SECONDS - .001
    diagnostic_status = 'deadline' if alarm else result['status']
    scoped_result = dict(result, status=diagnostic_status)
    closure, resource = closure_and_resource(scoped_result, accepted, (root / 'stderr.log').read_text(errors='replace'))
    stage_closure = max(0., closure - start) if closure is not None else None
    native = result['validation'].get('native_result', {})
    payload = json.loads((root / native['relative_path']).read_bytes()) if native.get('sha256') else None
    exact = payload.get('exact', payload.get('native_result', {})) if payload else {}
    work = native_work(payload) if payload else {'available': False}
    ready = [e for e in events if e.get('phase') == 'fixed_witness_ready']
    witness = json.loads(Path(task['witness_path']).read_bytes())
    checks = {'witness_before_execution': len(ready) == 1 and ready[0]['elapsed_s'] <= start
        and ready[0].get('routes_sha256') == witness['routes_sha256']
        and ready[0].get('checked_cost') == witness['checked_cost']
        and ready[0].get('supplied_to_native') is task['warm']
        and ready[0].get('cpu_affinity') == [task['cpu_core']]
        and ready[0].get('address_space_limit_bytes') == MEMORY_MB * 1024**2,
        'clean_probe': not result['issues'], 'normal_or_exact_deadline': diagnostic_status in ('completed', 'deadline')}
    checks['candidate_costs_consistent'] = all(c.get('valid') is True and finite(c.get('native_minus_checked'))
        and abs(c['native_minus_checked']) <= 1e-6 for c in result['validation'].get('candidates', []))
    if payload:
        checks['native_sidecar_identity'] = (payload.get('run_id') == result['run_id']
            and payload.get('manifest_hash') == identity['manifest_hash']
            and payload.get('caller_witness_sha256') == task['witness_sha256']
            and payload.get('caller_witness_routes_sha256') == witness['routes_sha256']
            and payload.get('caller_witness_supplied_to_native') is task['warm'])
    settings = exact.get('effective_backend_settings')
    if task['runtime_key'] == 'lera':
        backend_events = [e for e in events if e.get('phase') == 'backend_settings']
        checks['backend'] = bool(backend_events) and all(e.get('backend') == 'CPLEX'
            and e.get('effective', {}).get('threads') == 1
            and e.get('version') == '12.10.0.0'
            and e['effective'].get('primal_feasibility') == 1e-7
            and e['effective'].get('dual_feasibility') == 1e-7 for e in backend_events)
        launch = [e for e in events if e.get('phase') == 'native_launch']
        checks['native_metadata'] = bool(launch) and all(e.get('metadata_equivalence')
            and all(v is True for v in e['metadata_equivalence'].values())
            and e.get('native_instance_sha256') == task['native_instance_sha256']
            and {k: v for k, v in e.get('parameters', {}).items() if k != 'time_limit'} == task['native_parameters']
            and e.get('parameters', {}).get('time_limit') == EXACT_SECONDS for e in launch)
    else:
        checks['backend'] = bool(settings) and all(v.get('backend') == 'CPLEX' and v.get('threads') == 1
                                                 for v in settings.values()) if settings else alarm
        algorithms = exact.get('effective_algorithm_settings', {})
        checks['algorithm_defaults'] = (all(algorithms.get(k) is True for k in ['label_correction', 'lazy_extension'])
            and algorithms.get('cut_limit') == 500 and algorithms.get('algorithm_epsilon') == 1e-6) if payload else alarm
        warm = exact.get('warm_start')
        if task['warm'] and payload:
            checks['warm_route_count'] = bool(warm) and warm.get('routes_given') == len(witness['routes'])
            checks['warm_ub'] = bool(warm) and finite(warm.get('ub')) and abs(warm['ub'] - witness['checked_cost']) <= 1e-6
            if task['arm_id'] in ('ub-only', 'dedup-only'):
                policy = 'incumbent_only' if task['arm_id'] == 'ub-only' else 'columns_and_incumbent_deduplicated'
                checks['warm_policy'] = ((warm or {}).get('policy') == policy and warm.get('ub_set') is True
                    and warm.get('routes_feasible') == len(witness['routes']) and warm.get('witness_storage') == 'caller'
                    and algorithms.get('warm_start_policy') == policy)
                if task['arm_id'] == 'ub-only':
                    checks['no_warm_columns'] = warm.get('routes_added') == 0
                else:
                    skipped = sum(len(route) == 1 for route in witness['routes'])
                    checks['deduplicated_columns'] = (warm.get('duplicate_columns_skipped') == skipped
                        and warm.get('routes_added') == len(witness['routes']) - skipped)
            else:
                checks['current_warm_columns'] = warm.get('routes_added') == len(witness['routes'])
        elif not task['warm']:
            checks['cold_has_no_handoff'] = not warm
    raw_closure = stage_closure
    if not all(checks.values()):
        stage_closure = None
    return {**evidence, 'run_id': result['run_id'], 'audit_ok': all(checks.values()), 'checks': checks,
        'finished_task': True, 'raw_probe_result_status': result['status'], 'raw_result_status': diagnostic_status,
        'exact_stage_deadline_by_signal': alarm, 'exact_stage_start_process_elapsed_s': start,
        'raw_reported_closure_s': raw_closure, 'reported_closure_s': stage_closure,
        'capped_reported_closure_s': stage_closure if stage_closure is not None else EXACT_SECONDS,
        'resource_failure_kind': resource, 'result_issues': result['issues'], 'native_work': work,
        'invalid_candidate_count': sum(c.get('valid') is not True for c in result['validation'].get('candidates', [])),
        'warm_start': exact.get('warm_start'), 'native_result': native,
        'sources': {name: sha256(root / name) for name in ['identity.json', 'result.json', 'events.jsonl', 'late-events.jsonl']}}


def launch(args):
    no_overrides(os.environ)
    output = args.output.resolve()
    plan = verify_plan(output)
    if (os.environ.get('OAR_JOB_ID') != args.job_id or socket.gethostname().startswith(('fnancy', 'flyon'))
            or socket.gethostname().split('.')[0] not in {h.split('.')[0] for h in Path(os.environ['OAR_NODEFILE']).read_text().split()}):
        raise ValueError('Launch requires the declared real compute allocation and nodefile')
    if len(args.cores) != 4:
        raise ValueError('Exactly 4 distinct allocated physical cores are required')
    topology = topology_snapshot(args.cores)
    hardware = observed_hardware()
    if hardware['memory_kib']['MemAvailable'] < 80 * 1024**2:
        raise ValueError('Four 16 GiB lanes require 80 GiB available RAM including reserve')
    deadline = datetime.fromisoformat(args.stop_before)
    if deadline.tzinfo is None:
        raise ValueError('stop-before requires an explicit UTC offset')
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    monotonic_deadline = time.monotonic() + remaining
    if remaining < 5 * LAUNCH_SECONDS + 120:
        raise ValueError('Insufficient stop budget for all 5 rounds and the audit margin')
    (output / 'launch-claim').mkdir()
    for key in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
        os.environ[key] = '1'
    tasks = [dict(t, cpu_core=args.cores[t['lane']]) for t in plan['scheduled_cells']]
    atomic_json(output / 'launch.json', {'job_id': args.job_id, 'host': socket.gethostname(),
        'plan_sha256': sha256(output / 'plan.json'), 'actual_nodefile': Path(os.environ['OAR_NODEFILE']).read_text(),
        'physical_slots': topology, 'hardware': hardware, 'cohort_sha256': digest(hardware['cohort_hardware']),
        'stop_before': args.stop_before, 'created_utc': now(), 'scheduled_cells': tasks})
    def lane(index):
        rows = []
        for task in [t for t in tasks if t['lane'] == index]:
            row = dict(task, audit_ok=False, finished_task=True, raw_result_status='launch_or_audit_failure',
                reported_closure_s=None, capped_reported_closure_s=EXACT_SECONDS,
                native_work={'available': False}, started_utc=now())
            try:
                spec = plan['runtimes'][task['runtime_key']]
                manifest = json.loads(Path(spec['path']).read_bytes())
                expected, _, _ = cell_contract(task, spec, manifest, output / 'frozen-scripts', output)
                task_path = output / 'rows' / f"{task['task_id']}-task.json"
                atomic_json(task_path, task)
                atomic_json(output / 'rows' / f"{task['task_id']}-expected-identity.json", expected)
                remaining = min((deadline - datetime.now(timezone.utc)).total_seconds(), monotonic_deadline - time.monotonic())
                if remaining < LAUNCH_SECONDS + 30:
                    row['raw_result_status'] = 'skipped_stop_before'
                    raise ValueError('Stop-before leaves insufficient time for this exact-stage cell')
                command = [plan['checker_python'], str(output / 'frozen-scripts/ms58_fixed_witness.py'),
                    '--mode', 'cell', '--output', str(output), '--task', str(task_path)]
                process = run_command(output / 'launch-logs', task['task_id'], command,
                                      task['cpu_core'], LAUNCH_SECONDS, output)
                row['process'] = process
                if not process['passed']:
                    raise ValueError('Cell launcher failed or exceeded its outer deadline')
                result = json.loads((output / 'launch-logs' / f"{task['task_id']}.stdout").read_bytes())
                row.update(audit(task, output, result, expected))
            except Exception as error:
                row['error'] = f'{type(error).__name__}: {error}'
            row['finished_utc'] = now()
            atomic_json(output / 'rows' / f"{task['task_id']}.json", row)
            rows.append(row)
            print(json.dumps({'task_id': task['task_id'], 'audit_ok': row['audit_ok'], 'error': row.get('error')}), flush=True)
        return rows
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for completed in [pool.submit(lane, i) for i in range(4)] for row in completed.result()]
    ceilings = apply_feasible_consistency(rows)
    atomic_json(output / 'complete.json', {'finished_utc': now(), 'plan_sha256': sha256(output / 'plan.json'),
        'launch_sha256': sha256(output / 'launch.json'), 'scheduled_count': 20, 'completed_row_count': len(rows),
        'all_observations_usable': all(r['proof_observation_usable'] for r in rows), 'rows': rows,
        'feasible_ceilings': ceilings, 'qualification': plan['qualification'],
        'row_sha256': {p.name: sha256(p) for p in (output / 'rows').glob('*.json')}})
    return int(not all(r['proof_observation_usable'] for r in rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=['design', 'prepare', 'launch', 'cell'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--design', type=Path)
    parser.add_argument('--job-id')
    parser.add_argument('--cores', type=int, nargs='+')
    parser.add_argument('--stop-before')
    parser.add_argument('--task', type=Path)
    parser.add_argument('--variants', type=Path)
    parser.add_argument('--source-wave', type=Path)
    parser.add_argument('--inputs', type=Path)
    args = parser.parse_args()
    if args.mode == 'prepare' and not args.design:
        parser.error('prepare requires --design')
    if args.mode == 'design' and (not args.variants or not args.source_wave or not args.inputs):
        parser.error('design requires --variants --source-wave --inputs')
    if args.mode == 'launch' and (not args.job_id or not args.cores or not args.stop_before):
        parser.error('launch requires --job-id --cores --stop-before')
    if args.mode == 'cell' and not args.task:
        parser.error('cell requires --task')
    raise SystemExit({'design': make_design, 'prepare': prepare, 'launch': launch, 'cell': cell}[args.mode](args))
