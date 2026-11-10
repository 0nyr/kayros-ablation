"""Read-only C104 identity, external-reference and censoring regressions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import ms58_fixed_witness as fixed
import ms58_c104_fixed_witness_reaudit as audit


class C104ReauditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project = Path(__file__).resolve().parents[1]
        cls.panel = project / 'outputs/ms58/dabia-20261110/c104-fixed-witness-v1'
        cls.source = project / 'outputs/ms58/dabia-20261110/gros-five-arm-1h-v1'
        cls.context = audit.verify_context(cls.panel, cls.source) if (cls.panel / 'launch.json').exists() else None

    def test_all_real_frozen_identities_and_both_external_references(self):
        if self.context is None:
            self.skipTest('Local C104 frozen artifacts are unavailable')
        self.assertEqual(len(self.context['expected_records']), 15)
        self.assertTrue(all('identity' in r for r in self.context['expected_records'].values()))
        self.assertEqual(self.context['treatment']['routes_sha256'], audit.TREATMENT_ROUTES_SHA)
        self.assertEqual(self.context['treatment']['checked_cost'], 97829.81555195048)
        self.assertEqual({r['kind'] for r in self.context['references']},
                         {'source_run_checked_incumbent', 'original_frozen_n100_reference'})
        self.assertTrue(all(r['checked_cost'] == 96995.19799342303 for r in self.context['references']))

    def test_real_expected_identity_rejects_600_stage_or_changed_cpu(self):
        if self.context is None:
            self.skipTest('Local C104 frozen artifacts are unavailable')
        task = self.context['launch']['scheduled_cells'][0]
        spec = self.context['plan']['runtimes'][task['runtime_key']]
        manifest = json.loads((self.panel / 'manifests' / Path(spec['path']).name).read_bytes())
        original = self.context['expected_records'][task['task_id']]['identity']
        for mutation in ('exact', 'process', 'cpu', 'witness'):
            expected = copy.deepcopy(original)
            if mutation == 'exact': expected['command'][expected['command'].index('--exact-seconds')+1] = '600'
            if mutation == 'process': expected['horizon_s'] = 660.
            if mutation == 'cpu': expected['parameters']['cpu_core'] += 1
            if mutation == 'witness': expected['parameters']['witness_sha256'] = 'changed'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                audit.verify_expected(task, spec, manifest, expected, self.panel)

    def censored_rows(self):
        return [{'arm_id': arm, 'replicate': rep, 'audit_ok': True, 'raw_result_status': 'deadline',
                 'reported_closure_s': None, 'capped_reported_closure_s': 3600.,
                 'native_work': {'available': False}, 'resource_failure_kind': None,
                 'final_checked_cost': None} for arm in fixed.ARMS for rep in (1, 2, 3)]

    def test_all_censored_trials_keep_full_denominator_and_unknown_native_work(self):
        groups = audit.groups(self.censored_rows())
        self.assertEqual(len(groups), 5)
        for group in groups:
            self.assertEqual(group['scheduled_trials'], 3)
            self.assertEqual(group['mean_capped_reported_closure_s'], 3600.)
            self.assertEqual(group['time_censored_trials'], 3)
            self.assertEqual(group['reported_closures'], 0)
            self.assertEqual(group['native_work_available_trials'], 0)
            self.assertIsNone(group['native_work_mean_time_s'])

    def test_partial_native_work_does_not_become_a_filtered_mean(self):
        rows = self.censored_rows()
        rows[0].update(raw_result_status='completed', reported_closure_s=1200., capped_reported_closure_s=1200.,
            final_checked_cost=100., native_work={'available': True, 'status': 'Optimum',
                'whole_run': {'time': 1100., 'lp_time': 20., 'pricing_time': 900., 'branching_time': 30., 'nodes_closed': 1},
                'root_cg': {'iteration_count': 10, 'columns_added': 100}, 'root_labeling_time_s': 50.})
        group = audit.groups(rows)[0]
        self.assertEqual(group['mean_capped_reported_closure_s'], 2800.)
        self.assertEqual(group['reported_closures'], 1)
        self.assertEqual(group['native_work_available_trials'], 1)
        self.assertIsNone(group['native_work_mean_time_s'])
        self.assertEqual(group['root_columns_added'], [100, None, None])

    def test_native_limit_status_is_not_mistaken_for_solver_closure(self):
        rows = self.censored_rows()
        rows[0].update(raw_result_status='completed', native_work={'available': False, 'status': 'TimeLimitReached'})
        group = audit.groups(rows)[0]
        self.assertEqual(group['time_censored_trials'], 3)
        self.assertEqual(group['reported_closures'], 0)
        self.assertEqual(group['outcome_counts'], {'native_TimeLimitReached': 1, 'deadline': 2})
        rows[0]['native_work']['status'] = 'MemoryLimitReached'
        group = audit.groups(rows)[0]
        self.assertEqual(group['time_censored_trials'], 2)
        self.assertEqual(group['resource_failure_trials'], 1)
        self.assertEqual(group['mean_capped_reported_closure_s'], 3600.)

    def test_streamed_cg_progress_stays_a_rounded_rmp_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'stderr.log'
            path.write_text('time # value #cols\n0 0 138171 100\n820.108 44 96973 59383\nCuts: 10\n981.353 45 97026.4 59383\n')
            progress = audit.printed_progress(path)
            self.assertEqual(len(progress['rows']), 3)
            self.assertEqual(progress['rows'][-1]['rounded_restricted_master_value'], 97026.4)
            self.assertEqual(progress['cuts'], [{'after_printed_row': 1, 'cuts': 10}])
            self.assertIn('not certified global lower bounds', progress['meaning'])

    def test_external_ceiling_refutes_raw_closure_without_mutating_original_row(self):
        candidate = {'sequence': 0, 'elapsed_s': 0., 'checked_cost': 110.}
        row = {'task_id': 'fixture', 'instance_key': 'Dabia2013/n=100/C104',
            'instance_sha256': 'input', 'atf_sha256': 'atf', 'reference_cost': 120.,
            'reference_manifest_sha256': 'reference', 'horizon_s': 3600.,
            'audit_ok': True, 'finished_task': True, 'raw_result_status': 'completed',
            'reported_closure_s': 5., 'capped_reported_closure_s': 5.,
            'witness_evidence_audited': True, 'checked_candidates': [candidate],
            'global_bound_claims': [{'global_lb': 110., 'elapsed_s': 5.}],
            'raw_closure_claims': [{'sequence': 1, 'global_lb': 110., 'elapsed_s': 5.,
                                    'preceding_checked_candidate': candidate}]}
        references = [{'kind': kind, 'checked_cost': 100., 'routes_sha256': 'checked', 'sha256': kind}
                      for kind in ('source_run_checked_incumbent', 'original_frozen_n100_reference')]
        original = copy.deepcopy(row)
        rows, ceilings = audit.pool_references([row], references)
        self.assertEqual(row, original)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['reported_closure_s'])
        self.assertEqual(rows[0]['raw_reported_closure_s'], 5.)
        self.assertEqual(ceilings[row['instance_key']]['cost'], 100.)


if __name__ == '__main__':
    unittest.main()
