"""No-solver tests for the isolated U-opportunity compute adapter."""
import copy
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import ms58_dominance_profile_run as original_run
import ms58_dominance_profile_audit as original_audit
import ms58_u_filter_opportunity_compute as compute
from ms58_u_filter_opportunity_stage import stage
from checker_contract import sha256


class OpportunityComputeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.experiment = Path(__file__).resolve().parents[1]
        self.pins_path = self.experiment / 'configs/ms58-u-filter-opportunity-source.json'
        self.pins = json.loads(self.pins_path.read_text())

    def test_private_controller_preserves_original_module_and_venv_handling(self):
        before = sha256(Path(original_run.__file__))
        module = compute.controller()
        self.assertEqual(module.PROFILE, compute.SOURCE)
        self.assertEqual(original_run.PROFILE, compute.PARENT)
        self.assertEqual(original_audit.SCHEMA, 'kayros-ms58-dominance-profile-v1')
        self.assertEqual(sha256(Path(original_run.__file__)), before)
        plan = [{'name': name, 'source_id': source, 'manifest_path': 'unused',
                 'worker_python': 'unused', 'diagnostic_environment': {}} for name, source in module.SOURCES.items()]
        module.validate_plan(plan)
        with self.assertRaises(ValueError): original_run.validate_plan(plan)
        self.assertIn('python.absolute()', Path(__file__).with_name('ms58_u_filter_opportunity_compute.py').read_text())

    def test_build_paths_and_both_public_flags(self):
        source = Path(__file__).with_name('ms58_u_filter_opportunity_build.sh').read_text()
        self.assertNotIn('dominance-build-logs', source)
        self.assertIn('u-filter-build-logs', source)
        self.assertIn('KAYROS_DOMINANCE_PROFILING=ON', source)
        self.assertIn('KAYROS_U_FILTER_OPPORTUNITY_PROFILING=ON', source)
        self.assertIn('.u-filter-opportunity-source-identity.json', source)

    def test_patch_inventory_requires_both_diagnostic_increments(self):
        hashes = set(self.pins['baseline_patch_sha256']) | {self.pins['selected_full_patch_sha256']}
        value = {'source_patch_files': {str(i): h for i, h in enumerate(hashes)}}
        compute.patch_stack(value, self.pins, profile=False)
        value['source_patch_files']['parent'] = self.pins['parent_incremental_patch_sha256']
        with self.assertRaises(ValueError): compute.patch_stack(value, self.pins, profile=True)
        value['source_patch_files']['child'] = self.pins['incremental_patch_sha256']
        compute.patch_stack(value, self.pins, profile=True)

    def records(self):
        scale = {'certificate_violations': 0, 'actual_u_tests': 2, 'subset_pass': 0, 'subset_reject': 2, 'covered_actual_rejects': 1}
        empty_scale = dict.fromkeys(scale, 0)
        u = {'sampled_calls': 1, 'membership_violations': 0, 'u_relaxed_calls': 1, 'depot_shortcut_calls': 1,
             'scales': {name: copy.deepcopy(scale) for name in ('whole_bucket', 'block16', 'block32', 'block64')}}
        empty_u = {'sampled_calls': 0, 'membership_violations': 0,
                   'scales': {name: copy.deepcopy(empty_scale) for name in u['scales']}}
        empty = {'finished': 0, 'sampled_finished': 0, 'sampled_ns': 0,
                 'sampled_stage_ns': {'construction': 0, 'scan': 0, 'materialization': 0}, 'counters': {},
                 'sampled_u_filter_opportunity': empty_u}
        direction = {kind: {'attempts': 0, 'sampled_attempts': 0, 'survived': copy.deepcopy(empty),
                           'eliminated': copy.deepcopy(empty)} for kind in ('dominance', 'correction', 'merge_maintenance')}
        def record(event, call, **extra):
            return {'event': event, 'call': call, 'schema': compute.SCHEMA, 'diagnostic_only': True,
                    'forward': copy.deepcopy(direction), 'backward': copy.deepcopy(direction), **extra}
        opening = record('profile_open', 0, base_source=compute.BASE, sample_stride=1009, sample_offset=0)
        begin = record('pricing_begin', 1, pricing_returned=False,
                       context={'level': 'Exact', 'correcting': True, 'lazy_extension': True})
        end = record('pricing_end', 1, pricing_returned=True, native_status='Finished', native_log={'status': 'Finished'},
                     returned_routes=0, pool_size=0)
        end['forward']['dominance'].update(attempts=1, sampled_attempts=1)
        end['forward']['dominance']['survived'].update(finished=1, sampled_finished=1, sampled_u_filter_opportunity=u)
        return [opening, begin, end]

    def write(self, records):
        path = self.root / 'profile.jsonl'
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        return path

    def test_parser_uses_sample_denominators_and_nonadditive_scales(self):
        result = compute.summarizer()(self.write(self.records()))
        group = result['u_filter_opportunity_aggregates']['returned_tasks/Exact/forward/dominance/survived']
        self.assertEqual(group['scales']['block16']['coverage_of_actual_u_tests'], .5)
        self.assertEqual(group['u_relaxed_calls'] + group['depot_shortcut_calls'], 2)
        self.assertFalse(result['granularities_additive'])
        self.assertFalse(result['runtime_benefit_claim'])
        self.assertEqual(original_audit.SCHEMA, 'kayros-ms58-dominance-profile-v1')

    def test_profile_ending_immediately_after_begin_remains_unfinished(self):
        result = compute.summarizer()(self.write(self.records()[:2]))
        self.assertEqual(result['unfinished_calls'], [1])
        self.assertEqual(result['returned_tasks'], 0)
        groups = result['u_filter_opportunity_aggregates']
        self.assertTrue(groups)
        self.assertTrue(all(key.startswith('unfinished_tasks_last_snapshot/') for key in groups))
        self.assertTrue(all(group['sampled_calls'] == 0 for group in groups.values()))
        self.assertTrue(all(scale['actual_u_tests'] == 0 for group in groups.values() for scale in group['scales'].values()))

    def test_parser_rejects_membership_certificate_and_denominator_corruption(self):
        for corruption in ('member', 'certificate', 'sum', 'covered', 'sample'):
            records = self.records()
            u = records[2]['forward']['dominance']['survived']['sampled_u_filter_opportunity']
            if corruption == 'member': u['membership_violations'] = 1
            elif corruption == 'certificate': u['scales']['block16']['certificate_violations'] = 1
            elif corruption == 'sum': u['scales']['block16']['actual_u_tests'] += 1
            elif corruption == 'covered': u['scales']['block16']['covered_actual_rejects'] = 3
            else: u['sampled_calls'] = 2
            with self.assertRaises(ValueError): compute.summarizer()(self.write(records))

    def test_rc201_equivalence_requires_completed_native_search(self):
        module = compute.controller()
        rows = [{'instance_name': 'RC201', 'variant': name, 'proof_observation_usable': True,
                 'status': 'completed', 'raw_result_status': 'completed',
                 'native_work': {'status': 'Optimum', 'root_work_signature': 'root', 'whole_work_signature': 'whole'}}
                for name in ('correction-on', 'dominance-profile')]
        (self.root / 'profile-audits').mkdir()
        for expected in (True, False):
            (self.root / 'consistency-audit.json').write_text(json.dumps({'cells': rows,
                'consistency_failure_count': 0, 'unsupported_closure_count': 0}))
            with patch.object(module, 'screen_monitor', return_value={}), \
                    patch.object(module, 'verify_schedule', return_value=({}, 'hash')):
                result = module.monitor(self.root)
            self.assertEqual(result['rc201_completed_work_equivalence']['passed'], expected)
            rows[1]['native_work']['status'] = 'TimeLimitReached'

    def test_actual_seven_patch_staging(self):
        archive = self.experiment / 'artifacts/ms58-u-filter-opportunity-7d68859e8d6d/source.tar.gz'
        if not archive.exists(): self.skipTest('Local sealed source archive is absent')
        expected = set(self.pins['baseline_patch_sha256']) | {self.pins['selected_full_patch_sha256'],
            self.pins['parent_incremental_patch_sha256'], self.pins['incremental_patch_sha256']}
        found = {}
        for directory in (self.experiment / 'patches', self.experiment / 'artifacts/ms58-dominance-profile-a2dcf8870c17', archive.parent):
            for path in directory.rglob('*.patch'):
                value = sha256(path)
                if value in expected: found.setdefault(value, path)
        self.assertEqual(set(found), expected)
        patches = self.root / 'patches'; patches.mkdir()
        for index, path in enumerate(found.values()): shutil.copyfile(path, patches / f'{index}.patch')
        result = stage(archive, self.pins_path, patches, self.root / 'source', self.root / 'frozen-patches')
        self.assertEqual(result['solver_cells_started'], 0)
        self.assertTrue((self.root / 'source/.u-filter-opportunity-source-identity.json').is_file())
        self.assertEqual(len(list((self.root / 'frozen-patches').glob('*.patch'))), 7)


if __name__ == '__main__': unittest.main()
