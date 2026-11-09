"""Review prefix recommendation eligibility, weighting and lexicographic ties."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'analysis'))
from pilot_decision import decide, equal_instance_mean, prefix_ranking


class PilotDecisionTests(unittest.TestCase):
    def cell(self, prefix, instance='A', repetition=0, closure=10., integral=.2):
        return {'task_id': f'{instance}-{prefix}-{repetition}', 'arm': 'kayros-full-cplex',
                'prefix_s': prefix, 'instance_name': instance, 'replicate': repetition,
                'horizon_s': 100., 'reference_cost': 10., 'run_id': f'{instance}-{prefix}-{repetition}',
                'status': 'completed', 'invalid_candidate_count': 0, 'lambda': integral,
                'reported_closure_s': closure,
                'capped_reported_closure_s': closure if closure is not None else 100.}

    def report(self):
        return {'provisional': False, 'wave_name': 'tiny-review',
                'cells': [self.cell(15), self.cell(60, closure=20.)]}

    def write_results(self, root, report):
        for cell in report['cells']:
            directory = root/'cells'/cell['run_id']
            directory.mkdir(parents=True)
            (directory/'result.json').write_text(json.dumps({'status': cell['status'], 'returncode': 0,
                'issues': [], 'validation': {'candidates': [{'valid': True}],
                    'checked_envelope': [{'elapsed_s': 5., 'checked_cost': 10.}]}}))

    def test_equal_instance_weighting_keeps_failed_cell(self):
        cells = [self.cell(15, repetition=i, closure=0.) for i in range(3)]
        cells.append(self.cell(15, instance='B', closure=None, integral=1.))
        self.assertEqual(equal_instance_mean(cells, 'capped_reported_closure_s'), 50.)
        ranking = prefix_ranking(cells)
        self.assertEqual(ranking[0]['scheduled'], 4)
        self.assertEqual(ranking[0]['restricted_mean_reported_closure_s'], 50.)

    def test_exact_tie_order_is_time_then_lambda_then_prefix(self):
        self.assertEqual(prefix_ranking([self.cell(15, closure=20., integral=.1),
                                        self.cell(60, closure=10., integral=.9)])[0]['prefix_s'], 60)
        self.assertEqual(prefix_ranking([self.cell(15, integral=.3), self.cell(60, integral=.2)])[0]['prefix_s'], 60)
        self.assertEqual(prefix_ranking([self.cell(60), self.cell(15)])[0]['prefix_s'], 15)

    def test_clean_cases_and_source_census_warnings_remain_eligible(self):
        report = self.report()
        report['cells'][0]['closure_diagnostics'] = {'restricted_master_infeasible': 3,
                                                   'unbranched_fractional': 1}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            result = decide(report, root)
        self.assertTrue(result['recommendation_eligible'])
        self.assertEqual(result['selected_prefix_s'], 15)
        self.assertEqual(result['blocking_cells'], [])

    def test_invalid_candidate_blocks_recommendation_without_filtering_ranking(self):
        report = self.report()
        report['cells'][0]['invalid_candidate_count'] = 1
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            result = decide(report, root)
        self.assertFalse(result['recommendation_eligible'])
        self.assertIsNone(result['selected_prefix_s'])
        self.assertEqual(result['diagnostic_best_prefix_s'], 15)
        self.assertEqual(sum(row['scheduled'] for row in result['prefix_ranking']), 2)
        self.assertIn('invalid_candidates', result['blocking_cells'][0]['reasons'])

    def test_actual_result_issues_block_even_when_summary_looks_clean(self):
        report = self.report()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            path = root/'cells'/report['cells'][0]['run_id']/'result.json'
            raw = json.loads(path.read_text())
            raw['issues'] = ['worker integrity event: contradictory bound']
            path.write_text(json.dumps(raw))
            result = decide(report, root)
        self.assertFalse(result['recommendation_eligible'])
        self.assertIsNone(result['selected_prefix_s'])
        self.assertIn('result_issues', result['blocking_cells'][0]['reasons'])

    def test_clean_deadline_counts_as_unproved_without_blocking(self):
        report = self.report()
        report['cells'][0].update(status='deadline', reported_closure_s=None, capped_reported_closure_s=100.)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            path = root/'cells'/report['cells'][0]['run_id']/'result.json'
            raw = json.loads(path.read_text())
            raw['returncode'] = -9
            path.write_text(json.dumps(raw))
            result = decide(report, root)
        self.assertTrue(result['recommendation_eligible'])
        self.assertEqual(result['selected_prefix_s'], 60)
        self.assertEqual(result['prefix_ranking'][1]['scheduled'], 1)


if __name__ == '__main__':
    unittest.main()
