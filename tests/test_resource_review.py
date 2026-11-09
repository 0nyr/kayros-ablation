"""Identity joins and missing-data semantics, without performance measurements."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from resource_review import associate_processes


class ResourceReviewTests(unittest.TestCase):
    def specification(self, task='A'):
        return {'task_id': task, 'run_id': 'run-' + task, 'host': 'node-1.site',
                'cap_bytes': 16 * 1024 ** 3, 'native_result_path': '/native/unrelated-stem-' + task + '.json',
                'command': ['/python', '/worker.py', '--native-result', '/native/unrelated-stem-' + task + '.json']}

    def snapshot(self, spec):
        def member(pid, role, peak):
            return {'pid': pid, 'role': role, 'VmPeak_kib': peak, 'VmSize_kib': peak // 2,
                    'VmRSS_kib': peak // 4, 'rlimit_as_soft_bytes': str(spec['cap_bytes']),
                    'rlimit_as_hard_bytes': str(spec['cap_bytes'])}
        return {'measurement_revision': 4, 'host': 'node-1', 'processes': [{
            'worker_pid': 10, 'worker_argv': spec['command'], 'native_result_path': spec['native_result_path'],
            'member_maxima': {'10': member(10, 'worker', 2), '11': member(11, 'descendant', 7)}}]}

    def test_exact_command_path_not_run_id_and_no_virtual_sum(self):
        spec = self.specification()
        cells, unmatched = associate_processes([spec], [self.snapshot(spec)])
        self.assertEqual(unmatched, [])
        observation = cells[0]['observation']
        self.assertEqual(observation['maximum_observed_individual_vm_peak_kib'], 7)
        self.assertEqual(observation['peak_owner_pid'], 11)
        self.assertEqual(observation['peak_owner_role'], 'descendant')

    def test_missing_and_same_basename_elsewhere_do_not_become_zero(self):
        spec = self.specification()
        snapshot = self.snapshot(spec)
        process = snapshot['processes'][0]
        process['native_result_path'] = '/other/' + Path(spec['native_result_path']).name
        process['worker_argv'] = ['/python', '/worker.py', '--native-result', process['native_result_path']]
        cells, unmatched = associate_processes([spec, self.specification('B')], [snapshot])
        self.assertTrue(all(cell['observation'] is None for cell in cells))
        self.assertEqual(len(unmatched), 1)

    def test_matching_path_on_wrong_host_is_refused(self):
        spec = self.specification()
        snapshot = self.snapshot(spec)
        snapshot['host'] = 'node-2'
        with self.assertRaisesRegex(ValueError, 'host'):
            associate_processes([spec], [snapshot])


if __name__ == '__main__':
    unittest.main()
