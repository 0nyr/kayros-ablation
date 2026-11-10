"""Build the fe8e927 EPS diagnostic inside a declared compute allocation."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tarfile

from checker_contract import checker_identity, sha256
from probe_runner import atomic_json

SOURCE = 'fe8e927cf5a7ade98ce0d698b4af402a0caa5652'
CONTROL = 'f2c9e205bd1ecc9afb45d5efe7fcb0df881f2b33'
CPLEX_SHA = 'd2657ed57b8a34b1b5926f80c060f87da4caa4df49b6029aa0551d1c0b6ce179'
COMPILER = 'g++ (Debian 14.2.0-19) 14.2.0'
ARCHIVE_SHA = 'c233a1529e4cbe69ed353a89d980617463dd3024bd504b1339bd4278e5cfda94'
PATCH_SHA = '23b36405d306a839cdcbe5c5893057bdb45aee5e87b30d7790d2aa5c0d59f061'


def verify_source(source, identity):
    for name, expected in identity['files'].items():
        if sha256(source / name) != expected:
            raise ValueError(f'Source differs from pinned archive: {name}')
    observed = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
    if observed != set(identity['files']):
        raise ValueError('Source archive contains unrecorded files')


def main(args):
    if os.environ.get('OAR_JOB_ID') != args.job_id or socket.gethostname().startswith(('fnancy', 'flyon')):
        raise ValueError('Build requires its declared OAR compute job')
    if any(k.startswith(('KAYROS_', 'UPSTREAM_', 'PROBE_')) for k in os.environ):
        raise ValueError('Diagnostic overrides are forbidden')
    if any(os.environ.get(k) for k in ['CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS']):
        raise ValueError('Inherited compiler or linker flag overrides are forbidden')
    if not 1 <= args.jobs <= 4:
        raise ValueError('Use 1 through 4 build workers')
    identity_path = args.bundle.resolve() / 'source-identity.json'
    identity = json.loads(identity_path.read_bytes())
    if identity['source_id'] != SOURCE or identity['control_source_id'] != CONTROL:
        raise ValueError('The source bundle is not the isolated EPS candidate')
    if identity['archive_sha256'] != ARCHIVE_SHA or identity['patch_sha256'] != PATCH_SHA:
        raise ValueError('Bundle digest differs from the prepared archive')
    archive, patch = [args.bundle.resolve() / identity[k] for k in ['archive', 'patch']]
    for path, key in [(archive, 'archive_sha256'), (patch, 'patch_sha256')]:
        if sha256(path) != identity[key]:
            raise ValueError(f'Bundle artifact changed: {path}')
    compiler = shutil.which('g++')
    compiler_version = subprocess.check_output([compiler, '--version'], text=True).splitlines()[0]
    if compiler_version != COMPILER:
        raise ValueError('Compiler differs from the fixed minimal Lera build')
    cplex = Path(os.environ['CPLEX_BIN']).resolve()
    cplex_header = Path(os.environ['CPLEX_INCLUDE']).resolve() / 'ilcplex/cplex.h'
    boost = Path(os.environ['BOOST_BIN']).resolve()
    if sha256(cplex) != CPLEX_SHA:
        raise ValueError('CPLEX static library differs from the fixed control')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source, build = output / 'source', output / 'build'
    source.mkdir()
    with tarfile.open(archive) as tar:
        tar.extractall(source, filter='data')
    verify_source(source, identity)
    record = {'source_id': SOURCE, 'control_source_id': CONTROL, 'algorithm_eps': 1e-6,
        'schema_version': 1, 'backend': 'CPLEX', 'created_utc': datetime.now(timezone.utc).isoformat(),
        'host': socket.gethostname(), 'job_id': args.job_id, 'compiler': compiler_version,
        'compiler_path': compiler, 'compiler_sha256': sha256(Path(compiler)),
        'source_identity_sha256': sha256(identity_path), 'archive_sha256': identity['archive_sha256'],
        'patch_sha256': identity['patch_sha256'], 'source_root': str(source),
        'source_file_sha256': identity['files'], 'build_driver_sha256': sha256(Path(__file__)),
        'cplex_static_library': {'path': str(cplex), 'sha256': sha256(cplex)},
        'build_dependencies': {str(p): sha256(p) for p in [cplex, cplex_header, boost]},
        'environment': {k: os.environ[k] for k in ['CPLEX_BIN', 'CPLEX_INCLUDE', 'BOOST_BIN', 'BOOST_INCLUDE']},
        'commands': [], 'build_passed': False}
    atomic_json(output / 'build-started.json', record)
    environment = dict(os.environ, CC='gcc', CXX=compiler)
    try:
        for name, command in [('configure', ['cmake', '-S', str(source / 'code'), '-B', str(build),
                '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON']),
                ('compile', ['cmake', '--build', str(build), '--target', 'bp', '-j', str(args.jobs)])]:
            with (output / f'{name}.log').open('x') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           env=environment, start_new_session=True)
                try:
                    code = process.wait(timeout=1200)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    record['commands'].append({'command': command, 'outer_timeout': True,
                                               'returncode': process.returncode})
                    raise RuntimeError(f'{name} exceeded its 1200 s outer deadline')
            record['commands'].append({'command': command, 'returncode': code})
            if code:
                raise RuntimeError(f'{name} exited with {code}')
        verify_source(source, identity)
        binary = build / 'bp'
        ldd = subprocess.check_output(['ldd', str(binary)], text=True)
        (output / 'ldd.txt').write_text(ldd)
        libraries = {str(Path(p).resolve()): sha256(Path(p).resolve())
            for p in re.findall(r'(/\S+)\s+\(', ldd)}
        record.update(binary=str(binary), binary_sha256=sha256(binary), dynamic_libraries=libraries,
            cmake_cache_sha256=sha256(build / 'CMakeCache.txt'),
            compile_commands_sha256=sha256(build / 'compile_commands.json'), build_passed=True)
        checker = checker_identity()
        runtime = {str(binary): sha256(binary), **libraries,
            str(build / 'CMakeCache.txt'): record['cmake_cache_sha256'],
            str(build / 'compile_commands.json'): record['compile_commands_sha256']}
        manifest = {'schema_version': 1, 'runtime_kind': 'native', 'source_id': SOURCE,
            'source_commit': SOURCE, 'backend': 'CPLEX', 'checker': checker, 'algorithm_eps': 1e-6,
            'compiler': compiler_version, 'cplex_static_library': record['cplex_static_library'],
            'runtime_files': runtime, 'source_patch_files': {str(patch): sha256(patch)},
            'source_archive_sha256': identity['archive_sha256'], 'source_file_sha256': identity['files'],
            'diagnostic_environment': {}, 'worker_environment': {
                'UPSTREAM_PROBE_BINARY': str(binary), 'UPSTREAM_SOURCE_ROOT': str(source)},
            'purpose': 'Isolated EPS diagnostic, never a replacement for the fixed f2c9e20 control'}
        atomic_json(output / 'manifest.json', manifest)
        record['manifest_sha256'] = sha256(output / 'manifest.json')
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
    record['finished_utc'] = datetime.now(timezone.utc).isoformat()
    atomic_json(output / 'build-complete.json', record)
    return int(not record['build_passed'] or 'error' in record)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=4)
    raise SystemExit(main(parser.parse_args()))
