"""Prepare the five-arm design after correction-on validation passes.

This reads installed runtimes and stored gate artifacts. It never runs a
solver, reserves a node, chooses slots or launches a wave. Run with the pinned
checker Python inside the declared compute allocation after both gates pass.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_dabia_native import CORRECTION_ON_FILES
from ms58_paired_probe import candidate_manifest, frozen_existing_manifest
from probe_runner import atomic_json

SOURCE = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
PATCH = '18b453c73422771ce0a2c6c5cbd6b934b62bcded0566c9c495d253106df28e87'
REFERENCE = 'ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'
LERA_BINARY = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
LERA_PATCH = 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf'
CORE_BINARY = 'b527271e7224fbd1b14b6ed84de49098395e672172ab038a2cc45258572287f3'
EXACT_BINARIES = {'cplex': '54b2f60bf61268a37c09dd49d0a60d3f71232948804a9910f188a87d12347b98',
                 'highs': 'd6a40dd16e1668bb745b6473ac48233c180b4216f37553ee7eed2a6562ae3b3c'}
SETTINGS = {'label_correction': True, 'lazy_extension': True}
INSTANCES = ['C101', 'C104', 'C201', 'C202', 'R101', 'R112', 'R201', 'R202',
             'RC104', 'RC105', 'RC201', 'RC202']


def read(path):
    return json.loads(path.read_bytes())


def runtime(python):
    query = ("import hashlib,json;from pathlib import Path;from kayros import _core,_lera;"
        "print(json.dumps({'backend':_lera.LP_BACKEND,"
        "'core_sha256':hashlib.sha256(Path(_core.__file__).read_bytes()).hexdigest(),"
        "'lera_sha256':hashlib.sha256(Path(_lera.__file__).read_bytes()).hexdigest()}))")
    return json.loads(subprocess.check_output([str(python), '-c', query], text=True, timeout=30))


def require_reports(root, validation_path, native_path):
    validation, plan = read(validation_path), read(validation_path.with_name('plan.json'))
    native, native_plan = read(native_path), read(native_path.with_name('plan.json'))
    if (validation.get('source_id') != SOURCE or validation.get('variant') != 'correction-on'
            or validation.get('all_checks_passed') is not True or validation.get('native_checks_passed') is not True
            or validation.get('expected_algorithm_settings') != SETTINGS
            or plan.get('source_id') != SOURCE or plan.get('expected_algorithm_settings') != SETTINGS
            or plan.get('core_binary_matches_reference') is not True):
        raise ValueError('The correction-on coupled validation gate has not passed with the intended defaults')
    cases = validation.get('cases', [])
    if len(cases) != 4 or {row['case']['key'] for row in cases} != {'n25-C201', 'n50-R201', 'n100-C101', 'n100-RC201'}:
        raise ValueError('Validation case coverage differs from the declared Dabia gate')
    for row in cases:
        if row.get('all_checks_passed') is not True or not row.get('checks') or not all(v is True for v in row['checks'].values()):
            raise ValueError('A declared validation case did not pass')
        if row.get('feasible_consistency_audit', {}).get('all_claims_usable') is not True:
            raise ValueError('The shared cross-arm feasible-witness validation gate is absent or failed')
    helper_hash = sha256(Path(__file__).with_name('ms58_proof_consistency.py'))
    if validation.get('proof_consistency_helper_sha256') != helper_hash:
        raise ValueError('Validation did not use the current reviewed shared consistency helper')
    if (native.get('source_id') != SOURCE or native.get('fixture_source_id') != SOURCE
            or native.get('all_checks_passed') is not True or native_plan.get('source_id') != SOURCE
            or native_plan.get('fixture_source_id') != SOURCE or native_plan.get('backend') != 'CPLEX'
            or native_plan.get('pinned_source_files') != CORRECTION_ON_FILES):
        raise ValueError('The correction-on native gate has not passed for the pinned source and fixtures')
    required = {'compile-test_label_correction', 'test_label_correction',
                'compile-test_label_correction_pricing', 'test_label_correction_pricing'}
    if (len(native.get('results', [])) != 4 or {row['name'] for row in native['results']} != required
            or any(row.get('passed') is not True for row in native['results'])):
        raise ValueError('Native gate does not contain all 4 successful compile/run outcomes')
    source = root / 'sources/correction-on'
    for relative, expected in CORRECTION_ON_FILES.items():
        if sha256(source / relative) != expected:
            raise ValueError('Correction-on source changed after native validation')
    library_hash = sha256(source / 'build/cplex/cpp/lera/liblera.a')
    if native_plan['library_sha256'] != library_hash or plan['native_library_sha256'] != library_hash:
        raise ValueError('Gate native archive differs from the final CPLEX build')
    for name, expected in plan.get('source_files', {}).items():
        if sha256(Path(name)) != expected:
            raise ValueError('Source file changed after coupled validation')
    return validation, plan, native_plan


def prepare(args):
    if os.environ.get('OAR_JOB_ID') != args.job_id or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise ValueError('Artifact preparation requires the declared reserved compute node')
    if any(key.startswith(('KAYROS_', 'UPSTREAM_', 'PROBE_')) for key in os.environ):
        raise ValueError('Artifact preparation must not inherit solver overrides')
    root = args.root.resolve()
    checker = checker_identity()
    validation_path = args.validation or root / 'dabia-20261110/validation-correction-on/complete.json'
    native_path = args.native or root / 'dabia-20261110/native/correction-on/complete.json'
    validation, validation_plan, native_plan = require_reports(root, validation_path, native_path)
    if validation_plan['checker'] != checker:
        raise ValueError('Validation checker differs from the final preparation checker')
    reference_path = args.references.resolve()
    if sha256(reference_path) != REFERENCE:
        raise ValueError('Final design requires the original frozen n100 reference snapshot')
    references = read(reference_path)
    if references['checker'] != checker:
        raise ValueError('Reference checker differs from the final preparation checker')
    selection = read(args.pilot_selection)
    if (selection['reference_manifest_sha256'] != REFERENCE or len(selection['selected_instances']) != 12
            or sorted(row['instance'] for row in selection['selected_instances']) != INSTANCES):
        raise ValueError('Final cohort differs from the preserved 12-instance n100 development selection')
    reference_by_name = {row['instance_name']: row for row in references['instances']}
    for row in selection['selected_instances']:
        if row['instance_sha256'] != reference_by_name[row['instance']]['instance_sha256']:
            raise ValueError('Selected input bytes differ from the frozen reference')
    patches = root / 'patches-correction-on'
    patch_hashes = {p.name: sha256(p) for p in patches.glob('*.patch')}
    baseline = {p.name: sha256(p) for p in (root / 'patches-reference').glob('*.patch')}
    if (len(patch_hashes) != 5 or len(baseline) != 4 or PATCH not in patch_hashes.values()
            or any(patch_hashes.get(name) != value for name, value in baseline.items())):
        raise ValueError('Final patch inventory must contain the exact full repair and all 4 baseline patches')
    pythons = {backend: root / f'venv-correction-on-{backend}/bin/python' for backend in ['cplex', 'highs']}
    runtimes = {backend: runtime(python) for backend, python in pythons.items()}
    for backend, identity in runtimes.items():
        if (identity['backend'].lower() != backend or identity['core_sha256'] != CORE_BINARY
                or identity['lera_sha256'] != EXACT_BINARIES[backend]):
            raise ValueError('Final backend or binary identity differs from the declared builds')
    if (validation_plan['candidate_runtime'] != runtimes['cplex']
            or validation_plan['reference_runtime']['core_sha256'] != CORE_BINARY):
        raise ValueError('Final CPLEX runtime or unchanged ILS binary differs from validated identity')
    validated_manifest = read(validation_path.parent / 'manifests/candidate.json')
    for filename, expected in validated_manifest['runtime_files'].items():
        if sha256(Path(filename)) != expected:
            raise ValueError('Validated CPLEX runtime changed after the gate')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest_specs = {}
    for backend, python in pythons.items():
        target = output / f'{backend}-patches'
        target.mkdir()
        manifest = candidate_manifest(python, patches, target, checker, SOURCE)
        manifest.update(backend=backend, diagnostic_environment={}, expected_algorithm_settings=SETTINGS,
            ils_core_sha256=CORE_BINARY, declared_source_commit=SOURCE,
            purpose='Five-arm Dabia n100 development panel, 3600 s per cell')
        path = output / f'correction-on-{backend}.json'
        atomic_json(path, manifest)
        manifest_specs[backend] = {'path': str(path), 'sha256': sha256(path), 'worker_python': str(python)}
    target = output / 'upstream-patches'
    target.mkdir()
    upstream = frozen_existing_manifest(validation_path.parent / 'manifests/minimal-lera.json', target,
        validation_path.parent / 'frozen-scripts', root / 'experiment/patches', checker)
    binary = Path(upstream['worker_environment']['UPSTREAM_PROBE_BINARY'])
    if (upstream['source_id'] != 'f2c9e20' or sha256(binary) != LERA_BINARY
            or upstream['runtime_files'].get(str(binary)) != LERA_BINARY
            or LERA_PATCH not in upstream['source_patch_files'].values()):
        raise ValueError('Final Lera reference differs from the minimal initialized control')
    upstream.update(backend='cplex', diagnostic_environment={})
    path = output / 'minimal-lera.json'
    atomic_json(path, upstream)
    manifest_specs['upstream'] = {'path': str(path), 'sha256': sha256(path),
        'worker_python': str(args.lera_worker_python.absolute())}
    design = {'schema_version': 1, 'dataset': 'Dabia2013', 'purpose': 'performance',
        'kayros_source_id': SOURCE, 'repetitions': 3, 'inputs_root': str(args.inputs_root.resolve()),
        'checker_python': sys.executable, 'references': {'100': {'path': str(reference_path), 'sha256': REFERENCE}},
        'selection': [{'num_customers': 100, 'instance_name': name} for name in INSTANCES],
        'manifests': manifest_specs}
    atomic_json(output / 'design.json', design)
    gate_files = [validation_path, validation_path.with_name('plan.json'), native_path,
                  native_path.with_name('plan.json'), reference_path, args.pilot_selection]
    atomic_json(output / 'artifact-gates.json', {'source_id': SOURCE, 'source_patch_sha256': PATCH,
        'expected_algorithm_settings': SETTINGS, 'runtimes': runtimes, 'ils_binary_unchanged': True,
        'gate_files': {str(p): sha256(p) for p in gate_files}, 'design_sha256': sha256(output / 'design.json'),
        'helper_sha256': sha256(Path(__file__)), 'launched': False, 'slots_assigned': False,
        'scheduled_cell_count': 180, 'qualification': 'Development candidate. Passing gates does not certify proof completeness or broad performance.'})
    shutil.copyfile(__file__, output / Path(__file__).name)
    print(json.dumps({'design': str(output / 'design.json'), 'scheduled_cells': 180,
                      'launched': False, 'slots_assigned': False}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--root', type=Path, default=Path.home() / 'ms58-bpca')
    parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--pilot-selection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validation', type=Path)
    parser.add_argument('--native', type=Path)
    parser.add_argument('--inputs-root', type=Path, default=Path.home() / 'ms56-ablation/inputs')
    parser.add_argument('--lera-worker-python', type=Path, default=Path.home() / 'ms56-ablation/experiment/.venv/bin/python')
    prepare(parser.parse_args())
