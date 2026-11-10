"""No-solver checks for the extension diagnostic controller and profile audit."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ms58_extension_profile_run as run
from ms58_extension_profile_audit import BASE, SCHEMA, compare_rows, summarize_profile


class ExtensionProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def plan(self):
        return [{'name': name, 'source_id': source, 'manifest_path': 'unused',
                 'worker_python': 'unused', 'diagnostic_environment': {}}
                for name, source in run.EXPECTED_SOURCES.items()]

    def records(self):
        def record(event, call, **extra):
            return dict(schema=SCHEMA, diagnostic_only=True, event=event, call=call, **extra)
        direction = {'attempts': 2, 'sampled_attempts': 1, 'sampled_finished': 1,
            'sampled_total_ns': 30, 'stages': {'pwl_addition': {'entries': 2, 'sampled_entries': 1, 'sampled_ns': 10},
                'pwl_composition': {'entries': 2, 'sampled_entries': 1, 'sampled_ns': 20}},
            'events': {'returned_label': 2}, 'sampled_pieces': {'parent': {'observations': 1, 'sum': 4, 'maximum': 4}}}
        common = dict(forward=direction, backward=direction)
        return [record('profile_open', 0, base_source=BASE, sample_stride=1009, sample_offset=0),
            record('pricing_begin', 1, context={'level': 'HeuristicCost'}, pricing_returned=False, **common),
            record('pricing_end', 1, pricing_returned=True, native_log={'status': 'SolutionLimitReached'},
                   returned_routes=3, pool_size=3, **common),
            record('pricing_begin', 2, context={'level': 'Exact'}, pricing_returned=False, **common),
            record('pricing_progress', 2, pricing_returned=False, **common)]

    def write(self, records):
        path = self.root / 'profile.jsonl'
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        return path

    def test_required_pinned_arms_and_no_inherited_plan_switches(self):
        run.validate_plan(self.plan())
        for mutation in ('missing_lera', 'wrong_source', 'profile_path'):
            plan = self.plan()
            if mutation == 'missing_lera':
                plan.pop()
            elif mutation == 'wrong_source':
                plan[0]['source_id'] = '1234567'
            else:
                plan[1]['diagnostic_environment'] = {'KAYROS_EXTENSION_PROFILE': '/tmp/reused'}
            with self.assertRaises(ValueError):
                run.validate_plan(plan)

    def test_full_patch_stack_is_accepted_only_with_four_baseline_pins(self):
        def manifest(hashes):
            return {'source_patch_files': {str(i): h for i, h in enumerate(hashes)}}
        run.validate_profile_patch_stack(manifest(run.BASELINE_PATCHES | {run.PROFILE_FULL_PATCH}))
        run.validate_profile_patch_stack(manifest(run.BASELINE_PATCHES | {run.PROFILE_PATCH, run.BEST_PATCH}))
        with self.assertRaises(ValueError):
            run.validate_profile_patch_stack(manifest({run.PROFILE_FULL_PATCH}))
        with self.assertRaises(ValueError):
            run.validate_profile_patch_stack(manifest(run.BASELINE_PATCHES | {run.BEST_PATCH}))

    def test_incomplete_task_and_stage_counts_stay_separate(self):
        summary = summarize_profile(self.write(self.records()))
        self.assertEqual(summary['returned_tasks'], 1)
        self.assertEqual(summary['unfinished_calls'], [2])
        self.assertEqual(summary['returned_native_statuses'], {'SolutionLimitReached': 1})
        group = summary['aggregates']['unfinished_tasks_last_snapshot/Exact/forward']
        self.assertEqual(group['attempts'], 2)
        self.assertEqual(group['stages']['pwl_composition']['sampled_fraction'], 2 / 3)
        self.assertFalse(summary['throughput_claim'])

    def test_truncated_tail_retained_without_inventing_end(self):
        path = self.write(self.records())
        with path.open('ab') as stream:
            stream.write(b'{"event":"pricing_end"')
        summary = summarize_profile(path)
        self.assertGreater(summary['trailing_fragment_bytes'], 0)
        self.assertEqual(summary['unfinished_calls'], [2])

    def test_corrupt_sequence_and_overlapping_timers_rejected(self):
        records = self.records()
        records[-1]['call'] = 3
        with self.assertRaises(ValueError):
            summarize_profile(self.write(records))
        records = copy.deepcopy(self.records())
        records[-1]['forward']['sampled_total_ns'] += 1
        with self.assertRaises(ValueError):
            summarize_profile(self.write(records))

    def test_missing_sidecar_never_claims_work_equivalence(self):
        rows = [{'instance_name': 'C104', 'variant': name, 'native_work': {'available': False}}
                for name in run.EXPECTED_SOURCES]
        for comparison in compare_rows(rows):
            self.assertIsNone(comparison['native_work_comparison']['root_work_signature']['equal'])
            self.assertFalse(comparison['throughput_claim'])

    def test_failed_native_gate_starts_no_solver(self):
        arguments = ['controller', '--mode', 'run', '--output', str(self.root),
            '--plan', 'unused.json', '--profile-source', 'unused-source', '--job-id', '123', '--cores', '2', '4']
        with patch.object(run.sys, 'argv', arguments), patch.object(run, 'allocation', return_value={}), \
                patch.object(run, 'checker_identity', return_value={}), patch.object(run, 'prepare', return_value={}), \
                patch.object(run, 'native_gates', return_value=False), patch.object(run, 'execute') as execute:
            self.assertEqual(run.main(), 1)
            execute.assert_not_called()
        self.assertEqual(json.loads((self.root / 'gates-failed.json').read_text())['solver_cells_started'], 0)

    def test_prepare_freezes_six_cells_and_distinct_profile_paths(self):
        inputs = self.root / 'benchmarks/TDVRPTW/Dabia2013/n=100'
        inputs.mkdir(parents=True)
        native_root = self.root / 'native'
        (native_root / 'instances/dabia_et_al_2013').mkdir(parents=True)
        for name in run.INSTANCES:
            (inputs / f'{name}.vrp.json').write_text(json.dumps({'instance_name': name,
                'num_customers': 100, 'td': {'atf_path': f'{name}.atf.json'}}))
            (inputs / f'{name}.atf.json').write_text('{}')
            (native_root / f'instances/dabia_et_al_2013/{name}_100.json').write_text('{}')
        python = self.root / 'python'
        python.write_text('fake executable, never invoked')
        modules = {}
        for name in ('best', 'extension-profile'):
            modules[name] = self.root / f'{name}.so'
            modules[name].write_bytes(b'kayros-ms58-extension-profile-v1' if name == 'extension-profile' else b'uninstrumented')
        output = self.root / 'new-output'
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps(self.plan()))
        args = SimpleNamespace(plan=plan, output=output, profile_source=self.root / 'source',
            inputs=inputs, sample_stride=1009, sample_offset=0)
        def frozen(variant, destination, checker):
            name = variant['name']
            runtime = {} if name == 'lera-minimal' else {str(modules[name]): run.sha256(modules[name])}
            manifest = {'declared_source_commit': variant['source_id'], 'runtime_files': runtime,
                'source_patch_files': {str(i): h for i, h in enumerate(run.BASELINE_PATCHES | {run.PROFILE_FULL_PATCH})},
                'worker_environment': {'UPSTREAM_SOURCE_ROOT': str(native_root)} if name == 'lera-minimal' else {}}
            return manifest, str(python), str(destination / 'frozen-scripts/kayros_worker.py')
        allocated = {'host': 'fixture-node', 'physical_slots': [{'cpu_core': 2}, {'cpu_core': 4}]}
        with patch.object(run, 'PROFILE_BINARY', run.sha256(modules['extension-profile'])), \
                patch.object(run, 'freeze_variant', side_effect=frozen), patch.object(run.subprocess,
                'check_output', side_effect=[str(modules['best']) + '\n', str(modules['extension-profile']) + '\n']):
            spec = run.prepare(args, allocated, {'fixture': True})
        tasks = spec['scheduled_cells']
        self.assertEqual(spec['scheduled_count'], 6)
        self.assertEqual({(t['instance_name'], t['variant']) for t in tasks},
                         {(i, v) for i in run.INSTANCES for v in run.EXPECTED_SOURCES})
        profiles = [t for t in tasks if t['variant'] == 'extension-profile']
        self.assertEqual(len({t['diagnostic_environment']['KAYROS_EXTENSION_PROFILE'] for t in profiles}), 2)
        self.assertTrue(all(t['horizon_s'] == 600 and t['memory_mb'] == 16384 for t in tasks))
        self.assertTrue(all(not t['diagnostic_environment'] for t in tasks if t not in profiles))
        with self.assertRaises(FileExistsError):
            run.prepare(args, allocated, {'fixture': True})


if __name__ == '__main__':
    unittest.main()
