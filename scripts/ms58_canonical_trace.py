"""Observe first-branch reductions separately from canonical timing runs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_compare_pricing_traces import read_trace
from ms58_paired_probe import frozen_existing_manifest
from probe_runner import atomic_json


def physical_core(index):
    cores, seen = [], set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = tuple((topology / name).read_text().strip()
                    for name in ['physical_package_id', 'core_id'])
        if key not in seen:
            seen.add(key)
            cores.append(cpu)
    return cores[index]


def reduction_difference(record):
    canonical = record['canonical_candidates']
    legacy = record['legacy_order_candidates']
    key = lambda arc: (arc['tail'], arc['head'])
    a, b = {key(arc): arc['fraction'] for arc in canonical}, {key(arc): arc['fraction'] for arc in legacy}
    return {'same_rank_order': [key(arc) for arc in canonical] == [key(arc) for arc in legacy],
            'same_candidate_set': a.keys() == b.keys(),
            'common_candidate_count': len(a.keys() & b.keys()),
            'common_fraction_differences': [
                {'tail': tail, 'head': head, 'canonical': a[(tail, head)], 'legacy': b[(tail, head)],
                 'difference': a[(tail, head)] - b[(tail, head)]}
                for tail, head in sorted(a.keys() & b.keys()) if a[(tail, head)] != b[(tail, head)]]}


def main():
    assert os.environ.get('OAR_JOB_ID') == '7013766'
    assert not socket.gethostname().startswith(('fnancy', 'flyon'))
    root = Path.home() / 'ms58-bpca'
    prior = root / 'resume-20261110/canonical-comparison/C201'
    out = root / 'resume-20261110/canonical-trace'
    out.mkdir(parents=True, exist_ok=False)
    scripts = out / 'frozen-scripts'
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns('__pycache__'))
    core = physical_core(10)
    checker = checker_identity()
    instance = Path.home() / 'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100/C201.vrp.json'
    payload = json.loads(instance.read_bytes())
    atf = (instance.parent / payload['td']['atf_path']).resolve()
    tasks = []
    for variant, environment, commit in [('reference', 'stable-branch', '37e4801'),
                                          ('candidate', 'canonical-normalize', '0bec4d4')]:
        patch_dir = out / f'{variant}-patches'
        patch_dir.mkdir()
        manifest = frozen_existing_manifest(prior / 'manifests' / f'{variant}.json',
            patch_dir, scripts, root / 'experiment/patches', checker)
        assert manifest['source_id'] == commit
        trace = out / f'{variant}-first-branch.jsonl'
        manifest.update(purpose='First-branch allocation-order diagnostic, no timing claim',
                        diagnostic_environment={'KAYROS_FIRST_BRANCH_TRACE': str(trace)})
        atomic_json(out / f'{variant}-manifest.json', manifest)
        command = [sys.executable, str(scripts / 'run_solver_probe.py'),
            '--manifest', str(out / f'{variant}-manifest.json'),
            '--worker-python', str(root / f'venv-{environment}/bin/python'),
            '--instance', str(instance), '--output', str(out / 'cells'),
            '--arm', 'kayros-bpca-cplex', '--horizon', '30', '--memory-mb', '16384',
            '--cpu-core', str(core), '--repetition', '1']
        tasks.append({'variant': variant, 'source_id': commit, 'trace': str(trace), 'command': command})
    atomic_json(out / 'schedule.json', {'scheduled_count': 2, 'tasks': tasks,
        'host': socket.gethostname(), 'oar_job_id': os.environ['OAR_JOB_ID'], 'cpu': core,
        'checker': checker, 'horizon_s': 30, 'memory_mb': 16384,
        'input_sha256': sha256(instance), 'atf_sha256': sha256(atf),
        'driver_sha256': sha256(Path(__file__)),
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'interpretation': 'Sequential first-branch traces. No runtime comparison or proof claim.'})
    rows = []
    for task in tasks:
        environment = {key: value for key, value in os.environ.items() if not key.startswith('KAYROS_')}
        environment['KAYROS_FIRST_BRANCH_TRACE'] = task['trace']
        with (out / f"{task['variant']}-result.json").open('x') as stream, \
                (out / f"{task['variant']}.stderr").open('x') as err:
            process = subprocess.run(task['command'], stdout=stream, stderr=err, env=environment)
        trace = Path(task['trace'])
        row = {**task, 'launcher_returncode': process.returncode, 'trace_complete': False,
               'timing_claim': False, 'solver_proof_claim': False}
        if trace.is_file():
            row['trace_sha256'] = sha256(trace)
            records, trace_identity = read_trace(trace)
            row['partial_final_line'] = trace_identity['partial_final_line']
            begin = next((r for r in records if r.get('event') == 'first_branch_begin'), None)
            selected = next((r for r in records if r.get('event') == 'first_branch_selected'), None)
            row['trace_complete'] = begin is not None and selected is not None
            if begin:
                row.update(node=begin['node'], bound=begin['bound'],
                           observed_reduction_difference=reduction_difference(begin))
            if selected:
                row['selected_branch'] = selected
        rows.append(row)
        atomic_json(out / 'rows.json', rows)
    atomic_json(out / 'complete.json', {'scheduled_count': 2, 'rows': rows})


if __name__ == '__main__':
    main()
