"""Run three local numerical PWL composition cases with checked STL indexing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys

F2 = 'f2c9e205bd1ecc9afb45d5efe7fcb0df881f2b33'
SELECTED = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
CASES = ('disjoint-constant', 'empty-outer', 'valid-overlap')
UNITS = ('math/pwl_function.cpp', 'math/linear_function.cpp', 'math/interval.cpp',
         'math/point_2d.cpp', 'print/printable.cpp')
FLAGS = ('-std=c++23', '-O0', '-g', '-D_GLIBCXX_ASSERTIONS', '-ffp-contract=off')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(output, name, command):
    with (output / (name + '.stdout')).open('x') as out, (output / (name + '.stderr')).open('x') as err:
        process = subprocess.run(command, stdout=out, stderr=err, timeout=180)
    return dict(name=name, command=command, returncode=process.returncode,
                stdout_sha256=sha(output / (name + '.stdout')), stderr_sha256=sha(output / (name + '.stderr')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--f2-source', type=Path, required=True)
    parser.add_argument('--selected-source', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).resolve().parents[1] / 'tests/cpp/test_pwl_compose_constant.cpp')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiler', default='c++')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    compiler = Path(shutil.which(args.compiler) or args.compiler).resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = args.fixture.resolve()
    shutil.copyfile(fixture, output / 'test_pwl_compose_constant.cpp')
    identities = []
    for variant, repository, commit, prefix in (
            ('f2', args.f2_source.resolve(), F2, 'code/goc'),
            ('selected9d', args.selected_source.resolve(), SELECTED, 'cpp/lera/goc')):
        observed = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()
        if observed != commit:
            raise ValueError(f'{variant} must remain at its exact declared source commit')
        root = repository / prefix
        units = [root / 'src' / name for name in UNITS]
        source_files = {str(path): sha(path) for path in units}
        source_files.update({str(p): sha(p) for p in (root / 'include').rglob('*') if p.is_file()})
        identities.append(dict(variant=variant, source_commit=commit, root=str(root), source_files=source_files))
    version = subprocess.check_output([str(compiler), '--version'], text=True)
    plan = dict(compiler=str(compiler), compiler_sha256=sha(compiler), compiler_version=version,
                flags=list(FLAGS), fixture_sha256=sha(fixture), sources=identities,
                cases=list(CASES), scope='Ordinary local numerical tests only. No routing run or reachability derivative.')
    (output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    rows = []
    for identity in identities:
        variant, root = identity['variant'], Path(identity['root'])
        binary = output / (variant + '-compose')
        command = [str(compiler), *FLAGS, '-I' + str(root / 'include'), str(fixture),
                   *[str(root / 'src' / name) for name in UNITS], '-o', str(binary)]
        compiled = run(output, variant + '-compile', command)
        rows.append(compiled)
        if compiled['returncode']:
            continue
        identity['executable_sha256'] = sha(binary)
        for name in CASES:
            row = run(output, variant + '-' + name, [str(binary), name])
            row.update(variant=variant, case=name,
                outcome='returned_expected_numerical_result' if row['returncode'] == 0 else
                        'checked_index_assertion' if row['returncode'] == -6 and 'Assertion' in (output / (row['name'] + '.stderr')).read_text() else 'other_failure')
            rows.append(row)
        for filename, expected in identity['source_files'].items():
            if sha(Path(filename)) != expected:
                raise ValueError('Source changed during local unit testing')
    summary = dict(plan_sha256=sha(output / 'plan.json'), rows=rows, source_identities=identities,
        f2_control_source_unchanged=True, selected_source_unchanged=True,
        reachability_on_routing_instances='unproven and not tested', solver_runs=0,
        conclusion='Each case is isolated in its own process so a checked-index assertion preserves the remaining numerical test cases.')
    (output / 'complete.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps([{k: r[k] for k in ('name', 'returncode', 'outcome') if k in r} for r in rows], indent=2))
    return int(any(r['returncode'] for r in rows if r['name'].endswith('-compile')))


if __name__ == '__main__':
    raise SystemExit(main())
