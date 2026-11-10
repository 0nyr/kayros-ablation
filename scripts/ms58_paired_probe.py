"""Run cold exact diagnostic pairs sequentially on one reserved CPU core.

Invoke with the pinned checker Python inside an owned OAR allocation. Required
arguments are --output, --reference-python, and --reference-manifest. Supply
--candidate-python, --candidate-source-id and --candidate-patch-dir for the
candidate comparison, and/or --upstream-manifest for the fixed Lera comparison.
Run one invocation per instance/core to parallelize independent lanes. Each
invocation freezes its own inputs, schedule, scripts, patches and manifests.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone

from checker_contract import checker_identity, sha256
from probe_runner import atomic_json


def paired_schedule(instances, comparators, repetitions, seed):
    """Alternate randomized AB/BA order within each instance/comparator pair."""
    rng = random.Random(seed)
    pairs = [(name, comparator) for name in instances for comparator in comparators]
    rng.shuffle(pairs)
    initial_reverse = {pair: bool(rng.getrandbits(1)) for pair in pairs}
    schedule = []
    for repetition in range(1, repetitions + 1):
        ordered = pairs.copy()
        rng.shuffle(ordered)
        for name, comparator in ordered:
            variants = ['reference', comparator]
            if initial_reverse[(name, comparator)] ^ bool((repetition - 1) % 2):
                variants.reverse()
            pair_id = f'{name}--reference-vs-{comparator}--rep{repetition}'
            for position, variant in enumerate(variants, start=1):
                schedule.append({'cell_id': f'{pair_id}--position{position}--{variant}',
                                 'pair_id': pair_id, 'instance': name,
                                 'comparison': comparator, 'repetition': repetition,
                                 'position': position, 'variant': variant})
    return schedule


def frozen_existing_manifest(source, patches, scripts, fallback_patches, identity):
    """Rebind archived auxiliary files while requiring their original bytes."""
    manifest = json.loads(source.read_bytes())
    if manifest.get('schema_version') != 1 or manifest.get('checker') != identity:
        raise ValueError(f'Invalid checker/schema in {source}')
    rebound = {}
    for filename, expected in manifest['source_patch_files'].items():
        original = Path(filename)
        if not original.is_file():
            original = fallback_patches / original.name
        if sha256(original) != expected:
            raise ValueError(f'Frozen source patch mismatch: {original}')
        target = patches / Path(filename).name
        if target.exists() and sha256(target) != expected:
            raise ValueError(f'Conflicting source patch basename: {target.name}')
        shutil.copyfile(original, target)
        rebound[str(target)] = expected
    if not rebound:
        raise ValueError('Existing manifest has no source patches')
    manifest['source_patch_files'] = rebound
    rebound = {}
    for filename, expected in manifest['runtime_files'].items():
        path = Path(filename)
        if path.name == 'upstream_event_worker.py':
            path = scripts / path.name
        if sha256(path) != expected:
            raise ValueError(f'Frozen runtime mismatch: {path}')
        rebound[str(path)] = expected
    manifest['runtime_files'] = rebound
    return manifest


def candidate_manifest(python, patch_dir, target, identity, source_id):
    query = ("import importlib.metadata as m,json; d=m.distribution('kayros'); "
             "print(json.dumps([str(d.locate_file(p).resolve()) for p in d.files "
             "if str(p).endswith(('.so','.py'))]))")
    installed = json.loads(subprocess.check_output([str(python), '-c', query], text=True))
    if not installed or not any(Path(name).name.startswith('_lera.') for name in installed):
        raise ValueError('Candidate environment has no installed exact extension')
    patches = sorted(patch_dir.glob('*.patch'))
    if not patches:
        raise ValueError('Candidate patch directory must include its complete patch inventory')
    for patch in patches:
        shutil.copyfile(patch, target / patch.name)
    return {'schema_version': 1, 'checker': identity, 'runtime_kind': 'kayros',
            'backend': 'cplex', 'source_id': source_id,
            'runtime_files': {name: sha256(Path(name)) for name in installed},
            'source_patch_files': {str(target / path.name): sha256(path) for path in patches}}


def parser():
    home = Path.home()
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument('--output', type=Path, required=True, help='New lane directory, must not exist')
    result.add_argument('--reference-python', type=Path, required=True, help='Corrected frozen Kayros CPLEX environment')
    result.add_argument('--reference-manifest', type=Path, required=True, help='Frozen ms56 or reconstructed reference manifest')
    result.add_argument('--reference-source-id', default='50eebc5 plus frozen patches 0001 through 0004')
    result.add_argument('--candidate-python', type=Path)
    result.add_argument('--candidate-source-id', help='Commit and/or source tree identity')
    result.add_argument('--candidate-patch-dir', type=Path, help='Complete candidate *.patch inventory, including baseline repairs')
    result.add_argument('--upstream-manifest', type=Path, help='Fixed upstream native runtime manifest')
    result.add_argument('--patch-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'patches',
                        help='Fallback for missing archived patch paths, verified against their original hashes')
    result.add_argument('--inputs', type=Path, default=home / 'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100')
    result.add_argument('--instances', nargs='+', default=['C104', 'R201', 'RC201', 'C101', 'R101', 'C201'])
    result.add_argument('--horizon', type=float, default=300.)
    result.add_argument('--memory-mb', type=int, default=16384)
    result.add_argument('--repetitions', type=int, default=1)
    result.add_argument('--seed', type=int, default=20261109, help='Pair-order randomization only, never a solver seed')
    result.add_argument('--cpu-core', type=int, help='An allowed OAR core, defaults to the first allowed core')
    result.add_argument('--allow-diagnostic-environment', action='store_true',
                        help='Explicitly retain inherited Kayros tracing/algorithm switches, recorded in the schedule')
    return result


def main():
    argument_parser = parser()
    args = argument_parser.parse_args()
    if not os.environ.get('OAR_JOB_ID') or socket.gethostname().startswith(('fnancy', 'flyon')):
        argument_parser.error('Execution requires an owned OAR compute-node allocation')
    if args.repetitions < 1 or args.memory_mb <= 0 or not 0 < args.horizon < float('inf'):
        argument_parser.error('Repetitions, memory and finite horizon must be positive')
    if len(set(args.instances)) != len(args.instances) or any('/' in name or '..' in name for name in args.instances):
        argument_parser.error('Instance names must be distinct simple names')
    candidate_options = [bool(args.candidate_python), bool(args.candidate_source_id), bool(args.candidate_patch_dir)]
    if any(candidate_options) and not all(candidate_options):
        argument_parser.error('Candidate requires --candidate-python, --candidate-source-id and --candidate-patch-dir together')
    comparators = (['candidate'] if args.candidate_python else []) + (['upstream'] if args.upstream_manifest else [])
    if not comparators:
        argument_parser.error('Supply a candidate and/or upstream runtime')
    diagnostic_environment = {key: value for key, value in os.environ.items()
                              if key.startswith('KAYROS_TRACE_') or key in
                              ['KAYROS_PRICING_TRACE', 'KAYROS_STEP_EXACT', 'KAYROS_LBL_SYMMETRIC']}
    if diagnostic_environment and not args.allow_diagnostic_environment:
        argument_parser.error('Inherited diagnostic/algorithm switches require --allow-diagnostic-environment: '
                              + ', '.join(sorted(diagnostic_environment)))
    allowed = os.sched_getaffinity(0)
    core = min(allowed) if args.cpu_core is None else args.cpu_core
    if core not in allowed:
        argument_parser.error('Requested CPU is outside the OAR affinity')
    available_kib = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
    if available_kib < (args.memory_mb + 16384) * 1024:
        argument_parser.error('Available RAM cannot cover this lane plus 16 GiB reserve')
    identity = checker_identity()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    scripts = output / 'frozen-scripts'
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['manifests', 'frozen-patches', 'rows', 'launch-logs']:
        (output / name).mkdir()
    manifests = {}
    for variant, manifest_path in [('reference', args.reference_manifest), ('upstream', args.upstream_manifest)]:
        if manifest_path is None:
            continue
        patches = output / 'frozen-patches' / variant
        patches.mkdir()
        manifest = frozen_existing_manifest(manifest_path, patches, scripts, args.patch_dir, identity)
        manifest['parent_manifest_sha256'] = sha256(manifest_path)
        manifest['source_id'] = args.reference_source_id if variant == 'reference' else 'fixed upstream cabc56a with audited ms56 patches'
        manifests[variant] = manifest
    if args.candidate_python:
        patches = output / 'frozen-patches/candidate'
        patches.mkdir()
        manifests['candidate'] = candidate_manifest(args.candidate_python, args.candidate_patch_dir, patches,
                                                    identity, args.candidate_source_id)
    for variant, manifest in manifests.items():
        manifest.update(purpose='ms58 cold-only paired diagnostics, no main-campaign claim',
                        variant=variant, oar_job_id=os.environ['OAR_JOB_ID'])
        atomic_json(output / 'manifests' / f'{variant}.json', manifest)
    inputs = {}
    for name in args.instances:
        path = (args.inputs / f'{name}.vrp.json').resolve()
        data = json.loads(path.read_bytes())
        if data['instance_name'] != name:
            raise ValueError(f'Instance identity mismatch: {path}')
        atf = (path.parent / data['td']['atf_path']).resolve()
        inputs[name] = {'path': str(path), 'sha256': sha256(path), 'atf_path': str(atf), 'atf_sha256': sha256(atf)}
    schedule = paired_schedule(args.instances, comparators, args.repetitions, args.seed)
    for cell in schedule:
        variant = cell['variant']
        manifest = {**manifests[variant], 'paired_diagnostic_cell': cell}
        path = output / 'manifests' / f"{cell['cell_id']}.json"
        atomic_json(path, manifest)
        cell['manifest_path'] = str(path)
    plan = {'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'oar_job_id': os.environ['OAR_JOB_ID'], 'host': socket.gethostname(),
            'cpu_core': core, 'allowed_cores_at_start': sorted(allowed),
            'memory_available_kib_at_start': available_kib, 'memory_mb': args.memory_mb,
            'horizon_s': args.horizon, 'repetitions': args.repetitions, 'randomization_seed': args.seed,
            'checker': identity, 'inputs': inputs, 'scheduled_count': len(schedule), 'scheduled_cells': schedule,
            'diagnostic_environment': diagnostic_environment,
            'driver_sha256': sha256(Path(__file__)),
            'scope': 'Each pair executes sequentially on the same core. Separate invocations may share a node.'}
    atomic_json(output / 'schedule.json', plan)
    if os.environ.get('OAR_NODEFILE') and Path(os.environ['OAR_NODEFILE']).is_file():
        shutil.copyfile(os.environ['OAR_NODEFILE'], output / 'allocated-nodefile.txt')
    os.sched_setaffinity(0, {core})
    rows = []
    for cell in schedule:
        variant = cell['variant']
        worker_python = (args.reference_python if variant == 'reference' else args.candidate_python) if variant != 'upstream' else Path(sys.executable)
        command = [sys.executable, str(scripts / 'run_solver_probe.py'),
                   '--manifest', cell['manifest_path'], '--worker-python', str(worker_python.absolute()),
                   '--instance', inputs[cell['instance']]['path'], '--output', str(output / 'cells'),
                   '--arm', 'lera-exact' if variant == 'upstream' else 'kayros-bpca-cplex',
                   '--horizon', str(args.horizon), '--memory-mb', str(args.memory_mb),
                   '--cpu-core', str(core), '--repetition', str(cell['repetition'])]
        if variant == 'upstream':
            command += ['--worker', str(scripts / 'upstream_event_worker.py')]
        row = {**cell, 'command': command, 'started_utc': datetime.now(timezone.utc).isoformat()}
        stdout = output / 'launch-logs' / f"{cell['cell_id']}.json"
        stderr = output / 'launch-logs' / f"{cell['cell_id']}.stderr.log"
        try:
            frozen_input = inputs[cell['instance']]
            if (sha256(Path(frozen_input['path'])) != frozen_input['sha256']
                    or sha256(Path(frozen_input['atf_path'])) != frozen_input['atf_sha256']):
                raise ValueError('Input bytes changed after schedule freeze')
            with stdout.open('x') as out, stderr.open('x') as err:
                process = subprocess.run(command, stdout=out, stderr=err)
            if process.returncode:
                raise RuntimeError(f'Launcher exit {process.returncode}; inspect {stderr}')
            result = json.loads(stdout.read_bytes())
            row.update(run_id=result['run_id'], status=result['status'], returncode=result['returncode'],
                       result_path=str(output / 'cells' / result['run_id'] / 'result.json'),
                       checked_cost=result['validation'].get('best_checked_cost'))
        except Exception as error:
            row.update(status='launch_failure', reason=f'{type(error).__name__}: {error}')
        row['finished_utc'] = datetime.now(timezone.utc).isoformat()
        atomic_json(output / 'rows' / f"{cell['cell_id']}.json", row)
        rows.append(row)
        print(json.dumps({key: row.get(key) for key in ['cell_id', 'status', 'checked_cost', 'reason']}), flush=True)
    atomic_json(output / 'complete.json', {'scheduled_count': len(schedule), 'row_count': len(rows),
                'launch_failures': sum(row['status'] == 'launch_failure' for row in rows),
                'finished_utc': datetime.now(timezone.utc).isoformat()})
    if any(row['status'] == 'launch_failure' for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
