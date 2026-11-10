"""Offline allocation and cohort checks. No OAR process or solver is launched."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from probe_runner import digest
import ms58_dabia_preflight as preflight


class PreflightTests(unittest.TestCase):
    def hosts(self):
        return [f'gros-{i}.nancy.grid5000.fr' for i in range(1, 46)]

    def snapshots(self):
        identity = {'hardware': {'cpu_model': 'frozen-model', 'kernel': 'frozen-kernel'},
                    'runtimes': {'cplex': {'extension_sha256': 'frozen-extension'}}}
        return [{'status': 'passed', 'requested_host': host, 'observed_hostname': host.split('.')[0],
            'oar_job_id': '123', 'memory_kib': {'MemAvailable': preflight.MIN_AVAILABLE_KIB},
            'cohort_identity': copy.deepcopy(identity), 'cohort_sha256': digest(identity),
            'allowed_cpu_ids': list(range(8)), 'selected_physical_slots': [
                {'cpu_core': i, 'physical_package_id': 0, 'physical_core_id': i,
                 'thread_siblings_list': f'{i},{i+4}', 'numa_nodes': [0]} for i in range(4)]}
            for host in self.hosts()]

    def test_real_nodefile_host_denominator_repetitions_and_aliases(self):
        raw = '\n'.join(host for host in self.hosts() for _ in range(18))
        self.assertEqual(len(preflight.nodefile_hosts(raw)), 45)
        for bad in ['\n'.join(self.hosts()[:-1]), '\n'.join(self.hosts() + [self.hosts()[0].split('.')[0]]),
                    '\n'.join(self.hosts()[:-1] + ['bad;hostname'])]:
            with self.assertRaises(ValueError):
                preflight.nodefile_hosts(bad)

    def test_exact_180_slot_success(self):
        slots = preflight.cohort_slots(self.snapshots(), self.hosts(), '123')
        self.assertEqual(len(slots), 180)
        self.assertEqual(len({(row['host'], row['cpu_core']) for row in slots}), 180)

    def test_missing_unreachable_wrong_job_and_low_ram_fail_whole_cohort(self):
        for field, value in [('status', 'failed'), ('oar_job_id', '456'), ('observed_hostname', 'wrong-node'),
                             ('memory_kib', {'MemAvailable': preflight.MIN_AVAILABLE_KIB - 1})]:
            rows = self.snapshots()
            rows[0][field] = value
            with self.assertRaises(ValueError):
                preflight.cohort_slots(rows, self.hosts(), '123')
        with self.assertRaises(ValueError):
            preflight.cohort_slots(self.snapshots()[:-1], self.hosts(), '123')

    def test_hardware_kernel_and_binary_drift_rejected(self):
        for field in ('cpu_model', 'kernel'):
            rows = self.snapshots()
            rows[0]['cohort_identity']['hardware'][field] = 'different'
            rows[0]['cohort_sha256'] = digest(rows[0]['cohort_identity'])
            with self.assertRaisesRegex(ValueError, 'Heterogeneous'):
                preflight.cohort_slots(rows, self.hosts(), '123')
        rows = self.snapshots()
        rows[0]['cohort_identity']['runtimes']['cplex']['extension_sha256'] = 'different'
        rows[0]['cohort_sha256'] = digest(rows[0]['cohort_identity'])
        with self.assertRaisesRegex(ValueError, 'Heterogeneous'):
            preflight.cohort_slots(rows, self.hosts(), '123')

    def test_loader_addresses_are_retained_but_excluded_from_identity(self):
        one = {'upstream': {'native_dependencies': {'inspection': 'lib => /lib (0x123)',
                                                   'resolved_libraries': {'/lib': 'sha'}}}}
        two = copy.deepcopy(one)
        two['upstream']['native_dependencies']['inspection'] = 'lib => /lib (0x789)'
        self.assertEqual(preflight.cohort_runtime_identity(one), preflight.cohort_runtime_identity(two))
        self.assertIn('inspection', one['upstream']['native_dependencies'])

    def test_select_four_physical_cores_inside_actual_affinity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for cpu in range(8):
                base = root / f'cpu{cpu}/topology'
                base.mkdir(parents=True)
                core = cpu % 4
                for name, value in [('physical_package_id', '0'), ('core_id', str(core)),
                                    ('thread_siblings_list', f'{core},{core+4}')]:
                    (base / name).write_text(value)
                (base.parent / 'node0').mkdir()
            selected = preflight.select_physical_slots({1, 2, 3, 4, 5, 6, 7}, root)
            self.assertEqual([row['cpu_core'] for row in selected], [1, 2, 3, 4])
            self.assertTrue(all(row['numa_nodes'] == [0] for row in selected))
            with self.assertRaisesRegex(ValueError, 'Fewer than 4'):
                preflight.select_physical_slots({0, 1, 4, 5}, root)

    def test_sibling_and_outside_affinity_slots_rejected(self):
        for mutation in ('sibling', 'outside'):
            rows = self.snapshots()
            slot = rows[0]['selected_physical_slots'][1]
            if mutation == 'sibling':
                slot['physical_core_id'] = 0
            else:
                slot['cpu_core'] = 999
            with self.assertRaises(ValueError):
                preflight.cohort_slots(rows, self.hosts(), '123')


if __name__ == '__main__':
    unittest.main()
