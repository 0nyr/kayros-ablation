"""Check the ILS to BPCA handoff and deterministic ILS state after BPCA."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from kayros import Params, _core, solve
    from kayros.io import load_instance
    from kayros.lera import routes_to_mamut, solve_duration
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import check_td_solution

    root = Path(os.environ['MAMUT_ROUTING_BENCHMARKS_ROOT'])
    report = {'purpose': 'Focused component coupling check, not a performance campaign',
              'core_sha256': hashlib.sha256(Path(_core.__file__).read_bytes()).hexdigest(),
              'cases': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for family, size, name in [('Dabia2013', 'n=25', 'C101'), ('Rifki2020', 'n=10', 'Rifki-1')]:
        path = root / 'TDVRPTW' / family / size / f'{name}.vrp.json'
        if not path.is_file():
            raise FileNotFoundError(path)
        loaded = load_instance(path, verify=True)
        params = Params(ils_max_iterations=200)
        before = solve(loaded, params, seed=58)
        started = time.monotonic()
        exact = solve_duration(loaded, time_limit_s=120.0,
                               initial_routes=before.routes, memory_limit_mb=8192)
        elapsed = time.monotonic() - started
        after = solve(loaded, params, seed=58)
        routes = ([route[1:-1] for route in routes_to_mamut(exact['routes'], loaded.instance.num_customers)]
                  if exact['routes'] else before.routes)
        witness = check_td_solution(loaded, BenchmarkSolution(instance_name=name, routes=routes))
        status = exact['exact_log']['status']
        row = {'family': family, 'size': size, 'instance': name,
               'ils_before_cost': before.duration, 'ils_after_cost': after.duration,
               'ils_before_routes': before.routes, 'ils_after_routes': after.routes,
               'ils_state_unchanged': before.duration == after.duration and before.routes == after.routes,
               'bpca_status': status, 'bpca_elapsed_s': elapsed, 'checked_valid': witness.is_valid(),
               'checked_cost': witness.routing_cost, 'exact_result': exact}
        report['cases'].append(row)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
        assert row['ils_state_unchanged'], row
        assert witness.is_valid(), row
        assert witness.routing_cost <= before.duration + 1e-6, row
        if status == 'Optimum':
            assert exact['value'] == witness.routing_cost, row
            assert exact['value'] <= before.duration + 1e-6, row
        bound = exact['exact_log'].get('best_bound')
        if bound is not None:
            assert bound <= before.duration + 1e-6, row
    print(json.dumps({'cases': len(report['cases']), 'all_ils_state_checks_passed': True}))


if __name__ == '__main__':
    main()
