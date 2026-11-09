"""Budget interpretation tests, independent of native solver performance."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from horizon_review import checkpoint, interval_mean_gap, review_reasons


class HorizonReviewTests(unittest.TestCase):
    def test_resource_failure_is_valid_but_does_not_hide_output_issues(self):
        cell = {'status': 'failed', 'resource_failure_kind': 'memory_allocation_failure'}
        self.assertEqual(review_reasons(cell, {}), [])
        self.assertIn('invalid_candidates', review_reasons({**cell, 'invalid_candidate_count': 1}, {}))
        self.assertIn('result_or_validation_issues', review_reasons(cell, {'issues': ['contradictory bound']}))
        self.assertIn('resource_outcome_with_closure_or_proof_claim',
                      review_reasons({**cell, 'resource_closure_conflict': True}, {}))
        self.assertIn('unclassified_failure_or_incomplete_record', review_reasons({'status': 'failed'}, {}))

    def test_startup_dilution_is_not_late_improvement(self):
        envelope = [{'elapsed_s': 10., 'checked_cost': 100.}]
        self.assertEqual(checkpoint(envelope, 100., 100.)['lambda'], .1)
        self.assertEqual(checkpoint(envelope, 100., 200.)['lambda'], .05)
        self.assertEqual(interval_mean_gap(envelope, 100., 100., 200.), 0.)

    def test_exact_boundary_and_later_improvement(self):
        envelope = [{'elapsed_s': 100., 'checked_cost': 300.}, {'elapsed_s': 150., 'checked_cost': 100.}]
        self.assertEqual(interval_mean_gap(envelope, 100., 100., 200.), .25)
        self.assertEqual(checkpoint(envelope, 100., 100.)['final_squeezed_gap'], .5)

    def test_no_incumbent_stays_in_the_denominator(self):
        self.assertEqual(interval_mean_gap([], 100., 100., 200.), 1.)

    def test_better_than_reference_can_remain_negative(self):
        envelope = [{'elapsed_s': 0., 'checked_cost': 50.}]
        self.assertAlmostEqual(interval_mean_gap(envelope, 100., 100., 200.), -1/3)


if __name__ == '__main__':
    unittest.main()
