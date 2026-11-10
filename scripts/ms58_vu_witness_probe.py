"""Preserve a checked Vu witness and a controlled cold or warm BPCA solve."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import time

from checker_contract import checker_identity, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--witness', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--warm', action='store_true')
    parser.add_argument('--cut-limit', type=int, default=100)
    parser.add_argument('--horizon', type=float, default=120)
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--memory-mb', type=int, default=8192)
    args = parser.parse_args()
    checker = checker_identity()
    from kayros import _core, _lera
    from kayros.lera import routes_to_mamut, solve_duration
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import check_td_solution, load_td_instance

    args.output.mkdir(parents=True, exist_ok=False)
    loaded = load_td_instance(args.instance, verify_sha256=True)
    witness = json.loads(args.witness.read_bytes())
    checked = check_td_solution(loaded, BenchmarkSolution(instance_name=loaded.instance.instance_name,
                                                         routes=witness['routes']))
    identity = {'checker': checker, 'source_id': args.source_id,
                'runtime_files': {str(Path(module.__file__).resolve()): sha256(Path(module.__file__))
                                  for module in [_core, _lera]},
                'input_sha256': sha256(args.instance), 'atf_sha256': sha256(loaded.atf_path),
                'witness_sha256': sha256(args.witness), 'script_sha256': sha256(Path(__file__)),
                'warm': args.warm, 'cut_limit': args.cut_limit, 'horizon_s': args.horizon,
                'memory_limit_mb': args.memory_mb,
                'host': socket.gethostname(), 'oar_job_id': os.environ.get('OAR_JOB_ID'),
                'affinity': sorted(os.sched_getaffinity(0)),
                'created_utc': datetime.now(timezone.utc).isoformat()}
    (args.output / 'identity.json').write_text(json.dumps(identity, indent=2) + '\n')
    (args.output / 'witness.json').write_text(json.dumps({'routes': witness['routes'],
        'valid': checked.is_valid(), 'checked_cost': checked.routing_cost,
        'status': str(checked.status), 'error': checked.error_message}, indent=2) + '\n')
    assert checked.is_valid(), checked
    with (args.output / 'events.jsonl').open('x') as events:
        def callback(payload):
            events.write(json.dumps(payload) + '\n')
            events.flush()
        started = time.monotonic()
        result = solve_duration(loaded, time_limit_s=args.horizon, cut_limit=args.cut_limit,
                                initial_routes=witness['routes'] if args.warm else None,
                                memory_limit_mb=args.memory_mb, on_event=callback)
        elapsed = time.monotonic() - started
    (args.output / 'native-result.json').write_text(json.dumps(result, indent=2) + '\n')
    routes = ([route[1:-1] for route in routes_to_mamut(result['routes'], loaded.instance.num_customers)]
              if result['routes'] else witness['routes'] if args.warm else None)
    returned = (check_td_solution(loaded, BenchmarkSolution(instance_name=loaded.instance.instance_name,
                                                            routes=routes)) if routes is not None else None)
    log = result['exact_log']
    summary = {'status': log['status'], 'value': result.get('value'), 'best_bound': log.get('best_bound'),
               'witness_checked_cost': checked.routing_cost,
               'certificate_exceeds_checked_witness': (log['status'] == 'Optimum'
                   and result.get('value') is not None and result['value'] > checked.routing_cost + 1e-6),
               'bound_exceeds_checked_witness': log.get('best_bound', -float('inf')) > checked.routing_cost + 1e-6,
               'returned_routes_valid': returned.is_valid() if returned is not None else None,
               'returned_routes_checked_cost': returned.routing_cost if returned is not None else None,
               'elapsed_s': elapsed, 'warm': args.warm, 'cut_limit': args.cut_limit}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
