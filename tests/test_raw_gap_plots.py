"""Raw-gap targets retain missing observations and weight instances equally."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from raw_gap_plots import attainment_curves


def cell(instance, envelope, status='completed'):
    return {'instance_name': instance, 'reference_cost': 100., 'status': status,
            'envelope': [{'elapsed_s': t, 'checked_cost': cost} for t, cost in envelope]}


class RawGapTests(unittest.TestCase):
    def test_missing_exact_threshold_below_reference_and_failure_carry(self):
        cells = [cell('A', [(2, 105.), (4, 101.), (6, 99.)], 'failed'), cell('B', [])]
        data = attainment_curves(cells, [0., .01, .05], 10.)
        self.assertEqual(data['elapsed_s'], [0., 2, 4, 6, 10.])
        self.assertEqual(data['targets'][0]['attainment_fraction'], [0., 0., 0., .5, .5])
        self.assertEqual(data['targets'][1]['attainment_fraction'], [0., 0., .5, .5, .5])
        self.assertEqual(data['targets'][2]['attainment_fraction'], [0., .5, .5, .5, .5])
        self.assertEqual(data['scheduled_cells'], 2)

    def test_equal_instance_then_equal_trial_weights(self):
        cells = [cell('A', [(1, 100.)]), cell('A', []), cell('B', [(1, 100.)])]
        data = attainment_curves(cells, [0.], 10.)
        self.assertEqual(data['targets'][0]['attainment_fraction'], [0., .75, .75])
        self.assertEqual(data['instance_trial_counts'], {'A': 2, 'B': 1})

    def test_late_or_nonmonotone_trace_is_refused(self):
        for envelope in [[(11, 100.)], [(2, 100.), (1, 99.)], [(1, 100.), (2, 101.)]]:
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                attainment_curves([cell('A', envelope)], [0.], 10.)


if __name__ == '__main__':
    unittest.main()
