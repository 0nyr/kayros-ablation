#!/usr/bin/env python3
"""Native upstream smoke adapter. Final output is not an anytime trace or proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--instance', required=True)
    parser.add_argument('--seconds', type=float, default=5)
    args = parser.parse_args()
    if not 0 < args.seconds <= 60:
        raise ValueError('Smoke adapter permits only 0 < seconds <= 60')
    instance_path = args.source_root / 'instances/dabia_et_al_2013' / (args.instance + '.json')
    experiment_path = args.source_root / 'experiments/bp.json'
    instance = json.loads(instance_path.read_bytes())
    experiment = next(e for e in json.loads(experiment_path.read_bytes())['experiments'] if e['name'] == 'BP-CUTS')
    experiment['time_limit'] = args.seconds
    payload = '\n'.join(json.dumps(x) for x in (experiment, instance, {})) + '\n'
    started = time.monotonic()
    def emit(event):
        print(json.dumps(event, allow_nan=False), flush=True)
    emit({'event': 'adapter_identity', 'binary_sha256': digest(args.binary),
          'native_input_sha256': digest(instance_path), 'experiment_sha256': digest(experiment_path),
          'native_instance': args.instance, 'parameters': experiment,
          'representation_status': 'native_upstream_input_pending_cross_adapter_equivalence',
          'proof_status': 'unverified_no_closure_instrumentation'})
    # Inherit the enclosing process group so the outer runner can terminate all work.
    result = subprocess.run([str(args.binary.resolve())], input=payload, text=True,
                            stdout=subprocess.PIPE, stderr=sys.stderr)
    if result.returncode:
        raise SystemExit(result.returncode)
    output = json.loads(result.stdout)
    emit({'event': 'native_final', 'worker_elapsed_s': time.monotonic() - started,
          'native_result': output, 'proof_status': 'unverified'})
    solution = output.get('Best solution', {})
    routes = solution.get('routes', [])
    customers = instance['digraph']['vertex_count'] - 2
    if routes:
        mapped = []
        for route in routes:
            path = route['path']
            if path[0] != 0 or path[-1] != customers + 1 or any(not 1 <= v <= customers for v in path[1:-1]):
                raise ValueError('Unexpected native customer/depot mapping')
            mapped.append(path[1:-1])
        emit({'event': 'candidate', 'routes': mapped, 'native_cost': solution['value'],
              'origin': 'native_final_only', 'mapping': 'drop native depots 0 and n+1; customer IDs unchanged'})


if __name__ == '__main__':
    main()
