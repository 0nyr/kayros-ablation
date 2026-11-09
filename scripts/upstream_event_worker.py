"""Instrumented native upstream worker on the common process-launch clock."""
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
    parser.add_argument('--arm', choices=['lera-exact'], required=True)
    parser.add_argument('--memory-mb', type=float, required=True)
    parser.add_argument('--native-result', type=Path, required=True)
    args = parser.parse_args()
    os.sched_setaffinity(0, {int(os.environ['PROBE_CPU_AFFINITY'])})
    cap = int(args.memory_mb * 1024 ** 2)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    start = int(os.environ['PROBE_START_MONOTONIC_NS'])
    deadline = int(os.environ['PROBE_DEADLINE_MONOTONIC_NS'])
    def emit(event):
        event['elapsed_s'] = (time.monotonic_ns()-start)/1e9
        print(json.dumps(event, allow_nan=False), flush=True)
    binary = Path(os.environ['UPSTREAM_PROBE_BINARY']).resolve()
    source = Path(os.environ['UPSTREAM_SOURCE_ROOT']).resolve()
    materialized = json.loads(args.instance.read_bytes())
    size = materialized['num_customers']
    native_path = source / 'instances/dabia_et_al_2013' / f"{materialized['instance_name']}_{size}.json"
    native_bytes = native_path.read_bytes()
    expected_native = os.environ.get('UPSTREAM_NATIVE_INPUT_SHA256')
    if expected_native and hashlib.sha256(native_bytes).hexdigest() != expected_native:
        raise ValueError('Native input differs from immutable cell identity')
    native = json.loads(native_bytes)
    checks = {
        'demands': native['demands'][:-1] == materialized['demands'],
        'service_times': native['service_times'][:-1] == materialized['service_times'],
        'time_windows': native['time_windows'][:-1] == materialized['time_windows'],
        'coordinates': native['digraph']['coordinates'][:-1] == materialized['coordinates'],
        'capacity': native['capacity'] == materialized['vehicle_capacity'],
        'horizon': native['horizon'] == materialized['horizon'],
        'vehicle_count': native['vehicle_count'] == materialized['num_vehicles'],
        'depots': native['start_depot'] == 0 and native['end_depot'] == size+1,
    }
    if not all(checks.values()):
        raise ValueError(f'Native/materialized metadata mismatch: {checks}')
    experiment_path = source / 'experiments/bp.json'
    experiment = next(e for e in json.loads(experiment_path.read_bytes())['experiments'] if e['name']=='BP-CUTS')
    experiment['time_limit'] = max(0.,(deadline-time.monotonic_ns())/1e9)
    emit({'event':'phase','phase':'native_launch','native_instance_sha256':hashlib.sha256(native_bytes).hexdigest(),
          'parameters':experiment,'metadata_equivalence':checks,
          'cpu_affinity':sorted(os.sched_getaffinity(0)),'address_space_limit_bytes':cap})
    payload = '\n'.join(json.dumps(value) for value in (experiment,native,{}))+'\n'
    # The outer harness owns this worker's process group, including the binary.
    process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=sys.stderr, text=True, bufsize=1,
                               env=dict(os.environ,UPSTREAM_EXPECTED_END_DEPOT=str(size+1)))
    process.stdin.write(payload)
    process.stdin.close()
    final = None
    for line in process.stdout:
        event = json.loads(line)
        if event.get('event') == 'native_final':
            final = event['native_result']
            continue
        # Timestamp is emitted by C++ from the inherited monotonic origin.
        print(json.dumps(event, allow_nan=False), flush=True)
    code = process.wait()
    if code:
        raise SystemExit(code)
    if final is None:
        raise RuntimeError('Upstream exited without native final evidence')
    record = {'arm':args.arm,'run_id':os.environ['PROBE_RUN_ID'],
              'manifest_hash':os.environ['PROBE_MANIFEST_HASH'],
              'native_instance_sha256':hashlib.sha256(native_bytes).hexdigest(),
              'native_result':final}
    args.native_result.parent.mkdir(parents=True,exist_ok=True)
    with args.native_result.open('x') as stream:
        stream.write(json.dumps(record,indent=2,allow_nan=False)+'\n')
    emit({'event':'phase','phase':'worker_complete','native_result':str(args.native_result)})


if __name__ == '__main__':
    main()
