"""Compare normalization under explicitly ordered BPC valuation reductions."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from checker_contract import checker_identity
from ms58_paired_probe import candidate_manifest
from probe_runner import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', default='7013766')
    parser.add_argument('--validation-record', type=Path, required=True)
    parser.add_argument('--latest-start-utc', default='2026-11-10T06:40:00+00:00')
    args = parser.parse_args()
    assert os.environ.get('OAR_JOB_ID') == args.job_id
    assert not socket.gethostname().startswith(('fnancy', 'flyon'))
    assert datetime.now(timezone.utc) < datetime.fromisoformat(args.latest_start_utc)
    assert not any(key.startswith('KAYROS_') for key in os.environ)
    validation = json.loads(args.validation_record.read_bytes())
    assert validation['ordered_valuation_property_passed'] is True
    assert validation['reference_source_id'] == '37e4801'
    assert validation['candidate_source_id'] == '0bec4d4'
    root = Path.home() / 'ms58-bpca'
    output = root / 'resume-20261110/canonical-comparison'
    output.mkdir(parents=True, exist_ok=False)
    patches = output / 'reference-manifest-patches'
    patches.mkdir()
    checker = checker_identity()
    reference = candidate_manifest(root / 'venv-stable-branch/bin/python',
        root / 'patches-stable-branch', patches, checker, '37e4801')
    reference['purpose'] = 'Diagnostic reference with column-index-ordered BPC reductions'
    atomic_json(output / 'reference-manifest.json', reference)
    atomic_json(output / 'validation.json', validation)
    core = min(os.sched_getaffinity(0))
    command = [sys.executable, str(root / 'experiment/scripts/ms58_paired_probe.py'),
        '--output', str(output / 'C201'),
        '--reference-python', str(root / 'venv-stable-branch/bin/python'),
        '--reference-manifest', str(output / 'reference-manifest.json'),
        '--reference-source-id', '37e4801',
        '--candidate-python', str(root / 'venv-canonical-normalize/bin/python'),
        '--candidate-source-id', '0bec4d4',
        '--candidate-patch-dir', str(root / 'patches-canonical-normalize'),
        '--patch-dir', str(root / 'experiment/patches'),
        '--instances', 'C201', '--repetitions', '3', '--horizon', '300',
        '--memory-mb', '16384', '--cpu-core', str(core)]
    atomic_json(output / 'launch-plan.json', {'command': command,
        'oar_job_id': args.job_id, 'host': socket.gethostname(), 'cpu': core,
        'source_reference': '37e4801', 'source_candidate': '0bec4d4',
        'scope': 'Exploratory allocation-order control. Stable search work is a measured outcome, not an assumption. No candidate promotion or full-proof claim.'})
    with (output / 'lane.log').open('x') as stream:
        process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
    atomic_json(output / 'complete.json', {'returncode': process.returncode})
    return process.returncode


if __name__ == '__main__':
    raise SystemExit(main())
