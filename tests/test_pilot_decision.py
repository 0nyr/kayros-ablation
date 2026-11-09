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

    def test_graceful_resource_with_closure_requires_review(self):
        import hashlib
        from test_resource_outcomes import evidence
        report = self.report()
        identity, raw = evidence('kayros-full-cplex', 0, 'completed')
        report['cells'][0]['run_id'] = identity['run_id']
        events = [{'event': 'phase', 'phase': 'exact_return', 'status': 'MemoryLimitReached'},
                  {'event': 'closure', 'elapsed_s': 20.}]
        event_bytes = ('\n'.join(json.dumps(e) for e in events)+'\n').encode()
        raw.update(events_sha256=hashlib.sha256(event_bytes).hexdigest(), issues=[],
                   validation={'candidates': [{'valid': True}], 'checked_envelope': []})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            directory = root/'cells'/identity['run_id']
            (directory/'result.json').write_text(json.dumps(raw))
            (directory/'identity.json').write_text(json.dumps(identity))
            (directory/'events.jsonl').write_bytes(event_bytes)
            result = decide(report, root)
        self.assertFalse(result['recommendation_eligible'])
        self.assertIn('resource_outcome_with_closure_or_proof_claim', result['blocking_cells'][0]['reasons'])
        self.assertEqual(result['resource_outcome_cells'][0]['resource_failure_kind'], 'native_memory_limit_reached')
        self.assertEqual(sum(r['scheduled'] for r in result['prefix_ranking']), 2)

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

    def test_allocation_failure_remains_eligible_and_capped_with_incumbent(self):
        report = self.report()
        report['cells'][0].update(status='failed', reported_closure_s=None, capped_reported_closure_s=100.)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            directory = root/'cells'/report['cells'][0]['run_id']
            raw = json.loads((directory/'result.json').read_text())
            raw['returncode'] = 1
            (directory/'result.json').write_text(json.dumps(raw))
            (directory/'stderr.log').write_text('Traceback (most recent call last):\nMemoryError: std::bad_alloc\n')
            result = decide(report, root)
        self.assertTrue(result['recommendation_eligible'])
        self.assertEqual(result['selected_prefix_s'], 60)
        self.assertEqual(result['prefix_ranking'][1]['restricted_mean_reported_closure_s'], 100.)
        self.assertEqual(sum(row['scheduled'] for row in result['prefix_ranking']), 2)
        self.assertEqual(len(result['resource_outcome_cells']), 1)
        self.assertTrue(all(row['feasible'] == 1 for row in result['checkpoints']))

    def test_resource_failure_does_not_excuse_invalid_output(self):
        report = self.report()
        report['cells'][0].update(status='failed', reported_closure_s=None,
                                  capped_reported_closure_s=100., invalid_candidate_count=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            directory = root/'cells'/report['cells'][0]['run_id']
            raw = json.loads((directory/'result.json').read_text())
            raw['returncode'] = 1
            (directory/'result.json').write_text(json.dumps(raw))
            (directory/'stderr.log').write_text('MemoryError: std::bad_alloc\n')
            result = decide(report, root)
        self.assertFalse(result['recommendation_eligible'])
        self.assertIn('invalid_candidates', result['blocking_cells'][0]['reasons'])
        self.assertEqual(len(result['resource_outcome_cells']), 1)

    def test_unknown_crash_is_not_assumed_to_be_resource_failure(self):
        report = self.report()
        report['cells'][0].update(status='failed', reported_closure_s=None, capped_reported_closure_s=100.)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_results(root, report)
            directory = root/'cells'/report['cells'][0]['run_id']
            raw = json.loads((directory/'result.json').read_text())
            raw['returncode'] = -11
            (directory/'result.json').write_text(json.dumps(raw))
            (directory/'stderr.log').write_text('Earlier allocation message: MemoryError\nSegmentation fault\n')
            result = decide(report, root)
        self.assertFalse(result['recommendation_eligible'])
        self.assertEqual(result['resource_outcome_cells'], [])


if __name__ == '__main__':
    unittest.main()
