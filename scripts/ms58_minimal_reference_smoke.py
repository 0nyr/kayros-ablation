"""Validate the one-line upstream correction-off reference as a separate arm."""
from concurrent.futures import ThreadPoolExecutor
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
from ms58_correction_runs import physical_cores
from ms58_paired_probe import frozen_existing_manifest
from probe_runner import atomic_json


def now():
    return datetime.now(timezone.utc).isoformat()


def run_cell(task, root, out, scripts):
    command = [sys.executable, str(scripts / 'run_solver_probe.py'),
        '--manifest', str(out / 'manifest.json'), '--worker-python', sys.executable,
        '--worker', str(scripts / 'upstream_event_worker.py'),
        '--instance', task['input_path'], '--output', str(out / 'cells'),
        '--arm', 'lera-exact', '--horizon', '300', '--memory-mb', '16384',
        '--cpu-core', str(task['cpu']), '--repetition', '1']
    row = dict(task, command=command, started_utc=now(), status='pending',
               native_work={'available': False}, reported_closure_s=None,
               capped_closure_s=300, corrected_count=None)
    stdout = out / 'launch-logs' / f"{task['instance']}.json"
    stderr = out / 'launch-logs' / f"{task['instance']}.stderr"
    try:
        for path_key, digest_key in [('input_path', 'input_sha256'), ('atf_path', 'atf_sha256')]:
            if sha256(Path(task[path_key])) != task[digest_key]:
                raise ValueError(f'{path_key} changed after schedule freeze')
        with stdout.open('x') as stream, stderr.open('x') as err:
            process = subprocess.run(command, stdout=stream, stderr=err)
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
            capped_closure_s=closure if closure is not None else 300)
        native = validation.get('native_result', {})
        if native.get('sha256'):
            path = Path(native['path'])
            if sha256(path) != native['sha256']:
                raise ValueError('Native sidecar digest mismatch')
            row['native_sha256'] = native['sha256']
            row['native_work'] = native_work(json.loads(path.read_bytes()))
            counts = [level.get('corrected_count') for level in row['native_work']['root_labeling'].values()]
            if counts and all(count is not None for count in counts):
                row['corrected_count'] = sum(counts)
                if row['corrected_count'] != 0:
                    raise ValueError('Minimal correction-off reference reported positive correction count')
        previous_path = root / 'outputs/resume-20261110/correction/rows' / f"{task['instance']}-correcting-0.json"
        if previous_path.exists():
            previous = json.loads(previous_path.read_bytes())
            if previous.get('input_sha256') != task['input_sha256'] or previous.get('correcting') is not False:
                raise ValueError('Explicit-off comparison identity mismatch')
            if previous.get('native_work', {}).get('available') and row['native_work']['available']:
                row['explicit_off_comparison'] = {
                    'source_row_sha256': sha256(previous_path),
                    'root_work_matches': row['native_work']['root_work_signature'] == previous['native_work']['root_work_signature'],
                    'whole_work_matches': row['native_work']['whole_work_signature'] == previous['native_work']['whole_work_signature'],
                    'same_checked_cost': row['checked_cost'] == previous['checked_cost']}
    except Exception as error:
        row.update(status='launch_or_audit_failure', reason=f'{type(error).__name__}: {error}')
    row['finished_utc'] = now()
    atomic_json(out / 'rows' / f"{task['instance']}.json", row)
    print(json.dumps({key: row.get(key) for key in
                     ['instance', 'status', 'checked_cost', 'corrected_count', 'explicit_off_comparison', 'reason']}), flush=True)
    return row


def main():
    if os.environ.get('OAR_JOB_ID') != '7013766' or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise RuntimeError('This validation schedule requires owned compute job 7013766')
    if any(key.startswith('KAYROS_') for key in os.environ):
        raise RuntimeError('Minimal reference validation requires no inherited KAYROS settings')
    root = Path.home() / 'ms58-bpca'
    out = root / 'outputs/resume-20261110/minimal-reference'
    cores = physical_cores()
    if len(cores) < 10:
        raise RuntimeError('Need at least 10 distinct allocated physical cores')
    binary = root / 'upstream-minimal-build/bp'
    expected_binary = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
    if sha256(binary) != expected_binary:
        raise ValueError('Minimal reference binary differs from the declared build')
    out.mkdir(parents=True, exist_ok=False)
    for name in ['frozen-patches', 'rows', 'launch-logs']:
        (out / name).mkdir()
    scripts = out / 'frozen-scripts'
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns('__pycache__'))
    checker = checker_identity()
    manifest = frozen_existing_manifest(root / 'archived-manifests/upstream.json',
        out / 'frozen-patches', scripts, root / 'experiment/patches', checker)
    old = manifest['worker_environment']['UPSTREAM_PROBE_BINARY']
    del manifest['runtime_files'][old]
    manifest['runtime_files'][str(binary)] = expected_binary
    manifest['worker_environment']['UPSTREAM_PROBE_BINARY'] = str(binary)
    patch = root / 'scripts/upstream-minimal-reference.patch'
    frozen_patch = out / 'frozen-patches' / patch.name
    shutil.copyfile(patch, frozen_patch)
    manifest['source_patch_files'][str(frozen_patch)] = sha256(frozen_patch)
    manifest['source_id'] = 'f2c9e20, one-line correcting=false initialization atop frozen f931288'
    manifest['purpose'] = 'Validate separately identified minimal repaired reference, not a main-campaign comparison'
    manifest['diagnostic_environment'] = {}
    atomic_json(out / 'manifest.json', manifest)
    tasks = []
    for name, index in [('C101', 8), ('RC201', 9)]:
        path = Path.home() / f'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100/{name}.vrp.json'
        instance = json.loads(path.read_bytes())
        atf = (path.parent / instance['td']['atf_path']).resolve()
        tasks.append({'instance': name, 'cpu': cores[index], 'horizon_s': 300, 'memory_mb': 16384,
            'input_path': str(path), 'input_sha256': sha256(path),
            'atf_path': str(atf), 'atf_sha256': sha256(atf)})
    atomic_json(out / 'schedule.json', {'host': socket.gethostname(), 'oar_job_id': os.environ['OAR_JOB_ID'],
        'created_utc': now(), 'checker': checker, 'scheduled_count': 2, 'tasks': tasks,
        'source_id': manifest['source_id'], 'driver_sha256': sha256(Path(__file__)),
        'binary_sha256': expected_binary, 'source_patch_sha256': sha256(frozen_patch),
        'interpretation': 'Check declared correction-off behavior and checked incumbents. All failures retained. No independent proof of pricing completeness.'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda task: run_cell(task, root, out, scripts), tasks))
    atomic_json(out / 'complete.json', {'scheduled_count': 2, 'row_count': len(rows),
        'launch_or_audit_failures': sum(row['status'] == 'launch_or_audit_failure' for row in rows),
        'rows': rows, 'finished_utc': now()})


if __name__ == '__main__':
    main()
