"""Bounded distributed development probes inside an existing real OAR job.

Coordinator freezes orchestration and patch bytes before launching one node
controller per host with oarsh. Every slot uses a distinct allowed CPU core.
Per-cell rows are atomically published as they finish. This is not a main
campaign scheduler and does not accept native proof claims.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import shlex
import socket
import subprocess
import sys
import uuid

from checker_contract import checker_identity, sha256
from probe_runner import atomic_json

ARMS = ['kayros-bpca-cplex', 'kayros-bpca-highs', 'kayros-heuristics', 'kayros-full-cplex']



def repetition_slots(count: int) -> list[tuple[int, int]]:
    """Keep legacy single-trial ID 0; assign explicit paired multi-trial seeds."""
    seeds = [42, 123, 456, 789, 2026]
    if count not in {1, 3, 5}:
        raise ValueError('Development repetitions must be 1, 3 or 5')
    return [(0, 42)] if count == 1 else list(enumerate(seeds[:count], start=1))



def planned_cells(names, full_prefixes, repetitions, include_upstream=False, allow_unselected_prefix=False) -> list[dict]:
    names = list(names)
    prefixes = [None if prefix is None and allow_unselected_prefix else float(prefix) for prefix in full_prefixes]
    if len(set(names)) != len(names) or len(set(prefixes)) != len(prefixes):
        raise ValueError('Instance identities and full prefixes must be unique')
    configurations = [(arm, None) for arm in ARMS if arm != 'kayros-full-cplex']
    configurations += [('kayros-full-cplex', prefix) for prefix in prefixes]
    if include_upstream:
        configurations += [('lera-exact', None)]
    return [{'instance_name': name, 'arm': arm, 'prefix_s': prefix, 'replicate': repetition,
             'seed': seed if arm in ['kayros-heuristics', 'kayros-full-cplex'] else None}
            for name in sorted(names) for arm, prefix in configurations
            for repetition, seed in repetition_slots(repetitions)]


def launcher_command(task: dict, wave: Path, python: str, taskset: str) -> list[str]:
    command = [taskset, '-c', str(task['cpu_core']), python, str(wave / 'frozen-scripts/run_solver_probe.py'),
               '--manifest', task['manifest_path'], '--worker-python', task['worker_python'],
               '--instance', task['instance_path'], '--output', task['output_directory'],
               '--arm', task['arm'], '--horizon', str(task['horizon_s']),
               '--memory-mb', str(task['memory_mb']), '--cpu-core', str(task['cpu_core']),
               '--repetition', str(task['replicate'])]
    if task.get('worker_path'):
        command += ['--worker', task['worker_path']]
    if task['seed'] is not None:
        command += ['--seed', str(task['seed'])]
    if task['prefix_s'] is not None:
        command += ['--prefix-seconds', str(task['prefix_s'])]
    return command



def claim_node_start(wave: Path, tasks: list[dict], hostname: str) -> Path:
    """Refuse accidental re-entry before it can replace completed summary rows."""
    if (wave / f'node-{hostname}.json').exists():
        raise FileExistsError('Node metadata already exists; use read-only recovery audit')
    for task in tasks:
        artifacts = [wave / 'rows' / (task['task_id'] + '.json')]
        artifacts += [Path(task[key]) for key in ['launcher_stdout', 'launcher_stderr'] if key in task]
        if any(path.exists() for path in artifacts):
            raise FileExistsError('Node task artifacts already exist; use read-only recovery audit')
    claims = wave / 'node-start-claims'
    claims.mkdir(exist_ok=True)
    claim = claims / hostname
    claim.mkdir()  # Atomic exclusive ownership, including concurrent re-entry.
    atomic_json(claim / 'owner.json', {'host': hostname, 'controller_pid': os.getpid(),
                'oar_job_id': os.environ.get('OAR_JOB_ID'), 'task_ids': [task['task_id'] for task in tasks]})
    return claim


def node(root: Path, wave: Path) -> None:
    specification = json.loads((wave / 'wave.json').read_text())
    hostname = socket.gethostname().split('.')[0]
    tasks = [task for task in specification['scheduled_cells'] if task['host'].split('.')[0] == hostname]
    cores = sorted({task['cpu_core'] for task in tasks})
    if not tasks or not set(cores) <= os.sched_getaffinity(0):
        raise RuntimeError('Node task assignment is absent, overlaps cores or exceeds OAR affinity')
    claim_node_start(wave, tasks, hostname)
    # Reserve memory for simultaneous core lanes, including queued repetitions.
    memory_available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    if len(cores) * max(task['memory_mb'] for task in tasks) * 1024 + 16 * 1024**2 > memory_available:
        raise RuntimeError('Insufficient available RAM for declared simultaneous address-space caps plus 16GiB reserve')
    atomic_json(wave / f'node-{hostname}.json', {'host': hostname, 'allowed_cores': sorted(os.sched_getaffinity(0)),
                'memory_available_kib_at_start': memory_available, 'tasks': len(tasks), 'concurrent_slots': len(cores), 'reserved_memory_mb_per_task': tasks[0]['memory_mb']})

    def execute(task: dict) -> dict:
        taskset = shutil.which('taskset')
        if taskset is None:
            raise RuntimeError('taskset is required to isolate each complete cell including post-run checking')
        command = launcher_command(task, wave, sys.executable, taskset)
        row = dict(task)
        try:
            with Path(task['launcher_stdout']).open('x') as stdout, Path(task['launcher_stderr']).open('x') as stderr:
                completed = subprocess.run(command, stdout=stdout, stderr=stderr)
            if completed.returncode:
                raise RuntimeError(f'Launcher exit {completed.returncode}; see launcher_stderr')
            result = json.loads(Path(task['launcher_stdout']).read_bytes())
            row.update(run_id=result['run_id'], result_path=str(Path(task['output_directory']) / result['run_id'] / 'result.json'),
                       status=result['status'], returncode=result['returncode'], event_count=result['event_count'],
                       valid_candidates=sum(candidate['valid'] for candidate in result['validation'].get('candidates', [])),
                       checked_cost=result['validation'].get('best_checked_cost'),
                       sampled_solver_peak_rss_kib=result.get('sampled_solver_peak_rss_kib'),
                       proof_status=result['validation'].get('proof', {}).get('status'))
        except Exception as error:
            row.update(status='launch_failure', reason=f'{type(error).__name__}: {error}')
        atomic_json(wave / 'rows' / (task['task_id'] + '.json'), row)
        print(json.dumps({key: row.get(key) for key in ['task_id', 'host', 'cpu_core', 'status', 'checked_cost', 'valid_candidates']}), flush=True)
        return row

    rows = []
    def lane(core):
        return [execute(task) for task in tasks if task['cpu_core'] == core]
    with ThreadPoolExecutor(max_workers=len(cores)) as pool:
        futures = [pool.submit(lane, core) for core in cores]
        for future in as_completed(futures):
            rows.extend(future.result())
            temporary = wave / f'rows-{hostname}.json.tmp'
            temporary.write_text(json.dumps(rows, indent=2, allow_nan=False) + '\n')
            os.replace(temporary, wave / f'rows-{hostname}.json')
    atomic_json(wave / f'complete-{hostname}.json', {'host': hostname, 'completed_cells': len(rows), 'launch_failures': sum(row['status'] == 'launch_failure' for row in rows)})



def load_selection(path: Path, references: dict, expected_count: int, expected_reference_digest: str) -> dict:
    selection = json.loads(path.read_bytes())
    entries = selection['selected_instances']
    selected = {entry['instance']: entry for entry in entries}
    if (selection.get('schema_version') != 1 or selection.get('reference_manifest_sha256') != expected_reference_digest
            or len(entries) != expected_count or len(selected) != expected_count):
        raise ValueError('Frozen selection digest, count or identity uniqueness mismatch')
    for name, entry in selected.items():
        if name not in references or entry['instance_sha256'] != references[name]['instance_sha256']:
            raise ValueError(f'Selected instance identity mismatch: {name}')
    return selected


def coordinate(args) -> None:
    nodefile_name = os.environ.get('OAR_NODEFILE')
    if not nodefile_name or not Path(nodefile_name).is_file() or not os.environ.get('OAR_JOB_ID'):
        raise RuntimeError('Coordinator must run inside attached OAR job with its real OAR_NODEFILE')
    allocated = list(dict.fromkeys(Path(nodefile_name).read_text().split()))
    hosts = args.hosts or allocated[:3]
    if not hosts or len(set(hosts)) != len(hosts) or not set(hosts) <= set(allocated):
        raise RuntimeError('Distinct measuring hosts must belong to the real nodefile')
    slots = repetition_slots(args.repetitions)
    if args.workers_per_host < 1 or not math.isfinite(args.horizon) or args.horizon <= 0 or args.memory_mb <= 0 or args.expected_count <= 0:
        raise ValueError('Worker count, horizon and repetitions must be positive')
    if any(not math.isfinite(prefix) or prefix <= 0 or prefix >= args.horizon for prefix in args.full_prefixes):
        raise ValueError('Full prefixes must be positive and shorter than the horizon')
    wave_id = uuid.uuid4().hex
    created_utc = datetime.now(timezone.utc).isoformat()
    wave = args.root / 'outputs' / args.wave_name
    wave.mkdir(parents=True, exist_ok=False)
    for directory in ['rows', 'launch-logs', 'node-logs', 'manifests']:
        (wave / directory).mkdir()
    shutil.copytree(args.source / 'scripts', wave / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(args.patch_dir, wave / 'frozen-patches')
    shutil.copyfile(nodefile_name, wave / 'allocated-nodefile.txt')
    snapshot = args.source / 'references/dabia2013-n100-v011-2026-11-09'
    expected_reference = json.loads((args.source / 'configs/source_pins.json').read_text())['dataset']['reference_manifest_sha256']
    if sha256(snapshot / 'manifest.json') != expected_reference:
        raise RuntimeError('Reference manifest mismatch')
    reference = json.loads((snapshot / 'manifest.json').read_text())
    selection_source = args.selection_file or args.source / 'configs/pilot-selection.json'
    selection_path = wave / 'selection.json'
    shutil.copyfile(selection_source, selection_path)
    references = {entry['instance_name']: entry for entry in reference['instances']}
    selected = load_selection(selection_path, references, args.expected_count, expected_reference)
    checker = checker_identity()
    if checker != reference['checker']:
        raise RuntimeError('Checker differs from frozen reference')
    manifests = {}
    for backend, python, patches in [('cplex', args.cplex_python, args.cplex_patch or ['0001-kayros-bound-events.patch', '0002-cplex-single-thread.patch']),
                                      ('highs', args.highs_python, args.highs_patch or ['0001-kayros-bound-events.patch'])]:
        query = "import importlib.metadata as m,json; d=m.distribution('kayros'); print(json.dumps([str(d.locate_file(p).resolve()) for p in d.files if str(p).endswith(('.so','.py'))]))"
        installed = json.loads(subprocess.check_output([str(python), '-c', query], text=True))
        manifest = {'schema_version': 1, 'checker': checker, 'wave_name': args.wave_name,
                    'purpose': args.label, 'wave_id': wave_id, 'created_utc': created_utc,
                    'oar_job_id': os.environ['OAR_JOB_ID'], 'accepted_proof_policy': 'proof_unverified',
                    'backend': backend, 'reference_manifest_sha256': expected_reference,
                    'selection_sha256': sha256(selection_path),
                    'runtime_files': {filename: sha256(Path(filename)) for filename in installed},
                    'source_patch_files': {str(wave / 'frozen-patches' / name): sha256(wave / 'frozen-patches' / name) for name in patches}}
        path = wave / 'manifests' / (backend + '.json')
        atomic_json(path, manifest)
        manifests[backend] = str(path)
    if args.upstream_manifest:
        native = json.loads(args.upstream_manifest.read_bytes())
        if native.get('checker') != checker or native.get('runtime_kind') != 'native':
            raise RuntimeError('Upstream manifest checker or runtime kind mismatch')
        native.update(wave_name=args.wave_name, purpose=args.label, wave_id=wave_id, created_utc=created_utc, oar_job_id=os.environ['OAR_JOB_ID'])
        upstream_worker = wave / 'frozen-scripts/upstream_event_worker.py'
        shutil.copyfile(args.upstream_worker, upstream_worker)
        native['runtime_files'][str(upstream_worker)] = sha256(upstream_worker)
        path = wave / 'manifests/upstream.json'
        atomic_json(path, native)
        manifests['upstream'] = str(path)
    available_cores = {}
    for host in hosts:
        code = 'import os,json; print(json.dumps(sorted(os.sched_getaffinity(0))))'
        cores = json.loads(subprocess.check_output(['oarsh', host, shlex.join([str(args.checker_python), '-c', code])], text=True))
        if len(cores) < args.workers_per_host:
            raise RuntimeError(f'{host} has fewer allowed CPU cores than requested slots')
        available_cores[host] = cores[:args.workers_per_host]
    combinations = planned_cells(selected, args.full_prefixes, args.repetitions, bool(args.upstream_manifest))
    random.Random(20261109).shuffle(combinations)
    tasks = []
    for index, planned in enumerate(combinations):
        name, arm, prefix, repetition, seed = (planned[key] for key in ['instance_name', 'arm', 'prefix_s', 'replicate', 'seed'])
        entry = references[name]
        instance = args.inputs / entry['instance_path']
        atf = args.inputs / entry['atf_path']
        if (sha256(instance) != selected[name]['instance_sha256'] or sha256(instance) != entry['instance_sha256']
                or sha256(atf) != entry['atf_file_sha256']):
            raise RuntimeError(f'Input hash mismatch for {name}')
        host = hosts[index % len(hosts)]
        slot = (index // len(hosts)) % args.workers_per_host
        backend = 'upstream' if arm == 'lera-exact' else ('highs' if arm.endswith('highs') else 'cplex')
        task_id = f'{name}--{arm}--prefix{prefix}--rep{repetition}'
        tasks.append({'wave_id': wave_id, 'task_id': task_id, 'instance_name': name, 'arm': arm, 'horizon_s': args.horizon,
                      'seed': seed if arm in ['kayros-heuristics', 'kayros-full-cplex'] else None,
                      'prefix_s': prefix, 'replicate': repetition,
                      'host': host, 'cpu_core': available_cores[host][slot], 'memory_mb': args.memory_mb,
                      'instance_path': str(instance), 'instance_sha256': entry['instance_sha256'],
                      'reference_cost': float.fromhex(entry['reference_cost_hex']),
                      'output_directory': str(wave / 'cells'), 'manifest_path': manifests[backend],
                      'worker_python': str(args.checker_python if backend == 'upstream' else (args.highs_python if backend == 'highs' else args.cplex_python)),
                      'worker_path': str(wave / 'frozen-scripts/upstream_event_worker.py') if backend == 'upstream' else None,
                      'launcher_stdout': str(wave / 'launch-logs' / (task_id + '.json')),
                      'launcher_stderr': str(wave / 'launch-logs' / (task_id + '.stderr.log'))})
    atomic_json(wave / 'wave.json', {'schema_version': 1, 'wave_name': args.wave_name, 'purpose': args.label,
                'wave_id': wave_id, 'created_utc': created_utc, 'launch_ready_utc': datetime.now(timezone.utc).isoformat(),
                'workers_per_host': args.workers_per_host, 'scheduled_count': len(tasks), 'available_concurrent_slots': len(hosts) * args.workers_per_host,
                'oar_job_id': os.environ['OAR_JOB_ID'], 'reference_manifest_sha256': expected_reference,
                'selection_sha256': sha256(selection_path), 'selection_source_path': str(selection_source),
                'expected_instance_count': args.expected_count, 'horizon_s': args.horizon,
                'repetition_slots': [{'replicate': repetition, 'paired_heuristic_seed': seed} for repetition, seed in slots],
                'driver_sha256': sha256(Path(__file__)), 'scheduled_cells': tasks})
    processes = []
    for host in hosts:
        logfile = (wave / 'node-logs' / (host.split('.')[0] + '.log')).open('x')
        remote_command = ['env', 'PYTHONPATH=' + str(wave / 'frozen-scripts'), str(args.checker_python),
                          str(wave / 'frozen-scripts/development_wave.py'), '--mode', 'node', '--root', str(args.root), '--wave', str(wave)]
        process = subprocess.Popen(['oarsh', host, shlex.join(remote_command)],
                     stdout=logfile, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        processes.append((host, process, logfile))
    failures = []
    for host, process, logfile in processes:
        if process.wait() != 0:
            failures.append(host)
        logfile.close()
    atomic_json(wave / 'coordinator-complete.json', {'scheduled_cells': len(tasks), 'node_failures': failures,
                'completed_rows': len(list((wave / 'rows').glob('*.json')))})
    if failures:
        raise RuntimeError(f'Node controllers failed: {failures}')


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['coordinator', 'node'], required=True)
    parser.add_argument('--root', type=Path, default=home / 'ms56-phase2')
    parser.add_argument('--source', type=Path, default=home / 'ms56-phase2/experiment')
    parser.add_argument('--selection-file', type=Path)
    parser.add_argument('--expected-count', type=int, default=12)
    parser.add_argument('--inputs', type=Path, default=home / 'ms56-ablation/inputs')
    parser.add_argument('--patch-dir', type=Path, default=home / 'ms56-ablation/experiment/patches')
    parser.add_argument('--checker-python', type=Path, default=home / 'ms56-ablation/experiment/.venv/bin/python')
    parser.add_argument('--cplex-python', type=Path, default=home / 'ms56-ablation/solver/venv-cplex/bin/python')
    parser.add_argument('--highs-python', type=Path, default=home / 'ms56-ablation/solver/venv-highs/bin/python')
    parser.add_argument('--cplex-patch', action='append')
    parser.add_argument('--highs-patch', action='append')
    parser.add_argument('--hosts', nargs='+')
    parser.add_argument('--workers-per-host', type=int, default=16)
    parser.add_argument('--memory-mb', type=int, default=8192, help='Per-process address-space cap in MiB; node RAM guard remains mandatory')
    parser.add_argument('--horizon', type=float, default=60)
    parser.add_argument('--full-prefixes', type=float, nargs='+', default=[15])
    parser.add_argument('--repetitions', type=int, default=1)
    parser.add_argument('--upstream-manifest', type=Path)
    parser.add_argument('--upstream-worker', type=Path, default=home / 'ms56-phase2/upstream/upstream_event_worker.py')
    parser.add_argument('--wave-name', default='development60-current')
    parser.add_argument('--label', default='development wave with current native parameters and unverified bounds/proof claims')
    parser.add_argument('--wave', type=Path)
    args = parser.parse_args()
    if args.mode == 'node':
        node(args.root, args.wave)
    else:
        coordinate(args)


if __name__ == '__main__':
    main()
