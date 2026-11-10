"""Run focused synthetic native gates for Dabia-screen candidates."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from checker_contract import sha256
from ms58_correction_runs import physical_cores
from ms58_dabia_validate import run_command, stamp
from probe_runner import atomic_json

CORRECTION_ON_SOURCE = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
CORRECTION_ON_FILES = {
    'cpp/lera/bridge/lera_bridge.cpp': 'ef399159edc08fcc474f60f3a7127d0e167a1b37f2276d6c3fd8bb62632bbd6c',
    'cpp/lera/main/src/labeling/bidirectional_labeling.cpp': '32311a0d6fdcc19795d770aba3749fde2e920d4bdbb52181b0c330fc76ad221c',
    'cpp/lera/main/src/labeling/monodirectional_labeling.cpp': 'c80a424fe5b6165c8ddf9f52bb392fa351195ceda3478ed861bff61e4ec92b3b',
    'cpp/lera/main/include/labeling/bidirectional_labeling.h': '80a90e77599133968e7ae7ba76f61e433d0fdcb480d26490ec6e736c19567aca',
    'cpp/lera/main/include/labeling/monodirectional_labeling.h': '79b03f80ab1aaf535a18e650bf87e85de83845608cd2a201e258ac6b5b02060e',
    'tests/cpp/test_label_correction.cpp': 'e6b27272be1c78ada3ceb056212cb350be1e5b0d0cf4b5806874a88ff0d33d01',
    'tests/cpp/test_label_correction_pricing.cpp': '6484e52409f9350c308a8eeb80f3f339a6fc141731557f960e17e9caa725af49',
}


def correction_on_source_identity(source):
    for relative, expected in CORRECTION_ON_FILES.items():
        if sha256(source / relative) != expected:
            raise ValueError(f'Correction-on source or fixture differs from {CORRECTION_ON_SOURCE}: {relative}')
    return CORRECTION_ON_FILES.copy()


def correction_on_build_identity(source):
    cache = source / 'build/cplex/CMakeCache.txt'
    entries = {}
    for line in cache.read_text().splitlines():
        if '=' in line and ':' in line.split('=', 1)[0] and not line.startswith(('#', '//')):
            key, value = line.split('=', 1)
            entries[key.split(':', 1)[0]] = value
    if entries.get('LERA_LP_BACKEND') != 'cplex':
        raise ValueError('Native correction-on archive must have a CPLEX CMake build')
    if Path(entries.get('CMAKE_HOME_DIRECTORY', '')).resolve() != source.resolve():
        raise ValueError('Native correction-on archive belongs to another source directory')
    return {'cache_sha256': sha256(cache), 'source_directory': entries['CMAKE_HOME_DIRECTORY'],
            'backend': entries['LERA_LP_BACKEND'], 'compiler': entries.get('CMAKE_CXX_COMPILER'),
            'boost_include': entries.get('Boost_INCLUDE_DIR')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--variant', choices=['scratch', 'safe-correction', 'correction-on'], required=True)
    args = parser.parse_args()
    if os.environ.get('OAR_JOB_ID') != args.job_id or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise ValueError('Native gates require the declared reserved compute job')
    if any(k.startswith('KAYROS_') for k in os.environ):
        raise ValueError('Native gates require no inherited Kayros settings')
    root = Path.home() / 'ms58-bpca'
    source = root / 'sources' / args.variant
    output = root / 'dabia-20261110/native' / args.variant
    output.mkdir(parents=True, exist_ok=False)
    core = physical_cores()[10]
    library = source / 'build/cplex/cpp/lera/liblera.a'
    fixtures = (['test_dominance_scratch'] if args.variant == 'scratch'
                else ['test_label_correction', 'test_label_correction_pricing'])
    source_id = {'scratch': 'e27f288', 'safe-correction': 'a63cf65',
                 'correction-on': CORRECTION_ON_SOURCE}[args.variant]
    fixture_source_id = {'scratch': 'e27f288', 'safe-correction': '151a4ef',
                        'correction-on': CORRECTION_ON_SOURCE}[args.variant]
    source_identity = correction_on_source_identity(source) if args.variant == 'correction-on' else {}
    build_identity = correction_on_build_identity(source) if args.variant == 'correction-on' else {}
    library_sha256 = sha256(library)
    python = root / f'venv-{args.variant}-cplex/bin/python'
    backend = subprocess.check_output([str(python), '-c', 'from kayros import _lera; print(_lera.LP_BACKEND)'], text=True).strip()
    if backend != 'CPLEX':
        raise ValueError('Native library must belong to the CPLEX build')
    atomic_json(output / 'plan.json', {'variant': args.variant, 'source_id': source_id,
        'fixture_source_id': fixture_source_id, 'pinned_source_files': source_identity,
        'native_build_identity': build_identity,
        'oar_job_id': args.job_id, 'host': socket.gethostname(), 'cpu': core,
        'backend': backend, 'created_utc': stamp(), 'library_sha256': library_sha256,
        'fixtures': {name: sha256(source / 'tests/cpp' / f'{name}.cpp') for name in fixtures},
        'runner_sha256': sha256(Path(__file__))})
    rows = []
    for name in fixtures:
        if args.variant == 'correction-on':
            correction_on_source_identity(source)
            if sha256(library) != library_sha256:
                raise ValueError('Correction-on native archive changed after the plan was frozen')
        executable = output / name
        command = [build_identity.get('compiler') or 'c++', '-std=c++23', '-O3', '-DNDEBUG', '-ffp-contract=off',
            *['-I' + str(source / path) for path in
              ['cpp/lera/goc/include', 'cpp/lera/main/include', 'cpp/lera/nyr/include', 'cpp/lera/magic_enum/include']],
            *(['-I' + build_identity['boost_include']] if build_identity.get('boost_include') else []),
            str(source / 'tests/cpp' / f'{name}.cpp'), str(library), '-ldl', '-lm', '-o', str(executable)]
        compiled = run_command(output, 'compile-' + name, command, core, 180, source)
        rows.append(compiled)
        if compiled['passed']:
            rows.append(run_command(output, name, [str(executable)], core, 120, source))
    passed = len(rows) == 2 * len(fixtures) and all(row['passed'] for row in rows)
    atomic_json(output / 'complete.json', {'source_id': source_id, 'fixture_source_id': fixture_source_id,
        'results': rows,
        'all_checks_passed': passed, 'finished_utc': stamp(),
        'scope': 'Synthetic native invariants, not a benchmark speed or full pricing-completeness claim.'})
    return int(not passed)


if __name__ == '__main__':
    raise SystemExit(main())
