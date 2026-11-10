"""Independently price checked witness routes against captured pricing duals."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from checker_contract import checker_identity, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ['trace', 'instance', 'witness', 'output']:
        parser.add_argument('--' + option, type=Path, required=True)
    args = parser.parse_args()
    identity = checker_identity()
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import check_td_solution, compute_route_duration, load_td_instance
    loaded = load_td_instance(args.instance, verify_sha256=True)
    witness = json.loads(args.witness.read_bytes())
    routes = witness['routes']
    checked = check_td_solution(loaded, BenchmarkSolution(instance_name=loaded.instance.instance_name, routes=routes))
    if not checked.is_valid():
        raise ValueError('The supplied witness is not checker valid')
    costs = [compute_route_duration(loaded.instance, loaded.atfs, route).duration for route in routes]
    events = [json.loads(line) for line in args.trace.read_text().splitlines()]
    ends = {event['call']: event for event in events if event['event'] == 'pricing_end'}
    calls = []
    for event in events:
        if event['event'] != 'pricing_begin':
            continue
        context = event.get('context')
        if context is None:
            calls.append({'call': event['call'], 'level': event['level'], 'context_missing': True})
            continue
        profits, cuts, cut_duals = context['profits'], context['cuts'], context['cuts_duals']
        if len(cuts) != len(cut_duals):
            raise ValueError('Cut and dual dimensions differ')
        forbidden = {tuple(arc) for arc in context['forbidden_arcs']}
        rows = []
        for route, cost in zip(routes, costs, strict=True):
            path = [0, *route, loaded.instance.num_customers + 1]
            arcs = set(zip(path[:-1], path[1:]))
            incompatible = sorted(arcs.intersection(forbidden))
            coefficients = [int(len(set(route).intersection(cut)) >= 2) for cut in cuts]
            customer_dual_sum = sum(profits[customer] for customer in route)
            cut_dual_sum = sum(coefficient * dual for coefficient, dual in zip(coefficients, cut_duals, strict=True))
            reduced_cost = cost - customer_dual_sum - cut_dual_sum
            rows.append({'path': path, 'checker_cost': cost, 'customer_dual_sum': customer_dual_sum,
                         'cut_coefficients': coefficients, 'cut_dual_sum': cut_dual_sum,
                         'reduced_cost': reduced_cost, 'forbidden_arcs_in_route': incompatible,
                         'compatible_negative_column': not incompatible and reduced_cost < -1e-6})
        end = ends.get(event['call'])
        cut_activities = [sum(row['cut_coefficients'][index] for row in rows) for index in range(len(cuts))]
        calls.append({'call': event['call'], 'level': event['level'], 'context': context,
                      'search_finished': end['search_finished'] if end else None,
                      'returned_route_count': end['route_count'] if end else None,
                      'witness_cut_activities': cut_activities,
                      'witness_satisfies_all_captured_cuts': all(value <= 1 for value in cut_activities),
                      'negative_witness_routes': [row for row in rows if row['compatible_negative_column']],
                      'routes': rows})
    output = {'checker': identity, 'trace_sha256': sha256(args.trace),
              'instance_sha256': sha256(args.instance), 'atf_sha256': sha256(loaded.atf_path),
              'witness_sha256': sha256(args.witness), 'witness_checked_cost': checked.routing_cost,
              'calls': calls,
              'scope': 'Forbidden-arc compatibility and checker reduced costs. Preprocessed graph availability is not checked.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'calls': len(calls), 'last': calls[-1] if calls else None}))


if __name__ == '__main__':
    main()
