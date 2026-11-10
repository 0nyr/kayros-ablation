"""Correction-on integrated batching build seal, 17-case native gate and artifact preparation."""
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
from ms58_dabia_screen import LERA, LERA_BINARY, LERA_PATCH, immutable_json, now
from ms58_dabia_validate import run_command
from ms58_dabia_wave import checked_file, no_overrides, topology_snapshot
from ms58_paired_probe import candidate_manifest

BATCH = '234d838b4128eda40f11cb53f66005d533cf24af'
PRODUCTION = 'ef0e18288883dd7b4c2a5941105176d6ff312cfd'
BEST = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
BEST_PATCH = '18b453c73422771ce0a2c6c5cbd6b934b62bcded0566c9c495d253106df28e87'
BATCH_PATCH = '5959de45e39c403e92369dfa235bdec4f66ae633bdef6b5a73e5ef8a9659a9cc'
SOURCE_IDENTITY_SHA = 'a2cc79ba6eb1f894cbc9185e69277067bffc9167acde068117bd08e279cdcfc0'
FIXTURE_SOURCE = BATCH
FIXTURE_PATH = 'tests/cpp/test_cplex_variable_domains.cpp'
FIXTURE_SHA = 'd3e60eb627d97af0b8551c505ac01f03526721aac88eb151b99f20964af65ac8'
BATCH_VARIANT = 'correction-on-domain-batch'
CONTROL_VARIANT = 'correction-on'
BEST_BINARY = '54b2f60bf61268a37c09dd49d0a60d3f71232948804a9910f188a87d12347b98'
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


def verify_sources(root, source_identity, control_root):
    checked_file(source_identity, SOURCE_IDENTITY_SHA)
    pins = json.loads(source_identity.read_bytes())
    if (pins['source_id'] != BATCH or pins['production_source_id'] != PRODUCTION
            or pins['control_source_id'] != BEST or len(pins['patch_stack']) != 7):
        raise ValueError('Require the exact corrected integrated tree and seven-patch stack')
    source = root / 'sources' / BATCH_VARIANT
    control = control_root / 'sources' / CONTROL_VARIANT
    for directory, inventory in ((source, pins['files']), (control, pins['control_files'])):
        for relative, expected in inventory.items():
            checked_file(directory / relative, expected)
        observed = {p.relative_to(directory).as_posix() for p in (directory / 'cpp').rglob('*') if p.is_file()}
        if observed != {name for name in inventory if name.startswith('cpp/')}:
            raise ValueError('Unexpected native source file population')
    checked_file(source / FIXTURE_PATH, FIXTURE_SHA)
    patches = {p.name: sha256(p) for p in (root / ('patches-' + BATCH_VARIANT)).glob('*.patch')}
    if patches != {p['filename']: p['sha256'] for p in pins['patch_stack']}:
        raise ValueError('Require exactly four baseline, 9d full, batching production and fixture patches')
    control_patches = {p.name: sha256(p) for p in (control_root / 'patches-correction-on').glob('*.patch')}
    if len(control_patches) != 5 or set(control_patches.values()) != {p['sha256'] for p in pins['patch_stack'][:5]}:
        raise ValueError('Control requires the exact four baseline patches and 9d full patch')
    return pins, source, control, patches


