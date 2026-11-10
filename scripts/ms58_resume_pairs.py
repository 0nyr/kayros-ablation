"""Launch the normalization comparison after resumed validation."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import socket
import subprocess
import sys

from checker_contract import checker_identity
from ms58_paired_probe import candidate_manifest
from probe_runner import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--run-label', default='resume-20261110')
    parser.add_argument('--latest-start-utc', required=True)
    args = parser.parse_args()
    assert os.environ.get('OAR_JOB_ID') == args.job_id
    assert not socket.gethostname().startswith(('fnancy', 'flyon'))
    assert datetime.now(timezone.utc) < datetime.fromisoformat(args.latest_start_utc)
    root = Path.home() / 'ms58-bpca'
    run = root / args.run_label
    assert (run / 'validation-reviewed.json').is_file()
    import json
    review = json.loads((run / 'validation-reviewed.json').read_text())
    assert review['candidate_timing_authorized'] is True
    cores = []
    seen = set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = tuple((topology / name).read_text().strip()
                    for name in ['physical_package_id', 'core_id'])
        if key not in seen:
            seen.add(key)
            cores.append(cpu)
    assert len(cores) >= 7
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    # Six timing lanes, three correction lanes, one diagnostic lane and reserve.
    assert available > (10 * 16384 + 16384) * 1024
    patches = run / 'reference-manifest-patches'
    patches.mkdir(exist_ok=False)
    manifest = candidate_manifest(root / 'venv-reference/bin/python', root / 'patches-reference',
                                  patches, checker_identity(), '0787925')
    atomic_json(run / 'reference-manifest.json', manifest)
    tasks = []
    for index, name in enumerate(['C101', 'R101', 'C201', 'RC201', 'R201', 'C104']):
        repetitions = 1 if name == 'C104' else 3
        horizon = 600 if name == 'C104' else 300
        command = [sys.executable, str(root / 'experiment/scripts/ms58_paired_probe.py'),
                   '--output', str(run / 'paired' / name),
                   '--reference-python', str(root / 'venv-reference/bin/python'),
                   '--reference-manifest', str(run / 'reference-manifest.json'),
                   '--reference-source-id', '0787925',
                   '--candidate-python', str(root / 'venv-normalize/bin/python'),
                   '--candidate-source-id', '20625ce production, eed117b fixture',
                   '--candidate-patch-dir', str(root / 'patches-normalize'),
                   '--patch-dir', str(root / 'experiment/patches'),
                   '--instances', name, '--repetitions', str(repetitions), '--horizon', str(horizon),
                   '--memory-mb', '16384', '--cpu-core', str(cores[index])]
        tasks.append({'instance': name, 'cpu': cores[index], 'command': command})
    atomic_json(run / 'launch-plan.json', {'tasks': tasks, 'spare_core': cores[6],
                'source_reference': '0787925', 'production_candidate': '20625ce',
                'candidate_fixture': 'eed117b', 'memory_available_kib': available,
                'scope': 'Exploratory candidate comparison, not proof-speed confirmation.',
                'oar_job_id': args.job_id, 'host': socket.gethostname()})
    processes = []
    for task in tasks:
        with (run / f"lane-{task['instance']}.log").open('x') as log:
            process = subprocess.Popen(task['command'], stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        print(task['instance'], process.pid, flush=True)
    codes = [process.wait() for process in processes]
    atomic_json(run / 'lanes-complete.json', {'returncodes': codes})
    return int(any(codes))


if __name__ == '__main__':
    raise SystemExit(main())
