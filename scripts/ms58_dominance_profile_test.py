"""No-solver checks for the isolated dominance profiler controllers."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ms58_dominance_profile_audit as audit
import ms58_dominance_profile_prepare as preparation
import ms58_dominance_profile_run as run
from ms58_dominance_profile_stage import stage


class DominanceHelpers(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pins = json.loads((Path(__file__).resolve().parents[1] / 'configs/ms58-dominance-profile-source.json').read_text())

    def plan(self):
        return [{'name': name, 'source_id': source, 'manifest_path': 'unused',
                 'worker_python': 'unused', 'diagnostic_environment': {}}
                for name, source in run.SOURCES.items()]

    def records(self):
        outcome = {'finished': 0, 'sampled_finished': 0, 'sampled_ns': 0,
                   'sampled_stage_ns': {'construction': 0, 'scan': 0, 'materialization': 0},
                   'counters': {'partial_false': 0, 'partial_split': 0}}
        operation = {'attempts': 0, 'sampled_attempts': 0,
                     'survived': copy.deepcopy(outcome), 'eliminated': copy.deepcopy(outcome)}
        direction = {kind: copy.deepcopy(operation) for kind in audit.KINDS}
        def record(event, call, **fields):
            return {'schema': audit.SCHEMA, 'diagnostic_only': True, 'event': event, 'call': call,
                    'forward': copy.deepcopy(direction), 'backward': copy.deepcopy(direction), **fields}
        opening = record('profile_open', 0, base_source=audit.BASE, sample_stride=1009, sample_offset=0)
        context = {'level': 'Exact', 'correcting': True, 'lazy_extension': True}
        begin = record('pricing_begin', 1, context=context, pricing_returned=False)
        end = record('pricing_end', 1, pricing_returned=True, native_status='TimeLimitReached',
                     native_log={'status': 'TimeLimitReached'}, returned_routes=2, pool_size=3)
        op = end['forward']['dominance']
        op.update(attempts=1, sampled_attempts=1)
        op['survived'].update(finished=1, sampled_finished=1, sampled_ns=30,
                             sampled_stage_ns={'construction': 5, 'scan': 20, 'materialization': 5},
                             counters={'partial_false': 1, 'partial_split': 1})
        unfinished = record('pricing_begin', 2, context=context, pricing_returned=False)
        progress = record('pricing_progress', 2, pricing_returned=False)
        progress['forward']['dominance'].update(attempts=1, sampled_attempts=1)
        return [opening, begin, end, unfinished, progress]

    def write(self, records):
        path = self.root / 'profile.jsonl'
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        return path

    def test_exact_plan_and_patch_stacks(self):
        run.validate_plan(self.plan())
        for variant in self.plan():
            variant['source_id'] = '327e481'
            with self.assertRaises(ValueError):
                run.validate_plan([variant])
        expected = set(self.pins['baseline_patch_sha256']) | {self.pins['selected_full_patch_sha256']}
        manifest = {'source_patch_files': {str(i): value for i, value in enumerate(expected)}}
        preparation.validate_patch_stack(manifest, self.pins, profile=False)
        with self.assertRaises(ValueError):
            preparation.validate_patch_stack(manifest, self.pins, profile=True)
        manifest['source_patch_files']['profile'] = self.pins['incremental_patch_sha256']
        preparation.validate_patch_stack(manifest, self.pins, profile=True)
        manifest['source_patch_files']['duplicate'] = self.pins['incremental_patch_sha256']
        with self.assertRaises(ValueError):
            preparation.validate_patch_stack(manifest, self.pins, profile=True)

    def test_correction_on_is_expected_and_observed_separately(self):
        task = {'variant': 'correction-on'}
        self.assertEqual(run.algorithm_setting_audit(task, None)['label_correction']['expected'], True)
        self.assertEqual(run.algorithm_setting_audit(task, None)['status'], 'unknown')
        for variant in ('correction-on', 'dominance-profile'):
            task['variant'] = variant
            observed = {'exact': {'effective_algorithm_settings': {'label_correction': True, 'lazy_extension': True}}}
            self.assertEqual(run.algorithm_setting_audit(task, observed)['status'], 'passed')
            observed['exact']['effective_algorithm_settings']['label_correction'] = False
            self.assertEqual(run.algorithm_setting_audit(task, observed)['status'], 'failed')
            self.assertEqual(run.algorithm_setting_audit(task, {'exact': {}})['status'], 'failed')
        task['variant'] = 'lera-minimal'
        observed['exact']['effective_algorithm_settings']['label_correction'] = False
        self.assertEqual(run.algorithm_setting_audit(task, observed)['status'], 'passed')

    def test_unfinished_sample_is_retained_and_partial_survivor_mutates(self):
        path = self.write(self.records())
        with path.open('ab') as output:
            output.write(b'{"event":"pricing_end"')
        result = audit.summarize_profile(path)
        self.assertEqual(result['unfinished_calls'], [2])
        self.assertEqual(result['returned_tasks'], 1)
        self.assertEqual(result['returned_native_statuses'], {'TimeLimitReached': 1})
        self.assertGreater(result['trailing_fragment_bytes'], 0)
        group = result['aggregates']['returned_tasks/Exact/forward/dominance/survived']
        self.assertEqual(group['counters'], {'partial_false': 1, 'partial_split': 1})
        self.assertEqual(group['sampled_stage_fraction']['scan'], 2 / 3)
        self.assertFalse(result['throughput_claim'])

    def test_profile_source_mode_and_timer_corruption_rejected(self):
        for mutation in ('mode', 'status', 'timers', 'unfinished_end'):
            records = self.records()
            if mutation == 'mode':
                records[1]['context']['correcting'] = False
            elif mutation == 'status':
                records[2]['native_status'] = 'Finished'
            elif mutation == 'timers':
                records[2]['forward']['dominance']['survived']['sampled_ns'] += 1
            else:
                records[2]['forward']['dominance']['attempts'] += 1
            with self.assertRaises(ValueError):
                audit.summarize_profile(self.write(records))

    def test_real_budget_release_precedes_any_allocation_or_solver(self):
        args = SimpleNamespace(output=self.root, release_real_budget='wrong')
        with patch.object(run, 'verify_schedule', return_value=({}, 'correct')), \
                patch.object(run, 'allocation') as allocated, patch.object(run, 'execute_cell') as execute:
            with self.assertRaises(ValueError):
                run.launch(args)
            allocated.assert_not_called()
            execute.assert_not_called()

    def test_stop_before_retains_skipped_cell(self):
        (self.root / 'rows').mkdir()
        task = {'task_id': 'skipped', 'variant': 'dominance-profile'}
        deadline = datetime.now(timezone.utc) + timedelta(seconds=1)
        with patch.object(run, 'remaining_seconds', return_value=1), patch.object(run.subprocess, 'Popen') as process:
            row = run.execute_cell(task, self.root, {}, deadline, 1)
            process.assert_not_called()
        self.assertEqual(row['status'], 'skipped_stop_before')
        self.assertFalse(row['launched'])
        self.assertTrue((self.root / 'rows/skipped.json').is_file())

    def test_prepare_preserves_each_venv_interpreter_symlink(self):
        from checker_contract import sha256
        base_python = self.root / 'base-python'
        base_python.write_text('Base interpreter without venv packages')
        pythons = {}
        installed = {}
        manifests = {}
        checker = {'fixture': True}
        patch_path = self.root / 'source.patch'
        patch_path.write_text('source patch bytes')
        for name, source in run.SOURCES.items():
            python = self.root / name / 'bin/python'
            python.parent.mkdir(parents=True)
            python.symlink_to(base_python)
            pythons[name] = python
            core = self.root / f'{name}-core.so'
            lera = self.root / f'{name}-lera.so'
            core.write_bytes(b'core')
            lera.write_bytes(b'kayros-ms58-dominance-profile-v1' if name == 'dominance-profile' else b'control')
            installed[str(python)] = {'backend': 'CPLEX', 'core': str(core), 'lera': str(lera)}
            manifests[name] = {'schema_version': 1, 'checker': checker, 'source_id': source,
                'runtime_files': {str(core): sha256(core), str(lera): sha256(lera)},
                'source_patch_files': {str(patch_path): sha256(patch_path)}}
        runtime_pins = {}
        for name in ('correction-on', 'dominance-profile'):
            runtime_pins[name] = {'source_id': run.SOURCES[name], **{
                kind + '_sha256': sha256(Path(installed[str(pythons[name])][kind])) for kind in ('core', 'lera')}}
        runtime_path = self.root / 'runtime.json'
        runtime_path.write_text(json.dumps(runtime_pins))
        pins_path = self.root / 'pins.json'
        pins_path.write_text(json.dumps(self.pins))
        control_path = self.root / 'control.json'
        control_path.write_text(json.dumps(manifests['correction-on']))
        lera_path = self.root / 'lera.json'
        lera_path.write_text(json.dumps(manifests['lera-minimal']))
        gate_path = self.root / 'gate.json'
        gate_path.write_text('{}')
        worker = self.root / 'worker.py'
        worker.write_text('never executed')
        args = SimpleNamespace(job_id='123', cores=[2, 4], native_gate=gate_path,
            profile_source=self.root / 'source', control_source=self.root / 'control',
            source_pins=pins_path, runtime_pins=runtime_path, output=self.root / 'artifact-output',
            control_python=pythons['correction-on'], control_manifest=control_path,
            profile_python=pythons['dominance-profile'], profile_patches=self.root,
            lera_python=pythons['lera-minimal'], lera_manifest=lera_path, lera_worker=worker)
        with patch.object(preparation, 'allocation', return_value={}), \
                patch.object(preparation, 'checker_identity', return_value=checker), \
                patch.object(preparation, 'validate_gate', return_value={'identity': {}}), \
                patch.object(preparation, 'validate_patch_stack'), \
                patch.object(preparation, 'candidate_manifest', return_value=copy.deepcopy(manifests['dominance-profile'])), \
                patch.object(preparation, 'installed', side_effect=lambda python: installed[str(python)]):
            self.assertEqual(preparation.prepare(args), 0)
        plan = json.loads((args.output / 'plan.json').read_text())
        for variant in plan:
            lexical = pythons[variant['name']].absolute()
            self.assertEqual(variant['worker_python'], str(lexical))
            self.assertNotEqual(variant['worker_python'], str(lexical.resolve()))
            self.assertTrue(Path(variant['worker_python']).is_symlink())

    def test_shared_pooled_bound_gate_uses_other_arms_checked_witness(self):
        common = {'instance_key': 'Dabia2013/n=100/C104', 'instance_sha256': 'instance',
            'atf_sha256': 'atf', 'reference_cost': 100, 'reference_manifest_sha256': 'fixed',
            'horizon_s': 600, 'audit_ok': True, 'finished_task': True, 'status': 'completed',
            'observation_usable': True, 'witness_evidence_audited': True,
            'checked_candidates': [], 'global_bound_claims': [], 'raw_closure_claims': []}
        control = dict(copy.deepcopy(common), task_id='control')
        control['global_bound_claims'] = [{'global_lb': 95, 'sequence': 1, 'elapsed_s': 1}]
        diagnostic = dict(copy.deepcopy(common), task_id='diagnostic')
        diagnostic['checked_candidates'] = [{'checked_cost': 90, 'sequence': 2, 'elapsed_s': 2}]
        run.apply_feasible_consistency([control, diagnostic])
        self.assertFalse(control['proof_observation_usable'])
        self.assertEqual(control['consistency_audit']['status'], 'contradicted')
        self.assertEqual(control['consistency_audit']['feasible_ceiling']['cost'], 90)
        self.assertEqual(control['reference_cost'], 100)

    def test_prepare_freezes_six_cells_without_starting_solver(self):
        inputs = self.root / 'benchmarks/TDVRPTW/Dabia2013/n=100'
        inputs.mkdir(parents=True)
        native = self.root / 'native/instances/dabia_et_al_2013'
        native.mkdir(parents=True)
        for name in run.INSTANCES:
            (inputs / f'{name}.vrp.json').write_text(json.dumps({'instance_name': name,
                'num_customers': 100, 'td': {'atf_path': f'{name}.atf.json'}}))
            (inputs / f'{name}.atf.json').write_text('{}')
            (native / f'{name}_100.json').write_text('{}')
        python = self.root / 'python'
        python.write_text('never executed')
        worker = self.root / 'worker.py'
        worker.write_text('never executed')
        plan_directory = self.root / 'prepared'
        plan_directory.mkdir()
        plan_path = plan_directory / 'plan.json'
        plan_path.write_text(json.dumps(self.plan()))
        pins = self.root / 'pins.json'
        pins.write_text(json.dumps(self.pins))
        gate_path = self.root / 'gate.json'
        gate_path.write_text('{}')
        from checker_contract import sha256
        seal = {'native_gate_sha256': sha256(gate_path), 'native_identity': {}, 'checker': {'fixture': True}}
        output = self.root / 'pilot'
        args = SimpleNamespace(job_id='123', cores=[2, 4], plan=plan_path, native_gate=gate_path,
            profile_source=self.root / 'source', control_source=self.root / 'control', source_pins=pins,
            output=output, inputs=inputs, references=self.root / 'refs', sample_stride=1009, sample_offset=0)
        def frozen(variant, destination, checker):
            hashes = set(self.pins['baseline_patch_sha256']) | {self.pins['selected_full_patch_sha256']}
            if variant['name'] == 'dominance-profile':
                hashes.add(self.pins['incremental_patch_sha256'])
            manifest = {'declared_source_commit': variant['source_id'],
                'source_patch_files': {str(i): value for i, value in enumerate(hashes)},
                'runtime_files': {}, 'worker_environment': {'UPSTREAM_SOURCE_ROOT': str(self.root / 'native')}
                if variant['name'] == 'lera-minimal' else {}}
            return manifest, str(python), str(worker)
        allocated = {'host': 'fixture', 'oar_job_id': '123', 'physical_slots': [{'cpu_core': 2}, {'cpu_core': 4}]}
        with patch.object(run, 'allocation', return_value=allocated), \
                patch.object(run, 'prepared_artifacts', return_value=seal), \
                patch.object(run, 'validate_gate', return_value={'identity': {}}), \
                patch.object(run, 'checker_identity', return_value={'fixture': True}), \
                patch.object(run, 'freeze_variant', side_effect=frozen), \
                patch.object(run, 'freeze_references'), patch.object(run, 'execute_cell') as execute:
            self.assertEqual(run.prepare(args), 0)
            execute.assert_not_called()
        spec = json.loads((output / 'schedule.json').read_text())
        cells = spec['scheduled_cells']
        self.assertEqual(len(cells), 6)
        self.assertEqual({(cell['instance_name'], cell['variant']) for cell in cells},
                         {(name, arm) for name in run.INSTANCES for arm in run.SOURCES})
        self.assertEqual([cell['variant'] for cell in cells[:3]], list(reversed([cell['variant'] for cell in cells[3:]])))
        self.assertTrue(all(cell['horizon_s'] == 600 and cell['memory_mb'] == 16384 for cell in cells))
        profiles = [cell for cell in cells if cell['variant'] == 'dominance-profile']
        self.assertEqual(len({cell['diagnostic_environment']['KAYROS_DOMINANCE_PROFILE'] for cell in profiles}), 2)
        self.assertTrue(all(not cell['diagnostic_environment'] for cell in cells if cell not in profiles))
        self.assertEqual(json.loads((output / 'stop-before-real-budget.json').read_text())['solver_cells_started'], 0)

    def test_actual_source_archive_stages_and_preserves_all_six_patches(self):
        experiment = Path(__file__).resolve().parents[1]
        archive = experiment / 'artifacts/ms58-dominance-profile-a2dcf8870c17/source.tar.gz'
        if not archive.is_file():
            self.skipTest('Local source artifact is absent')
        expected = set(self.pins['baseline_patch_sha256']) | {
            self.pins['selected_full_patch_sha256'], self.pins['incremental_patch_sha256']}
        from checker_contract import sha256
        patches = self.root / 'patches'
        patches.mkdir()
        found = {}
        for directory in (experiment / 'patches', archive.parent):
            for path in directory.rglob('*.patch'):
                value = sha256(path)
                if value in expected:
                    found.setdefault(value, path)
        self.assertEqual(set(found), expected)
        import shutil
        for index, path in enumerate(found.values()):
            shutil.copyfile(path, patches / f'{index}.patch')
        result = stage(archive, experiment / 'configs/ms58-dominance-profile-source.json', patches,
                       self.root / 'source', self.root / 'patches-frozen')
        self.assertEqual(result['solver_cells_started'], 0)
        self.assertEqual(len(list((self.root / 'patches-frozen').glob('*.patch'))), 6)
        self.assertEqual((self.root / 'source/cpp/lera/main/include/labeling/dominance_profile.h').is_file(), True)
        with self.assertRaises(FileExistsError):
            stage(archive, experiment / 'configs/ms58-dominance-profile-source.json', patches,
                  self.root / 'source', self.root / 'new-patches')


if __name__ == '__main__':
    unittest.main()
