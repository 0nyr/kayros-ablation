"""No-solver regressions for optional termination bounds and strict proof claims."""
import copy
import math
import unittest

from ms58_analyze_pairs import EXPECTED_CHECKER
from ms58_proof_consistency import apply_feasible_consistency, proof_evidence


class OptionalBoundTests(unittest.TestCase):
    def evidence(self, events, late=()):
        inputs = {'instance_name': 'C104', 'instance_sha256': 'instance', 'atf_sha256': 'atf'}
        validation = dict(inputs, checker=EXPECTED_CHECKER, candidates=[])
        return proof_evidence(validation, events, 600, late, expected_inputs=inputs)

    def audit(self, evidence):
        row = {'instance_key': 'Dabia2013/n=100/C104', 'instance_sha256': 'instance',
            'atf_sha256': 'atf', 'reference_cost': 100, 'reference_manifest_sha256': 'reference',
            'task_id': 'fixture', 'horizon_s': 600, 'audit_ok': True, 'finished_task': True,
            'status': 'completed', 'reported_closure_s': None, **evidence}
        apply_feasible_consistency([row])
        return row

    def termination(self, **changes):
        return {'sequence': 0, 'elapsed_s': 599., 'event': 'phase', 'phase': 'exact_terminated',
                'source': 'search_terminated', 'global_lb': None, **changes}

    def test_recognized_null_termination_is_retained_as_unavailable(self):
        event = self.termination()
        before = copy.deepcopy(event)
        evidence = self.evidence([event])
        self.assertEqual(event, before)
        self.assertEqual(evidence['global_bound_claims'], [])
        self.assertEqual(evidence['raw_closure_claims'], [])
        self.assertEqual(len(evidence['unavailable_global_bound_events']), 1)
        unavailable = evidence['unavailable_global_bound_events'][0]
        self.assertEqual(unavailable['global_lb'], None)
        self.assertEqual(unavailable['reason'], 'search_terminated_without_global_lower_bound')
        self.assertEqual(unavailable['timing_scope'], 'in_budget')
        row = self.audit(evidence)
        self.assertEqual(row['consistency_audit']['status'], 'consistent')
        self.assertIsNone(row['reported_closure_s'])

    def test_late_recognized_null_termination_retains_scope(self):
        evidence = self.evidence([], [self.termination(elapsed_s=601.)])
        self.assertEqual(evidence['unavailable_global_bound_events'][0]['timing_scope'], 'after_horizon')
        self.assertEqual(evidence['global_bound_claims'], [])

    def test_unrecognized_null_claims_remain_invalid(self):
        cases = [self.termination(event='bound'), self.termination(event='closure'),
                 self.termination(phase='another_phase'), self.termination(source='another_source')]
        missing_source = self.termination()
        del missing_source['source']
        cases.append(missing_source)
        for event in cases:
            with self.subTest(event=event):
                evidence = self.evidence([event])
                self.assertEqual(evidence['unavailable_global_bound_events'], [])
                self.assertEqual(len(evidence['global_bound_claims']), 1)
                row = self.audit(evidence)
                self.assertEqual(row['consistency_audit']['status'], 'contradicted')
                self.assertEqual(row['consistency_audit']['contradictions'][0]['kind'], 'invalid_global_bound')
                if event['event'] == 'closure':
                    self.assertEqual(len(row['raw_closure_claims']), 1)
                    self.assertEqual(len(row['consistency_audit']['unsupported_in_budget_closure_claims']), 1)

    def test_nonfinite_numeric_values_remain_invalid_for_all_event_kinds(self):
        for bound in (math.inf, -math.inf, math.nan):
            for event_type in ('phase', 'bound', 'closure'):
                with self.subTest(bound=bound, event_type=event_type):
                    evidence = self.evidence([self.termination(event=event_type, global_lb=bound)])
                    self.assertEqual(evidence['unavailable_global_bound_events'], [])
                    self.assertEqual(len(evidence['global_bound_claims']), 1)
                    row = self.audit(evidence)
                    self.assertEqual(row['consistency_audit']['status'], 'contradicted')
                    self.assertEqual(row['consistency_audit']['contradictions'][0]['kind'], 'invalid_global_bound')
                    if event_type == 'closure':
                        self.assertEqual(len(row['raw_closure_claims']), 1)
                        self.assertEqual(len(row['consistency_audit']['unsupported_in_budget_closure_claims']), 1)

    def test_excessive_finite_termination_bound_is_still_a_contradiction(self):
        evidence = self.evidence([self.termination(global_lb=101.)])
        self.assertEqual(evidence['unavailable_global_bound_events'], [])
        row = self.audit(evidence)
        contradiction = row['consistency_audit']['contradictions'][0]
        self.assertEqual(contradiction['kind'], 'global_bound_above_feasible_cost')
        self.assertEqual(contradiction['excess'], 1.)

    def test_valid_finite_termination_bound_is_still_checked(self):
        evidence = self.evidence([self.termination(global_lb=99.)])
        row = self.audit(evidence)
        self.assertEqual(row['consistency_audit']['global_bound_claims_checked'], 1)
        self.assertEqual(row['consistency_audit']['status'], 'consistent')

    def test_other_malformed_values_remain_invalid(self):
        for bound in (True, False, 'nan', 'Infinity', {}, []):
            with self.subTest(bound=bound):
                evidence = self.evidence([self.termination(global_lb=bound)])
                self.assertEqual(evidence['unavailable_global_bound_events'], [])
                row = self.audit(evidence)
                self.assertEqual(row['consistency_audit']['status'], 'contradicted')


if __name__ == '__main__':
    unittest.main()
