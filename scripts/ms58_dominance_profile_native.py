"""Run only synthetic native profiler gates on declared reserved compute."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket

from checker_contract import sha256
from ms58_dabia_screen import immutable_json, now
from ms58_dabia_validate import run_command
from ms58_dabia_wave import checked_file, no_overrides, topology_snapshot

PROFILE = 'a2dcf8870c17e7edeccb472b27bee0e544eec997'
BASE = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
PWL_RESULT = 'PASS native PWL bitwise cases=5040 digest=dcbfcebb1acf42ef'


def allocation(job_id, cores, memory_gib=48):
    no_overrides(os.environ)
    host = socket.gethostname().split('.')[0]
    nodefile = Path(os.environ.get('OAR_NODEFILE', '/nonexistent'))
    if (os.environ.get('OAR_JOB_ID') != job_id or not host.startswith('gros-') or not nodefile.is_file()
            or host not in {name.split('.')[0] for name in nodefile.read_text().split()}):
        raise ValueError('A declared real Gros compute allocation is required')
    if len(cores) != 2:
        raise ValueError('Exactly two distinct physical lanes are required')
    topology = topology_snapshot(cores)
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if available < memory_gib * 1024 ** 2:
        raise ValueError('Need 48 GiB available for two 16 GiB lanes and reserve')
    return {'host': socket.gethostname(), 'oar_job_id': job_id, 'physical_slots': topology,
            'allowed_cores': sorted(os.sched_getaffinity(0)), 'nodefile': nodefile.read_text(),
            'memory_available_kib': available, 'created_utc': now()}


def cache_entries(cache):
    return {line.split('=', 1)[0].split(':', 1)[0]: line.split('=', 1)[1]
            for line in cache.read_text().splitlines() if '=' in line and ':' in line.split('=', 1)[0]
            and not line.startswith(('#', '//'))}


def source_build(source, files, enabled):
    source = source.resolve()
    for relative, expected in files.items():
        checked_file(source / relative, expected)
    observed_cpp = {p.relative_to(source).as_posix() for p in (source / 'cpp').rglob('*') if p.is_file()}
    if observed_cpp != {name for name in files if name.startswith('cpp/')} :
        raise ValueError('Native source tree has an unexpected C++ file population')
    cache = source / 'build/cplex/CMakeCache.txt'
    entries = cache_entries(cache)
    if (entries.get('LERA_LP_BACKEND') != 'cplex'
            or (entries.get('KAYROS_DOMINANCE_PROFILING') == 'ON') != enabled
            or entries.get('KAYROS_EXTENSION_PROFILING', 'OFF') == 'ON'
            or Path(entries['CMAKE_HOME_DIRECTORY']).resolve() != source):
        raise ValueError('Native build source/backend/diagnostic mode differs from the pinned arm')
    library = source / 'build/cplex/cpp/lera/liblera.a'
    compiler = Path(entries['CMAKE_CXX_COMPILER']).resolve()
    return {'source': str(source), 'profiling': enabled, 'entries': entries,
            'files': {str(p): sha256(p) for p in (cache, compiler, library)},
            'source_files': files, 'library': str(library), 'compiler': str(compiler)}


def identities(source, control_source, pins_path):
    pins = json.loads(pins_path.read_bytes())
    if pins['source_id'] != PROFILE or pins['base_source_id'] != BASE:
        raise ValueError('Source pins differ from a2dc/selected9d')
    return {'profile': source_build(source, pins['files'], True),
            'control': source_build(control_source, pins['base_files'], False),
            'pins_sha256': sha256(pins_path)}


def validate_gate(path, source, control_source, pins_path):
    gate = json.loads(path.read_bytes())
    if (gate.get('source_id') != PROFILE or gate.get('base_source_id') != BASE
            or gate.get('all_checks_passed') is not True or len(gate.get('results', [])) != 8
            or not all(row.get('passed') is True for row in gate['results'])
            or gate.get('pwl_result') != PWL_RESULT):
        raise ValueError('All eight compile/run accounting, ON/OFF PWL and labeling gates must pass')
    if gate['identity'] != identities(source, control_source, pins_path):
        raise ValueError('Source/build identity changed after the native gate')
    for relative, expected in gate['artifacts'].items():
        checked_file(path.parent / relative, expected)
    return gate


def run(args):
    allocated = allocation(args.job_id, args.cores)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    immutable_json(output / 'allocation.json', allocated)
    try:
        identity = identities(args.profile_source, args.control_source, args.source_pins)
        immutable_json(output / 'identity.json', identity)
        shutil.copyfile(args.source_pins, output / 'source-pins.json')
        rows = []
        fixtures = [('test_dominance_profile', True, False), ('test_dominance_profile_pwl_on', True, True),
                    ('test_dominance_profile_pwl_off', False, True), ('test_dominance_profile_labeling', True, True)]
        for name, enabled, linked in fixtures:
            arm = identity['profile' if enabled else 'control']
            for filename, expected in arm['files'].items():
                checked_file(filename, expected)
            source = Path(identity['profile']['source'])
            fixture = name.replace('_pwl_on', '_pwl').replace('_pwl_off', '_pwl')
            executable = output / name
            entries = arm['entries']
            command = [arm['compiler'], '-std=c++23', '-O2', '-DNDEBUG', '-ffp-contract=off',
                       *(['-DKAYROS_DOMINANCE_PROFILING=1'] if enabled else []),
                       *['-I' + str(source / relative) for relative in ('cpp/lera/main/include',
                           'cpp/lera/goc/include', 'cpp/lera/nyr/include', 'cpp/lera/magic_enum/include')],
                       *(['-I' + entries['Boost_INCLUDE_DIR']] if entries.get('Boost_INCLUDE_DIR') else []),
                       str(source / 'tests/cpp' / f'{fixture}.cpp')]
            if linked:
                command += [arm['library']]
                if name.endswith('_labeling'):
                    graph = entries.get('Boost_GRAPH_LIBRARY_RELEASE')
                    command += [graph] if graph and Path(graph).is_file() else ['-lboost_graph']
                command += ['-ldl', '-lm']
            command += ['-o', str(executable)]
            compiled = run_command(output, 'compile-' + name, command, args.cores[0], 240, source)
            rows.append(compiled)
            if compiled['passed']:
                result = run_command(output, name, [str(executable)], args.cores[0], 90, source,
                                     expected_text=PWL_RESULT if '_pwl_' in name else 'PASS')
                rows.append(result)
                if '_pwl_' in name and result['passed']:
                    # Require the exact complete native PWL output, including its digest.
                    result['passed'] = (output / f'{name}.stdout').read_text().strip() == PWL_RESULT
        passed = len(rows) == 8 and all(row['passed'] for row in rows)
        result = {'source_id': PROFILE, 'base_source_id': BASE, 'all_checks_passed': passed,
                  'identity': identity, 'results': rows, 'pwl_result': PWL_RESULT,
                  'solver_cells_started': 0, 'finished_utc': now(),
                  'artifacts': {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()}}
        immutable_json(output / 'complete.json', result)
        print(json.dumps({'all_checks_passed': passed, 'solver_cells_started': 0, 'gate': str(output / 'complete.json')}))
        return int(not passed)
    except Exception as error:
        immutable_json(output / 'failure.json', {'error': f'{type(error).__name__}: {error}',
                       'solver_cells_started': 0, 'finished_utc': now()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-source', type=Path, required=True)
    parser.add_argument('--control-source', type=Path, required=True)
    parser.add_argument('--source-pins', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--cores', type=int, nargs=2, required=True)
    return run(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
