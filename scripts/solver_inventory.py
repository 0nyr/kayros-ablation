"""Record build and installed artifacts without evaluating any route costs."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--backend', required=True, choices=['cplex', 'highs'])
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--patch-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cplex-library', type=Path)
    args = parser.parse_args()

    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    distribution = importlib.metadata.distribution('kayros')
    installed = {str(distribution.locate_file(p).resolve()): digest(Path(distribution.locate_file(p)))
                 for p in distribution.files if str(p).endswith(('.py', '.so'))}
    source = {str(p.relative_to(args.source)): digest(p)
              for p in sorted(args.source.rglob('*'))
              if p.is_file() and not any(x in p.relative_to(args.source).parts for x in ['build', '.git', '__pycache__'])}
    source_digest = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    patches = {p.name: digest(p) for p in sorted(args.patch_dir.glob('000*-*.patch'))}
    from checker_contract import checker_identity
    from kayros import _lera
    inventory = {'schema_version': 1, 'source_commit': '50eebc57964ac4f6762719ff21033783a31c12da',
                 'source_tree_sha256': source_digest, 'source_files': source, 'patches': patches,
                 'backend': _lera.LP_BACKEND, 'wheel_sha256': digest(args.wheel),
                 'installed_files': installed, 'checker': checker_identity(),
                 'python': platform.python_version(),
                 'compiler': subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
                 'installed_dependencies': sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions())}
    cache = args.source/'build'/args.backend/'CMakeCache.txt'
    if cache.exists():
        inventory['cmake_cache_sha256'] = digest(cache)
        inventory['effective_cmake_cache'] = cache.read_text()
    linked = {}
    for path in installed:
        if not path.endswith('.so'):
            continue
        output = subprocess.check_output(['ldd', path], text=True)
        for filename in re.findall(r'(/[^\s()]+)', output):
            dependency = Path(filename)
            if dependency.is_file():
                linked[str(dependency)] = digest(dependency)
    inventory['dynamic_libraries'] = linked
    if args.cplex_library is not None:
        inventory['cplex_static_library'] = {'path': str(args.cplex_library), 'sha256': digest(args.cplex_library)}
    highs_git = args.source/'build/highs/_deps/highs-src'
    if args.backend == 'highs' and highs_git.exists():
        inventory['highs_commit'] = subprocess.check_output(['git', '-C', str(highs_git), 'rev-parse', 'HEAD'], text=True).strip()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2)+'\n')


if __name__ == '__main__':
    main()
