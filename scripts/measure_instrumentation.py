"""Paired fixed-work supplemental-instrumentation check on one reserved core."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys

from checker_contract import checker_identity, sha256
from probe_runner import atomic_json, digest


def fixed_work(value):
    """Keep native work, primal/dual values and witnesses, removing clocks."""
    if isinstance(value, dict):
        return {key: fixed_work(item) for key, item in value.items()
                if 'time' not in key and key not in ('screen_output', 'ablation_final_pricing_certified')}
    if isinstance(value, list):
        return [fixed_work(item) for item in value]
    return value


def work_signature(native):
    routes = sorted(native.get('routes', []), key=lambda route: tuple(route['path']))
    return digest({'exact_log': fixed_work(native['exact_log']), 'value': native.get('value'),
                   'routes': routes, 'checker_infeasible_columns': native.get('checker_infeasible_columns')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cplex-python', type=Path, required=True)
    parser.add_argument('--highs-python', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=10)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--patch-dir', type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get('OAR_JOB_ID'):
        raise RuntimeError('Run inside a real allocated OAR node')
    args.output.mkdir(parents=True, exist_ok=False)
    scripts = args.output/'frozen-scripts'
    patches = args.output/'frozen-patches'
    shutil.copytree(args.source/'scripts', scripts, ignore=shutil.ignore_patterns('__pycache__'))
    patches.mkdir()
    names = ['0001-kayros-bound-events.patch', '0002-cplex-single-thread.patch',
             '0003-kayros-node-closure-census.patch', '0004-kayros-effective-backend-settings.patch',
             'overhead-0001-toggle-supplemental-instrumentation.patch']
    for name in names:
        shutil.copyfile(args.patch_dir/name, patches/name)
    cases = args.case or ['C101:100', 'R101:100', 'C104:100']
    core = min(os.sched_getaffinity(0))
    rng = random.Random(20261109)
    rows = []
    manifests = {}
    for backend, python in [('cplex', args.cplex_python), ('highs', args.highs_python)]:
        query = "import importlib.metadata as m,json; d=m.distribution('kayros'); print(json.dumps([str(d.locate_file(p).resolve()) for p in d.files if str(p).endswith(('.py','.so'))]))"
        installed = json.loads(subprocess.check_output([str(python), '-c', query], text=True))
        manifest = {'schema_version': 1, 'checker': checker_identity(),
                    'purpose': 'paired-supplemental-instrumentation-overhead', 'backend': backend,
                    'runtime_files': {name: sha256(Path(name)) for name in installed},
                    'source_patch_files': {str(patches/name): sha256(patches/name) for name in names}}
        path = args.output/(backend+'-manifest.json')
        atomic_json(path, manifest)
        manifests[backend] = path

    def execute(backend, case, repetition, enabled):
        name, n = case.split(':')
        instance = args.inputs/f'benchmarks/TDVRPTW/Dabia2013/n={n}/{name}.vrp.json'
        worker_python = args.cplex_python if backend == 'cplex' else args.highs_python
        env = dict(os.environ, KAYROS_ABLATION_INSTRUMENTATION=str(int(enabled)))
        command = [sys.executable, str(scripts/'run_solver_probe.py'), '--manifest', str(manifests[backend]),
                   '--worker-python', str(worker_python), '--instance', str(instance), '--output', str(args.output/'cells'),
                   '--arm', f'kayros-bpca-{backend}', '--horizon', '10', '--memory-mb', '8192',
                   '--cpu-core', str(core), '--repetition', str(repetition)]
        completed = subprocess.run(command, env=env, text=True, capture_output=True)
        row = {'backend': backend, 'case': case, 'repetition': repetition, 'enabled': enabled,
               'launcher_returncode': completed.returncode}
        if completed.returncode:
            row['launcher_stderr'] = completed.stderr
        else:
            result = json.loads(completed.stdout)
            row.update(run_id=result['run_id'], status=result['status'], solver_elapsed_s=result['solver_elapsed_s'],
                       checked_cost=result['validation'].get('best_checked_cost'))
            artifact = result.get('native_result') or result['validation'].get('native_result')
            if artifact and Path(artifact['path']).is_file():
                native = json.loads(Path(artifact['path']).read_bytes())['exact']
                row.update(native_status=native['exact_log']['status'], native_elapsed_s=native['exact_log']['time'],
                           instrumentation_enabled=native.get('instrumentation_enabled'),
                           fixed_work_sha256=work_signature(native))
                if enabled:
                    row['effective_backend_settings'] = native.get('effective_backend_settings')
            row['result_path'] = str(args.output/'cells'/result['run_id']/'result.json')
        rows.append(row)
        atomic_json(args.output/'rows.json', rows)
        print(json.dumps(row), flush=True)
        return row

    summaries = []
    for backend in ['cplex', 'highs']:
        for case in cases:
            baseline = execute(backend, case, -1, False)
            if baseline.get('native_status') != 'Optimum' or baseline.get('native_elapsed_s', 10) > 5:
                summaries.append({'backend': backend, 'case': case, 'status': 'excluded_not_closed_within_5_native_seconds', 'baseline': baseline})
                continue
            pairs = []
            for repetition in range(args.repetitions):
                order = [False, True]
                rng.shuffle(order)
                pair = {enabled: execute(backend, case, repetition, enabled) for enabled in order}
                off, on = pair[False], pair[True]
                matched = (off.get('status') == on.get('status') == 'completed'
                           and off.get('native_status') == on.get('native_status') == 'Optimum'
                           and off.get('instrumentation_enabled') is False and on.get('instrumentation_enabled') is True
                           and off.get('checked_cost') == on.get('checked_cost') is not None
                           and off.get('fixed_work_sha256') == on.get('fixed_work_sha256'))
                pairs.append({'repetition': repetition, 'matched_fixed_work': matched,
                              'native_delta_s': on.get('native_elapsed_s', 0)-off.get('native_elapsed_s', 0),
                              'outer_delta_s': on.get('solver_elapsed_s', 0)-off.get('solver_elapsed_s', 0),
                              'off_native_s': off.get('native_elapsed_s'), 'on_native_s': on.get('native_elapsed_s'),
                              'off_outer_s': off.get('solver_elapsed_s'), 'on_outer_s': on.get('solver_elapsed_s')})
            valid = [p for p in pairs if p['matched_fixed_work']]
            summary = {'backend': backend, 'case': case, 'pairs': pairs, 'matched_pairs': len(valid),
                       'status': 'measured' if valid else 'no_matched_work_pairs'}
            if valid:
                for metric in ['native_delta_s', 'outer_delta_s', 'off_native_s', 'on_native_s', 'off_outer_s', 'on_outer_s']:
                    summary['median_'+metric] = statistics.median(p[metric] for p in valid)
                summary['median_native_relative_delta'] = statistics.median(p['native_delta_s']/p['off_native_s'] for p in valid if p['off_native_s'] > 0)
                summary['median_outer_relative_delta'] = statistics.median(p['outer_delta_s']/p['off_outer_s'] for p in valid if p['off_outer_s'] > 0)
            summaries.append(summary)
            atomic_json(args.output/'summary.json', {'schema_version': 1, 'cpu_core': core, 'cases': summaries,
                'limitation': 'Supplemental bound/census/settings instrumentation only. Existing incumbent callbacks, native logs and final JSON remain enabled in both modes. Native timer has millisecond resolution. Single-node development evidence.'})


if __name__ == '__main__':
    main()
