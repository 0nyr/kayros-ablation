"""Independent bounded per-process virtual-memory snapshots on an unused CPU."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import time


def process_status(pid):
    base = Path('/proc') / str(pid)
    data = dict(line.split(':', 1) for line in (base / 'status').read_text().splitlines() if ':' in line)
    limit = next(line.split() for line in (base / 'limits').read_text().splitlines() if line.startswith('Max address space'))
    return {'pid': int(pid), 'VmSize_kib': int(data.get('VmSize', '0 kB').split()[0]),
            'VmPeak_kib': int(data.get('VmPeak', '0 kB').split()[0]),
            'VmRSS_kib': int(data.get('VmRSS', '0 kB').split()[0]),
            'rlimit_as_soft_bytes': limit[3], 'rlimit_as_hard_bytes': limit[4]}


def largest_member(members, key):
    member = max(members.values(), key=lambda item: item[key], default=None)
    return {'value_kib': member[key], 'owner_pid': member['pid']} if member else None


def observe(wave):
    found = subprocess.check_output(['ps', '-u', str(os.getuid()), '-o', 'pid=,args='], text=True)
    observations = []
    for line in found.splitlines():
        pid, _, command = line.strip().partition(' ')
        if str(wave / 'frozen-scripts') not in command:
            continue
        try:
            argv = (Path('/proc') / pid / 'cmdline').read_bytes().decode().split('\0')[:-1]
            if len(argv) < 2 or argv[1] not in [str(wave / 'frozen-scripts' / name) for name in ['kayros_worker.py', 'upstream_event_worker.py']]:
                continue
            field = lambda name: argv[argv.index(name) + 1] if name in argv else None
            members, pending = [], [int(pid)]
            while pending:
                current = pending.pop()
                try:
                    members.append({**process_status(current), 'role': 'worker' if current == int(pid) else 'descendant'})
                    pending.extend(int(child) for child in Path(f'/proc/{current}/task/{current}/children').read_text().split())
                except (OSError, ValueError, StopIteration):
                    pass
            observations.append({'worker_pid': int(pid), 'worker_argv': argv,
                'native_result_path': field('--native-result'), 'instance_path': field('--instance'),
                'arm': field('--arm'), 'prefix_s': field('--prefix-seconds'), 'members': members})
        except (OSError, ValueError):
            pass
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--seconds', type=int, required=True)
    args = parser.parse_args()
    core = max(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {core})
    output = args.wave / 'virtual-memory'
    output.mkdir(exist_ok=True)
    host = socket.gethostname().split('.')[0]
    records = {}
    source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with (output / (host + '.jsonl')).open('x') as trace:
        for sample in range((args.seconds + 59) // 60):
            observations = observe(args.wave)
            now = datetime.now(timezone.utc).isoformat()
            trace.write(json.dumps({'measurement_revision': 4, 'utc': now, 'observations': observations}) + '\n')
            trace.flush()
            for observation in observations:
                pid = str(observation['worker_pid'])
                record = records.setdefault(pid, {key: value for key, value in observation.items() if key != 'members'})
                members = record.setdefault('member_maxima', {})
                for member in observation['members']:
                    previous = members.setdefault(str(member['pid']), dict(member))
                    for key in ['VmSize_kib', 'VmPeak_kib', 'VmRSS_kib']:
                        previous[key] = max(previous[key], member[key])
                record['max_member_VmSize'] = largest_member(members, 'VmSize_kib')
                record['max_member_VmPeak'] = largest_member(members, 'VmPeak_kib')
            complete = (args.wave / 'coordinator-complete.json').exists()
            temporary = output / (host + '.json.tmp')
            temporary.write_text(json.dumps({'schema_version': 1, 'measurement_revision': 4, 'monitor_source_sha256': source_sha256, 'host': host, 'monitor_cpu': core, 'checkpoint_utc': now,
                'bound_s': args.seconds, 'nominal_interval_s': 60, 'wave_complete': complete,
                'scope': 'Per-process VmSize/VmPeak, never summed across worker and descendants. Kernel VmPeak includes earlier lifetime for processes still alive at observation. Earlier short-lived processes may be absent.',
                'processes': list(records.values())}, indent=2) + '\n')
            os.replace(temporary, output / (host + '.json'))
            if complete:
                break
            time.sleep(60)


if __name__ == '__main__':
    main()
