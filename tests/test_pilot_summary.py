"""Check schedule association independently of solver/checker execution."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from pilot_summary import assert_task_result_identity, assert_scheduled_row, digest


class PilotIdentityTests(unittest.TestCase):
    def fixture(self, repetition=1):
        pin = 'a' * 64
        reference = {'instance_name': 'C101', 'instance_sha256': 'b' * 64,
                     'atf_file_sha256': 'c' * 64, 'reference_cost': 100.}
        wave = {'wave_name': 'pilot', 'wave_id': 'frozen-wave', 'horizon_s': 3600,
                'reference_manifest_sha256': pin, 'selection_sha256': 'd' * 64}
        manifest = {**wave, 'schema_version': 1}
        task = {'task_id': 'C101-full-rep' + str(repetition), 'instance_name': 'C101',
                'arm': 'kayros-full-cplex', 'prefix_s': 15., 'replicate': repetition,
                'seed': 42, 'horizon_s': 3600, 'instance_sha256': reference['instance_sha256'],
                'reference_cost': 100., 'cpu_core': 2, 'memory_mb': 8192,
                'output_directory': '/remote/wave/cells'}
        identity = {'schema_version': 1, 'manifest': manifest, 'manifest_hash': digest(manifest),
                    'horizon_s': 3600, 'command': ['python', 'worker.py'],
                    'environment_hash': 'e' * 64, 'cwd': '/remote/wave',
                    'parameters': {'instance_name': 'C101', 'arm': task['arm'],
                                   'prefix_seconds': 15., 'repetition': repetition,
                                   'search_seed': 42, 'instance_sha256': reference['instance_sha256'],
                                   'atf_sha256': reference['atf_file_sha256'], 'cpu_affinity': [2],
                                   'memory_mb': 8192, 'memory_cap_bytes': 8192 * 1024 ** 2}}
        row = dict(task)
        result = {'schema_version': 1}
        self.resign(identity, row, result)
        return dict(task=task, row=row, identity=identity, result=result, wave=wave,
                    reference=reference, expected_reference_manifest_sha256=pin,
                    scheduled_manifest=deepcopy(manifest))

    def resign(self, identity, row, result):
        identity['manifest_hash'] = digest(identity['manifest'])
        run_id = digest({k: v for k, v in identity.items() if k not in ('run_id', 'manifest')})
        identity['run_id'] = row['run_id'] = result['run_id'] = run_id
        result['identity_hash'] = run_id
        result['manifest_hash'] = identity['manifest_hash']
        row['result_path'] = '/remote/wave/cells/' + run_id + '/result.json'

    def test_matching_cell_and_historical_trial_zero(self):
        for repetition in (0, 1, 2, 3):
            with self.subTest(repetition=repetition):
                assert_task_result_identity(**self.fixture(repetition))

    def test_swapped_repetition_prefix_and_seed_refused_after_valid_rehash(self):
        for field, value in [('repetition', 2), ('prefix_seconds', 60.), ('search_seed', 123)]:
            with self.subTest(field=field):
                data = self.fixture()
                data['identity']['parameters'][field] = value
                self.resign(data['identity'], data['row'], data['result'])
                with self.assertRaisesRegex(ValueError, field):
                    assert_task_result_identity(**data)

    def test_swapped_row_association_refused(self):
        for field, value in [('task_id', 'another-cell'), ('replicate', 2), ('prefix_s', 60.), ('seed', 123)]:
            with self.subTest(field=field):
                data = self.fixture()
                data['row'][field] = value
                with self.assertRaises(ValueError):
                    assert_task_result_identity(**data)

    def test_failed_row_still_must_match_schedule(self):
        with self.assertRaisesRegex(ValueError, 'replicate'):
            assert_scheduled_row({'task_id': 'cell', 'replicate': 1},
                                 {'task_id': 'cell', 'replicate': 2, 'status': 'launch_failure'})

    def test_other_wave_and_reference_refused(self):
        for field, value in [('wave_id', 'other-wave'), ('reference_manifest_sha256', 'f' * 64)]:
            with self.subTest(field=field):
                data = self.fixture()
                data['identity']['manifest'][field] = value
                data['scheduled_manifest'][field] = value
                self.resign(data['identity'], data['row'], data['result'])
                with self.assertRaisesRegex(ValueError, field):
                    assert_task_result_identity(**data)

    def test_manifest_digest_and_result_path_refused(self):
        data = self.fixture()
        data['scheduled_manifest']['purpose'] = 'another immutable configuration'
        with self.assertRaisesRegex(ValueError, 'Scheduled manifest'):
            assert_task_result_identity(**data)
        data = self.fixture()
        data['row']['result_path'] = '/remote/wave/cells/another-run/result.json'
        with self.assertRaisesRegex(ValueError, 'result path'):
            assert_task_result_identity(**data)

    def test_legacy_native_manifest_without_optional_reference_fields(self):
        data = self.fixture(0)
        for field in ('wave_id', 'reference_manifest_sha256', 'selection_sha256'):
            data['identity']['manifest'].pop(field)
            data['scheduled_manifest'].pop(field)
        data['wave'].pop('wave_id')
        self.resign(data['identity'], data['row'], data['result'])
        assert_task_result_identity(**data)


if __name__ == '__main__':
    unittest.main()
