"""Measure the point repair against the fixed reference on 6 Dabia cases."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_paired_probe import candidate_manifest
from probe_runner import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', default='7013766')
    parser.add_argument('--latest-start-utc', default='2026-11-10T06:40:00+00:00')
    args = parser.parse_args()
    assert args.job_id == '7013766' and os.environ.get('OAR_JOB_ID') == args.job_id
    assert not socket.gethostname().startswith(('fnancy', 'flyon'))
    now = datetime.now(timezone.utc)
    latest = datetime.fromisoformat(args.latest_start_utc)
    deadline = datetime.fromisoformat('2026-11-10T07:15:00+00:00')
    assert latest <= datetime.fromisoformat('2026-11-10T06:40:00+00:00')
    assert now < latest and now + timedelta(seconds=1800 + 300) < deadline
    assert not any(key.startswith('KAYROS_') for key in os.environ)
    root = Path.home() / 'ms58-bpca'
    run = root / 'resume-20261110'
    validation_path = run / 'point-validation/complete.json'
    native_path = run / 'point-validation/native-comparison.json'
    fuzz_path = run / 'point-fuzz/complete.json'
    validation = json.loads(validation_path.read_bytes())
    native = json.loads(native_path.read_bytes())
    fuzz = json.loads(fuzz_path.read_bytes())
    assert validation['candidate_source'] == '250105c'
    assert validation['reference_source'] == '0787925'
    assert validation['all_candidate_processes_passed'] is True
    assert native['reference_expected_failure_observed'] is True
    assert native['candidate_passed'] is True
    assert validation['native_comparison'] == native
    assert fuzz['reference_source'] == '0787925' and fuzz['candidate_binary_source'] == '250105c'
    assert fuzz['all_batches_passed'] is True
    assert {row['variant'] for row in fuzz['results']} == {'reference', 'point-addition'}
    assert all(row['executed_cases'] == 5 and not row['unsound_lines']
               and row['returncode'] == 0 and not row['outer_timeout'] for row in fuzz['results'])
    assert fuzz['results'][0]['case_lines'] == fuzz['results'][1]['case_lines']
    cores, seen = [], set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = tuple((topology / name).read_text().strip()
                    for name in ['physical_package_id', 'core_id'])
        if key not in seen:
            seen.add(key)
            cores.append(cpu)
    assert len(cores) >= 10
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    # Six comparison lanes, one canonical lane, two Lera lanes and 16 GiB reserve.
    assert available >= 160 * 1024 ** 2
    reference_patches = {p.name: sha256(p) for p in (root / 'patches-reference').glob('*.patch')}
    point_patches = {p.name: sha256(p) for p in (root / 'patches-point-addition').glob('*.patch')}
    assert len(reference_patches) == 4 and len(point_patches) == 5
    assert all(point_patches.get(name) == digest for name, digest in reference_patches.items())
    output = run / 'point-comparison'
    output.mkdir(parents=True, exist_ok=False)
    for source in [validation_path, native_path, fuzz_path]:
        shutil.copyfile(source, output / f'{source.parent.name}-{source.name}')
    shutil.copyfile(__file__, output / Path(__file__).name)
    patches = output / 'reference-manifest-patches'
    patches.mkdir()
    manifest = candidate_manifest(root / 'venv-reference/bin/python', root / 'patches-reference',
                                  patches, checker_identity(), '0787925')
    atomic_json(output / 'reference-manifest.json', manifest)
    tasks = []
    for name, index in zip(['C101', 'R101', 'C201', 'RC201', 'R201', 'C104'], [0, 1, 2, 3, 4, 6]):
        repetitions = 1 if name == 'C104' else 3
        horizon = 600 if name == 'C104' else 300
        command = [sys.executable, str(root / 'experiment/scripts/ms58_paired_probe.py'),
                   '--output', str(output / 'paired' / name),
                   '--reference-python', str(root / 'venv-reference/bin/python'),
                   '--reference-manifest', str(output / 'reference-manifest.json'),
                   '--reference-source-id', '0787925',
                   '--candidate-python', str(root / 'venv-point-addition/bin/python'),
                   '--candidate-source-id', '250105c',
                   '--candidate-patch-dir', str(root / 'patches-point-addition'),
                   '--patch-dir', str(root / 'experiment/patches'),
                   '--instances', name, '--repetitions', str(repetitions), '--horizon', str(horizon),
                   '--memory-mb', '16384', '--cpu-core', str(cores[index])]
        tasks.append({'instance': name, 'cpu': cores[index], 'physical_core_index': index,
                      'repetitions': repetitions, 'horizon_s': horizon, 'command': command})
    plan = {'tasks': tasks, 'source_reference': '0787925', 'production_candidate': '250105c',
            'scheduled_cells': 32, 'memory_available_kib': available,
            'memory_preflight_gib': 160, 'oar_job_id': args.job_id, 'host': socket.gethostname(),
            'created_utc': now.isoformat(), 'latest_start_utc': latest.isoformat(),
            'planned_completion_deadline_utc': deadline.isoformat(),
            'reserved_other_core_indices': [5, 8, 9],
            'source_patch_files': point_patches,
            'validation_files': {str(p): sha256(p) for p in [validation_path, native_path, fuzz_path]},
            'fuzz_coverage': {'requested': 6, 'actual_per_variant': 5,
                              'omission': 'Seed 23 case 0 selects absent TDVRPTW/Solomon1987/n=25.'},
            'scope': 'Exploratory performance impact of a separate correctness repair. No main-campaign promotion or proof-speed confirmation. All failures and timeouts remain in the paired records.'}
    atomic_json(output / 'launch-plan.json', plan)
    processes, rows = [], []
    for task in tasks:
        try:
            with (output / f"lane-{task['instance']}.log").open('x') as log:
                process = subprocess.Popen(task['command'], stdout=log, stderr=subprocess.STDOUT)
            processes.append((task, process))
            print(task['instance'], process.pid, flush=True)
        except Exception as error:
            row = {'instance': task['instance'], 'returncode': None,
                   'launch_failure': f'{type(error).__name__}: {error}'}
            rows.append(row)
            atomic_json(output / f"lane-{task['instance']}-result.json", row)
    for task, process in processes:
        row = {'instance': task['instance'], 'returncode': process.wait(),
               'finished_utc': datetime.now(timezone.utc).isoformat()}
        rows.append(row)
        atomic_json(output / f"lane-{task['instance']}-result.json", row)
    atomic_json(output / 'lanes-complete.json', {'results': rows,
                'returncodes': [row['returncode'] for row in rows],
                'finished_utc': datetime.now(timezone.utc).isoformat()})
    return int(any(row['returncode'] != 0 for row in rows))


if __name__ == '__main__':
    raise SystemExit(main())
