"""Read-only minute checkpoints outside the frozen solver instrumentation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time


def node_health(wave):
    os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
    process = subprocess.run(['ps', '-u', str(os.getuid()), '-o', 'pid=,ppid=,rss=,comm=,args='], capture_output=True, text=True, check=True)
    records = []
    workers = []
    for line in process.stdout.splitlines():
        fields = line.split(None, 4)
        if len(fields) != 5:
            continue
        pid, ppid, rss, comm, command = fields
        item = {'pid': int(pid), 'ppid': int(ppid), 'rss_kib': int(rss), 'comm': comm, 'command': command}
        records.append(item)
        try:
            argv = (Path('/proc') / pid / 'cmdline').read_bytes().decode().split('\0')[:-1]
        except OSError:
            argv = []
        if len(argv) > 1 and argv[1] in [str(wave / 'frozen-scripts' / name) for name in ['kayros_worker.py', 'upstream_event_worker.py']]:
            workers.append(item)
    worker_ids = {item['pid'] for item in workers}
    native = [item for item in records if item['ppid'] in worker_ids and item['comm'] == 'bp']
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    return {'host': socket.gethostname(), 'workers_alive': len(workers), 'native_children_alive': len(native),
            'worker_rss_sum_kib': sum(item['rss_kib'] for item in workers),
            'worker_rss_max_kib': max([item['rss_kib'] for item in workers], default=0),
            'native_child_rss_sum_kib': sum(item['rss_kib'] for item in native),
            'native_child_rss_max_kib': max([item['rss_kib'] for item in native], default=0),
            'memory_available_kib': available}


def controller(wave):
    os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
    spec = json.loads((wave / 'wave.json').read_bytes())
    actual = set(Path(os.environ['OAR_NODEFILE']).read_text().split())
    hosts = sorted({task['host'] for task in spec['scheduled_cells']})
    if not set(hosts) <= actual or str(spec['oar_job_id']) != os.environ['OAR_JOB_ID']:
        raise RuntimeError('Health monitor must use the matching real OAR job')
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), '--mode', 'node', '--wave', str(wave)])
    def sample(host):
        try:
            completed = subprocess.run(['oarsh', '-n', host, command], capture_output=True, text=True, timeout=20, check=True)
            return json.loads(completed.stdout)
        except Exception as error:
            return {'host': host, 'error': f'{type(error).__name__}: {error}'}
    with (wave / 'health.jsonl').open('a') as stream:
        with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
            while True:
                checkpoint = {'measurement_revision': 2, 'utc': datetime.now(timezone.utc).isoformat(), 'scheduled': len(spec['scheduled_cells']),
                    'completed_rows': len(list((wave / 'rows').glob('*.json'))), 'nodes': list(pool.map(sample, hosts)),
                    'coordinator_complete': (wave / 'coordinator-complete.json').exists()}
                stream.write(json.dumps(checkpoint, allow_nan=False) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
                temporary = wave / 'health-latest.json.tmp'
                temporary.write_text(json.dumps(checkpoint, indent=2) + '\n')
                os.replace(temporary, wave / 'health-latest.json')
                if checkpoint['coordinator_complete']:
                    break
                time.sleep(60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['node', 'controller'], required=True)
    parser.add_argument('--wave', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'node':
        print(json.dumps(node_health(args.wave)))
    else:
        controller(args.wave)
