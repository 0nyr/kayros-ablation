"""External feasible references strengthen proof checks without changing trials."""
import copy
import json
from pathlib import Path
import unittest

from ms58_fixed_witness_reaudit import pooled_external_audit, verify_expected


class ExternalCeilingTests(unittest.TestCase):
    def row(self):
        candidate = {'sequence': 0, 'elapsed_s': 0., 'checked_cost': 110.}
        return {'task_id': 'trial', 'instance_name': 'C201', 'instance_key': 'Dabia2013/n=100/C201',
            'instance_sha256': 'input', 'atf_sha256': 'atf', 'reference_cost': 120.,
            'reference_manifest_sha256': 'original-handoff', 'horizon_s': 600.,
            'audit_ok': True, 'finished_task': True, 'raw_result_status': 'completed',
            'reported_closure_s': 5., 'capped_reported_closure_s': 5., 'witness_evidence_audited': True,
            'checked_candidates': [candidate], 'global_bound_claims': [{'global_lb': 110., 'elapsed_s': 5.}],
            'raw_closure_claims': [{'global_lb': 110., 'elapsed_s': 5., 'sequence': 1,
                                    'preceding_checked_candidate': candidate}]}

    def reference(self):
        return {'instance_name': 'C201', 'instance_key': 'Dabia2013/n=100/C201',
            'instance_sha256': 'input', 'atf_sha256': 'atf', 'source_solution_sha256': 'solution',
            'checked_external_witness': {'checked_cost': 100., 'routes_sha256': 'checked-routes'}}

    def test_known_external_reference_refutes_false_closure_and_preserves_input_trial(self):
        row = self.row()
        original = copy.deepcopy(row)
        audited, ceilings = pooled_external_audit([row], [self.reference()])
        self.assertEqual(row, original)
        self.assertEqual(len(audited), 1)
        self.assertIsNone(audited[0]['reported_closure_s'])
        self.assertEqual(audited[0]['raw_reported_closure_s'], 5.)
        self.assertEqual(audited[0]['reference_cost'], 120.)
        self.assertEqual(ceilings[row['instance_key']]['cost'], 100.)
        self.assertEqual(ceilings[row['instance_key']]['source'], 'external_frozen_checked_reference')

    def test_wrong_input_external_reference_is_rejected(self):
        reference = self.reference()
        reference['atf_sha256'] = 'different'
        with self.assertRaises(ValueError):
            pooled_external_audit([self.row()], [reference])

    def test_real_frozen_pilot_expected_identities_remain_valid(self):
        panel = Path(__file__).resolve().parents[1] / 'outputs/ms58/dabia-20261110/fixed-witness-v1'
        if not (panel / 'launch.json').exists():
            self.skipTest('Local pilot artifacts are unavailable')
        plan = json.loads((panel / 'plan.json').read_bytes())
        launch = json.loads((panel / 'launch.json').read_bytes())
        checked = 0
        for task in launch['scheduled_cells']:
            path = panel / 'rows' / f"{task['task_id']}-expected-identity.json"
            if not path.exists():
                continue
            spec = plan['runtimes'][task['runtime_key']]
            manifest = json.loads((panel / 'manifests' / Path(spec['path']).name).read_bytes())
            verify_expected(task, spec, manifest, json.loads(path.read_bytes()), panel)
            checked += 1
        self.assertGreater(checked, 0)


if __name__ == '__main__':
    unittest.main()