def observed_build(root, source_identity, control_root):
    _, source, control, patches = verify_sources(root, source_identity, control_root)
    library = checked_file(Path.home() / 'cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a', CPLEX_LIBRARY)
    header = Path.home() / 'cplex1210/cplex/include/ilcplex/cplex.h'
    builds, runtime = {}, {}
    for variant, directory, environment_root in ((BATCH_VARIANT, source, root), (CONTROL_VARIANT, control, control_root)):
        cache = directory / 'build/cplex/CMakeCache.txt'
        entries = {line.split('=', 1)[0].split(':', 1)[0]: line.split('=', 1)[1]
            for line in cache.read_text().splitlines() if '=' in line and ':' in line.split('=', 1)[0]
            and not line.startswith(('#', '//'))}
        if (entries.get('LERA_LP_BACKEND') != 'cplex'
                or Path(entries.get('CMAKE_HOME_DIRECTORY', '')).resolve() != directory.resolve()
                or any(entries.get(key, 'OFF').upper() not in ('OFF', 'FALSE', '0')
                       for key in ('KAYROS_EXTENSION_PROFILING', 'KAYROS_DOMINANCE_PROFILING'))):
            raise ValueError('Both builds require their own CPLEX source directory with profiling disabled')
        compiler = Path(entries['CMAKE_CXX_COMPILER'])
        builds[variant] = dict(cache_sha256=sha256(cache), native_library_sha256=sha256(directory / 'build/cplex/cpp/lera/liblera.a'),
                               compiler=str(compiler), compiler_sha256=sha256(compiler))
        python = environment_root / f'venv-{variant}-cplex/bin/python'
        query = 'import json; from kayros import _core,_lera; print(json.dumps(dict(backend=_lera.LP_BACKEND,core=_core.__file__,lera=_lera.__file__)))'
        observed = json.loads(subprocess.check_output([str(python), '-c', query], text=True, timeout=30))
        if observed['backend'] != 'CPLEX':
            raise ValueError('Both installed runtimes must be CPLEX')
        checked_file(observed['core'], ILS_BINARY)
        if variant == CONTROL_VARIANT:
            checked_file(observed['lera'], BEST_BINARY)
        if b'kayros-ms58-extension-profile-v1' in Path(observed['lera']).read_bytes():
            raise ValueError('Timing runtime contains extension profiling')
        runtime[variant] = {key: {'path': observed[key], 'sha256': sha256(Path(observed[key]))} for key in ('core', 'lera')}
    wheels = sorted((root / 'artifacts' / (BATCH_VARIANT + '-cplex')).glob('*.whl'))
    if len(wheels) != 1:
        raise ValueError('Require exactly one integrated wheel')
    return dict(source_id=BATCH, production_source_id=PRODUCTION, fixture_source_id=FIXTURE_SOURCE,
        fixture_sha256=FIXTURE_SHA, control_source_id=BEST, control_root=str(control_root),
        source_inventory_sha256=SOURCE_IDENTITY_SHA, source_directory=str(source),
        compiler=builds[BATCH_VARIANT]['compiler'], compiler_sha256=builds[BATCH_VARIANT]['compiler_sha256'],
        cplex_library_sha256=sha256(library), cplex_header_sha256=sha256(header),
        builds=builds, runtime=runtime, patches=patches, wheel={'path': str(wheels[0]), 'sha256': sha256(wheels[0])})


def source_and_build(root, source_identity):
    seal_path = root / 'build-complete.json'
    seal = json.loads(seal_path.read_bytes())
    if seal.get('build_passed') is not True or seal.get('source_id') != BATCH:
        raise ValueError('Integrated source must have its own successful sealed build')
    observed = observed_build(root, source_identity, Path(seal['identity']['control_root']))
    if observed != seal['identity']:
        raise ValueError('Integrated/control source or runtime differs from the sealed build')
    return dict(observed, build_record_sha256=sha256(seal_path))


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
    for variant, source in [(CONTROL_VARIANT, BEST), (BATCH_VARIANT, BATCH)]:
        patches = artifacts / f'patches-{variant}'
        patches.mkdir()
        environment_root = Path(json.loads((root / 'build-complete.json').read_bytes())['identity']['control_root']) if variant == CONTROL_VARIANT else root
        python = environment_root / f'venv-{variant}-cplex/bin/python'
        manifest = candidate_manifest(python, environment_root / f'patches-{variant}', patches, checker, source)
        required_patch = BEST_PATCH if variant == CONTROL_VARIANT else BATCH_PATCH
        if required_patch not in manifest['source_patch_files'].values():
            raise ValueError('Runtime manifest lacks the exact source patch')
        manifest.update(diagnostic_environment={}, native_gate_sha256=sha256(gate),
                        native_fixture_source_commit=FIXTURE_SOURCE, native_fixture_sha256=FIXTURE_SHA,
                        declared_source_commit=source, expected_algorithm_settings={'label_correction': True, 'lazy_extension': True},
                        purpose='Matched correction-on CPLEX integrated batching screen')
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
    parser.add_argument('--mode', choices=('seal-build', 'gate'), default='gate')
    parser.add_argument('--control-root', type=Path, default=Path.home() / 'ms58-bpca')
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--core', type=int, required=True)
    parser.add_argument('--root', type=Path, default=Path.home() / 'ms58-correction-domain-batch')
    parser.add_argument('--source-identity', type=Path, required=True)
    parser.add_argument('--upstream-manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    allocated = allocation(args.job_id, [args.core], 32)
    output = args.output.resolve()
    if args.mode == 'seal-build':
        if output != args.root.resolve() / 'build-complete.json':
            raise ValueError('Build seal must be ROOT/build-complete.json')
        identity = observed_build(args.root.resolve(), args.source_identity.resolve(), args.control_root.resolve())
        immutable_json(output, dict(source_id=BATCH, build_passed=True, identity=identity,
            runner_sha256=sha256(Path(__file__)), allocation=allocated, finished_utc=now()))
        print(json.dumps({'build_passed': True, 'build_record': str(output), 'sha256': sha256(output)}))
        return 0
    if not args.upstream_manifest:
        parser.error('gate requires --upstream-manifest')
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
        ninja = args.root.resolve() / 'venv-build-tools/bin/ninja'
        if not ninja.is_file():
            raise ValueError('An available Ninja executable is required to read the existing build graph')
        complete['ninja_sha256'] = sha256(ninja)
        raw = subprocess.check_output([str(ninja), '-C', str(build), '-t', 'commands', 'ms58_cplex_domain_fixture'],
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
