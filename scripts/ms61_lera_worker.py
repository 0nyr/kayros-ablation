"""ms61 Lera f2 cell worker, adapted from the ms58 upstream_event_worker.py.

It feeds the pinned f2 'bp' binary exactly as ms58's lera-minimal arm did.
Stdin holds three JSON lines: the BP-CUTS experiment from experiments/bp.json with time_limit overridden, the native instance and an empty object.
The binary reads PROBE_START_MONOTONIC_NS and PROBE_DEADLINE_MONOTONIC_NS (inherited from the runner) for its timestamps and its time limit.
UPSTREAM_EXPECTED_END_DEPOT is set to n+1 for route numbering checks.
Event lines from the binary are forwarded to stdout, the console log goes to stderr, and the final native result is written atomically to --native-result.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--memory-mb', type=float, required=True)
    parser.add_argument('--native-result', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    args = parser.parse_args()
    os.sched_setaffinity(0, {int(os.environ['PROBE_CPU_AFFINITY'])})
    cap = int(args.memory_mb * 1024 ** 2)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    start = int(os.environ['PROBE_START_MONOTONIC_NS'])
    deadline = int(os.environ['PROBE_DEADLINE_MONOTONIC_NS'])

    def emit(event):
        event['elapsed_s'] = (time.monotonic_ns() - start) / 1e9
        print(json.dumps(event, allow_nan=False), flush=True)

    binary = args.binary.resolve()
    source = args.source_root.resolve()
    materialized = json.loads(args.instance.read_bytes())
    size = materialized['num_customers']
    native_path = source / 'instances/dabia_et_al_2013' / f"{materialized['instance_name']}_{size}.json"
    native_bytes = native_path.read_bytes()
    native = json.loads(native_bytes)
    checks = {
        'demands': native['demands'][:-1] == materialized['demands'],
        'service_times': native['service_times'][:-1] == materialized['service_times'],
        'time_windows': native['time_windows'][:-1] == materialized['time_windows'],
        'coordinates': native['digraph']['coordinates'][:-1] == materialized['coordinates'],
        'capacity': native['capacity'] == materialized['vehicle_capacity'],
        'horizon': native['horizon'] == materialized['horizon'],
        'vehicle_count': native['vehicle_count'] == materialized['num_vehicles'],
        'depots': native['start_depot'] == 0 and native['end_depot'] == size + 1,
    }
    if not all(checks.values()):
        raise ValueError(f'Native/materialized metadata mismatch: {checks}')
    experiment_path = source / 'experiments/bp.json'
    experiment = next(e for e in json.loads(experiment_path.read_bytes())['experiments'] if e['name'] == 'BP-CUTS')
    experiment['time_limit'] = max(0., (deadline - time.monotonic_ns()) / 1e9)
    emit({'event': 'phase', 'phase': 'native_launch', 'binary': str(binary),
          'native_instance': str(native_path),
          'native_instance_sha256': hashlib.sha256(native_bytes).hexdigest(),
          'experiment_sha256': hashlib.sha256(experiment_path.read_bytes()).hexdigest(),
          'parameters': experiment, 'metadata_equivalence': checks,
          'cpu_affinity': sorted(os.sched_getaffinity(0)), 'address_space_limit_bytes': cap})
    payload = '\n'.join(json.dumps(value) for value in (experiment, native, {})) + '\n'
    # The runner owns this worker's process group, including the binary.
    process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=sys.stderr, text=True, bufsize=1,
                               env=dict(os.environ, UPSTREAM_EXPECTED_END_DEPOT=str(size + 1)))
    process.stdin.write(payload)
    process.stdin.close()
    final = None
    for line in process.stdout:
        event = json.loads(line)
        if event.get('event') == 'native_final':
            final = event['native_result']
            continue
        # The C++ side timestamps events from the inherited monotonic origin.
        print(json.dumps(event, allow_nan=False), flush=True)
    code = process.wait()
    if code:
        raise SystemExit(code)
    if final is None:
        raise RuntimeError('Upstream exited without native final evidence')
    record = {'arm': 'lera-exact', 'binary': str(binary),
              'native_instance_sha256': hashlib.sha256(native_bytes).hexdigest(),
              'native_result': final}
    path = args.native_result
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('x') as stream:
        stream.write(json.dumps(record, indent=2, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    emit({'event': 'phase', 'phase': 'worker_complete', 'native_result': str(path)})


if __name__ == '__main__':
    main()
