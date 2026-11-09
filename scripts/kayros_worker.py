"""Native Kayros development probe worker; all route checking happens afterwards."""
import argparse
import json
import os
import resource
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', required=True)
    parser.add_argument('--arm', required=True, choices=['kayros-bpca-cplex', 'kayros-bpca-highs', 'kayros-heuristics', 'kayros-full-cplex'])
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--prefix-seconds', type=float)
    parser.add_argument('--memory-mb', type=float, default=8000)
    parser.add_argument('--native-result', required=True)
    args = parser.parse_args()
    core = int(os.environ['PROBE_CPU_AFFINITY'])
    os.sched_setaffinity(0, {core})
    memory_bytes = int(args.memory_mb * 1024**2)
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    start = int(os.environ['PROBE_START_MONOTONIC_NS'])
    deadline = int(os.environ['PROBE_DEADLINE_MONOTONIC_NS'])

    def emit(event, **payload):
        print(json.dumps({'event': event, 'elapsed_s': (time.monotonic_ns()-start)/1e9, **payload}, allow_nan=False), flush=True)

    def remaining():
        return max(0., (deadline-time.monotonic_ns())/1e9)

    from checker_contract import checker_identity
    identity = checker_identity()
    import kayros
    from mamut_routing_lib.td.artifacts import load_td_instance
    from kayros.lera import solve_duration, routes_to_mamut
    emit('phase', phase='loading', checker_identity=identity, kayros_version=kayros.__version__, cpu_affinity=sorted(os.sched_getaffinity(0)), address_space_limit_bytes=memory_bytes)
    loaded = load_td_instance(args.instance, verify_sha256=True)
    native = {'arm': args.arm, 'instance': args.instance}
    witness = None
    witness_cost = float("inf")
    if args.arm in ('kayros-heuristics', 'kayros-full-cplex'):
        emit('phase', phase='ils', seed=args.seed)
        budget = remaining()
        if args.arm == 'kayros-full-cplex':
            if args.prefix_seconds is None:
                raise ValueError('Full arm requires fixed --prefix-seconds')
            budget = min(budget, max(0., args.prefix_seconds-(time.monotonic_ns()-start)/1e9))

        def record_heuristic(cost, routes, source, native_elapsed_s=None):
            nonlocal witness, witness_cost
            produced_ns = time.monotonic_ns()
            payload = {'phase': 'ils', 'native_cost': cost, 'routes': routes,
                       'routes_format': 'mamut-customers', 'source': source,
                       'elapsed_s': (produced_ns-start)/1e9}
            if native_elapsed_s is not None:
                payload['native_elapsed_s'] = native_elapsed_s
            if args.arm == 'kayros-full-cplex' and produced_ns > start+int(args.prefix_seconds*1e9):
                payload['phase'] = 'ils_late_candidate'
                emit('phase', **payload)
                return
            emit('candidate', **payload)
            if cost < witness_cost:
                witness = [list(route) for route in routes]
                witness_cost = cost

        def heuristic_hook(inc, routes):
            record_heuristic(inc.value, routes, 'callback', inc.seconds)

        if budget > 0:
            from kayros import _core
            from kayros.io import to_core
            core_instance = to_core(loaded, objective_function='Duration')
            budget = remaining()
            if args.arm == 'kayros-full-cplex':
                budget = min(budget, max(0., args.prefix_seconds-(time.monotonic_ns()-start)/1e9))
            if budget > 0:
                result = _core.solve_ils(core_instance, kayros.Params(strategy='ils')._to_ils_core(), args.seed, budget, heuristic_hook)
                if result.routes:
                    record_heuristic(result.value, result.routes, 'heuristic_return')
                native['heuristic'] = {'value': result.value, 'routes': result.routes}
                native['handoff_witness'] = {'native_cost': witness_cost, 'routes': witness} if witness else None
    if args.arm == 'kayros-full-cplex':
        prefix_end = min(deadline, start + int(args.prefix_seconds * 1e9))
        while time.monotonic_ns() < prefix_end:
            time.sleep(max(0., (prefix_end-time.monotonic_ns())/1e9))
        emit('phase', phase='fixed_prefix_complete', prefix_seconds=args.prefix_seconds, warm_witness=witness is not None)
    if args.arm != 'kayros-heuristics' and remaining() > 0:
        from kayros import _lera
        expected = 'HiGHS' if args.arm == 'kayros-bpca-highs' else 'CPLEX'
        if _lera.LP_BACKEND != expected:
            raise RuntimeError(f'Expected {expected}, found {_lera.LP_BACKEND}')
        emit('phase', phase='exact', lp_backend=_lera.LP_BACKEND, warm_start=witness is not None)
        n = loaded.instance.num_customers

        def exact_hook(inc):
            routes = []
            for route in inc['routes']:
                path = route.get('path', [])
                if len(path) < 2 or path[0] != 0 or path[-1] != n+1 or any(type(v) is not int or not 1 <= v <= n for v in path[1:-1]):
                    emit('integrity', phase='exact', reason='invalid_native_route_numbering', native_route=route)
                    return
                routes.append(path[1:-1])
            emit('candidate', phase='exact', native_cost=inc['value'], native_elapsed_s=inc['time'], routes=routes, routes_format='mamut-customers')

        def event_hook(event):
            payload = dict(event)
            kind = payload.pop('event')
            payload['native_elapsed_s'] = payload.pop('time')
            if kind == 'global_bound':
                emit('bound', phase='exact', **payload)
            elif kind == 'search_closed':
                payload.setdefault('status', 'Optimum')
                emit('closure', phase='exact', proof_acceptance='unverified', **payload)
            else:
                emit('phase', phase='exact_terminated', **payload)

        native['exact'] = solve_duration(loaded, time_limit_s=remaining(), cut_limit=500, memory_limit_mb=args.memory_mb,
            initial_routes=witness, on_incumbent=exact_hook, on_event=event_hook)
        emit('phase', phase='exact_return', status=native['exact']['exact_log']['status'])
    path = Path(args.native_result)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('x') as stream:
        stream.write(json.dumps(native, indent=2)+'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path)
    temporary.unlink()
    emit('phase', phase='worker_complete', native_result=str(path))


if __name__ == '__main__':
    main()
