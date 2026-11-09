"""Protect semantic workload matching in the overhead replay."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from measure_instrumentation import work_signature


class InstrumentationAnalysisTests(unittest.TestCase):
    def fixture(self):
        return {'exact_log': {'status': 'Optimum', 'time': 1.,
                             'root_log': {'iteration_count': 7, 'time': .9,
                                          'iterations': [{'processed_count': 50, 'time': .5}]}},
                'value': 10., 'routes': [{'path': [0, 1, 3], 'duration': 4., 't0': 0.},
                                         {'path': [0, 2, 3], 'duration': 6., 't0': 1.}],
                'checker_infeasible_columns': 0}

    def test_route_set_order_and_clock_changes_preserve_work(self):
        left = self.fixture()
        right = copy.deepcopy(left)
        right['routes'].reverse()
        right['exact_log']['time'] = 2.
        right['exact_log']['root_log']['iterations'][0]['time'] = 1.5
        self.assertEqual(work_signature(left), work_signature(right))

    def test_cg_iteration_count_remains_part_of_work(self):
        left = self.fixture()
        right = copy.deepcopy(left)
        right['exact_log']['root_log']['iteration_count'] += 1
        self.assertNotEqual(work_signature(left), work_signature(right))

    def test_customer_path_changes_do_not_get_canonicalized_away(self):
        left = self.fixture()
        right = copy.deepcopy(left)
        right['routes'][0]['path'] = [0, 2, 1, 3]
        self.assertNotEqual(work_signature(left), work_signature(right))


if __name__ == '__main__':
    unittest.main()
