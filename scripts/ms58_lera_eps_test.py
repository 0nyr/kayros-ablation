"""Offline schedule and identity checks for the isolated EPS package."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from checker_contract import sha256
from ms58_lera_eps_pairs import audit, expected_identity, launch_timeout, paired_lanes
from ms58_lera_eps_build import verify_source
from probe_runner import digest
import run_solver_probe


class EpsHarnessTests(unittest.TestCase):
    def test_both_shapes_balance_each_instance(self):
        for count in (3, 6):
            lanes = paired_lanes(list(range(count)))
            tasks = [task for lane in lanes for task in lane]
            self.assertEqual(len(tasks), 12)
            self.assertEqual(len({t['task_id'] for t in tasks}), 12)
            for name in ('C201', 'RC201', 'C104'):
                observed = [t for t in tasks if t['instance_name'] == name]
                self.assertEqual([(t['replicate'], t['variant'], t['position']) for t in observed],
                    [(1, 'minimal-lera', 1), (1, 'eps-parity', 2),
                     (2, 'eps-parity', 1), (2, 'minimal-lera', 2)])
                for rep in (1, 2):
                    self.assertEqual(len({t['cpu_core'] for t in observed if t['replicate'] == rep}), 1)

    def test_invalid_core_shapes_are_rejected(self):
        for cores in ([0], [0, 1, 2, 3], [0, 1, 1]):
            with self.assertRaises(ValueError):
                paired_lanes(cores)

    def test_stop_before_counts_preparation_and_checks_both_clocks(self):
        initial = datetime(2026, 11, 10, tzinfo=timezone.utc)
        for horizon in (600, 1200):
            deadline = initial + timedelta(seconds=horizon + 90 + 180)
            with patch('ms58_lera_eps_pairs.datetime') as clock, \
                    patch('ms58_lera_eps_pairs.time.monotonic', return_value=100.):
                clock.now.return_value = initial
                self.assertEqual(launch_timeout(deadline, 100. + horizon + 90 + 180, horizon), horizon + 90)
                clock.now.return_value = initial + timedelta(seconds=151)
                with self.assertRaisesRegex(ValueError, 'Stop-before'):
                    launch_timeout(deadline, 100. + horizon + 90 + 180, horizon)
                clock.now.return_value = initial
                with self.assertRaisesRegex(ValueError, 'Stop-before'):
                    launch_timeout(deadline, 100. + horizon + 90 + 29, horizon)

    def test_source_build_rejects_changed_and_unrecorded_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            path = source / 'frozen.cpp'
            path.write_text('frozen source')
            identity = {'files': {'frozen.cpp': sha256(path)}}
            verify_source(source, identity)
            (source / 'unexpected.cpp').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'unrecorded files'):
                verify_source(source, identity)
            (source / 'unexpected.cpp').unlink()
            path.write_text('changed source')
            with self.assertRaisesRegex(ValueError, 'differs from pinned archive'):
                verify_source(source, identity)

    def test_expected_identity_matches_the_actual_launcher_at_both_horizons(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'frozen-scripts'
            scripts.mkdir()
            for name in ['upstream_event_worker.py', 'run_solver_probe.py', 'probe_runner.py',
                         'validate_probe.py', 'checker_contract.py']:
                (scripts / name).write_text('fixture\n')
            binary, patch_file, atf = [root / name for name in ['bp', 'source.patch', 'atf.json']]
            for path in [binary, patch_file, atf]:
                path.write_text('fixture\n')
            instance = root / 'C104.vrp.json'
            instance.write_text(json.dumps({'instance_name': 'C104', 'td': {'atf_path': 'atf.json'}}))
            manifest = {'schema_version': 1, 'checker': {'fixture': True}, 'runtime_kind': 'native',
                'runtime_files': {str(binary): sha256(binary)},
                'source_patch_files': {str(patch_file): sha256(patch_file)},
                'worker_environment': {'UPSTREAM_PROBE_BINARY': str(binary), 'UPSTREAM_SOURCE_ROOT': str(root)}}
            manifest_path = root / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest))
            core = min(os.sched_getaffinity(0))
            for horizon in (600, 1200):
                task = {'instance_name': 'C104', 'instance_sha256': sha256(instance), 'atf_sha256': sha256(atf),
                    'cpu_core': core, 'replicate': 2, 'instance_path': str(instance), 'horizon_s': horizon}
                expected = expected_identity(task, manifest, scripts, root)
                args = ['run_solver_probe', '--manifest', str(manifest_path), '--worker-python', sys.executable,
                    '--worker', str(scripts / 'upstream_event_worker.py'), '--instance', str(instance),
                    '--output', str(root / 'cells'), '--arm', 'lera-exact', '--horizon', str(horizon),
                    '--memory-mb', '16384', '--cpu-core', str(core), '--repetition', '2']
                with patch.object(sys, 'argv', args), patch.object(run_solver_probe, '__file__', str(scripts / 'run_solver_probe.py')), \
                        patch.object(run_solver_probe, 'checker_identity', return_value={'fixture': True}), \
                        patch.object(run_solver_probe, 'run_probe', return_value={}) as probe, \
                        contextlib.redirect_stdout(io.StringIO()):
                    run_solver_probe.main()
                captured = probe.call_args.kwargs
                actual = {'schema_version': 1, 'manifest_hash': digest(captured['manifest']),
                    'parameters': captured['parameters'], 'command': captured['command'],
                    'horizon_s': captured['horizon_s'], 'cwd': str(captured['cwd']),
                    'environment_hash': digest(captured['environment'])}
                self.assertEqual(expected, actual)

    def test_archived_minimal_native_backend_and_work_are_readable(self):
        output = Path(__file__).resolve().parents[1] / 'outputs/ms58/resume-20261110/minimal-reference'
        identities = list((output / 'cells').glob('*/identity.json'))
        if not identities:
            self.skipTest('Local archived minimal-reference artifacts are unavailable')
        for path in identities:
            identity = json.loads(path.read_bytes())
            expected = {k: v for k, v in identity.items() if k not in ('manifest', 'run_id')}
            result = json.loads(path.with_name('result.json').read_bytes())
            events = [json.loads(line) for line in path.with_name('events.jsonl').read_text().splitlines()]
            launch = next(e for e in events if e.get('phase') == 'native_launch')
            task = {**identity['parameters'], 'horizon_s': identity['horizon_s'],
                'cpu_core': identity['parameters']['cpu_affinity'][0],
                'native_instance_sha256': launch['native_instance_sha256'],
                'native_parameters': {k: v for k, v in launch['parameters'].items() if k != 'time_limit'}}
            observed = audit(task, output, result, expected)
            self.assertTrue(observed['audit_ok'], observed['checks'])
            self.assertTrue(observed['native_work']['available'])

    def test_native_parameter_audit_failure_removes_closure_and_retains_raw_proof(self):
        output = Path(__file__).resolve().parents[1] / 'outputs/ms58/resume-20261110/minimal-reference'
        identities = list((output / 'cells').glob('*/identity.json'))
        if not identities:
            self.skipTest('Local archived minimal-reference artifacts are unavailable')
        for path in identities:
            identity = json.loads(path.read_bytes())
            expected = {k: v for k, v in identity.items() if k not in ('manifest', 'run_id')}
            result = json.loads(path.with_name('result.json').read_bytes())
            events = [json.loads(line) for line in path.with_name('events.jsonl').read_text().splitlines()]
            launch = next(e for e in events if e.get('phase') == 'native_launch')
            task = {**identity['parameters'], 'horizon_s': identity['horizon_s'],
                'cpu_core': identity['parameters']['cpu_affinity'][0],
                'native_instance_sha256': launch['native_instance_sha256'],
                'native_parameters': {'incorrect-contract': True}}
            observed = audit(task, output, result, expected)
            self.assertFalse(observed['audit_ok'])
            self.assertFalse(observed['checks']['native_parameters_match'])
            self.assertIsNone(observed['reported_closure_s'])
            self.assertEqual(observed['capped_reported_closure_s'], task['horizon_s'])
            self.assertIsNotNone(observed['raw_reported_closure_s'])
            self.assertTrue(observed['raw_closure_claims'])
            self.assertTrue(observed['checked_candidates'])


if __name__ == '__main__':
    unittest.main()
