"""Prepare pinned candidate manifests after reserved-node builds and native gates."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

from checker_contract import checker_identity, sha256
from ms58_paired_probe import candidate_manifest
from probe_runner import atomic_json

SOURCES = {'best': '327e481713e1b9a3187fda2fc9f10f8d9d75b0c5',
           'scratch': 'e27f288d7b504c2e8e9f2b8d9086ba0b7da69f02',
           'safe-correction': 'a63cf65d3f7e90e4c310ffcedd7a6dabfae0ce3d'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--include', nargs='*', choices=['scratch', 'safe-correction'], default=[])
    args = parser.parse_args()
    if os.environ.get('OAR_JOB_ID') != args.job_id or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise ValueError('Manifest preparation requires the declared compute job')
    if any(key.startswith('KAYROS_') for key in os.environ):
        raise ValueError('Do not inherit candidate switches during preparation')
    root = Path.home() / 'ms58-bpca'
    run = root / 'dabia-20261110'
    validation = json.loads((run / 'validation/complete.json').read_bytes())
    if not validation.get('all_checks_passed') or validation.get('source_id') != SOURCES['best']:
        raise ValueError('Integrated Dabia validation has not passed')
    for variant in args.include:
        native = json.loads((run / f'native/{variant}/complete.json').read_bytes())
        if not native.get('all_checks_passed') or not SOURCES[variant].startswith(native['source_id']):
            raise ValueError(f'Native gate is not passed for {variant}')
    output = run / 'screen-artifacts'
    output.mkdir(exist_ok=False)
    checker = checker_identity()
    upstream_path = run / 'validation/manifests/minimal-lera.json'
    upstream = json.loads(upstream_path.read_bytes())
    if upstream['source_id'] != 'f2c9e20' or upstream['checker'] != checker:
        raise ValueError('Minimal reference identity mismatch')
    atomic_json(output / 'minimal-lera.json', upstream)
    plan = [{'name': 'minimal-lera', 'source_id': 'f2c9e20',
        'manifest_path': str(output / 'minimal-lera.json'),
        'worker_python': str(Path.home() / 'ms56-ablation/experiment/.venv/bin/python'),
        'worker_path': str(root / 'experiment/scripts/upstream_event_worker.py'),
        'diagnostic_environment': {}}]
    for variant in ['best', *args.include]:
        python = root / f'venv-{variant}-cplex/bin/python'
        backend = subprocess.check_output([str(python), '-c', 'from kayros import _lera; print(_lera.LP_BACKEND)'], text=True).strip()
        if backend != 'CPLEX':
            raise ValueError('Every screen candidate must use CPLEX')
        patches = output / f'{variant}-patches'
        patches.mkdir()
        manifest = candidate_manifest(python, root / f'patches-{variant}', patches, checker, SOURCES[variant])
        environment = {'KAYROS_DIAGNOSTIC_CORRECTING': '1'} if variant == 'safe-correction' else {}
        manifest['diagnostic_environment'] = environment
        manifest['purpose'] = 'Dabia-only bounded CPLEX candidate screen'
        manifest['build_log_sha256'] = sha256(run / f'build-logs/{variant}-cplex.log')
        atomic_json(output / f'{variant}.json', manifest)
        plan.append({'name': variant, 'source_id': SOURCES[variant],
            'manifest_path': str(output / f'{variant}.json'), 'worker_python': str(python),
            'diagnostic_environment': environment})
    shutil.copyfile(__file__, output / Path(__file__).name)
    atomic_json(output / 'variants.json', plan)
    print(json.dumps({'variants': [v['name'] for v in plan], 'plan': str(output / 'variants.json')}))


if __name__ == '__main__':
    main()
