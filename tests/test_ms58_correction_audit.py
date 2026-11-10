"""A late artifact audit failure cannot retain an accepted timing observation."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from checker_contract import sha256
from ms58_correction_runs import run_lane


class CorrectionAuditTests(unittest.TestCase):
    def test_native_digest_failure_censors_previously_observed_closure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in ['launch-logs', 'rows', 'cells/case']:
                (root / directory).mkdir(parents=True)
            input_path, atf_path = root / 'instance.json', root / 'atf.json'
            input_path.write_text('{}')
            atf_path.write_text('{}')
            native_path = root / 'native.json'
            native_path.write_text('{}')
            (root / 'cells/case/events.jsonl').write_text(
                json.dumps({'event': 'candidate', 'sequence': 0, 'elapsed_s': 1}) + '\n' +
                json.dumps({'event': 'closure', 'sequence': 1, 'elapsed_s': 2}) + '\n')
            (root / 'cells/case/stderr.log').write_text('Explicit diagnostic correcting: 0\n')
            result = {'run_id': 'case', 'status': 'completed', 'returncode': 0, 'issues': [],
                      'validation': {'best_checked_cost': 10,
                                     'candidates': [{'sequence': 0, 'valid': True}],
                                     'native_result': {'path': str(native_path), 'sha256': 'wrong'}}}

            def fake_probe(command, *, stdout, stderr, env):
                stdout.write(json.dumps(result))
                return SimpleNamespace(returncode=0)

            lane = {'instance': 'C104', 'order': [0], 'horizon_s': 300, 'cpu': 0,
                    'input_path': str(input_path), 'input_sha256': sha256(input_path),
                    'atf_path': str(atf_path), 'atf_sha256': sha256(atf_path)}
            with patch('ms58_correction_runs.subprocess.run', side_effect=fake_probe):
                row = run_lane(lane, root, root, root)[0]
            self.assertEqual(row['status'], 'launch_or_audit_failure')
            self.assertIn('Native sidecar digest mismatch', row['reason'])
            self.assertIsNone(row['reported_closure_s'])
            self.assertEqual(row['capped_closure_s'], 300)


if __name__ == '__main__':
    unittest.main()
