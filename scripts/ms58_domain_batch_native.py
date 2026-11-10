"""Native gate of cd5dee0 and post-gate runtime-manifest preparation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_dabia_screen import BEST, BEST_PATCH, LERA, LERA_BINARY, LERA_PATCH, immutable_json, now
from ms58_dabia_validate import run_command
from ms58_dabia_wave import checked_file, no_overrides, topology_snapshot
from ms58_paired_probe import candidate_manifest

BATCH = 'cd5dee0b2543e9aa65718889379bedc6181d3161'
BATCH_PATCH = '057fe3dec78bbbbf38cb629994a78c6624fafd85e9177fdf29f5adca976a455c'
SOURCE_IDENTITY_SHA = '9282115b0fc081ac20a0ae98fbf8694318bb1175dcce0a9edf3876e2c0b9a145'
FIXTURE_SOURCE = '1f5102b0997f277a4d5d8e20c4a422d0bb70a4e6'
FIXTURE_PATH = 'tests/cpp/test_cplex_variable_domains.cpp'
ORIGINAL_FIXTURE_SHA = '4d854094a24c577854ad1bcd871804a1a65a67a5fb6fb8f3f5950f241498131a'
FIXTURE_SHA = 'd3e60eb627d97af0b8551c505ac01f03526721aac88eb151b99f20964af65ac8'
BATCH_BINARY = 'ff4868d7e4cbb12d4b6e557047e4ab89d617b48ce5e712dd543c5e6f28e1865f'
BEST_BINARY = 'd3bdc3d0741bb01880db9341230f2672fa9cf547a460f4d8c64a542d55371cd5'
ILS_BINARY = 'b527271e7224fbd1b14b6ed84de49098395e672172ab038a2cc45258572287f3'
CPLEX_LIBRARY = 'd2657ed57b8a34b1b5926f80c060f87da4caa4df49b6029aa0551d1c0b6ce179'
CASES = {'mixed-CIB', 'mixed-BIC', 'mixed-IBC', 'continuous-MILP', 'fixed-binaries',
    'fractional-binary-bounds', 'wide-binary-bounds', 'infinite-continuous-bounds',
    'one-integer', 'empty-LP', 'empty-MILP', 'semitype-fallback',
    'empty-quadratic-MIQP-fallback', 'explicit-LP-error', 'fixed-MILP-restore-error',
    'production-solve-lp', 'derived-formulation'}


def allocation(job_id, cores, memory_gib):
    no_overrides(os.environ)
    host = socket.gethostname().split('.')[0]
    nodefile = Path(os.environ.get('OAR_NODEFILE', '/missing-nodefile'))
    if not job_id or os.environ.get('OAR_JOB_ID') != job_id or host.startswith(('fnancy', 'flyon')):
        raise ValueError('A declared real OAR compute job is required')
    if not nodefile.is_file() or host not in {h.split('.')[0] for h in nodefile.read_text().split()}:
        raise ValueError('Current host is absent from the real OAR nodefile')
    topology = topology_snapshot(cores)
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if available < memory_gib * 1024 ** 2:
        raise ValueError(f'Available memory must cover {memory_gib} GiB including reserve')
    return {'host': socket.gethostname(), 'oar_job_id': job_id, 'created_utc': now(),
        'physical_slots': topology, 'memory_available_kib': available,
        'allowed_cores': sorted(os.sched_getaffinity(0)), 'nodefile': nodefile.read_text()}


def validate_fixture(value):
    cases = value.get('cases', [])
    if (value.get('passed') is not True or len(cases) != 17
            or {case.get('case') for case in cases} != CASES
            or not str(value.get('cplex_version', '')).startswith('12.10')
            or value.get('timing_claim') is not False):
        raise ValueError('Native gate requires all 17 cases passed on the pinned CPLEX 12.10 build')


def verify_source_files(source, inventory):
    """Allow only the pinned fixture repair over the unchanged production inventory."""
    if inventory.get(FIXTURE_PATH) != ORIGINAL_FIXTURE_SHA:
        raise ValueError('Production inventory must contain the exact original native fixture')
    for relative, expected in inventory.items():
        checked_file(source / relative, FIXTURE_SHA if relative == FIXTURE_PATH else expected)
    return {'source_id': FIXTURE_SOURCE, 'path': FIXTURE_PATH, 'sha256': FIXTURE_SHA,
        'production_sha256': ORIGINAL_FIXTURE_SHA}


def source_and_build(root, source_identity):
    checked_file(source_identity, SOURCE_IDENTITY_SHA)
    identity = json.loads(source_identity.read_bytes())
    if identity['source_id'] != BATCH or identity['full_patch']['sha256'] != BATCH_PATCH:
        raise ValueError('Source inventory is not the declared batching candidate')
    source = root / 'sources/cplex-domain-batch'
    fixture_override = verify_source_files(source, identity['source_files'])
    patches = {path.name: sha256(path) for path in (root / 'patches-cplex-domain-batch').glob('*.patch')}
    if patches != {row['filename']: row['sha256'] for row in identity['patch_stack']}:
        raise ValueError('Batch source requires its exact 5-patch inventory')
    cache = source / 'build/cplex/CMakeCache.txt'
    entries = {}
    for line in cache.read_text().splitlines():
        if '=' in line and ':' in line.split('=', 1)[0] and not line.startswith(('#', '//')):
            key, value = line.split('=', 1)
            entries[key.split(':', 1)[0]] = value
    if (entries.get('LERA_LP_BACKEND') != 'cplex'
            or Path(entries.get('CMAKE_HOME_DIRECTORY', '')).resolve() != source.resolve()
            or entries.get('KAYROS_EXTENSION_PROFILING', 'OFF').upper() not in ('OFF', 'FALSE', '0')):
        raise ValueError('Candidate build must be its own CPLEX directory with profiling disabled')
    library = checked_file(Path.home() / 'cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a', CPLEX_LIBRARY)
    header = Path.home() / 'cplex1210/cplex/include/ilcplex/cplex.h'
    runtime = {}
    for variant in ('best', 'cplex-domain-batch'):
        python = root / f'venv-{variant}-cplex/bin/python'
        query = 'import json; from kayros import _core,_lera; print(json.dumps(dict(backend=_lera.LP_BACKEND,core=_core.__file__,lera=_lera.__file__)))'
        observed = json.loads(subprocess.check_output([str(python), '-c', query], text=True, timeout=30))
        if observed['backend'] != 'CPLEX':
            raise ValueError('Both Kayros runtimes must be CPLEX')
        checked_file(observed['core'], ILS_BINARY)
        checked_file(observed['lera'], BATCH_BINARY if variant == 'cplex-domain-batch' else BEST_BINARY)
        if b'kayros-ms58-extension-profile-v1' in Path(observed['lera']).read_bytes():
            raise ValueError('Timing runtime unexpectedly contains extension profiling')
        runtime[variant] = {key: {'path': observed[key], 'sha256': sha256(Path(observed[key]))}
                            for key in ('core', 'lera')}
    return {'source_id': BATCH, 'source_inventory_sha256': SOURCE_IDENTITY_SHA,
        'fixture_override': fixture_override,
        'source_directory': str(source), 'cache_sha256': sha256(cache),
        'native_library_sha256': sha256(source / 'build/cplex/cpp/lera/liblera.a'),
        'compiler': entries['CMAKE_CXX_COMPILER'], 'compiler_sha256': sha256(Path(entries['CMAKE_CXX_COMPILER'])),
        'cplex_library_sha256': sha256(library), 'cplex_header_sha256': sha256(header),
        'runtime': runtime, 'patches': patches}


def fixture_commands(raw, source, compiler):
    """Read the existing Ninja target without rerunning CMake or rebuilding dependencies."""
    compile_commands, link_commands = [], []
    for line in raw.splitlines():
        if 'ms58_cplex_domain_fixture' not in line:
            continue
        tokens = shlex.split(line)
        while tokens[:2] == [':', '&&']:
            tokens = tokens[2:]
        while tokens[-2:] == ['&&', ':']:
            tokens = tokens[:-2]
        if any(token in {'&&', '||', ';', '|', '>', '<'} or '$(' in token or '`' in token for token in tokens):
            raise ValueError('Unexpected shell expression in generated fixture command')
        if not tokens or Path(tokens[0]).resolve() != Path(compiler).resolve() or '-o' not in tokens:
            raise ValueError('Generated fixture compiler differs from frozen CMake compiler')
        target = tokens[tokens.index('-o') + 1]
        if '-c' in tokens and str(source / 'tests/cpp/test_cplex_variable_domains.cpp') in tokens:
            compile_commands.append(tokens)
        elif target == 'cpp/lera/ms58_cplex_domain_fixture' and 'cpp/lera/liblera.a' in tokens:
            link_commands.append(tokens)
    if len(compile_commands) != 1 or len(link_commands) != 1:
        raise ValueError('Expected exactly one recorded fixture compile and link command')
    return compile_commands[0], link_commands[0]


def prepare_artifacts(root, output, gate, upstream_path):
    artifacts = output / 'artifacts'
    artifacts.mkdir()
    checker = checker_identity()
    plans = []
    for variant, source in [('best', BEST), ('cplex-domain-batch', BATCH)]:
        patches = artifacts / f'patches-{variant}'
        patches.mkdir()
        python = root / f'venv-{variant}-cplex/bin/python'
        manifest = candidate_manifest(python, root / f'patches-{variant}', patches, checker, source)
        required_patch = BEST_PATCH if variant == 'best' else BATCH_PATCH
        if required_patch not in manifest['source_patch_files'].values():
            raise ValueError('Runtime manifest lacks the exact source patch')
        manifest.update(diagnostic_environment={}, native_gate_sha256=sha256(gate),
                        native_fixture_source_commit=FIXTURE_SOURCE, native_fixture_sha256=FIXTURE_SHA,
                        declared_source_commit=source, purpose='CPLEX domain-batch screen')
        path = artifacts / f'{variant}.json'
        immutable_json(path, manifest)
        plans.append({'name': variant, 'source_id': source, 'manifest_path': str(path),
                      'worker_python': str(python), 'diagnostic_environment': {}})
    upstream = json.loads(upstream_path.read_bytes())
    if (upstream.get('source_id') != LERA or upstream.get('checker') != checker
            or upstream.get('backend', '').lower() != 'cplex'
            or upstream.get('diagnostic_environment', {}) != {}):
        raise ValueError('Upstream artifact must be explicitly fixed Lera f2c9e20')
    for filename, expected in {**upstream['runtime_files'], **upstream['source_patch_files']}.items():
        checked_file(filename, expected)
    checked_file(upstream['worker_environment']['UPSTREAM_PROBE_BINARY'], LERA_BINARY)
    if LERA_PATCH not in upstream['source_patch_files'].values():
        raise ValueError('Upstream minimal initialization repair is absent')
    upstream['native_gate_sha256'] = sha256(gate)
    upstream_path_copy = artifacts / 'minimal-lera.json'
    immutable_json(upstream_path_copy, upstream)
    plans.append({'name': 'minimal-lera', 'source_id': LERA, 'manifest_path': str(upstream_path_copy),
        'worker_python': sys.executable, 'worker_path': str(Path(__file__).with_name('upstream_event_worker.py')),
        'diagnostic_environment': {}})
    immutable_json(artifacts / 'plan.json', plans)
    immutable_json(artifacts / 'complete.json', {'native_gate_sha256': sha256(gate),
        'files': {str(path.relative_to(artifacts)): sha256(path) for path in artifacts.rglob('*') if path.is_file()},
        'created_utc': now()})
    return artifacts / 'plan.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--core', type=int, required=True)
    parser.add_argument('--root', type=Path, default=Path.home() / 'ms58-bpca')
    parser.add_argument('--source-identity', type=Path, required=True)
    parser.add_argument('--upstream-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    allocated = allocation(args.job_id, [args.core], 32)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    complete = {'source_id': BATCH, 'fixture_source_id': FIXTURE_SOURCE, 'fixture_sha256': FIXTURE_SHA,
        'all_checks_passed': False, 'results': [], 'created_utc': now()}
    try:
        identity = source_and_build(args.root.resolve(), args.source_identity.resolve())
        complete['identity'] = identity
        immutable_json(output / 'plan.json', {'identity': identity, 'allocation': allocated,
            'runner_sha256': sha256(Path(__file__)), 'checker': checker_identity()})
        shutil.copyfile(args.source_identity, output / 'source-identity.json')
        source = Path(identity['source_directory'])
        build = source / 'build/cplex'
        ninja = shutil.which('ninja')
        if not ninja:
            raise ValueError('An available Ninja executable is required to read the existing build graph')
        raw = subprocess.check_output([ninja, '-C', str(build), '-t', 'commands', 'ms58_cplex_domain_fixture'],
                                      text=True, timeout=30)
        (output / 'ninja-target-commands.txt').write_text(raw)
        compile_command, link_command = fixture_commands(raw, source, identity['compiler'])
        complete['ninja_target_commands_sha256'] = sha256(output / 'ninja-target-commands.txt')
        for name, command in [('compile', compile_command), ('link', link_command)]:
            target = (build / command[command.index('-o') + 1]).resolve()
            if not target.is_relative_to(build.resolve()):
                raise ValueError('Fixture output escapes its own CMake build directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            result = run_command(output, name, command, args.core, 180, build)
            complete['results'].append(result)
            if not result['passed']:
                raise ValueError(f'Native fixture {name} failed')
        executable = source / 'build/cplex/cpp/lera/ms58_cplex_domain_fixture'
        complete['executable_sha256'] = sha256(executable)
        result = run_command(output, 'fixture', [str(executable)], args.core, 180, source)
        complete['results'].append(result)
        if not result['passed']:
            raise ValueError('Native fixture failed')
        value = json.loads((output / 'fixture.stdout').read_bytes())
        validate_fixture(value)
        if source_and_build(args.root.resolve(), args.source_identity.resolve()) != identity:
            raise ValueError('Source or runtime changed during the native gate')
        complete.update(all_checks_passed=True, case_count=len(value['cases']),
            fixture_stdout_sha256=sha256(output / 'fixture.stdout'), fixture_stderr_sha256=sha256(output / 'fixture.stderr'),
            cplex_version=value['cplex_version'])
    except Exception as error:
        complete['error'] = f'{type(error).__name__}: {error}'
    complete['finished_utc'] = now()
    immutable_json(output / 'complete.json', complete)
    if not complete['all_checks_passed']:
        print(json.dumps(complete, indent=2))
        return 1
    try:
        plan = prepare_artifacts(args.root.resolve(), output, output / 'complete.json', args.upstream_manifest)
    except Exception as error:
        immutable_json(output / 'artifact-preparation-failed.json', {'error': f'{type(error).__name__}: {error}'})
        raise
    print(json.dumps({'all_checks_passed': True, 'case_count': 17, 'plan': str(plan)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
