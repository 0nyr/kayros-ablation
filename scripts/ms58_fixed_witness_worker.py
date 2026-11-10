"""Execute one prepared CPLEX stage without ILS or native caller-ID coercion."""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

from checker_contract import sha256
from ms58_fixed_witness import (ARMS, EXACT_SECONDS, INSTANCES, MEMORY_MB,
                               checked_file, checked_witness, validate_original_routes)
from probe_runner import atomic_json


def arm_stage_deadline(stage_start_ns, exact_seconds=EXACT_SECONDS):
    if exact_seconds not in (600, 3600):
        raise ValueError('Only the declared 600 s pilot or 3600 s C104 stage is supported')
    deadline = stage_start_ns + int(exact_seconds * 1e9)
    # Native Lera consults this deadline. Its original timestamp origin stays.
    os.environ['PROBE_DEADLINE_MONOTONIC_NS'] = str(deadline)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, max(1e-6, (deadline - time.monotonic_ns()) / 1e9))
    return deadline


def native_metadata(native, materialized):
    n = materialized['num_customers']
    checks = {'demands': native['demands'][:-1] == materialized['demands'],
        'service_times': native['service_times'][:-1] == materialized['service_times'],
        'time_windows': native['time_windows'][:-1] == materialized['time_windows'],
        'coordinates': native['digraph']['coordinates'][:-1] == materialized['coordinates'],
        'capacity': native['capacity'] == materialized['vehicle_capacity'],
        'horizon': native['horizon'] == materialized['horizon'],
        'vehicle_count': native['vehicle_count'] == materialized['num_vehicles'],
        'depots': native['start_depot'] == 0 and native['end_depot'] == n + 1}
    if not all(checks.values()):
        raise ValueError('Native/materialized instance metadata differ')
    return checks


