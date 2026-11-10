"""Compare explicit upstream correction settings as exploratory diagnostics."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_analyze_pairs import closure_and_resource, native_work
from ms58_paired_probe import frozen_existing_manifest
from probe_runner import atomic_json


def now():
    return datetime.now(timezone.utc).isoformat()


def physical_cores():
    cores, seen = [], set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = tuple((topology / name).read_text().strip()
                    for name in ['physical_package_id', 'core_id'])
        if key not in seen:
            seen.add(key)
            cores.append(cpu)
    return cores


def run_lane(lane, root, out, scripts):
    rows = []
    for setting in lane['order']:
        cell_id = f"{lane['instance']}-correcting-{setting}"
        manifest = out / f'correcting-{setting}-manifest.json'
        command = [sys.executable, str(scripts / 'run_solver_probe.py'),
                   '--manifest', str(manifest), '--worker-python', sys.executable,
                   '--worker', str(scripts / 'upstream_event_worker.py'),
                   '--instance', lane['input_path'], '--output', str(out / 'cells'),
                   '--arm', 'lera-exact', '--horizon', str(lane['horizon_s']),
                   '--memory-mb', '16384', '--cpu-core', str(lane['cpu']),
                   '--repetition', '1']
        environment = dict(os.environ, KAYROS_DIAGNOSTIC_CORRECTING=str(setting))
        row = dict(lane, cell_id=cell_id, correcting=bool(setting), command=command,
                   diagnostic_environment={'KAYROS_DIAGNOSTIC_CORRECTING': str(setting)},
                   started_utc=now(), status='pending', native_work={'available': False},
                   reported_closure_s=None, capped_closure_s=lane['horizon_s'])
        stdout = out / 'launch-logs' / f'{cell_id}.json'
        stderr = out / 'launch-logs' / f'{cell_id}.stderr'
        try:
            if sha256(Path(lane['input_path'])) != lane['input_sha256']:
                raise ValueError('Input changed after schedule freeze')
            if sha256(Path(lane['atf_path'])) != lane['atf_sha256']:
                raise ValueError('ATF changed after schedule freeze')
            with stdout.open('x') as stream, stderr.open('x') as err:
                process = subprocess.run(command, stdout=stream, stderr=err, env=environment)
            if process.returncode:
                raise RuntimeError(f'Probe launcher exit {process.returncode}')
            result = json.loads(stdout.read_bytes())
            cell = out / 'cells' / result['run_id']
            events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
            worker_stderr = (cell / 'stderr.log').read_text(errors='replace')
            closure, resource = closure_and_resource(result, events, worker_stderr)
            validation = result['validation']
            row.update(run_id=result['run_id'], status=result['status'],
                       returncode=result['returncode'], checked_cost=validation.get('best_checked_cost'),
                       resource_outcome=resource, reported_closure_s=closure,
                       capped_closure_s=closure if closure is not None else lane['horizon_s'],
                       setting_stderr_confirmed=f'Explicit diagnostic correcting: {setting}\n' in worker_stderr)
            native = validation.get('native_result', {})
            if native.get('sha256'):
                path = Path(native['path'])
                if sha256(path) != native['sha256']:
                    raise ValueError('Native sidecar digest mismatch')
                raw = json.loads(path.read_bytes())
                row['native_work'] = native_work(raw)
                declared = raw['native_result'].get('ms58_diagnostic_configuration', {}).get('correcting')
                row['native_correcting'] = declared
                if declared is not bool(setting):
                    raise ValueError('Native correction setting mismatch')
            if not row['setting_stderr_confirmed']:
                row['setting_observation'] = 'Unknown or not reached, retain this outcome'
        except Exception as error:
            row.update(status='launch_or_audit_failure', reason=f'{type(error).__name__}: {error}',
                       reported_closure_s=None, capped_closure_s=lane['horizon_s'])
        row['finished_utc'] = now()
        atomic_json(out / 'rows' / f'{cell_id}.json', row)
        rows.append(row)
        print(json.dumps({key: row.get(key) for key in
                         ['cell_id', 'status', 'checked_cost', 'reported_closure_s', 'reason']}), flush=True)
    return rows


def main():
    if os.environ.get('OAR_JOB_ID') != '7013762' or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise RuntimeError('This diagnostic schedule requires owned compute job 7013762')
    unexpected = {key: value for key, value in os.environ.items()
                  if key.startswith('KAYROS_')}
    if unexpected:
        raise RuntimeError(f'Unexpected inherited diagnostic settings: {sorted(unexpected)}')
    root = Path.home() / 'ms58-bpca'
    out = root / 'outputs/resume-20261110/correction'
    cores = physical_cores()
    if len(cores) < 10:
        raise RuntimeError('Need at least 10 distinct allocated physical cores')
    out.mkdir(parents=True, exist_ok=False)
    for directory in ['frozen-patches', 'rows', 'launch-logs']:
        (out / directory).mkdir()
    scripts = out / 'frozen-scripts'
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns('__pycache__'))
    checker = checker_identity()
    manifest = frozen_existing_manifest(root / 'archived-manifests/upstream.json',
        out / 'frozen-patches', scripts, root / 'experiment/patches', checker)
    old_binary = manifest['worker_environment']['UPSTREAM_PROBE_BINARY']
    binary = root / 'correction-build/bp'
    del manifest['runtime_files'][old_binary]
    manifest['runtime_files'][str(binary)] = sha256(binary)
    manifest['worker_environment']['UPSTREAM_PROBE_BINARY'] = str(binary)
    patch = root / 'scripts/upstream-correction-control-fixed.patch'
    frozen_patch = out / 'frozen-patches' / patch.name
    shutil.copyfile(patch, frozen_patch)
    manifest['source_patch_files'][str(frozen_patch)] = sha256(frozen_patch)
    manifest['source_id'] = '92ae000 explicit correction derivative of f931288 (production identical to 68f0f9a)'
    manifest['purpose'] = 'Exploratory correction-setting comparison, not frozen Lera timing or proof validation'
    for setting in [0, 1]:
        variant = deepcopy(manifest)
        variant['diagnostic_environment'] = {'KAYROS_DIAGNOSTIC_CORRECTING': str(setting)}
        atomic_json(out / f'correcting-{setting}-manifest.json', variant)
    lanes = []
    for name, horizon, index, order in [('C104', 600, 7, [0, 1]),
                                      ('R201', 300, 8, [1, 0]),
                                      ('RC201', 300, 9, [0, 1])]:
        instance_path = Path.home() / f'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100/{name}.vrp.json'
        instance = json.loads(instance_path.read_bytes())
        atf = (instance_path.parent / instance['td']['atf_path']).resolve()
        lanes.append({'instance': name, 'horizon_s': horizon, 'cpu': cores[index],
                      'order': order, 'memory_mb': 16384, 'input_path': str(instance_path),
                      'input_sha256': sha256(instance_path), 'atf_path': str(atf), 'atf_sha256': sha256(atf)})
    atomic_json(out / 'schedule.json', {'host': socket.gethostname(), 'oar_job_id': os.environ['OAR_JOB_ID'],
        'created_utc': now(), 'checker': checker, 'scheduled_count': 6, 'lanes': lanes,
        'driver_sha256': sha256(Path(__file__)), 'binary_sha256': sha256(binary),
        'source_patch_sha256': sha256(frozen_patch), 'source_id': manifest['source_id'],
        'interpretation': 'Single exploratory paired observation per instance. All failures retained. No claim of valid exhaustive pricing or fixed-control timing.'})
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda lane: run_lane(lane, root, out, scripts), lanes))
    rows = [row for lane_rows in results for row in lane_rows]
    atomic_json(out / 'complete.json', {'scheduled_count': 6, 'row_count': len(rows),
        'launch_or_audit_failures': sum(row['status'] == 'launch_or_audit_failure' for row in rows),
        'rows': rows, 'finished_utc': now()})


if __name__ == '__main__':
    main()
