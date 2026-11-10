"""Freeze the three runtime manifests after native gates. Never starts solver cells."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from checker_contract import checker_identity, sha256
from ms58_dabia_screen import LERA, immutable_json
from ms58_dabia_wave import checked_file, source_matches
from ms58_dominance_profile_native import BASE, PROFILE, allocation, validate_gate
from ms58_paired_probe import candidate_manifest


def validate_patch_stack(manifest, pins, *, profile):
    expected = set(pins['baseline_patch_sha256']) | {pins['selected_full_patch_sha256']}
    if profile:
        expected.add(pins['incremental_patch_sha256'])
    if set(manifest['source_patch_files'].values()) != expected or len(manifest['source_patch_files']) != len(expected):
        raise ValueError('Require exactly baseline4 + selected9d full + optional a2dc incremental, each once')


def installed(python):
    query = ('import hashlib,json;from pathlib import Path;from kayros import _core,_lera;'
             'print(json.dumps({"backend":_lera.LP_BACKEND,"core":str(Path(_core.__file__).resolve()),'
             '"lera":str(Path(_lera.__file__).resolve())}))')
    return json.loads(subprocess.check_output([str(python), '-c', query], text=True, timeout=30))


def prepare(args):
    allocated = allocation(args.job_id, args.cores)
    checker = checker_identity()
    gate = validate_gate(args.native_gate, args.profile_source, args.control_source, args.source_pins)
    pins = json.loads(args.source_pins.read_bytes())
    runtime_pins = json.loads(args.runtime_pins.read_bytes())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name, source in [('source-pins.json', args.source_pins), ('runtime-pins.json', args.runtime_pins),
                         ('native-gate.json', args.native_gate)]:
        shutil.copyfile(source, output / name)
    variants = []
    for name, source, python, manifest_path in [
            ('correction-on', BASE, args.control_python, args.control_manifest),
            ('dominance-profile', PROFILE, args.profile_python, None),
            ('lera-minimal', LERA, args.lera_python, args.lera_manifest)]:
        patches = output / (name + '-patches')
        patches.mkdir()
        if manifest_path:
            manifest = json.loads(manifest_path.read_bytes())
            if manifest.get('checker') != checker or not source_matches(manifest.get('source_id'), source):
                raise ValueError('Control manifest checker/source differs from the declared pin')
            for filename, expected in {**manifest['runtime_files'], **manifest['source_patch_files']}.items():
                checked_file(filename, expected)
            rebound = {}
            for index, (filename, expected) in enumerate(manifest['source_patch_files'].items()):
                target = patches / f'{index:02d}-{Path(filename).name}'
                shutil.copyfile(filename, target)
                rebound[str(target)] = expected
            manifest['source_patch_files'] = rebound
            manifest['parent_manifest_sha256'] = sha256(manifest_path)
        else:
            manifest = candidate_manifest(python, args.profile_patches, patches, checker, PROFILE)
        if name != 'lera-minimal':
            validate_patch_stack(manifest, pins, profile=name == 'dominance-profile')
            observed = installed(python)
            expected = runtime_pins[name]
            if expected.get('source_id') != source or observed['backend'] != 'CPLEX':
                raise ValueError('Runtime source declaration or CPLEX backend differs')
            for kind in ('core', 'lera'):
                path = Path(observed[kind])
                checked_file(path, expected[kind + '_sha256'])
                if manifest['runtime_files'].get(str(path)) != expected[kind + '_sha256']:
                    raise ValueError('Installed runtime differs from supplied G5K runtime pins')
            compiled = b'kayros-ms58-dominance-profile-v1' in Path(observed['lera']).read_bytes()
            if compiled != (name == 'dominance-profile'):
                raise ValueError('Diagnostic binary marker differs from declared source mode')
            manifest['expected_algorithm_settings'] = {'label_correction': True, 'lazy_extension': True}
        manifest.update(diagnostic_environment={}, native_gate_sha256=sha256(args.native_gate),
                        purpose='Selected9d dominance mechanism diagnostic, no throughput claim')
        target = output / f'{name}.json'
        immutable_json(target, manifest)
        variant = {'name': name, 'source_id': source, 'manifest_path': str(target),
                   'worker_python': str(python.absolute()), 'diagnostic_environment': {}}
        if name == 'lera-minimal':
            variant['worker_path'] = str(args.lera_worker.resolve())
        variants.append(variant)
    immutable_json(output / 'plan.json', variants)
    immutable_json(output / 'complete.json', {'source_id': PROFILE, 'base_source_id': BASE,
        'native_gate_sha256': sha256(args.native_gate), 'native_identity': gate['identity'],
        'allocation': allocated, 'checker': checker, 'solver_cells_started': 0,
        'files': {p.relative_to(output).as_posix(): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}})
    print(json.dumps({'plan': str(output / 'plan.json'), 'solver_cells_started': 0}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'native-gate', 'source-pins', 'runtime-pins', 'profile-source', 'control-source',
                 'control-python', 'control-manifest', 'profile-python', 'profile-patches',
                 'lera-python', 'lera-manifest', 'lera-worker'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--cores', type=int, nargs=2, required=True)
    return prepare(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
