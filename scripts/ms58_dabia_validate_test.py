"""Offline audit regressions. These tests never launch a solver."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from ms58_analyze_pairs import EXPECTED_CHECKER
from ms58_dabia_validate import VARIANTS, audit_probe, pooled_case_consistency


class ValidationAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = {'key': 'n25-C201', 'instance': 'C201', 'input_sha256': 'input',
                     'atf_sha256': 'atf', 'horizon_s': 60, 'coupling': False}
        witness = self.root / 'n25-C201-witness'
        witness.mkdir()
        self.write(witness / 'witness.json', {'valid': True, 'checked_cost': 101, 'routes': [[1]]})

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def probe(self, name, cost=100, closed=True, settings=True, late_bound=None):
        directory = self.root / name
        directory.mkdir()
        identity = {'run_id': name, 'manifest_hash': 'manifest', 'schema_version': 1, 'horizon_s': 60}
        self.write(directory / 'identity.json', identity)
        events = [{'sequence': 0, 'elapsed_s': 1, 'event': 'candidate', 'routes': [[1]],
                   'backend': 'CPLEX'}]
        if settings:
            events[0]['effective_algorithm_settings'] = {'label_correction': True, 'lazy_extension': True}
        if closed:
            events.append({'sequence': 1, 'elapsed_s': 2, 'event': 'closure', 'global_lb': cost,
                           'status': 'Optimum'})
        (directory / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        late = [] if late_bound is None else [{**identity, 'sequence': 0, 'elapsed_s': 61,
            'event': 'bound', 'global_lb': late_bound, 'excluded_reason': 'after_horizon'}]
        (directory / 'late-events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in late))
        validation = {'checker': EXPECTED_CHECKER, 'instance_name': 'C201', 'instance_sha256': 'input',
            'atf_sha256': 'atf', 'proof': {'status': 'reported' if closed else 'open'},
            'candidates': [{'sequence': 0, 'elapsed_s': 1, 'valid': True, 'checked_cost': cost,
                            'native_minus_checked': 0, 'routes': [[1]]}]}
        result = {'status': 'completed', 'issues': [], 'validation': validation}
        audit = audit_probe(result, directory, 101, 'candidate', 'correction-on', expected_case=self.case)
        audit['expected_run_id'] = name
        return audit, result, directory

    def pooled(self, candidate, control):
        return pooled_case_consistency(self.case, {'candidate': candidate, 'minimal-lera': control,
            'witness_checked_cost': 101}, self.root, EXPECTED_CHECKER,
            VARIANTS['correction-on']['source_id'])

    def test_missing_settings_fails(self):
        audit, _, _ = self.probe('candidate', settings=False)
        self.assertFalse(audit['all_checks_passed'])
        self.assertFalse(audit['checks']['algorithm_setting_label_correction'])
        self.assertFalse(audit['checks']['algorithm_setting_lazy_extension'])

    def test_equal_feasible_closures_pass(self):
        candidate, _, _ = self.probe('candidate')
        control, _, _ = self.probe('control')
        self.assertTrue(self.pooled(candidate, control)['all_claims_usable'])

    def test_better_open_control_refutes_candidate_closure(self):
        candidate, _, _ = self.probe('candidate')
        control, _, _ = self.probe('control', cost=99, closed=False)
        result = self.pooled(candidate, control)
        self.assertFalse(result['all_claims_usable'])
        self.assertEqual(result['feasible_ceilings']['n25-C201']['cost'], 99)
        self.assertIsNone(result['evidence_cells'][0]['reported_closure_s'])

    def test_better_open_candidate_refutes_control_closure(self):
        candidate, _, _ = self.probe('candidate', cost=99, closed=False)
        control, _, _ = self.probe('control')
        self.assertFalse(self.pooled(candidate, control)['all_claims_usable'])

    def test_input_mismatch_is_rejected(self):
        _, result, directory = self.probe('candidate')
        result['validation']['atf_sha256'] = 'other-atf'
        with self.assertRaisesRegex(ValueError, 'another input'):
            audit_probe(result, directory, 101, 'candidate', 'correction-on', expected_case=self.case)

    def test_late_bound_contradiction_is_retained(self):
        candidate, _, _ = self.probe('candidate', late_bound=102)
        control, _, _ = self.probe('control')
        result = self.pooled(candidate, control)
        self.assertFalse(result['all_claims_usable'])
        claims = result['evidence_cells'][0]['consistency_audit']['contradictions']
        self.assertTrue(any(c.get('timing_scope') == 'after_horizon' for c in claims))

    def coupling(self, cost=99, bound=99, status='Optimum'):
        self.case['coupling'] = True
        directory = self.root / 'n25-C201-coupling'
        directory.mkdir()
        identity = {'checker': EXPECTED_CHECKER, 'instance': 'C201', 'input_sha256': 'input',
            'atf_sha256': 'atf', 'source_id': VARIANTS['correction-on']['source_id']}
        self.write(directory / 'identity.json', identity)
        witness = {'valid': True, 'checked_cost': cost, 'routes': [[1]]}
        for filename in ['witness.json', 'ils-before.json', 'ils-after.json']:
            self.write(directory / filename, witness)
        self.write(directory / 'summary.json', {'returned': witness, 'all_checks_passed': True})
        self.write(directory / 'native-result.json', {'exact_log': {'status': status, 'best_bound': bound}})
        (directory / 'events.jsonl').write_text('')

    def test_coupling_witness_refutes_both_cold_closures(self):
        candidate, _, _ = self.probe('candidate')
        control, _, _ = self.probe('control')
        self.coupling()
        result = self.pooled(candidate, control)
        self.assertFalse(result['all_claims_usable'])
        self.assertEqual(result['feasible_ceilings']['n25-C201']['cost'], 99)
        self.assertEqual(result['feasible_ceilings']['n25-C201']['evidence_kind'],
                         'independently_checked_coupling_witness')

    def test_coupling_optimum_without_bound_fails(self):
        candidate, _, _ = self.probe('candidate')
        control, _, _ = self.probe('control')
        self.coupling(cost=100, bound=None)
        result = self.pooled(candidate, control)
        self.assertFalse(result['all_claims_usable'])
        self.assertTrue(result['evidence_cells'][-1]['consistency_audit']['unsupported_closure_claims'])

    def test_coupling_input_mismatch_is_rejected(self):
        candidate, _, _ = self.probe('candidate')
        control, _, _ = self.probe('control')
        self.coupling(cost=100, bound=100)
        path = self.root / 'n25-C201-coupling/identity.json'
        identity = json.loads(path.read_bytes())
        identity['input_sha256'] = 'other-input'
        self.write(path, identity)
        with self.assertRaisesRegex(ValueError, 'another checker, input or source'):
            self.pooled(candidate, control)


if __name__ == '__main__':
    unittest.main()
