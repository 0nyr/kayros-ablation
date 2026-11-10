"""Freeze and launch the five-arm Dabia panel through the existing probe.

Prepare (read-only runtime inspection, no solvers) accepts --design and --slots.
The design JSON has schema_version=1, dataset='Dabia2013', purpose='performance'
or 'validation', kayros_source_id (full commit), repetitions (1, 3 or 5),
inputs_root, checker_python, references={'100': {'path': ..., 'sha256': ...}},
selection=[{'num_customers': 100, 'instance_name': 'C201'}, ...], and
manifests={'cplex': {'path': ..., 'sha256': ..., 'worker_python': ...},
           'highs': {...}, 'upstream': {...}}.
References use the existing freeze_references.py snapshot schema. Sizes 25 and
50 are validation only. Performance decisions require size 100 exclusively.
Slots JSON is a list of {'host': FQDN, 'cpu_core': integer}; one lane per slot.

--mode prepare --wave NEW_DIRECTORY freezes all schedules without launching.
--mode launch --wave DIRECTORY --job-id ID requires its real OAR allocation.
Node controllers reject sibling CPUs, inherited solver overrides, backend
drift and inadequate RAM. Launches always use a 3600 s process-launch horizon
and 16384 MiB cap. Every scheduled cell remains in the denominator on failure.
The existing launcher includes input loading and the 5 s / 15 s ILS prefix in
the single outer clock. Checker time follows that clock, as in prior probes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import uuid

from checker_contract import checker_identity, sha256
from development_wave import claim_node_start, launcher_command, repetition_slots
from probe_runner import atomic_json, digest, load_completed

HORIZON = 3600.0
MEMORY_MB = 16384
UPSTREAM_SOURCE = 'f2c9e20'
UPSTREAM_BINARY_SHA256 = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
UPSTREAM_MINIMAL_PATCH_SHA256 = 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf'
ARMS = (
    ('lera-minimal', 'lera-exact', 'upstream', None),
    ('kayros-full-cplex-prefix5s', 'kayros-full-cplex', 'cplex', 5.0),
    ('kayros-full-cplex-prefix15s', 'kayros-full-cplex', 'cplex', 15.0),
    ('kayros-cold-cplex', 'kayros-bpca-cplex', 'cplex', None),
    ('kayros-cold-highs', 'kayros-bpca-highs', 'highs', None),
)


def now():
    return datetime.now(timezone.utc).isoformat()


def no_overrides(environment):
    forbidden = sorted(k for k in environment if k.startswith(('KAYROS_', 'UPSTREAM_', 'PROBE_')))
    if forbidden:
        raise ValueError(f'Inherited solver overrides are forbidden: {forbidden}')


def checked_file(path, expected):
    path = Path(path).resolve()
    if sha256(path) != expected:
        raise ValueError(f'Frozen artifact hash mismatch: {path}')
    return path


def source_matches(declared, required):
    token = re.match(r'^[0-9a-f]{7,40}(?=$|[,\s])', str(declared))
    return bool(token and (required.startswith(token.group()) or token.group().startswith(required)))


def input_size(path):
    match = re.search(r'(?:^|/)benchmarks/TDVRPTW/Dabia2013/n=(25|50|100)/[^/]+\.vrp\.json$', str(path))
    if match is None:
        raise ValueError('Only materialized Dabia2013 n=25, n=50 or n=100 inputs are allowed')
    return int(match.group(1))


def validate_slots(slots):
    if not isinstance(slots, list) or not slots:
        raise ValueError('An explicit nonempty slot list is required')
    seen = set()
    for slot in slots:
        host, core = slot['host'], slot['cpu_core']
        if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*', host):
            raise ValueError('Invalid slot hostname')
        if type(core) is not int or core < 0:
            raise ValueError('CPU IDs must be nonnegative integers')
        key = (host.split('.')[0], core)
        if key in seen:
            raise ValueError('Duplicate physical-lane assignment')
        seen.add(key)


def topology_snapshot(cores, sys_cpu=Path('/sys/devices/system/cpu'), allowed=None):
    allowed = set(os.sched_getaffinity(0) if allowed is None else allowed)
    if not cores or len(set(cores)) != len(cores) or not set(cores) <= allowed:
        raise ValueError('Selected CPUs must be distinct members of the actual OAR affinity')
    records, physical = [], set()
    for cpu in cores:
        topology = sys_cpu / f'cpu{cpu}' / 'topology'
        package = int((topology / 'physical_package_id').read_text())
        core = int((topology / 'core_id').read_text())
        if (package, core) in physical:
            raise ValueError('Selected CPUs share one physical core')
        physical.add((package, core))
        records.append({'cpu_core': cpu, 'physical_package_id': package, 'physical_core_id': core,
                        'thread_siblings_list': (topology / 'thread_siblings_list').read_text().strip()})
    return records


def planned_tasks(inputs, slots, repetitions):
    validate_slots(slots)
    tasks = []
    for entry in sorted(inputs, key=lambda x: x['instance_key']):
        for replicate, paired_seed in repetition_slots(repetitions):
            for arm_id, arm, backend, prefix in ARMS:
                tasks.append({**entry, 'arm_id': arm_id, 'arm': arm, 'runtime_key': backend,
                    'prefix_s': prefix, 'replicate': replicate,
                    'seed': paired_seed if prefix is not None else None,
                    'horizon_s': HORIZON, 'memory_mb': MEMORY_MB})
    random.Random(20261110).shuffle(tasks)
    for index, task in enumerate(tasks):
        task.update(slots[index % len(slots)])
        task['task_id'] = (f"Dabia2013-n{task['num_customers']}-{task['instance_name']}--"
                           f"{task['arm_id']}--rep{task['replicate']}")
    if len({t['task_id'] for t in tasks}) != len(tasks):
        raise ValueError('Duplicate scheduled identity')
    return tasks


def validate_task(task):
    arms = {a[0]: a[1:] for a in ARMS}
    expected = arms.get(task['arm_id'])
    if expected != (task['arm'], task['runtime_key'], task['prefix_s']):
        raise ValueError('Arm, runtime and prefix identity drift')
    if task['horizon_s'] != HORIZON or task['memory_mb'] != MEMORY_MB:
        raise ValueError('Per-cell budget drift')
    if (task['prefix_s'] is None) != (task['seed'] is None):
        raise ValueError('Seed must be present only for the full arms')
    n, name = task['num_customers'], task['instance_name']
    if input_size(task['instance_path']) != n or task['instance_key'] != f'Dabia2013/n={n}/{name}':
        raise ValueError('Cell dataset identity drift')


def freeze_manifest(spec, key, wave, checker, required_source):
    path = checked_file(spec['path'], spec['sha256'])
    manifest = json.loads(path.read_bytes())
    if manifest.get('schema_version') != 1 or manifest.get('checker') != checker:
        raise ValueError(f'{key} manifest schema/checker mismatch')
    if not source_matches(manifest.get('source_id'), required_source):
        raise ValueError(f'{key} source must be {required_source}, found {manifest.get("source_id")}')
    if manifest.get('diagnostic_environment', {}):
        raise ValueError('Timing manifests must not enable diagnostic environment variables')
    native = key == 'upstream'
    if manifest.get('runtime_kind', 'kayros') != ('native' if native else 'kayros'):
        raise ValueError('Runtime kind differs from scheduled arm')
    if not native and manifest.get('backend', '').lower() != key:
        raise ValueError(f'{key} manifest declares another backend')
    if native and manifest.get('backend', 'cplex').lower() != 'cplex':
        raise ValueError('Minimal Lera reference must use CPLEX')
    runtime = manifest.get('runtime_files', {})
    patches = manifest.get('source_patch_files', {})
    if not runtime or not patches:
        raise ValueError('Runtime files and complete patch stack must be pinned')
    for filename, expected in runtime.items():
        checked_file(filename, expected)
    target = wave / 'frozen-patches' / key
    target.mkdir(parents=True)
    frozen_patches = {}
    for index, (filename, expected) in enumerate(patches.items()):
        original = checked_file(filename, expected)
        copy = target / f'{index:03d}-{original.name}'
        shutil.copyfile(original, copy)
        frozen_patches[str(copy)] = expected
    environment = manifest.get('worker_environment', {})
    if native:
        if set(environment) - {'UPSTREAM_PROBE_BINARY', 'UPSTREAM_SOURCE_ROOT', 'UPSTREAM_NATIVE_INPUT_SHA256'}:
            raise ValueError('Minimal Lera controls cannot override algorithmic settings')
        binary = str(Path(environment['UPSTREAM_PROBE_BINARY']).resolve())
        if binary not in {str(Path(p).resolve()) for p in runtime}:
            raise ValueError('Upstream binary must be pinned in runtime_files')
        if sha256(Path(binary)) != UPSTREAM_BINARY_SHA256:
            raise ValueError('Minimal Lera binary differs from the validated f2c9e20 artifact')
        if UPSTREAM_MINIMAL_PATCH_SHA256 not in patches.values():
            raise ValueError('Minimal Lera one-line correction patch is absent from the frozen stack')
        experiment = Path(environment['UPSTREAM_SOURCE_ROOT']).resolve() / 'experiments/bp.json'
        runtime[str(experiment)] = sha256(experiment)
    elif environment:
        raise ValueError('Kayros timing manifests cannot set upstream environment')
    frozen = dict(manifest, source_patch_files=frozen_patches, runtime_files=runtime,
                  parent_manifest_sha256=sha256(path), diagnostic_environment={},
                  backend='cplex' if native else key, declared_source_commit=required_source)
    atomic_json(wave / 'manifests' / f'{key}-base.json', frozen)
    python = Path(spec['worker_python']).absolute()
    if not python.is_file():
        raise ValueError(f'Worker Python is absent: {python}')
    return frozen, str(python)


def freeze_inputs(design, wave, checker):
    reference_manifests, entries = {}, {}
    for size, spec in design['references'].items():
        if size not in {'25', '50', '100'}:
            raise ValueError('Reference size must be 25, 50 or 100')
        path = checked_file(spec['path'], spec['sha256'])
        reference = json.loads(path.read_bytes())
        if reference.get('schema_version') != 1 or reference.get('checker') != checker:
            raise ValueError('Reference checker/schema mismatch')
        target = wave / 'frozen-references' / f'n{size}.json'
        shutil.copyfile(path, target)
        reference_manifests[size] = {'path': str(target), 'sha256': sha256(target)}
        for entry in reference['instances']:
            n = input_size(entry['instance_path'])
            if n != int(size):
                raise ValueError('Reference manifest mixes customer counts')
            key = (n, entry['instance_name'])
            if key in entries:
                raise ValueError('Duplicate reference identity')
            entries[key] = (entry, path.parent)
    selected, seen = [], set()
    for item in design['selection']:
        n, name = item['num_customers'], item['instance_name']
        if type(n) is not int or n not in {25, 50, 100} or not re.fullmatch(r'(C|R|RC)[12][0-9]{2}', name):
            raise ValueError('Only declared Dabia customer counts and instance names are allowed')
        if design['purpose'] == 'performance' and n != 100:
            raise ValueError('Performance decision panels must use n=100 exclusively')
        if (n, name) in seen:
            raise ValueError('Duplicate selected instance')
        seen.add((n, name))
        entry, refroot = entries[(n, name)]
        instance = checked_file(Path(design['inputs_root']) / entry['instance_path'], entry['instance_sha256'])
        materialized = json.loads(instance.read_bytes())
        if input_size(instance) != n or materialized['num_customers'] != n or materialized['instance_name'] != name:
            raise ValueError('Materialized customer count or instance identity mismatch')
        atf = checked_file(instance.parent / materialized['td']['atf_path'], entry['atf_file_sha256'])
        if atf != (Path(design['inputs_root']) / entry['atf_path']).resolve():
            raise ValueError('ATF path differs from pinned reference')
        cost = float.fromhex(entry['reference_cost_hex'])
        if not math.isfinite(cost) or cost <= 0 or cost != entry['reference_cost']:
            raise ValueError('Reference cost must be finite, positive and exactly pinned')
        assets = wave / 'frozen-references' / f'n{n}'
        assets.mkdir(exist_ok=True)
        for kind in ('solution', 'check'):
            artifact = checked_file(refroot / entry[f'{kind}_path'], entry[f'{kind}_sha256'])
            shutil.copyfile(artifact, assets / f'{name}-{kind}.json')
        checked = json.loads((assets / f'{name}-check.json').read_bytes())
        if checked.get('status') != 'valid' or checked.get('routing_cost') != cost:
            raise ValueError('Reference cost lacks matching checked-solution evidence')
        selected.append({'instance_key': f'Dabia2013/n={n}/{name}', 'dataset': 'Dabia2013',
            'num_customers': n, 'instance_name': name, 'instance_path': str(instance),
            'instance_sha256': entry['instance_sha256'], 'atf_path': str(atf),
            'atf_sha256': entry['atf_file_sha256'], 'reference_cost': cost,
            'reference_manifest_sha256': reference_manifests[str(n)]['sha256']})
    if not selected:
        raise ValueError('The selected panel must be nonempty')
    return selected, reference_manifests


def prepare(args):
    no_overrides(os.environ)
    design = json.loads(args.design.read_bytes())
    if design.get('schema_version') != 1 or design.get('dataset') != 'Dabia2013':
        raise ValueError('Only the declared Dabia2013 design is supported')
    if design.get('purpose') not in {'performance', 'validation'}:
        raise ValueError('Design purpose must separate performance from validation')
    if not re.fullmatch(r'[0-9a-f]{40}', design.get('kayros_source_id', '')):
        raise ValueError('Pin the full latest corrected Kayros commit in the design')
    if set(design['manifests']) != {'cplex', 'highs', 'upstream'}:
        raise ValueError('All 3 runtime manifests are mandatory for the five-arm panel')
    slots = json.loads(args.slots.read_bytes())
    validate_slots(slots)
    checker = checker_identity()
    wave = args.wave.resolve()
    wave.mkdir(parents=True, exist_ok=False)
    for directory in ('rows', 'launch-logs', 'node-logs', 'manifests', 'frozen-references'):
        (wave / directory).mkdir()
    shutil.copyfile(args.design, wave / 'design.json')
    shutil.copyfile(args.slots, wave / 'slots.json')
    shutil.copytree(Path(__file__).resolve().parent, wave / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    inputs, references = freeze_inputs(design, wave, checker)
    manifests = {key: freeze_manifest(spec, key, wave, checker,
                    UPSTREAM_SOURCE if key == 'upstream' else design['kayros_source_id'])
                 for key, spec in design['manifests'].items()}
    tasks = planned_tasks(inputs, slots, design['repetitions'])
    for task in tasks:
        key = task['runtime_key']
        manifest, python = manifests[key]
        per_cell = json.loads(json.dumps(manifest))
        worker = wave / 'frozen-scripts' / ('upstream_event_worker.py' if key == 'upstream' else 'kayros_worker.py')
        if key == 'upstream':
            environment = per_cell['worker_environment']
            source = Path(environment['UPSTREAM_SOURCE_ROOT']).resolve()
            native_input = source / f"instances/dabia_et_al_2013/{task['instance_name']}_{task['num_customers']}.json"
            native_hash = sha256(native_input)
            environment['UPSTREAM_NATIVE_INPUT_SHA256'] = native_hash
            per_cell['runtime_files'][str(native_input)] = native_hash
            per_cell['runtime_files'][str(worker)] = sha256(worker)
            task['native_instance_sha256'] = native_hash
        per_cell['dabia_cell'] = {k: task[k] for k in ('task_id', 'instance_key', 'num_customers', 'arm_id',
            'arm', 'prefix_s', 'replicate', 'seed', 'horizon_s', 'memory_mb', 'reference_manifest_sha256')}
        manifest_path = wave / 'manifests' / f"{task['task_id']}.json"
        atomic_json(manifest_path, per_cell)
        task.update(manifest_path=str(manifest_path), manifest_sha256=sha256(manifest_path),
            worker_python=python, worker_python_sha256=sha256(Path(python)), worker_path=str(worker),
            output_directory=str(wave / 'cells'),
            launcher_stdout=str(wave / 'launch-logs' / f"{task['task_id']}.json"),
            launcher_stderr=str(wave / 'launch-logs' / f"{task['task_id']}.stderr.log"))
    checker_python = Path(design['checker_python']).absolute()
    frozen_scripts = {str(p.relative_to(wave)): sha256(p) for p in sorted((wave / 'frozen-scripts').glob('*.py'))}
    atomic_json(wave / 'wave.json', {'schema_version': 1, 'wave_id': uuid.uuid4().hex,
        'created_utc': now(), 'dataset': 'Dabia2013', 'purpose': design['purpose'],
        'decision_customer_count': 100, 'checker': checker, 'diagnostic_environment': {},
        'kayros_source_id': design['kayros_source_id'], 'upstream_source_id': UPSTREAM_SOURCE,
        'upstream_binary_sha256': UPSTREAM_BINARY_SHA256,
        'upstream_minimal_patch_sha256': UPSTREAM_MINIMAL_PATCH_SHA256,
        'reference_manifests': references, 'horizon_s': HORIZON, 'memory_mb': MEMORY_MB,
        'clock_origin': 'solver_process_launch', 'prefix_clock_origin': 'solver_process_launch',
        'scheduled_count': len(tasks), 'available_concurrent_slots': len(slots),
        'maximum_queued_rounds': math.ceil(len(tasks) / len(slots)),
        'checker_python': str(checker_python), 'checker_python_sha256': sha256(checker_python),
        'design_sha256': sha256(wave / 'design.json'), 'slots_sha256': sha256(wave / 'slots.json'),
        'frozen_scripts_sha256': frozen_scripts, 'scheduled_cells': tasks})
    print(json.dumps({'wave': str(wave), 'scheduled_cells': len(tasks), 'slots': len(slots),
                      'maximum_queued_rounds': math.ceil(len(tasks) / len(slots)), 'launched': False}))


def backend_audit(task, manifest, events, result):
    expected = 'HiGHS' if task['runtime_key'] == 'highs' else 'CPLEX'
    observed = [e['lp_backend'] for e in events if e.get('phase') == 'exact' and 'lp_backend' in e]
    if task['runtime_key'] == 'upstream':
        observed = ['CPLEX'] if any(e.get('phase') == 'native_launch' and
            e.get('native_instance_sha256') == task['native_instance_sha256'] and
            e.get('metadata_equivalence') and all(v is True for v in e['metadata_equivalence'].values())
            for e in events) else []
    for event in events:
        settings = event.get('effective_backend_settings') or {}
        for section in settings.values():
            if isinstance(section, dict) and 'backend' in section:
                observed.append(section['backend'])
                if section.get('threads') not in (None, 1):
                    return {'status': 'failed', 'passed': False, 'expected': expected,
                            'observed': observed, 'reason': 'Backend thread drift'}
    if observed and any(value != expected for value in observed):
        return {'status': 'failed', 'passed': False, 'expected': expected,
                'observed': observed, 'reason': 'Observed backend differs from frozen arm'}
    if observed:
        return {'status': 'passed', 'passed': True, 'expected': expected, 'observed': observed,
                'evidence': 'Pinned minimal native CPLEX artifact and native launch' if task['runtime_key'] == 'upstream' else 'Worker/backend events'}
    return {'status': 'unavailable', 'passed': False, 'expected': expected, 'observed': [],
            'reason': 'No exact-backend event, including early resource or worker failures'}


def execute(task, wave, checker_python):
    row = dict(task, started_utc=now())
    try:
        validate_task(task)
        checked_file(task['instance_path'], task['instance_sha256'])
        checked_file(task['atf_path'], task['atf_sha256'])
        checked_file(task['worker_python'], task['worker_python_sha256'])
        manifest = json.loads(checked_file(task['manifest_path'], task['manifest_sha256']).read_bytes())
        taskset = shutil.which('taskset')
        if not taskset:
            raise RuntimeError('taskset is required for the entire cell including route checking')
        command = launcher_command(task, wave, checker_python, taskset)
        row['command'] = command
        with Path(task['launcher_stdout']).open('x') as out, Path(task['launcher_stderr']).open('x') as err:
            code = subprocess.run(command, stdout=out, stderr=err).returncode
        if code:
            raise RuntimeError(f'Probe launcher failed with exit {code}')
        result = json.loads(Path(task['launcher_stdout']).read_bytes())
        cell = Path(task['output_directory']) / result['run_id']
        if load_completed(cell, expected_run_id=result['run_id']) != result:
            raise ValueError('Launcher result differs from audited stored result')
        events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
        audit = backend_audit(task, manifest, events, result)
        validation = result['validation']
        row.update(run_id=result['run_id'], result_path=str(cell / 'result.json'),
            status=result['status'], returncode=result['returncode'], backend_audit=audit,
            valid_candidates=sum(c['valid'] is True for c in validation.get('candidates', [])),
            invalid_candidates=sum(c['valid'] is not True for c in validation.get('candidates', [])),
            checked_cost=validation.get('best_checked_cost'),
            sampled_solver_peak_rss_kib=result.get('sampled_solver_peak_rss_kib'),
            proof_status=validation.get('proof', {}).get('status'))
        if audit['status'] == 'failed' or (audit['status'] == 'unavailable' and result['status'] == 'completed'):
            row.update(status='backend_audit_failure', reason=audit.get('reason'))
    except Exception as error:
        row.update(status='launch_or_audit_failure', reason=f'{type(error).__name__}: {error}')
    row['finished_utc'] = now()
    atomic_json(wave / 'rows' / f"{task['task_id']}.json", row)
    print(json.dumps({k: row.get(k) for k in ('task_id', 'status', 'checked_cost', 'reason')}), flush=True)
    return row


def verify_wave(wave):
    spec = json.loads((wave / 'wave.json').read_bytes())
    if spec['horizon_s'] != HORIZON or spec['memory_mb'] != MEMORY_MB:
        raise ValueError('Wave budget drift')
    for path, expected in spec['frozen_scripts_sha256'].items():
        checked_file(wave / path, expected)
    checked_file(spec['checker_python'], spec['checker_python_sha256'])
    checked_file(wave / 'design.json', spec['design_sha256'])
    checked_file(wave / 'slots.json', spec['slots_sha256'])
    if spec['scheduled_count'] != len(spec['scheduled_cells']):
        raise ValueError('Scheduled denominator mismatch')
    if len({t['task_id'] for t in spec['scheduled_cells']}) != spec['scheduled_count']:
        raise ValueError('Duplicate scheduled task ID')
    for task in spec['scheduled_cells']:
        validate_task(task)
        if spec['purpose'] == 'performance' and task['num_customers'] != 100:
            raise ValueError('Performance decisions cannot mix customer counts')
        manifest = json.loads(checked_file(task['manifest_path'], task['manifest_sha256']).read_bytes())
        if any(task.get(k) != v for k, v in manifest['dabia_cell'].items()):
            raise ValueError('Scheduled cell differs from frozen manifest')
    for reference in spec['reference_manifests'].values():
        checked_file(reference['path'], reference['sha256'])
    return spec


def node(args):
    wave = args.wave.resolve()
    spec = verify_wave(wave)
    host = socket.gethostname().split('.')[0]
    tasks = [t for t in spec['scheduled_cells'] if t['host'].split('.')[0] == host]
    cores = sorted({t['cpu_core'] for t in tasks})
    claim_node_start(wave, tasks, host)
    try:
        no_overrides(os.environ)
        if os.environ.get('OAR_JOB_ID') != args.job_id or host.startswith(('fnancy', 'flyon')):
            raise RuntimeError('Node controller requires the declared real OAR compute job')
        launch_record = json.loads((wave / 'launch.json').read_bytes())
        if launch_record['oar_job_id'] != args.job_id or launch_record['wave_sha256'] != sha256(wave / 'wave.json'):
            raise ValueError('Node wave differs from the claimed launch')
        topology = topology_snapshot(cores)
        available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        if available < (len(cores) * MEMORY_MB + 16384) * 1024:
            raise RuntimeError('Insufficient available RAM for all slot caps plus 16 GiB reserve')
        for key in ('cplex', 'highs'):
            task = next((t for t in tasks if t['runtime_key'] == key), None)
            if task:
                checked_file(task['worker_python'], task['worker_python_sha256'])
                query = 'from kayros import _lera; print(_lera.LP_BACKEND)'
                observed = subprocess.check_output([task['worker_python'], '-c', query], text=True).strip()
                if observed != ('CPLEX' if key == 'cplex' else 'HiGHS'):
                    raise RuntimeError(f'Installed backend drift: expected {key}, found {observed}')
        atomic_json(wave / f'node-{host}.json', {'host': host, 'oar_job_id': args.job_id,
            'allowed_cores': sorted(os.sched_getaffinity(0)), 'physical_slots': topology,
            'memory_available_kib': available, 'scheduled_cells': len(tasks), 'created_utc': now()})
    except Exception as error:
        for task in tasks:
            atomic_json(wave / 'rows' / f"{task['task_id']}.json", dict(task,
                status='node_preflight_failure', reason=f'{type(error).__name__}: {error}'))
        raise
    def lane(core):
        return [execute(task, wave, spec['checker_python']) for task in tasks if task['cpu_core'] == core]
    rows = []
    with ThreadPoolExecutor(max_workers=len(cores)) as pool:
        for future in as_completed([pool.submit(lane, core) for core in cores]):
            rows.extend(future.result())
    failures = [r['task_id'] for r in rows if r['status'] not in {'completed', 'deadline'}]
    atomic_json(wave / f'complete-{host}.json', {'host': host, 'completed_rows': len(rows),
                'failed_cells': failures, 'finished_utc': now()})
    if failures:
        raise RuntimeError(f'{len(failures)} cells failed; all rows retained')


def launch(args):
    no_overrides(os.environ)
    nodefile = os.environ.get('OAR_NODEFILE')
    if os.environ.get('OAR_JOB_ID') != args.job_id or not nodefile or not Path(nodefile).is_file():
        raise RuntimeError('Launch requires the declared real OAR job and nodefile')
    wave = args.wave.resolve()
    spec = verify_wave(wave)
    if checker_identity() != spec['checker']:
        raise ValueError('Launch checker differs from frozen wave')
    allocated = set(Path(nodefile).read_text().split())
    hosts = sorted({t['host'] for t in spec['scheduled_cells']})
    if not set(hosts) <= allocated:
        raise ValueError('Scheduled hosts are outside the real OAR nodefile')
    (wave / 'launch-claim').mkdir()
    shutil.copyfile(nodefile, wave / 'allocated-nodefile.txt')
    atomic_json(wave / 'launch.json', {'oar_job_id': args.job_id, 'hosts': hosts,
        'wave_sha256': sha256(wave / 'wave.json'), 'created_utc': now()})
    processes, failures = [], []
    for host in hosts:
        command = ['env', f'OAR_JOB_ID={args.job_id}', spec['checker_python'], str(wave / 'frozen-scripts/ms58_dabia_wave.py'),
                   '--mode', 'node', '--wave', str(wave), '--job-id', args.job_id]
        logfile = (wave / 'node-logs' / f'{host}.log').open('x')
        try:
            process = subprocess.Popen(['oarsh', host, shlex.join(command)], stdout=logfile,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            processes.append((host, process, logfile))
        except OSError as error:
            failures.append(host)
            logfile.close()
            for task in spec['scheduled_cells']:
                if task['host'] == host:
                    atomic_json(wave / 'rows' / f"{task['task_id']}.json", dict(task,
                        status='node_controller_failure', reason=f'{type(error).__name__}: {error}'))
    for host, process, logfile in processes:
        if process.wait() != 0:
            failures.append(host)
            for task in spec['scheduled_cells']:
                path = wave / 'rows' / f"{task['task_id']}.json"
                if task['host'] == host and not path.exists():
                    atomic_json(path, dict(task, status='node_controller_failure',
                        reason='Node controller failed without a completed row; raw partial cells retained'))
        logfile.close()
    atomic_json(wave / 'coordinator-complete.json', {'scheduled_cells': spec['scheduled_count'],
        'completed_rows': len(list((wave / 'rows').glob('*.json'))), 'node_failures': failures,
        'finished_utc': now()})
    if failures:
        raise RuntimeError(f'Node failures retained: {failures}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=('prepare', 'launch', 'node'), required=True)
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--design', type=Path)
    parser.add_argument('--slots', type=Path)
    parser.add_argument('--job-id')
    args = parser.parse_args()
    if args.mode == 'prepare' and (not args.design or not args.slots):
        parser.error('prepare requires --design and --slots')
    if args.mode != 'prepare' and not args.job_id:
        parser.error('launch and node require --job-id')
    {'prepare': prepare, 'launch': launch, 'node': node}[args.mode](args)


if __name__ == '__main__':
    main()
