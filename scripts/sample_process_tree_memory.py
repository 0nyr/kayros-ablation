"""Bounded independent process-tree RSS observation on an unused allocated CPU."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--wave', type=Path, required=True)
parser.add_argument('--seconds', type=int, default=3700)
args = parser.parse_args()
core = max(os.sched_getaffinity(0))
os.sched_setaffinity(0, {core})
started = datetime.now(timezone.utc).isoformat()
output = args.wave / 'process-tree-memory' / (socket.gethostname().split('.')[0] + '.json')
output.parent.mkdir(parents=True, exist_ok=True)
records = {}
if output.exists():
    prior = json.loads(output.read_bytes())
    started = prior['started_utc']
    for record in prior['processes']:
        argv = record['worker_argv']
        if len(argv) > 1 and argv[1] in [str(args.wave / 'frozen-scripts' / name) for name in ['kayros_worker.py', 'upstream_event_worker.py']]:
            records[str(record['worker_pid'])] = record

def status(pid):
    data = {}
    for line in (Path('/proc') / str(pid) / 'status').read_text().splitlines():
        key, _, value = line.partition(':')
        data[key] = value.strip()
    return data


def field(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None

for sample in range(args.seconds):
    found = subprocess.run(['ps', '-u', str(os.getuid()), '-o', 'pid=,args='], capture_output=True, text=True, check=True)
    for line in found.stdout.splitlines():
        pid, _, command = line.strip().partition(' ')
        if not any(str(args.wave / 'frozen-scripts' / name) in command for name in ['kayros_worker.py', 'upstream_event_worker.py']):
            continue
        try:
            root = Path('/proc') / pid
            argv = (root / 'cmdline').read_bytes().decode().split('\0')[:-1]
            if len(argv) <= 1 or argv[1] not in [str(args.wave / 'frozen-scripts' / name) for name in ['kayros_worker.py', 'upstream_event_worker.py']]:
                continue
            data = status(pid)
            pending = [int(pid)]
            tree_rss = 0
            child_hwm = 0
            observed = []
            while pending:
                current = pending.pop()
                try:
                    member = status(current)
                    tree_rss += int(member.get('VmRSS', '0 kB').split()[0])
                    if current != int(pid):
                        child_hwm += int(member.get('VmHWM', '0 kB').split()[0])
                    observed.append(current)
                    children = Path(f'/proc/{current}/task/{current}/children').read_text().split()
                    pending.extend(int(child) for child in children)
                except (OSError, ValueError):
                    pass
            record = records.setdefault(pid, {'worker_pid': int(pid), 'worker_argv': argv,
                'instance_path': field(argv, '--instance'), 'arm': field(argv, '--arm'),
                'native_result_path': field(argv, '--native-result'), 'prefix_s': field(argv, '--prefix-seconds'),
                'first_observed_utc': datetime.now(timezone.utc).isoformat(), 'samples': 0,
                'max_sampled_tree_rss_kib': 0, 'max_sampled_worker_vm_hwm_kib': 0,
                'max_sampled_descendant_vm_hwm_sum_kib': 0})
            record['samples'] += 1
            record['max_sampled_tree_rss_kib'] = max(record['max_sampled_tree_rss_kib'], tree_rss)
            record['max_sampled_worker_vm_hwm_kib'] = max(record['max_sampled_worker_vm_hwm_kib'], int(data.get('VmHWM', '0 kB').split()[0]))
            record['max_sampled_descendant_vm_hwm_sum_kib'] = max(record['max_sampled_descendant_vm_hwm_sum_kib'], child_hwm)
            record['last_observed_pids'] = observed
        except (OSError, ValueError, KeyError):
            pass
    complete = (args.wave / 'coordinator-complete.json').exists()
    temporary = output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'measurement_revision': 2, 'monitor_pid': os.getpid(), 'host': socket.gethostname(), 'monitor_cpu': core, 'started_utc': started,
        'checkpoint_utc': datetime.now(timezone.utc).isoformat(), 'samples': sample + 1, 'sample_interval_s': 1,
        'bound_s': args.seconds, 'wave_complete': complete,
        'measurement': 'Sampled simultaneous worker-plus-descendant RSS. VmHWM fields are supplemental sampled process high-water marks. Short-lived processes before observation can be missed.',
        'processes': list(records.values())}, indent=2) + '\n')
    os.replace(temporary, output)
    if complete:
        break
    time.sleep(1)
