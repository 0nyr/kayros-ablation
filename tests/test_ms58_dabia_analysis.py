"""Scientific plotting invariants for missing observations and frozen cohorts."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ms58_dabia_plots import curves
from ms58_dabia_summary import (EXPECTED_CHECKER, apply_feasible_consistency, digest,
                               observation, observed_backend, proof_evidence, task_reference,
                               validate_late_event_identity)


def cell(name, envelope, closure=None):
    return {'instance_key': name, 'reference_cost': 100., 'horizon_s': 10.,
            'envelope': [{'elapsed_s': t, 'checked_cost': z} for t, z in envelope],
            'reported_closure_s': closure}


class DabiaCurveTests(unittest.TestCase):
    def test_missing_incumbent_keeps_weight_and_does_not_attain_goal(self):
        data = curves([cell('A', [(2, 100.)], 3.), cell('B', [])])
        self.assertEqual(data['mean_squeezed_gap'][-1], .5)
        self.assertEqual(data['goal_1pct_fraction'][-1], .5)
        self.assertEqual(data['reported_closure_fraction'][-1], .5)
        self.assertIsNone(data['mean_raw_gap_percent'][-1])

    def test_trials_are_averaged_within_instances_first(self):
        data = curves([cell('A', [(1, 100.)]), cell('A', [(1, 100.)]), cell('B', [])])
        self.assertEqual(data['checked_incumbent_fraction'][-1], .5)

    def test_below_reference_cost_survives_without_a_closure(self):
        data = curves([cell('A', [(1, 99.)])])
        self.assertEqual(data['mean_raw_gap_percent'][-1], -1.)
        self.assertEqual(data['goal_1pct_fraction'][-1], 1.)
        self.assertEqual(data['reported_closure_fraction'][-1], 0.)

    def test_raw_mean_starts_only_when_the_entire_population_has_incumbents(self):
        data = curves([cell('A', [(1, 110.)]), cell('B', [(5, 100.)])])
        self.assertIsNone(data['mean_raw_gap_percent'][1])
        self.assertEqual(data['mean_raw_gap_percent'][2], 5.)

    def test_same_instance_name_at_another_size_cannot_use_the_reference(self):
        task = {'instance_key': 'Dabia2013/n=25/C101', 'num_customers': 25,
                'instance_name': 'C101', 'instance_sha256': 'n25', 'atf_sha256': 'atf25',
                'reference_manifest_sha256': 'manifest25', 'reference_cost': 100.}
        refs = {'Dabia2013/n=100/C101': {'instance_sha256': 'n100'}}
        with self.assertRaises(KeyError):
            task_reference(task, refs)

    def test_native_identity_is_required_even_when_metadata_matches(self):
        task = {'arm': 'lera-exact', 'native_instance_sha256': 'correct'}
        event = {'phase': 'native_launch', 'native_instance_sha256': 'wrong',
                 'metadata_equivalence': {'capacity': True}}
        self.assertFalse(observed_backend(task, [event])['passed'])
        self.assertFalse(observed_backend(task, [event,
            {'phase': 'backend_settings', 'backend': 'CPLEX', 'effective': {'threads': 1}}])['passed'])
        event['native_instance_sha256'] = 'correct'
        self.assertTrue(observed_backend(task, [event])['passed'])
        event['metadata_equivalence']['capacity'] = 1
        self.assertFalse(observed_backend(task, [event])['passed'])

    def test_backend_name_does_not_excuse_extra_threads(self):
        task = {'arm': 'kayros-bpca-highs'}
        events = [{'phase': 'exact', 'lp_backend': 'HiGHS'},
                  {'effective_backend_settings': {'lp': {'backend': 'HiGHS', 'threads': 2}}}]
        self.assertFalse(observed_backend(task, events)['passed'])
        events[1]['effective_backend_settings']['lp']['threads'] = 1
        self.assertTrue(observed_backend(task, events)['passed'])

    def test_controller_audit_failure_cannot_restore_raw_solver_closure(self):
        task = {'task_id': 'fixture', 'arm': 'kayros-bpca-cplex', 'instance_name': 'C101',
                'instance_sha256': 'input', 'atf_sha256': 'atf', 'prefix_s': None,
                'replicate': 1, 'seed': None, 'memory_mb': 16384, 'cpu_core': 0,
                'horizon_s': 3600., 'worker_python_sha256': 'python-sha',
                'worker_python': '/runtime/python', 'worker_path': '/recorded/frozen-scripts/kayros_worker.py'}
        fixture_sha = hashlib.sha256(b'fixture').hexdigest()
        orchestration = ['run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py']
        parameters = {'arm': task['arm'], 'instance_name': 'C101', 'instance_sha256': 'input',
                'atf_sha256': 'atf', 'prefix_seconds': None, 'repetition': 1, 'search_seed': None,
                'memory_mb': 16384, 'memory_cap_bytes': 16384*1024**2, 'cpu_affinity': [0],
                'worker_python_sha256': 'python-sha', 'worker_sha256': fixture_sha,
                'orchestration_files': {name: fixture_sha for name in orchestration}}
        manifest = {'runtime_files': {'fixture': 'fixture'}, 'source_id': 'fixture'}
        result = {'status': 'completed', 'validation': {'checker': EXPECTED_CHECKER,
                  'instance_name': 'C101', 'instance_sha256': 'input', 'atf_sha256': 'atf'}}
        with tempfile.TemporaryDirectory() as directory:
            wave = Path(directory)
            (wave / 'rows').mkdir()
            (wave / 'frozen-scripts').mkdir()
            for name in ['kayros_worker.py', *orchestration]:
                (wave / 'frozen-scripts' / name).write_bytes(b'fixture')
            cell = wave / 'cells' / 'fixture-run'
            cell.mkdir(parents=True)
            (wave / 'rows/fixture.json').write_text(json.dumps({**task, 'run_id': 'fixture-run',
                'status': 'launch_or_audit_failure', 'backend_audit': {'passed': True}}))
            (cell / 'identity.json').write_text(json.dumps({'manifest_hash': digest(manifest),
                'parameters': parameters, 'horizon_s': 3600.,
                'command': [task['worker_python'], task['worker_path']], 'cwd': '/recorded'}))
            (cell / 'events.jsonl').write_text(json.dumps({'sequence': 0, 'elapsed_s': 0.,
                'event': 'phase', 'phase': 'exact', 'lp_backend': 'CPLEX'})+'\n')
            (cell / 'stderr.log').write_text('')
            (cell / 'late-events.jsonl').write_text('')
            (cell / 'result.json').write_text(json.dumps(result))
            with patch('ms58_dabia_summary.load_completed', return_value=result), \
                    patch('ms58_dabia_summary.closure_and_resource', return_value=(3., None)):
                observed = observation(wave, task, 100., manifest)
            self.assertEqual(observed['status'], 'launch_or_audit_failure')
            self.assertEqual(observed['raw_result_status'], 'completed')
            self.assertIsNone(observed['reported_closure_s'])
            self.assertEqual(observed['capped_reported_closure_s'], 3600.)
            self.assertFalse(observed['audit_ok'])
            result['validation']['instance_sha256'] = 'another-instance'
            with patch('ms58_dabia_summary.load_completed', return_value=result):
                with self.assertRaisesRegex(ValueError, 'another input'):
                    observation(wave, task, 100., manifest)


def proof_cell(name, candidate_cost, bound, reference=100., closure=3.):
    candidate = {'sequence': 0, 'elapsed_s': 2., 'checked_cost': candidate_cost}
    return {'task_id': name, 'run_id': name, 'instance_key': 'Dabia2013/n=100/C201',
        'instance_sha256': 'input', 'atf_sha256': 'atf', 'reference_cost': reference,
        'reference_manifest_sha256': 'snapshot', 'horizon_s': 600., 'audit_ok': True,
        'observation_usable': True, 'status': 'completed', 'witness_evidence_audited': True,
        'checked_candidates': [candidate], 'reported_closure_s': closure,
        'capped_reported_closure_s': closure, 'envelope': [{'elapsed_s': 2., 'checked_cost': candidate_cost}],
        'global_bound_claims': [{'sequence': 1, 'elapsed_s': 3., 'global_lb': bound}],
        'raw_closure_claims': [{'sequence': 2, 'elapsed_s': 3., 'global_lb': bound,
                               'preceding_checked_candidate': candidate}]}


class FeasibleConsistencyTests(unittest.TestCase):
    def test_self_consistent_false_closure_is_rejected_by_frozen_reference(self):
        row = proof_cell('false-closure', 101., 101.)
        apply_feasible_consistency([row])
        self.assertIsNone(row['reported_closure_s'])
        self.assertEqual(row['raw_reported_closure_s'], 3.)
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['envelope'][0]['checked_cost'], 101.)
        self.assertEqual(row['capped_reported_closure_s'], 600.)
        self.assertFalse(row['observation_usable'])
        self.assertEqual(len(row['consistency_audit']['contradictions']), 2)

    def test_later_other_arm_witness_can_refute_an_earlier_closure(self):
        first = proof_cell('first', 100., 100., reference=101.)
        better = proof_cell('better', 99., 99., reference=101.)
        better['checked_candidates'][0]['elapsed_s'] = 500.
        better['raw_closure_claims'][0]['elapsed_s'] = 501.
        better['reported_closure_s'] = 501.
        apply_feasible_consistency([first, better])
        self.assertIsNone(first['reported_closure_s'])
        self.assertEqual(first['consistency_audit']['feasible_ceiling']['task_id'], 'better')
        self.assertEqual(better['reported_closure_s'], 501.)
        self.assertEqual(first['reference_cost'], 101.)

    def test_earlier_invalid_bound_is_not_hidden_by_a_final_consistent_bound(self):
        row = proof_cell('row', 100., 100.)
        row['global_bound_claims'].insert(0, {'sequence': 0, 'elapsed_s': 1., 'global_lb': 102.})
        apply_feasible_consistency([row])
        self.assertIsNone(row['reported_closure_s'])
        self.assertEqual(row['consistency_audit']['contradictions'][0]['excess'], 2.)

    def test_absolute_tolerance_does_not_hide_material_discrepancy(self):
        close = proof_cell('close', 100., 100.+0.5e-6)
        wrong = proof_cell('wrong', 100., 100.+2e-6)
        apply_feasible_consistency([close, wrong])
        self.assertEqual(close['reported_closure_s'], 3.)
        self.assertIsNone(wrong['reported_closure_s'])

    def test_late_checked_candidate_cannot_enter_feasible_pool(self):
        event = {'sequence': 0, 'elapsed_s': 601., 'event': 'candidate', 'routes': [[1]]}
        inputs = {'instance_name': 'C201', 'instance_sha256': 'input', 'atf_sha256': 'atf'}
        validation = {**inputs, 'checker': EXPECTED_CHECKER,
                      'candidates': [{**event, 'valid': True, 'checked_cost': 1.}]}
        with self.assertRaises(ValueError):
            proof_evidence(validation, [event], 600., expected_inputs=inputs)

    def test_identical_names_cannot_pool_different_input_bytes(self):
        first, second = proof_cell('first', 100., 100.), proof_cell('second', 100., 100.)
        second['instance_sha256'] = 'other-input'
        with self.assertRaises(ValueError):
            apply_feasible_consistency([first, second])

    def test_claimed_closure_must_match_its_checked_incumbent_and_lower_bound(self):
        gap = proof_cell('gap', 100., 90.)
        missing = proof_cell('missing', 100., 100.)
        del missing['raw_closure_claims'][0]['global_lb']
        apply_feasible_consistency([gap, missing])
        self.assertIsNone(gap['reported_closure_s'])
        self.assertEqual(gap['consistency_audit']['status'], 'contradicted')
        self.assertIsNone(missing['reported_closure_s'])
        self.assertEqual(missing['consistency_audit']['status'], 'unsupported')

    def test_late_bounds_require_the_same_identity_and_a_genuinely_late_timestamp(self):
        identity = {'run_id': 'run', 'manifest_hash': 'manifest', 'schema_version': 1, 'horizon_s': 600.}
        event = {**identity, 'sequence': 0, 'event': 'bound', 'global_lb': 100.,
                 'excluded_reason': 'after_horizon', 'elapsed_s': 601.}
        validate_late_event_identity([event], identity)
        with self.assertRaises(ValueError):
            validate_late_event_identity([{**event, 'run_id': 'other'}], identity)
        with self.assertRaises(ValueError):
            validate_late_event_identity([{**event, 'elapsed_s': 600.}], identity)

    def test_unsupported_late_claim_keeps_prior_closure_but_actual_late_contradiction_removes_it(self):
        row = proof_cell('row', 100., 100.)
        row['raw_closure_claims'].append({'sequence': 0, 'elapsed_s': 601., 'global_lb': 100.,
            'timing_scope': 'after_horizon', 'preceding_checked_candidate': None})
        apply_feasible_consistency([row])
        self.assertEqual(row['reported_closure_s'], 3.)
        self.assertEqual(len(row['consistency_audit']['unsupported_closure_claims']), 1)
        row['global_bound_claims'].append({'sequence': 0, 'elapsed_s': 601., 'global_lb': 101.,
            'timing_scope': 'after_horizon'})
        apply_feasible_consistency([row])
        self.assertIsNone(row['reported_closure_s'])


if __name__ == '__main__':
    unittest.main()