def main(args):
    exact_seconds = args.exact_seconds
    os.sched_setaffinity(0, {int(os.environ['PROBE_CPU_AFFINITY'])})
    cap = MEMORY_MB * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    origin = int(os.environ['PROBE_START_MONOTONIC_NS'])
    def emit(event, **payload):
        print(json.dumps({'event': event, 'elapsed_s': (time.monotonic_ns() - origin) / 1e9,
                          **payload}, allow_nan=False), flush=True)
    witness = json.loads(checked_file(args.witness, args.witness_sha256).read_bytes())
    materialized = json.loads(args.instance.read_bytes())
    allowed = ('C104',) if exact_seconds == 3600 else INSTANCES
    if materialized['instance_name'] not in allowed or materialized['num_customers'] != 100:
        raise ValueError('Only the instance cohort declared for this exact-stage budget is allowed')
    atf = args.instance.parent / materialized['td']['atf_path']
    if (sha256(args.instance) != witness['instance_sha256'] or sha256(atf) != witness['atf_sha256']
            or witness['instance_name'] != materialized['instance_name']):
        raise ValueError('Frozen witness belongs to another input')
    # This checks all original IDs before importing or invoking native code.
    validate_original_routes(materialized, witness['routes'])
    checked, loaded = checked_witness(args.instance, witness['routes'], witness['checked_cost'])
    warm = args.arm in ('current-warm', 'ub-only', 'dedup-only')
    emit('phase', phase='fixed_witness_ready', checked_cost=checked['checked_cost'],
         routes_sha256=checked['routes_sha256'], supplied_to_native=warm,
         cpu_affinity=sorted(os.sched_getaffinity(0)), address_space_limit_bytes=cap)
    if warm:
        emit('candidate', phase='caller_held_before_exact', native_cost=checked['checked_cost'],
             routes=witness['routes'], source='frozen_checked_caller_witness',
             routes_format='mamut-customers', supplied_to_native=True)
    manifest = json.loads(checked_file(args.runtime_manifest, args.runtime_manifest_sha256).read_bytes())
    native = args.arm == 'lera-minimal'
    if native:
        environment = manifest['worker_environment']
        source = Path(environment['UPSTREAM_SOURCE_ROOT']).resolve()
        native_path = source / f"instances/dabia_et_al_2013/{materialized['instance_name']}_100.json"
        checked_file(native_path, manifest['runtime_files'][str(native_path)])
        raw_native = json.loads(native_path.read_bytes())
        metadata = native_metadata(raw_native, materialized)
        parameters_path = source / 'experiments/bp.json'
        checked_file(parameters_path, manifest['runtime_files'][str(parameters_path)])
        parameters = next(e for e in json.loads(parameters_path.read_bytes())['experiments'] if e['name'] == 'BP-CUTS')
        parameters['time_limit'] = exact_seconds
    else:
        from kayros import _lera
        from kayros.lera import _has_stepwise_atfs, to_lera_payload
        if _lera.LP_BACKEND != 'CPLEX':
            raise ValueError('Fixed-witness solver must use CPLEX')
        if _has_stepwise_atfs(loaded):
            os.environ['KAYROS_STEP_EXACT'] = '1'
        else:
            os.environ.pop('KAYROS_STEP_EXACT', None)
        payload = json.dumps(to_lera_payload(loaded))
        kwargs = {'time_limit_s': exact_seconds, 'cut_limit': 500, 'solution_limit': 3000,
                  'stab_alpha': 0., 'memory_limit_mb': float(MEMORY_MB)}
        if warm:
            # Original IDs have already passed strict checks. No int() coercion.
            kwargs['initial_routes'] = witness['routes']
        def incumbent_hook(text):
            inc = json.loads(text)
            routes = []
            n = materialized['num_customers']
            for route in inc['routes']:
                path = route.get('path')
                if (not isinstance(path, list) or len(path) < 3 or path[0] != 0 or path[-1] != n + 1
                        or any(type(v) is not int or not 1 <= v <= n for v in path[1:-1])):
                    emit('integrity', reason='invalid_native_route_numbering', raw_native_route=route)
                    return
                routes.append(path[1:-1])
            emit('candidate', phase='exact', native_cost=inc['value'], native_elapsed_s=inc['time'],
                 routes=routes, routes_format='mamut-customers')
        def event_hook(text):
            event = json.loads(text)
            kind = event.pop('event')
            event['native_elapsed_s'] = event.pop('time')
            if kind == 'global_bound':
                emit('bound', phase='exact', **event)
            elif kind == 'search_closed':
                emit('closure', phase='exact', status='Optimum', proof_acceptance='unverified', **event)
            else:
                emit('phase', phase='exact_terminated', **event)
        kwargs.update(on_incumbent=incumbent_hook, on_event=event_hook)
    if (int(os.environ['PROBE_DEADLINE_MONOTONIC_NS']) - time.monotonic_ns()) / 1e9 < exact_seconds + 1:
        raise ValueError('Preparation exhausted the separate 60 s process allowance')
    stage_start_ns = time.monotonic_ns()
    emit('phase', phase='exact_stage_start', exact_budget_s=exact_seconds,
         elapsed_s=(stage_start_ns - origin) / 1e9,
         clock_scope='exact stage after preparation', native_backend='CPLEX')
    arm_stage_deadline(stage_start_ns, exact_seconds)
    if native:
        emit('phase', phase='native_launch', native_instance_sha256=sha256(native_path),
             metadata_equivalence=metadata, parameters=parameters)
        process = subprocess.Popen([environment['UPSTREAM_PROBE_BINARY']], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=sys.stderr, text=True, bufsize=1,
            env=dict(os.environ, UPSTREAM_EXPECTED_END_DEPOT='101'))
        process.stdin.write('\n'.join(json.dumps(v) for v in (parameters, raw_native, {})) + '\n')
        process.stdin.close()
        final = None
        for line in process.stdout:
            event = json.loads(line)
            if event.get('event') == 'native_final':
                final = event['native_result']
            else:
                print(json.dumps(event, allow_nan=False), flush=True)
        code = process.wait()
        if code or final is None:
            raise RuntimeError(f'Fixed native Lera failed with exit {code} or no final artifact')
        result = {'native_result': final}
    else:
        exact = json.loads(_lera.solve_duration_json(payload, **kwargs))
        result = {'exact': exact}
    signal.setitimer(signal.ITIMER_REAL, 0.)
    result.update(arm=args.arm, instance_name=materialized['instance_name'],
        caller_witness_sha256=args.witness_sha256, caller_witness_routes_sha256=checked['routes_sha256'],
        caller_witness_supplied_to_native=warm, run_id=os.environ['PROBE_RUN_ID'],
        manifest_hash=os.environ['PROBE_MANIFEST_HASH'])
    args.native_result.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.native_result, result)
    emit('phase', phase='worker_complete', native_result=str(args.native_result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--witness', type=Path, required=True)
    parser.add_argument('--witness-sha256', required=True)
    parser.add_argument('--arm', choices=ARMS, required=True)
    parser.add_argument('--runtime-manifest', type=Path, required=True)
    parser.add_argument('--runtime-manifest-sha256', required=True)
    parser.add_argument('--native-result', type=Path, required=True)
    parser.add_argument('--exact-seconds', type=int, choices=[600, 3600], default=600)
    main(parser.parse_args())
