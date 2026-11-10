"""Separate 15-cell C104 outcome-selected fixed-witness mechanism case.

proposal validates stored local gates and the actual Full15 seed456 rep3
witness without solver execution or remote actions. design/prepare freeze the
live runtime plan. After a 4-host Gros reservation, preflight inspects actual
physical cores/runtime identities and launch runs one cell per isolated slot.
The 3600 s exact stage, 3660 s process cap and 3680 s launcher cap are distinct.
This is neither an unbiased performance panel nor full-process timing.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

import ms58_fixed_witness as fixed
from checker_contract import checker_identity, sha256
from ms58_dabia_preflight import cohort_runtime_identity, native_dependencies, observed_hardware
from ms58_dabia_validate import run_command
from ms58_dabia_wave import checked_file, no_overrides, topology_snapshot
from ms58_proof_consistency import apply_feasible_consistency
from probe_runner import atomic_json, digest, run_probe
from validate_probe import validate_candidates

EXACT = 3600.
PROCESS = 3660.
LAUNCH = 3680.
COUNTS = (4, 4, 4, 3)
SCOPE = ('Outcome-selected C104 mechanism case using the main Full15 seed456 repetition3 witness that later closed. '
         'Three fixed-witness repetitions per arm. No ILS. Not an unbiased performance estimate or full-process timing. '
         'No global handoff-policy recommendation or independent proof certification.')


def now():
    return datetime.now(timezone.utc).isoformat()


def placement():
    tasks, used = [], Counter()
    for repetition in (1, 2, 3):
        for index, arm in enumerate(fixed.ARMS):
            host = (index + repetition - 1) % 4
            tasks.append({'task_id': f'C104--rep{repetition}--{arm}', 'instance_name': 'C104',
                'arm_id': arm, 'replicate': repetition, 'host_role': host, 'slot_ordinal': used[host]})
            used[host] += 1
    if tuple(used[i] for i in range(4)) != COUNTS:
        raise ValueError('C104 placement must occupy exactly 4/4/4/3 isolated slots')
    return tasks


def local_variant_artifacts(variants_path):
    variants = json.loads(variants_path.read_bytes())
    if isinstance(variants, dict):
        variants = variants['variants']
    names = {'correction-on': 'current', 'warm-incumbent-only': 'ub-only',
             'warm-dedup': 'dedup-only', 'lera-minimal': 'lera'}
    if len(variants) != 4 or {v['name'] for v in variants} != set(names):
        raise ValueError('The 4 successful warm-handoff gate variants are required')
    records = {}
    for variant in variants:
        key = names[variant['name']]
        manifest_path = variants_path.parent / variant['name'] / Path(variant['path']).name
        gate_path = variants_path.parent / variant['name'] / Path(variant['gate']['path']).name
        manifest = json.loads(checked_file(manifest_path, variant['sha256']).read_bytes())
        gate = json.loads(checked_file(gate_path, variant['gate']['sha256']).read_bytes())
        if (variant.get('all_checks_passed') is not True or gate.get('all_checks_passed') is not True
                or gate.get('source_id') != fixed.SOURCES[key]
                or not fixed.source_matches(manifest.get('source_id'), fixed.SOURCES[key])
                or manifest.get('checker') != fixed.EXPECTED_CHECKER
                or manifest.get('backend', '').lower() != 'cplex'
                or (gate.get('runtime_files_sha256') != digest(manifest['runtime_files'])
                    and gate.get('manifest_sha256') != variant['sha256'])):
            raise ValueError('Stored source, gate, backend, checker or runtime binding mismatch')
        if key == 'lera' and (fixed.LERA_BINARY not in manifest['runtime_files'].values()
                             or fixed.LERA_PATCH not in manifest['source_patch_files'].values()):
            raise ValueError('Stored fixed Lera identity differs')
        verified_patches = {}
        for filename, expected in manifest['source_patch_files'].items():
            relative = Path(filename).relative_to(Path(variant['path']).parent)
            copied = manifest_path.parent / relative
            checked_file(copied, expected)
            verified_patches[str(copied)] = expected
        records[key] = {'source_id': fixed.SOURCES[key], 'manifest_path': str(manifest_path),
            'manifest_sha256': sha256(manifest_path), 'gate_path': str(gate_path), 'gate_sha256': sha256(gate_path),
            'runtime_files_sha256': digest(manifest['runtime_files']),
            'runtime_files': manifest['runtime_files'], 'source_patch_files': manifest['source_patch_files'],
            'verified_stored_source_patches': verified_patches,
            'live_runtime_validation': 'Pending on the reserved allocation. Stored gate evidence checked locally.'}
    return records


def proposal(args):
    no_overrides(os.environ)
    records = local_variant_artifacts(args.variants.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'witnesses').mkdir()
    for key, record in records.items():
        target = output / 'stored-gates' / key
        (target / 'patches').mkdir(parents=True)
        shutil.copyfile(record['manifest_path'], target / 'manifest.json')
        shutil.copyfile(record['gate_path'], target / 'gate.json')
        for filename in record['verified_stored_source_patches']:
            shutil.copyfile(filename, target / 'patches' / Path(filename).name)
    sources = output / 'protocol-sources'
    sources.mkdir()
    for name in ['ms58_c104_fixed_witness.py', 'ms58_c104_fixed_witness_test.py', 'ms58_fixed_witness.py',
                 'ms58_fixed_witness_worker.py', 'ms58_fixed_witness_test.py', 'ms58_proof_consistency.py']:
        shutil.copyfile(Path(__file__).with_name(name), sources / name)
    design = {'source_wave': {'path': str(args.source_wave.resolve()), 'sha256': sha256(args.source_wave / 'wave.json')},
              'inputs': str(args.inputs.resolve())}
    row = fixed.freeze_inputs_and_witnesses(design, output, fixed.EXPECTED_CHECKER, c104_confirmation=True)['C104']
    witness = json.loads(Path(row['witness_path']).read_bytes())
    result = {'schema_version': 1, 'created_utc': now(), 'status': 'validated_stored_proposal_no_solver_runs',
        'qualification': SCOPE, 'instances': ['C104'], 'arms': list(fixed.ARMS), 'repetitions': 3,
        'scheduled_count': 15, 'physical_slot_counts': list(COUNTS), 'planned_placement': placement(),
        'exact_stage_s': EXACT, 'outer_process_s': PROCESS, 'launcher_cap_s': LAUNCH,
        'minimum_launch_budget_s': LAUNCH + 120, 'recommended_reservation': '4 Gros hosts for 1 h 20 min or 2 h',
        'witness': witness, 'frozen_input': row, 'stored_runtime_gate_validation': records,
        'source_wave_sha256': design['source_wave']['sha256'], 'gate_variants_sha256': sha256(args.variants),
        'shared_proof_helper_sha256': sha256(Path(__file__).with_name('ms58_proof_consistency.py')),
        'remaining_before_any_solver': ['Live runtime-file verification and imported-extension identity on every reserved host.',
            'Homogeneous CPU/kernel/runtime cohort and 15 physical cores inside the actual OAR allocation.',
            'Frozen live plan, exact host/slot assignment and stop-before budget.'],
        'new_solver_runs': 0}
    result['frozen_files_sha256'] = {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}
    atomic_json(output / 'proposal.json', result)
    print(json.dumps({'proposal': str(output / 'proposal.json'), 'sha256': sha256(output / 'proposal.json'),
                      'source_closure_s': witness['source_reported_closure_s'], 'new_solver_runs': 0}))


def design(args):
    fixed.make_design(args)
    result = json.loads(args.output.read_bytes())
    result.update(purpose='c104_outcome_selected_fixed_witness', exact_stage_s=EXACT, repetitions=3,
                  qualification=SCOPE, witness_source={'instance': 'C104', 'arm': 'kayros-full-cplex-prefix15s',
                                                       'seed': 456, 'replicate': 3})
    atomic_json(args.output, result)


def prepare(args):
    no_overrides(os.environ)
    declared = json.loads(args.design.read_bytes())
    if (declared.get('schema_version') != 1 or declared.get('purpose') != 'c104_outcome_selected_fixed_witness'
            or declared.get('exact_stage_s') != EXACT or declared.get('repetitions') != 3
            or set(declared['manifests']) != set(fixed.SOURCES)):
        raise ValueError('Use the declared C104 3-repetition 3600 s design')
    if checker_identity() != fixed.EXPECTED_CHECKER or Path(declared['checker_python']).absolute() != Path(sys.executable).absolute():
        raise ValueError('Prepare with the declared pinned checker Python')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ['manifests', 'gates', 'witnesses', 'rows', 'launch-logs', 'allocation-snapshots', 'allocation-logs', 'node-logs']:
        (output / name).mkdir()
    shutil.copyfile(args.design, output / 'design.json')
    shutil.copytree(Path(__file__).resolve().parent, output / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    inputs = fixed.freeze_inputs_and_witnesses(declared, output, fixed.EXPECTED_CHECKER, c104_confirmation=True)['C104']
    runtimes = {key: fixed.freeze_runtime(key, spec, output, fixed.EXPECTED_CHECKER, c104_confirmation=True)
                for key, spec in declared['manifests'].items()}
    lera = json.loads(Path(runtimes['lera']['path']).read_bytes())
    source = Path(lera['worker_environment']['UPSTREAM_SOURCE_ROOT']).resolve()
    params = next(e for e in json.loads((source / 'experiments/bp.json').read_bytes())['experiments'] if e['name'] == 'BP-CUTS')
    tasks = []
    for item in placement():
        arm, key = item['arm_id'], fixed.RUNTIMES[item['arm_id']]
        task = {**inputs, **item, 'runtime_key': key, 'source_id': fixed.SOURCES[key],
                'warm': arm in fixed.ARMS[:3], 'horizon_s': EXACT, 'process_horizon_s': PROCESS,
                'memory_mb': fixed.MEMORY_MB}
        if key == 'lera':
            task.update(native_instance_sha256=sha256(source / 'instances/dabia_et_al_2013/C104_100.json'),
                        native_parameters={k: v for k, v in params.items() if k != 'time_limit'})
        tasks.append(task)
    plan = {'schema_version': 1, 'purpose': declared['purpose'], 'created_utc': now(), 'qualification': SCOPE,
        'checker': fixed.EXPECTED_CHECKER, 'checker_python': sys.executable,
        'checker_python_sha256': sha256(Path(sys.executable)), 'runtimes': runtimes,
        'scheduled_count': 15, 'physical_slot_counts': list(COUNTS), 'scheduled_cells': tasks,
        'exact_stage_s': EXACT, 'process_cap_s': PROCESS, 'launcher_cap_s': LAUNCH}
    plan['frozen_files_sha256'] = {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}
    atomic_json(output / 'plan.json', plan)
    print(json.dumps({'plan': str(output / 'plan.json'), 'scheduled_cells': 15, 'launched': False}))


def verify_plan(output):
    plan = json.loads((output / 'plan.json').read_bytes())
    if (plan['checker'] != fixed.EXPECTED_CHECKER or plan['scheduled_count'] != 15
            or len(plan['scheduled_cells']) != 15 or plan['physical_slot_counts'] != list(COUNTS)
            or plan['exact_stage_s'] != EXACT or plan['process_cap_s'] != PROCESS or plan['launcher_cap_s'] != LAUNCH):
        raise ValueError('C104 source plan checker, budget or denominator mismatch')
    for relative, expected in plan['frozen_files_sha256'].items():
        checked_file(output / relative, expected)
    checked_file(plan['checker_python'], plan['checker_python_sha256'])
    for spec in plan['runtimes'].values():
        manifest = json.loads(checked_file(spec['path'], spec['sha256']).read_bytes())
        checked_file(spec['worker_python'], spec['worker_python_sha256'])
        for name, expected in {**manifest['runtime_files'], **manifest['source_patch_files']}.items():
            checked_file(name, expected)
    expected = {t['task_id']: t for t in placement()}
    if {t['task_id'] for t in plan['scheduled_cells']} != set(expected):
        raise ValueError('C104 arm/repetition population differs')
    for task in plan['scheduled_cells']:
        key = fixed.RUNTIMES[task['arm_id']]
        if (any(task.get(k) != v for k, v in expected[task['task_id']].items())
                or task['horizon_s'] != EXACT or task['process_horizon_s'] != PROCESS
                or task['source_id'] != fixed.SOURCES[key] or task['runtime_key'] != key
                or task['warm'] is not (task['arm_id'] in fixed.ARMS[:3])
                or task['memory_mb'] != fixed.MEMORY_MB):
            raise ValueError('C104 task placement, source, budget or handoff drift')
    return plan


def allocation_hosts(raw):
    hosts = sorted(set(raw.split()))
    if len(hosts) != 4 or any(not re.fullmatch(r'gros-[0-9]+(?:\.nancy\.grid5000\.fr)?', h) for h in hosts):
        raise ValueError('The real OAR allocation must contain exactly 4 distinct Gros hosts')
    if len({h.split('.')[0] for h in hosts}) != 4:
        raise ValueError('Duplicate Gros host aliases')
    return hosts


def require_job(job):
    no_overrides(os.environ)
    if os.environ.get('OAR_JOB_ID') != job or not socket.gethostname().split('.')[0].startswith('gros-'):
        raise ValueError('The declared real Gros compute job is required')


def runtime_snapshot(plan):
    observed = {}
    query = ("import json,hashlib,sys;from pathlib import Path;from kayros import _lera;import kayros.lera as w;"
        "libs={str(Path(p).resolve()):hashlib.sha256(Path(p).read_bytes()).hexdigest() for line in Path('/proc/self/maps').read_text().splitlines() "
        "for p in [line.split()[-1]] if p.startswith('/') and any(x in Path(p).name for x in ('libc.','libm.','libstdc++','libgcc_s','ld-linux'))};"
        "print(json.dumps({'backend':_lera.LP_BACKEND,'python_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),"
        "'imports':{str(Path(v.__file__).resolve()):hashlib.sha256(Path(v.__file__).read_bytes()).hexdigest() for v in (_lera,w)},'loaded_libraries':libs}))")
    for key, spec in plan['runtimes'].items():
        manifest = json.loads(Path(spec['path']).read_bytes())
        if key == 'lera':
            observed[key] = {'backend': 'CPLEX', 'native_dependencies': native_dependencies(
                Path(manifest['worker_environment']['UPSTREAM_PROBE_BINARY']))}
        else:
            value = json.loads(subprocess.check_output([spec['worker_python'], '-c', query], text=True, timeout=45))
            if value['backend'] != 'CPLEX' or any(manifest['runtime_files'].get(p) != v for p, v in value['imports'].items()):
                raise ValueError('Imported CPLEX runtime differs from its frozen build')
            observed[key] = value
    return cohort_runtime_identity(observed)


def snapshot(args):
    require_job(args.job_id)
    output = args.output.resolve()
    plan = verify_plan(output)
    hardware = observed_hardware()
    count = COUNTS[args.host_role]
    slots = hardware['selected_physical_slots'][:count]
    if hardware['memory_kib']['MemAvailable'] < (count * 16 + 16) * 1024**2:
        raise ValueError('Insufficient RAM for selected physical lanes and reserve')
    runtimes = runtime_snapshot(plan)
    cohort = {'hardware': hardware['cohort_hardware'], 'runtimes': runtimes,
        'runtime_environment': {k: os.environ.get(k) for k in ['LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'LOADEDMODULES']}}
    print(json.dumps({'status': 'passed', 'host': socket.gethostname().split('.')[0], 'host_role': args.host_role,
        'job_id': args.job_id, 'plan_sha256': sha256(output / 'plan.json'), 'hardware': hardware,
        'selected_slots': slots, 'cohort_identity': cohort, 'cohort_sha256': digest(cohort)}, allow_nan=False))


def validate_snapshots(rows, hosts, job, plan_sha):
    if len(rows) != 4 or len(hosts) != 4 or {r.get('host_role') for r in rows} != set(range(4)):
        raise ValueError('Every one of the 4 host roles requires an allocation snapshot')
    signatures = {r.get('cohort_sha256') for r in rows}
    if len(signatures) != 1:
        raise ValueError('Heterogeneous CPU/kernel/runtime cohort')
    slots = []
    for role, row in enumerate(sorted(rows, key=lambda r: r['host_role'])):
        if (row.get('status') != 'passed' or row.get('job_id') != job or row.get('plan_sha256') != plan_sha
                or row.get('host') != hosts[role].split('.')[0]
                or digest(row['cohort_identity']) != row['cohort_sha256']
                or len(row['selected_slots']) != COUNTS[role]):
            raise ValueError('Snapshot job/host/plan/cohort or lane count differs')
        physical = {(s['physical_package_id'], s['physical_core_id']) for s in row['selected_slots']}
        if len(physical) != COUNTS[role] or any(s['cpu_core'] not in row['hardware']['allowed_cpu_ids'] for s in row['selected_slots']):
            raise ValueError('Snapshot slots share a physical core or exceed actual OAR affinity')
        if row['hardware']['memory_kib']['MemAvailable'] < (COUNTS[role] * 16 + 16) * 1024**2:
            raise ValueError('Snapshot lacks lane memory and reserve')
        slots.extend({'host_role': role, 'host': hosts[role], 'slot_ordinal': index, 'cpu_core': s['cpu_core']}
                     for index, s in enumerate(row['selected_slots']))
    return slots


def preflight(args):
    require_job(args.job_id)
    output = args.output.resolve()
    plan = verify_plan(output)
    raw = Path(os.environ['OAR_NODEFILE']).read_text()
    hosts = allocation_hosts(raw)
    if socket.gethostname().split('.')[0] not in {h.split('.')[0] for h in hosts}:
        raise ValueError('Preflight coordinator is not in the actual allocation')
    for key in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
        os.environ[key] = '1'
    def inspect(role):
        host = hosts[role]
        command = ['env', f'OAR_JOB_ID={args.job_id}', plan['checker_python'],
            str(output / 'frozen-scripts/ms58_c104_fixed_witness.py'), '--mode', 'snapshot',
            '--output', str(output), '--job-id', args.job_id, '--host-role', str(role)]
        with (output / 'allocation-logs' / f'{host}.stdout').open('x') as out, (output / 'allocation-logs' / f'{host}.stderr').open('x') as err:
            code = subprocess.run(['oarsh', host, shlex.join(command)], stdout=out, stderr=err,
                                  stdin=subprocess.DEVNULL, timeout=180).returncode
        row = json.loads((output / 'allocation-logs' / f'{host}.stdout').read_bytes())
        if code:
            raise ValueError(f'Allocation inspection failed on {host}')
        atomic_json(output / 'allocation-snapshots' / f'{host}.json', row)
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(inspect, range(4)))
    slots = validate_snapshots(rows, hosts, args.job_id, sha256(output / 'plan.json'))
    if Path(os.environ['OAR_NODEFILE']).read_text() != raw:
        raise ValueError('Real allocation nodefile changed during inspection')
    atomic_json(output / 'allocation.json', {'job_id': args.job_id, 'hosts': hosts,
        'actual_nodefile': raw, 'plan_sha256': sha256(output / 'plan.json'), 'slots': slots,
        'all_checks_passed': True, 'cohort_sha256': rows[0]['cohort_sha256'],
        'snapshots_sha256': {p.name: sha256(p) for p in (output / 'allocation-snapshots').glob('*.json')}})
    print(json.dumps({'allocation': str(output / 'allocation.json'), 'physical_slots': 15, 'launched': False}))


def verify_allocation(output, plan, job, actual_hosts=None):
    allocation = json.loads((output / 'allocation.json').read_bytes())
    if (allocation.get('all_checks_passed') is not True or allocation['job_id'] != job
            or allocation['plan_sha256'] != sha256(output / 'plan.json')
            or allocation_hosts(allocation['actual_nodefile']) != allocation['hosts']
            or actual_hosts is not None and allocation['hosts'] != actual_hosts):
        raise ValueError('Inspected allocation differs from the declared launch job, plan or hosts')
    rows = [json.loads(checked_file(output / 'allocation-snapshots' / name, value).read_bytes())
            for name, value in allocation['snapshots_sha256'].items()]
    slots = validate_snapshots(rows, allocation['hosts'], job, allocation['plan_sha256'])
    if slots != allocation['slots'] or rows[0]['cohort_sha256'] != allocation['cohort_sha256']:
        raise ValueError('Inspected physical slots or cohort binding changed')
    return allocation


def assigned_tasks(plan, allocation):
    slots = {(s['host_role'], s['slot_ordinal']): s for s in allocation['slots']}
    return [dict(t, **{'host': slots[(t['host_role'], t['slot_ordinal'])]['host'],
                      'cpu_core': slots[(t['host_role'], t['slot_ordinal'])]['cpu_core']})
            for t in plan['scheduled_cells']]


def cell(args):
    output = args.output.resolve()
    plan = verify_plan(output)
    task = json.loads(args.task.read_bytes())
    launch = json.loads((output / 'launch.json').read_bytes())
    allocation = verify_allocation(output, plan, launch['job_id'])
    if (launch['plan_sha256'] != sha256(output / 'plan.json') or task not in launch['scheduled_cells']
            or launch['allocation_sha256'] != sha256(output / 'allocation.json')
            or launch['scheduled_cells'] != assigned_tasks(plan, allocation)
            or launch['job_id'] != os.environ.get('OAR_JOB_ID') or task['cpu_core'] not in os.sched_getaffinity(0)
            or task['host'].split('.')[0] != socket.gethostname().split('.')[0]
            or checker_identity() != plan['checker']):
        raise ValueError('C104 cell is not bound to its actual launched host, core, plan and checker')
    spec = plan['runtimes'][task['runtime_key']]
    manifest = json.loads(Path(spec['path']).read_bytes())
    identity, environment, native = fixed.cell_contract(task, spec, manifest, output / 'frozen-scripts', output)
    def validate(events):
        result = validate_candidates(events, instance_path=Path(task['instance_path']), instance_sha256=task['instance_sha256'],
            atf_path=Path(task['atf_path']), atf_sha256=task['atf_sha256'], instance_name='C104', expected_checker=plan['checker'])
        result['native_result'] = {'relative_path': '../native/' + native.name, 'sha256': sha256(native) if native.exists() else None}
        return result
    result = run_probe(output=output / 'cells', manifest=manifest, parameters=identity['parameters'],
        command=identity['command'], horizon_s=PROCESS, cwd=output, environment=environment, validate=validate)
    print(json.dumps(result, allow_nan=False))


def node(args):
    require_job(args.job_id)
    output = args.output.resolve()
    plan = verify_plan(output)
    launch = json.loads((output / 'launch.json').read_bytes())
    allocation = verify_allocation(output, plan, args.job_id)
    if (launch['job_id'] != args.job_id or launch['plan_sha256'] != sha256(output / 'plan.json')
            or launch['allocation_sha256'] != sha256(output / 'allocation.json')
            or launch['cohort_sha256'] != allocation['cohort_sha256']
            or launch['scheduled_cells'] != assigned_tasks(plan, allocation)):
        raise ValueError('Node controller differs from launched plan')
    host = socket.gethostname().split('.')[0]
    tasks = [t for t in launch['scheduled_cells'] if t['host'].split('.')[0] == host]
    if not tasks:
        raise ValueError('No inspected C104 slots belong to this host')
    topology_snapshot([t['cpu_core'] for t in tasks])
    hardware = observed_hardware()
    snapshot_path = output / 'allocation-snapshots' / f"{tasks[0]['host']}.json"
    inspected = json.loads(snapshot_path.read_bytes())
    if (hardware['cohort_hardware'] != inspected['cohort_identity']['hardware']
            or hardware['memory_kib']['MemAvailable'] < (len(tasks) * 16 + 16) * 1024**2):
        raise ValueError('Node hardware cohort or available lane memory changed after inspection')
    (output / f'node-claim-{host}').mkdir()
    stop = datetime.fromisoformat(launch['stop_before'])
    remaining = (stop - datetime.now(timezone.utc)).total_seconds()
    monotonic_stop = time.monotonic() + remaining
    for key in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
        os.environ[key] = '1'
    def execute(task):
        row = dict(task, audit_ok=False, finished_task=True, raw_result_status='launch_or_audit_failure',
            reported_closure_s=None, capped_reported_closure_s=EXACT, native_work={'available': False}, started_utc=now())
        try:
            spec = plan['runtimes'][task['runtime_key']]
            manifest = json.loads(Path(spec['path']).read_bytes())
            expected, _, _ = fixed.cell_contract(task, spec, manifest, output / 'frozen-scripts', output)
            task_path = output / 'rows' / f"{task['task_id']}-task.json"
            atomic_json(task_path, task)
            atomic_json(output / 'rows' / f"{task['task_id']}-expected-identity.json", expected)
            remaining = min((stop - datetime.now(timezone.utc)).total_seconds(), monotonic_stop - time.monotonic())
            if remaining < LAUNCH + 30:
                row['raw_result_status'] = 'skipped_stop_before'
                raise ValueError('Insufficient remaining stop budget for this 3600 s exact-stage cell')
            command = [plan['checker_python'], str(output / 'frozen-scripts/ms58_c104_fixed_witness.py'),
                '--mode', 'cell', '--output', str(output), '--task', str(task_path)]
            process = run_command(output / 'launch-logs', task['task_id'], command, task['cpu_core'], LAUNCH, output)
            row['process'] = process
            if not process['passed']:
                raise ValueError('C104 launcher failed or exceeded its outer deadline')
            result = json.loads((output / 'launch-logs' / f"{task['task_id']}.stdout").read_bytes())
            row.update(fixed.audit(task, output, result, expected))
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        row['finished_utc'] = now()
        atomic_json(output / 'rows' / f"{task['task_id']}.json", row)
        print(json.dumps({'task_id': task['task_id'], 'audit_ok': row['audit_ok'], 'error': row.get('error')}), flush=True)
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        list(pool.map(execute, tasks))


def launch(args):
    require_job(args.job_id)
    output = args.output.resolve()
    plan = verify_plan(output)
    hosts = allocation_hosts(Path(os.environ['OAR_NODEFILE']).read_text())
    allocation = verify_allocation(output, plan, args.job_id, hosts)
    stop = datetime.fromisoformat(args.stop_before)
    if stop.tzinfo is None or (stop - datetime.now(timezone.utc)).total_seconds() < LAUNCH + 120:
        raise ValueError('C104 launch needs a timezone-qualified stop with at least 3800 s remaining')
    (output / 'launch-claim').mkdir()
    tasks = assigned_tasks(plan, allocation)
    atomic_json(output / 'launch.json', {'job_id': args.job_id, 'created_utc': now(), 'hosts': hosts,
        'plan_sha256': sha256(output / 'plan.json'), 'allocation_sha256': sha256(output / 'allocation.json'),
        'cohort_sha256': allocation['cohort_sha256'], 'stop_before': args.stop_before, 'scheduled_cells': tasks})
    processes, failures = [], []
    for host in hosts:
        command = ['env', f'OAR_JOB_ID={args.job_id}', plan['checker_python'],
            str(output / 'frozen-scripts/ms58_c104_fixed_witness.py'), '--mode', 'node',
            '--output', str(output), '--job-id', args.job_id]
        log = (output / 'node-logs' / f'{host}.log').open('x')
        try:
            processes.append((host, subprocess.Popen(['oarsh', host, shlex.join(command)], stdout=log,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL), log))
        except OSError as error:
            failures.append(host)
            log.write(f'{type(error).__name__}: {error}\n')
            log.close()
    for host, process, log in processes:
        if process.wait():
            failures.append(host)
        log.close()
    rows = []
    for task in tasks:
        path = output / 'rows' / f"{task['task_id']}.json"
        if not path.exists():
            atomic_json(path, dict(task, audit_ok=False, finished_task=True, raw_result_status='node_controller_failure',
                reported_closure_s=None, capped_reported_closure_s=EXACT, native_work={'available': False},
                reason='Node returned without a completed row. Raw partial cells remain retained.'))
        rows.append(json.loads(path.read_bytes()))
    ceilings = apply_feasible_consistency(rows)
    atomic_json(output / 'complete.json', {'finished_utc': now(), 'qualification': SCOPE,
        'scheduled_count': 15, 'completed_row_count': len(rows), 'node_failures': failures,
        'plan_sha256': sha256(output / 'plan.json'), 'launch_sha256': sha256(output / 'launch.json'),
        'allocation_sha256': sha256(output / 'allocation.json'), 'cohort_sha256': allocation['cohort_sha256'],
        'all_observations_usable': all(r['proof_observation_usable'] for r in rows),
        'feasible_ceilings': ceilings, 'rows': rows,
        'row_sha256': {p.name: sha256(p) for p in (output / 'rows').glob('*.json')}})
    return int(bool(failures) or not all(r['proof_observation_usable'] for r in rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', required=True, choices=['proposal', 'design', 'prepare', 'preflight', 'snapshot', 'launch', 'node', 'cell'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--variants', type=Path)
    parser.add_argument('--source-wave', type=Path)
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--design', type=Path)
    parser.add_argument('--job-id')
    parser.add_argument('--host-role', type=int, choices=range(4))
    parser.add_argument('--stop-before')
    parser.add_argument('--task', type=Path)
    args = parser.parse_args()
    if args.mode in ('proposal', 'design') and (not args.variants or not args.source_wave or not args.inputs):
        parser.error('proposal/design require --variants --source-wave --inputs')
    if args.mode == 'prepare' and not args.design:
        parser.error('prepare requires --design')
    if args.mode in ('preflight', 'snapshot', 'launch', 'node') and not args.job_id:
        parser.error('allocation modes require --job-id')
    if args.mode == 'snapshot' and args.host_role is None:
        parser.error('snapshot requires --host-role')
    if args.mode == 'launch' and not args.stop_before:
        parser.error('launch requires --stop-before')
    if args.mode == 'cell' and not args.task:
        parser.error('cell requires --task')
    raise SystemExit(globals()[args.mode](args))
