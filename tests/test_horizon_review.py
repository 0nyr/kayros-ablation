"""Budget interpretation tests, independent of native solver performance."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from horizon_review import checkpoint, interval_mean_gap


class HorizonReviewTests(unittest.TestCase):
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
