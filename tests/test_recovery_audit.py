"""Synthetic recovery fixtures only. No solver or routing cost is evaluated."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_wave_recovery import audit_wave, sha
from probe_runner import canonical, digest, run_probe


class RecoveryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.wave = self.root / 'wave'
        for name in ['frozen-scripts', 'frozen-patches', 'manifests', 'rows', 'cells']:
            (self.wave / name).mkdir(parents=True, exist_ok=True)
        self.frozen = self.wave / 'frozen-scripts'
        for name in ['development_wave.py', 'run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py']:
            (self.frozen / name).write_text('# synthetic fixture\n')
        self.worker = self.frozen / 'kayros_worker.py'
        self.worker.write_text("import json; print(json.dumps(dict(event='candidate',elapsed_s=0,routes=[[1]],native_cost=11.0)),flush=True)\n")
        patch = self.wave / 'frozen-patches/fixture.patch'
        patch.write_text('synthetic fixture')
        self.instance = self.root / 'fixture.json'
        self.instance.write_text(json.dumps({'instance_name': 'fixture', 'td': {'atf_path': 'atf.json'}}))
        self.atf = self.root / 'atf.json'
        self.atf.write_text('{}')
        self.checker = {'version': 'synthetic fixture only'}
        ref = {'instance_name': 'fixture', 'instance_sha256': sha(self.instance),
               'atf_file_sha256': sha(self.atf), 'reference_cost': 10.0, 'reference_cost_hex': (10.0).hex()}
        self.reference = self.root / 'reference.json'
        self.reference.write_bytes(canonical({'checker': self.checker, 'instances': [ref]}))
        self.manifest = {'schema_version': 1, 'wave_id': 'a' * 32, 'wave_name': 'fixture',
            'checker': self.checker, 'reference_manifest_sha256': sha(self.reference),
            'runtime_files': {str(self.worker): sha(self.worker)}, 'source_patch_files': {str(patch): sha(patch)}}
        self.manifest_path = self.wave / 'manifests/fixture.json'
        self.manifest_path.write_bytes(canonical(self.manifest))
        core = min(os.sched_getaffinity(0))
        self.task = {'task_id': 'fixture--kayros-heuristics--rep1', 'wave_id': 'a' * 32,
            'instance_name': 'fixture', 'instance_path': str(self.instance), 'instance_sha256': sha(self.instance),
            'reference_cost': 10.0, 'arm': 'kayros-heuristics', 'prefix_s': None, 'replicate': 1,
            'seed': 42, 'horizon_s': 1, 'cpu_core': core, 'host': 'fixture-host', 'memory_mb': 8192,
            'manifest_path': str(self.manifest_path), 'output_directory': str(self.wave / 'cells'),
            'worker_python': sys.executable, 'worker_path': None}
        self.spec = {'schema_version': 1, 'wave_id': 'a' * 32, 'wave_name': 'fixture',
            'scheduled_count': 1, 'scheduled_cells': [self.task], 'horizon_s': 1,
            'reference_manifest_sha256': sha(self.reference), 'driver_sha256': sha(self.frozen / 'development_wave.py'),
            'repetition_slots': [{'replicate': 1, 'paired_heuristic_seed': 42}]}
        (self.wave / 'wave.json').write_bytes(canonical(self.spec))
        (self.wave / 'allocated-nodefile.txt').write_text('fixture-host\n')
        self.anchor = sha(self.wave / 'wave.json')
        params = {'instance_name': 'fixture', 'instance_sha256': sha(self.instance), 'atf_sha256': sha(self.atf),
            'arm': 'kayros-heuristics', 'prefix_seconds': None, 'repetition': 1, 'search_seed': 42,
            'cpu_affinity': [core], 'memory_mb': 8192, 'memory_cap_bytes': 8192 * 1024**2,
            'orchestration_files': {name: sha(self.frozen / name) for name in ['run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py']},
            'worker_sha256': sha(self.worker), 'worker_python_sha256': sha(Path(sys.executable))}
        def validate(events):
            event = events[0]
            point = {'sequence': 0, 'elapsed_s': 0, 'routes': [[1]], 'checked_cost': 10.0}
            candidate = {**point, 'native_cost': 11.0, 'native_minus_checked': 1.0,
                         'valid': True, 'checker_evidence': {'status': 'valid', 'routing_cost': 10.0}}
            return {'checker': self.checker, 'instance_name': 'fixture', 'instance_sha256': sha(self.instance),
                    'atf_sha256': sha(self.atf), 'candidates': [candidate], 'checked_envelope': [point],
                    'best_checked_cost': 10.0, 'best_routes': [[1]], 'proof': {'status': 'no_closure_claim'}}
        environment = dict(os.environ)
        command = [sys.executable, str(self.worker), '--instance', str(self.instance), '--arm', 'kayros-heuristics', '--memory-mb', '8192', '--seed', '42']
        native_name = digest({'manifest': self.manifest, 'parameters': params, 'horizon_s': 1, 'command': command, 'environment_hash': digest(environment), 'cwd': str(self.wave)}) + '.json'
        command += ['--native-result', str(self.wave / 'cells/native' / native_name)]
        self.result = run_probe(output=self.wave / 'cells', manifest=self.manifest, parameters=params,
            command=command, horizon_s=1, cwd=self.wave,
            environment=environment, validate=validate)
        self.cell = self.wave / 'cells' / self.result['run_id']
        self.row = {**self.task, 'run_id': self.result['run_id'], 'status': self.result['status'],
                    'result_path': str(self.cell / 'result.json')}
        self.row_path = self.wave / 'rows' / (self.task['task_id'] + '.json')
        self.row_path.write_bytes(canonical(self.row))

    def tearDown(self):
        self.temporary.cleanup()

    def audit(self):
        return audit_wave(self.wave, expected_wave_sha256=self.anchor, reference_manifest=self.reference,
                          manifest_anchors={'fixture.json': digest(self.manifest)})

    def test_completed_verified_without_any_file_mutation(self):
        before = {str(path): sha(path) for path in self.root.rglob('*') if path.is_file()}
        report = self.audit()
        after = {str(path): sha(path) for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(report['tasks'][0]['state'], 'verified_terminal')
        self.assertIsNone(report['tasks'][0]['automatic_action'])

    def test_missing_row_is_reported_reconstructable_without_writing(self):
        self.row_path.unlink()
        self.assertEqual(self.audit()['tasks'][0]['state'], 'missing_row_reconstructable')
        self.assertFalse(self.row_path.exists())

    def test_incomplete_claim_is_never_retried(self):
        self.row_path.unlink()
        (self.cell / 'result.json').unlink()
        row = self.audit()['tasks'][0]
        self.assertEqual(row['state'], 'incomplete_claim_needs_recovery')
        self.assertIn('may still be running', row['process_state'])
        self.assertIsNone(row['automatic_action'])

    def test_corrupt_envelope_is_rejected(self):
        record = json.loads((self.cell / 'result.json').read_bytes())
        record['validation']['checked_envelope'][0]['checked_cost'] = 1.0
        (self.cell / 'result.json').write_bytes(canonical(record))
        row = self.audit()['tasks'][0]
        self.assertEqual(row['state'], 'identity_or_artifact_error')
        self.assertIn('envelope mismatch', row['reason'])

    def test_runtime_drift_is_rejected(self):
        self.worker.write_text('changed')
        self.assertEqual(self.audit()['tasks'][0]['state'], 'identity_or_artifact_error')

    def test_swapped_repetition_row_is_rejected(self):
        self.row_path.write_bytes(canonical({**self.row, 'replicate': 2}))
        self.assertEqual(self.audit()['tasks'][0]['state'], 'identity_or_artifact_error')

    def test_failed_completed_outcome_remains_terminal(self):
        record = json.loads((self.cell / 'result.json').read_bytes())
        record.update(status='failed', returncode=1)
        (self.cell / 'result.json').write_bytes(canonical(record))
        self.row_path.write_bytes(canonical({**self.row, 'status': 'failed'}))
        report = self.audit()['tasks'][0]
        self.assertEqual(report['state'], 'verified_terminal')
        self.assertEqual(report['observed_status'], 'failed')
        self.assertIsNone(report['automatic_action'])

    def test_rehashed_cli_seed_disagreement_is_rejected(self):
        identity = json.loads((self.cell / 'identity.json').read_bytes())
        identity['command'] += ['--unexpected-option']
        core = {key:value for key,value in identity.items() if key not in ['run_id','manifest']}
        new_id = digest(core)
        identity['run_id'] = new_id
        events = [json.loads(line) for line in (self.cell / 'events.jsonl').read_bytes().splitlines()]
        for event in events:
            event['run_id'] = new_id
        raw = b''.join(canonical(event) + b'\n' for event in events)
        (self.cell / 'events.jsonl').write_bytes(raw)
        import hashlib
        record = json.loads((self.cell / 'result.json').read_bytes())
        record.update(run_id=new_id, identity_hash=new_id, events_sha256=hashlib.sha256(raw).hexdigest())
        (self.cell / 'identity.json').write_bytes(canonical(identity))
        (self.cell / 'result.json').write_bytes(canonical(record))
        new_cell = self.cell.with_name(new_id)
        self.cell.rename(new_cell)
        self.row_path.write_bytes(canonical({**self.row, 'run_id':new_id, 'result_path':str(new_cell / 'result.json')}))
        row = self.audit()['tasks'][0]
        self.assertEqual(row['state'], 'identity_or_artifact_error')
        self.assertIn('worker command', row['reason'])

    def test_ambiguous_matching_attempts_are_not_selected(self):
        import shutil
        duplicate = self.cell.with_name('untrusted-second-attempt')
        shutil.copytree(self.cell, duplicate)
        report = self.audit()['tasks'][0]
        self.assertEqual(report['state'], 'ambiguous_attempts')
        self.assertIsNone(report['automatic_action'])

    def test_wave_anchor_rejects_changed_schedule(self):
        (self.wave / 'wave.json').write_bytes(canonical({**self.spec, 'wave_name': 'changed'}))
        with self.assertRaisesRegex(ValueError, 'External wave'):
            self.audit()


if __name__ == '__main__':
    unittest.main()
