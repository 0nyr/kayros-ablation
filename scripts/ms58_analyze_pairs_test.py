"""Regression checks for censoring, witness acceptance and work comparison."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys

from ms58_analyze_pairs import EXPECTED_CHECKER, closure_and_resource, compare_pair, native_work, summarize
from ms58_paired_probe import paired_schedule
from probe_runner import atomic_json, run_probe


class PairAnalysisTests(unittest.TestCase):
    def test_timeout_does_not_become_a_measured_speedup(self):
        common = {'pair_id': 'pair', 'instance': 'C104', 'repetition': 1, 'lane': 'test',
                  'horizon_s': 300, 'status': 'deadline', 'audit_ok': True, 'native_work': {'available': False}}
        pair = compare_pair([{**common, 'variant': 'reference', 'position': 1,
                              'reported_closure_s': None, 'capped_closure_s': 300},
                             {**common, 'variant': 'candidate', 'position': 2,
                              'reported_closure_s': 200, 'capped_closure_s': 200}])
        self.assertEqual(pair['comparator_minus_reference_capped_s'], -100)
        self.assertIsNone(pair['reference_over_comparator_closure_ratio'])
        self.assertFalse(pair['both_closed'])

    def test_closure_requires_checked_witness_and_no_resource_failure(self):
        events = [{'event': 'candidate', 'sequence': 0, 'elapsed_s': 1},
                  {'event': 'closure', 'sequence': 1, 'elapsed_s': 2}]
        result = {'status': 'completed', 'issues': [], 'validation': {'candidates': [{'sequence': 0, 'valid': True}]}}
        self.assertEqual(closure_and_resource(result, events, ''), (2, None))
        self.assertEqual(closure_and_resource(result, events, 'MemoryError\n'), (None, 'python_memory_allocation_failure'))
        result['validation']['candidates'][0]['valid'] = False
        self.assertIsNone(closure_and_resource(result, events, '')[0])

    def test_work_signature_ignores_timing_but_detects_changed_labels(self):
        raw = {'exact': {'exact_log': {'status': 'Optimum', 'root_log': {'status': 'Optimum',
            'iteration_count': 1, 'columns_added': 5, 'iterations': [{'iteration_name': 'Exact',
            'status': 'Finished', 'time': 10, 'forward': {'processed_count': 20}, 'backward': {'processed_count': 30}}]}}}}
        changed = copy.deepcopy(raw)
        changed['exact']['exact_log']['root_log']['iterations'][0]['time'] = 5
        self.assertEqual(native_work(raw)['root_work_signature'], native_work(changed)['root_work_signature'])
        changed['exact']['exact_log']['root_log']['iterations'][0]['forward']['processed_count'] = 21
        self.assertNotEqual(native_work(raw)['root_work_signature'], native_work(changed)['root_work_signature'])
        self.assertIsNone(native_work(raw)['root_labeling']['Exact']['extended_count'])

    def test_pending_cells_remain_in_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = Path(directory)
            schedule = paired_schedule(['C104'], ['candidate'], 1, 42)
            (lane / 'schedule.json').write_text(json.dumps({'scheduled_count': 2, 'scheduled_cells': schedule,
                'host': 'test', 'cpu_core': 0, 'memory_mb': 16384, 'horizon_s': 300, 'checker': EXPECTED_CHECKER}))
            result = summarize([lane])
            self.assertEqual(result['status_counts'], {'pending': 2})
            self.assertEqual(result['groups'][0]['scheduled_pairs'], 1)
            self.assertEqual(result['groups'][0]['reference_mean_capped_s'], 300)
            self.assertEqual(result['groups'][0]['comparator_mean_capped_s'], 300)
            self.assertIsNone(result['groups'][0]['median_reference_over_comparator_closure_ratio_both_closed_only'])

    def test_completed_lane_binds_schedule_events_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            lane = Path(directory)
            (lane / 'manifests').mkdir()
            (lane / 'rows').mkdir()
            schedule = paired_schedule(['C101'], ['candidate'], 1, 42)
            for task in schedule:
                manifest = {'schema_version': 1, 'checker': EXPECTED_CHECKER,
                    'paired_diagnostic_cell': dict(task), 'runtime_files': {'fixture': 'fixture-digest'},
                    'source_id': 'synthetic fixture'}
                path = lane / 'manifests' / f"{task['cell_id']}.json"
                atomic_json(path, manifest)
                task['manifest_path'] = str(path)
                params = {'instance_name': 'C101', 'instance_sha256': 'instance', 'atf_sha256': 'atf',
                          'memory_mb': 16384, 'memory_cap_bytes': 16384 * 1024 ** 2, 'cpu_affinity': [0],
                          'repetition': 1, 'search_seed': None, 'prefix_seconds': None, 'arm': 'kayros-bpca-cplex'}
                fixture = "import json; print(json.dumps({'event':'candidate','elapsed_s':0.001})); print(json.dumps({'event':'closure','elapsed_s':0.002}))"
                result = run_probe(output=lane / 'cells', manifest=manifest, parameters=params,
                    command=[sys.executable, '-c', fixture], horizon_s=3, cwd=lane, environment={},
                    validate=lambda events: {'checker': EXPECTED_CHECKER, 'candidates': [{'sequence': 0, 'valid': True}], 'proof': {'status': 'proof_unverified'}})
                atomic_json(lane / 'rows' / f"{task['cell_id']}.json", {**task, 'run_id': result['run_id'], 'status': result['status']})
            atomic_json(lane / 'schedule.json', {'scheduled_count': 2, 'scheduled_cells': schedule,
                'host': 'test', 'cpu_core': 0, 'memory_mb': 16384, 'horizon_s': 3,
                'checker': EXPECTED_CHECKER, 'inputs': {'C101': {'sha256': 'instance', 'atf_sha256': 'atf'}}})
            summary = summarize([lane])
            self.assertEqual(summary['status_counts'], {'completed': 2})
            self.assertEqual(summary['groups'][0]['both_closed_pairs'], 1)
            self.assertEqual(summary['groups'][0]['unaudited_pairs'], 0)
            self.assertEqual(summary['groups'][0]['median_reference_over_comparator_closure_ratio_both_closed_only'], 1)


if __name__ == '__main__':
    unittest.main()
