"""Create an isolated three-arm extension diagnostic plan after the profile build.

This imports pinned best/screen-artifacts and minimal-Lera manifests, inventories the fresh profile install and full patch stack, and writes no solver results. The run helper executes both native gates before any pilot. Invoke with the pinned checker Python and --job-id inside the declared allocation.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

from checker_contract import checker_identity, sha256
from ms58_dabia_screen import BEST, LERA, immutable_json
from ms58_dabia_wave import checked_file, no_overrides, source_matches
from ms58_extension_profile_run import PROFILE, validate_plan, validate_profile_patch_stack
from ms58_paired_probe import candidate_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--root', type=Path, default=Path.home() / 'ms58-bpca')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--best-manifest', type=Path)
    parser.add_argument('--lera-manifest', type=Path)
    args = parser.parse_args()
    no_overrides(os.environ)
    if os.environ.get('OAR_JOB_ID') != args.job_id or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise ValueError('Artifact preparation requires the declared compute job')
    root = args.root.resolve()
    run = root / 'dabia-20261110'
    output = (args.output or run / 'extension-profile-artifacts').resolve()
    output.mkdir(parents=True, exist_ok=False)
    checker = checker_identity()
    paths = {'best': args.best_manifest or run / 'screen-artifacts/best.json',
             'lera-minimal': args.lera_manifest or run / 'validation/manifests/minimal-lera.json'}
    plan = []
    for name, path in paths.items():
        path = path.resolve()
        manifest = json.loads(path.read_bytes())
        source = BEST if name == 'best' else LERA
        if manifest.get('checker') != checker or not source_matches(manifest.get('source_id'), source):
            raise ValueError('Existing control checker/source differs from its pin')
        for filename, expected in {**manifest['runtime_files'], **manifest['source_patch_files']}.items():
            checked_file(filename, expected)
        manifest['parent_manifest_sha256'] = sha256(path)
        manifest['diagnostic_environment'] = {}
        target = output / f'{name}.json'
        immutable_json(target, manifest)
        variant = {'name': name, 'source_id': source, 'manifest_path': str(target),
            'worker_python': str(root / 'venv-best-cplex/bin/python') if name == 'best'
                else str(Path.home() / 'ms56-ablation/experiment/.venv/bin/python'),
            'diagnostic_environment': {}}
        if name == 'lera-minimal':
            variant['worker_path'] = str(root / 'experiment/scripts/upstream_event_worker.py')
        plan.append(variant)
    python = root / 'venv-extension-profile-cplex/bin/python'
    backend = subprocess.check_output([str(python), '-c',
        'from kayros import _lera; print(_lera.LP_BACKEND)'], text=True, timeout=30).strip()
    if backend != 'CPLEX':
        raise ValueError('Installed extension profile must use CPLEX')
    patches = output / 'profile-patches'
    patches.mkdir()
    manifest = candidate_manifest(python, root / 'patches-extension-profile', patches, checker, PROFILE)
    validate_profile_patch_stack(manifest)
    manifest.update(diagnostic_environment={}, purpose='Sampled extension diagnostics, no throughput claim',
        build_log_sha256=sha256(run / 'build-logs/extension-profile-cplex.log'))
    target = output / 'extension-profile.json'
    immutable_json(target, manifest)
    plan.append({'name': 'extension-profile', 'source_id': PROFILE, 'manifest_path': str(target),
        'worker_python': str(python), 'diagnostic_environment': {}})
    validate_plan(plan)
    immutable_json(output / 'variants.json', plan)
    shutil.copyfile(__file__, output / Path(__file__).name)
    immutable_json(output / 'identity.json', {'checker': checker, 'oar_job_id': args.job_id,
        'host': socket.gethostname(), 'script_sha256': sha256(Path(__file__)),
        'plan_sha256': sha256(output / 'variants.json'),
        'manifests': {p.name: sha256(p) for p in output.glob('*.json') if p.name != 'identity.json'}})
    print(json.dumps({'plan': str(output / 'variants.json'), 'variants': [v['name'] for v in plan],
                      'solver_cells_started': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
