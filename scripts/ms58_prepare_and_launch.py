"""Freeze this session's bounded candidate experiment and launch physical-core lanes."""
import json
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import time

from checker_contract import checker_identity, sha256
from ms58_paired_probe import candidate_manifest
from probe_runner import atomic_json

root = Path.home() / 'ms58-bpca'
assert os.environ.get('OAR_JOB_ID') == '7013080'
assert time.time() < datetime.fromisoformat('2026-11-09T14:20:00+00:00').timestamp()
for variant in ['reference', 'normalize', 'diagnostics']:
    assert (root / f'logs/build-{variant}.complete').exists(), variant
assert (root / 'logs/correctness.complete').exists()
cores = []
seen = set()
for cpu in sorted(os.sched_getaffinity(0)):
    topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
    key = tuple((topology / name).read_text().strip() for name in ['physical_package_id', 'core_id'])
    if key not in seen:
        seen.add(key)
        cores.append(cpu)
assert len(cores) >= 7
available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
assert available > (6 * 16384 + 16384) * 1024
patches = root / 'reference-manifest-patches'
patches.mkdir(exist_ok=False)
manifest = candidate_manifest(root / 'venv-reference/bin/python', root / 'patches-reference', patches,
                              checker_identity(), '0787925')
atomic_json(root / 'reference-manifest.json', manifest)
tasks = []
for index, name in enumerate(['C101', 'R101', 'C201', 'RC201', 'R201', 'C104']):
    # Three paired repetitions for throughput. C104 retains a longer bounded diagnostic horizon.
    repetitions = 1 if name == 'C104' else 3
    horizon = 600 if name == 'C104' else 300
    command = [sys.executable, str(root / 'experiment/scripts/ms58_paired_probe.py'),
               '--output', str(root / 'outputs/paired' / name),
               '--reference-python', str(root / 'venv-reference/bin/python'),
               '--reference-manifest', str(root / 'reference-manifest.json'),
               '--reference-source-id', '0787925',
               '--candidate-python', str(root / 'venv-normalize/bin/python'),
               '--candidate-source-id', '45992f7',
               '--candidate-patch-dir', str(root / 'patches-normalize'),
               '--patch-dir', str(root / 'experiment/patches'),
               '--instances', name, '--repetitions', str(repetitions), '--horizon', str(horizon),
               '--memory-mb', '16384', '--cpu-core', str(cores[index])]
    tasks.append({'instance': name, 'cpu': cores[index], 'command': command})
atomic_json(root / 'outputs/launch-plan.json', {'tasks': tasks, 'spare_core': cores[6],
            'source_reference': '0787925', 'source_candidate': '45992f7',
            'scope': 'Exploratory candidate comparison, not proof-speed confirmation.',
            'pause_deadline_utc': '2026-11-09T14:58:00Z'})
processes = []
for task in tasks:
    with (root / f"logs/lane-{task['instance']}.log").open('x') as log:
        process = subprocess.Popen(task['command'], stdout=log, stderr=subprocess.STDOUT)
    processes.append(process)
    print(task['instance'], process.pid, flush=True)
codes = [process.wait() for process in processes]
atomic_json(root / 'outputs/lanes-complete.json', {'returncodes': codes})
raise SystemExit(any(codes))
