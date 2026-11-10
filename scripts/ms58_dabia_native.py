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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--variant', choices=['scratch', 'safe-correction'], required=True)
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
    source_id = {'scratch': 'e27f288', 'safe-correction': 'a63cf65'}[args.variant]
    python = root / f'venv-{args.variant}-cplex/bin/python'
    backend = subprocess.check_output([str(python), '-c', 'from kayros import _lera; print(_lera.LP_BACKEND)'], text=True).strip()
    if backend != 'CPLEX':
        raise ValueError('Native library must belong to the CPLEX build')
    atomic_json(output / 'plan.json', {'variant': args.variant, 'source_id': source_id,
        'fixture_source_id': '151a4ef' if args.variant == 'safe-correction' else 'e27f288',
        'oar_job_id': args.job_id, 'host': socket.gethostname(), 'cpu': core,
        'backend': backend, 'created_utc': stamp(), 'library_sha256': sha256(library),
        'fixtures': {name: sha256(source / 'tests/cpp' / f'{name}.cpp') for name in fixtures},
        'runner_sha256': sha256(Path(__file__))})
    rows = []
    for name in fixtures:
        executable = output / name
        command = ['c++', '-std=c++23', '-O3', '-DNDEBUG', '-ffp-contract=off',
            *['-I' + str(source / path) for path in
              ['cpp/lera/goc/include', 'cpp/lera/main/include', 'cpp/lera/nyr/include', 'cpp/lera/magic_enum/include']],
            str(source / 'tests/cpp' / f'{name}.cpp'), str(library), '-ldl', '-lm', '-o', str(executable)]
        compiled = run_command(output, 'compile-' + name, command, core, 180, source)
        rows.append(compiled)
        if compiled['passed']:
            rows.append(run_command(output, name, [str(executable)], core, 120, source))
    passed = len(rows) == 2 * len(fixtures) and all(row['passed'] for row in rows)
    atomic_json(output / 'complete.json', {'source_id': source_id, 'results': rows,
        'all_checks_passed': passed, 'finished_utc': stamp(),
        'scope': 'Synthetic native invariants, not a benchmark speed or full pricing-completeness claim.'})
    return int(not passed)


if __name__ == '__main__':
    raise SystemExit(main())
