"""Independent bounded native-child RSS samples, separate from audit outcomes."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--samples', type=int, default=660)
args = parser.parse_args()
expected = args.binary.resolve()
records = {}
args.output.parent.mkdir(parents=True, exist_ok=True)
for sample in range(args.samples):
    found = subprocess.run(['pgrep', '-x', 'bp'], capture_output=True, text=True)
    for pid in found.stdout.split():
        try:
            process = Path('/proc') / pid
            if (process / 'exe').resolve() != expected:
                continue
            status = {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in (process / 'status').read_text().splitlines() if ':' in line}
            parent = Path('/proc') / status['PPid']
            argv = (parent / 'cmdline').read_bytes().decode().split('\0')[:-1]
            if not any('upstream_event_worker.py' in arg for arg in argv):
                continue
            rss = int(status.get('VmRSS', '0 kB').split()[0])
            hwm = int(status.get('VmHWM', '0 kB').split()[0])
            record = records.setdefault(pid, {'native_pid': int(pid), 'parent_pid': int(status['PPid']), 'parent_argv': argv,
                                             'samples': 0, 'max_sampled_rss_kib': 0, 'max_sampled_vm_hwm_kib': 0})
            record['samples'] += 1
            record['max_sampled_rss_kib'] = max(record['max_sampled_rss_kib'], rss)
            record['max_sampled_vm_hwm_kib'] = max(record['max_sampled_vm_hwm_kib'], hwm)
        except (OSError, ValueError, KeyError):
            pass
    temporary = args.output.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'host': socket.gethostname(), 'native_binary': str(expected), 'completed_samples': sample + 1,
        'sample_interval_s': 0.5, 'caveat': 'Monitoring started after wave launch. Short-lived children may be missed. This is native child memory, separate from worker-only harness VmHWM.',
        'processes': list(records.values())}, indent=2) + '\n')
    os.replace(temporary, args.output)
    time.sleep(0.5)
