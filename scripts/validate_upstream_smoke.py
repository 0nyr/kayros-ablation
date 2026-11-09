"""Validate a native smoke witness with the pinned checker, without audit timing."""
import json
import sys
from pathlib import Path
from checker_contract import checker_identity, sha256
from validate_probe import validate_candidates

root = Path.home() / 'ms56-ablation'
size = int(sys.argv[1]) if len(sys.argv) > 1 else 25
out = root / f'solver/upstream-probe-affinity-n{size}'
fixture = root / f'inputs/benchmarks/TDVRPTW/Dabia2013/n={size}/C101.vrp.json'
fixture_data = json.loads(fixture.read_bytes())
atf = fixture.parent / fixture_data['td']['atf_path']
events = [json.loads(line) for line in (out / f'C101_{size}.events.jsonl').read_text().splitlines()]
for i, event in enumerate(events):
    event['sequence'] = i
    event['elapsed_s'] = None  # Native final-only smoke has no incumbent production timestamps.
result = validate_candidates(events, instance_path=fixture, instance_sha256=sha256(fixture),
                             atf_path=atf, atf_sha256=sha256(atf), instance_name='C101')
(out / f'C101_{size}.validation.json').write_text(json.dumps(result, indent=2) + '\n')
