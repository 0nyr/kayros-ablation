"""Offline binding checks for the inspected final allocation and frozen wave."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from checker_contract import sha256
from probe_runner import digest
import ms58_dabia_wave as wave


class AllocationBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.preflight = self.root / 'preflight'
        self.destination = self.root / 'wave'
        self.destination.mkdir()
        self.hosts = [f'gros-{i}.nancy.grid5000.fr' for i in range(1, 46)]
        self.checker = {'checker': 'pinned'}
        self.design = {'kayros_source_id': wave.FINAL_SOURCE, 'selection': ['frozen-selection']}
        self.write(self.preflight / 'design.json', self.design)
        (self.preflight / 'allocated-nodefile.txt').write_text('\n'.join(self.hosts))
        plan = {'oar_job_id': '123', 'hosts': self.hosts, 'checker': self.checker,
            'design_sha256': sha256(self.preflight / 'design.json'), 'frozen_scripts_sha256': {},
            'nodefile_sha256': sha256(self.preflight / 'allocated-nodefile.txt')}
        self.write(self.preflight / 'plan.json', plan)
        identity = {'hardware': {'model': 'frozen'}, 'runtimes': {'cplex': 'pinned'}}
        self.slots = []
        hashes = {}
        for host in self.hosts:
            snapshot = {'status': 'passed', 'requested_host': host, 'observed_hostname': host,
                'oar_job_id': '123', 'plan_sha256': sha256(self.preflight / 'plan.json'),
                'memory_kib': {'MemAvailable': 80 * 1024**2}, 'cohort_identity': identity,
                'cohort_sha256': digest(identity), 'allowed_cpu_ids': list(range(4)),
                'selected_physical_slots': [{'cpu_core': cpu, 'physical_package_id': 0,
                    'physical_core_id': cpu} for cpu in range(4)]}
            path = self.write(self.preflight / 'snapshots' / f'{host}.json', snapshot)
            hashes[path.name] = sha256(path)
        for host in sorted(self.hosts):
            self.slots.extend({'host': host, 'cpu_core': cpu} for cpu in range(4))
        self.write(self.preflight / 'slots.json', self.slots)
        self.write(self.destination / 'slots.json', self.slots)
        self.complete = {'all_checks_passed': True, 'oar_job_id': '123',
            'expected_hosts': 45, 'expected_slots': 180, 'snapshot_count': 45,
            'snapshot_sha256': hashes, 'plan_sha256': sha256(self.preflight / 'plan.json'),
            'slots_sha256': sha256(self.preflight / 'slots.json'), 'cohort_sha256': digest(identity)}
        self.pin_complete()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def pin_complete(self):
        path = self.write(self.preflight / 'complete.json', self.complete)
        self.design['allocation_preflight'] = {'path': str(path), 'sha256': sha256(path)}
        self.write(self.destination / 'design.json', self.design)

    def freeze(self):
        binding = wave.freeze_allocation_preflight(self.design, self.destination, self.checker)
        return {**self.design, 'checker': self.checker, 'allocation_preflight': binding,
                'scheduled_cells': copy.deepcopy(self.slots)}

    def test_copy_is_self_contained_and_job_host_slot_binding_is_exact(self):
        spec = self.freeze()
        self.write(self.preflight / 'complete.json', {'changed': True})
        wave.verify_allocation_preflight(self.destination, spec, job_id='123', hosts=self.hosts)
        with self.assertRaisesRegex(ValueError, 'Launch job'):
            wave.verify_allocation_preflight(self.destination, spec, job_id='456')
        with self.assertRaisesRegex(ValueError, 'Launch hosts'):
            wave.verify_allocation_preflight(self.destination, spec, hosts=self.hosts[:-1])
        spec['scheduled_cells'][0]['cpu_core'] = 999
        with self.assertRaisesRegex(ValueError, 'every inspected slot'):
            wave.verify_allocation_preflight(self.destination, spec)

    def test_failed_preflight_is_rejected(self):
        self.complete['all_checks_passed'] = False
        self.pin_complete()
        with self.assertRaisesRegex(ValueError, 'did not pass'):
            self.freeze()
    def test_changed_design_is_rejected(self):
        spec = self.freeze()
        self.design['selection'] = ['another-selection']
        self.write(self.destination / 'design.json', self.design)
        with self.assertRaisesRegex(ValueError, 'Wave design differs'):
            wave.verify_allocation_preflight(self.destination, spec)

    def test_changed_snapshot_and_cohort_hash_are_rejected(self):
        spec = self.freeze()
        snapshot = self.destination / 'frozen-preflight/snapshots' / f'{self.hosts[0]}.json'
        self.write(snapshot, {'status': 'changed'})
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            wave.verify_allocation_preflight(self.destination, spec)

    def test_wrong_slots_and_missing_required_binding_are_rejected(self):
        self.write(self.destination / 'slots.json', self.slots[::-1])
        with self.assertRaisesRegex(ValueError, 'Wave slots differ'):
            self.freeze()
        with self.assertRaisesRegex(ValueError, 'requires allocation_preflight'):
            wave.verify_allocation_preflight(self.destination, {'kayros_source_id': wave.FINAL_SOURCE})

    def test_changed_cohort_binding_is_rejected(self):
        spec = self.freeze()
        spec['allocation_preflight']['cohort_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'cohort identity'):
            wave.verify_allocation_preflight(self.destination, spec)


if __name__ == '__main__':
    unittest.main()
