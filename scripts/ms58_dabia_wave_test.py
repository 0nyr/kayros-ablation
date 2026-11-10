"""Contract checks for the Dabia schedule, immutable preparation and isolation."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from checker_contract import sha256
import ms58_dabia_wave as wave


class DabiaContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def input(self, name='C201', n=100):
        return {'instance_key': f'Dabia2013/n={n}/{name}', 'num_customers': n,
                'instance_name': name, 'instance_path': f'/inputs/benchmarks/TDVRPTW/Dabia2013/n={n}/{name}.vrp.json'}

    def test_five_arm_180_cell_schedule_and_prefix_identity(self):
        names = ['C101', 'C104', 'C201', 'C202', 'R101', 'R112', 'R201', 'R202', 'RC104', 'RC105', 'RC201', 'RC202']
        slots = [{'host': 'node-1.test', 'cpu_core': n} for n in range(10)]
        tasks = wave.planned_tasks([self.input(name) for name in names], slots, 3)
        self.assertEqual(len(tasks), 180)
        self.assertEqual(len({t['task_id'] for t in tasks}), 180)
        self.assertEqual(set(t['arm_id'] for t in tasks), {a[0] for a in wave.ARMS})
        for task in tasks:
            wave.validate_task(task)
            if task['arm'] == 'kayros-full-cplex':
                self.assertIn(task['prefix_s'], (5., 15.))
                self.assertEqual(task['seed'], [42, 123, 456][task['replicate'] - 1])
            else:
                self.assertIsNone(task['seed'])
        full = next(t for t in tasks if t['prefix_s'] == 5.)
        full.update(manifest_path='/m.json', worker_python='/python', output_directory='/out')
        command = wave.launcher_command(full, Path('/wave'), '/checker', '/taskset')
        self.assertEqual(command[command.index('--horizon') + 1], '3600.0')
        self.assertEqual(command[command.index('--prefix-seconds') + 1], '5.0')
        bad = dict(full, prefix_s=15.)
        with self.assertRaisesRegex(ValueError, 'prefix identity'):
            wave.validate_task(bad)

    def test_reject_other_datasets_sizes_and_overrides(self):
        for path in ('/benchmarks/TDVRPTW/Vu2018/n=100/C101.vrp.json',
                     '/benchmarks/TDVRPTW/Dabia2013/n=200/C101.vrp.json'):
            with self.assertRaises(ValueError):
                wave.input_size(path)
        for prefix in ('KAYROS_', 'UPSTREAM_', 'PROBE_'):
            with self.assertRaises(ValueError):
                wave.no_overrides({prefix + 'UNEXPECTED': '1'})

    def test_distinct_sizes_do_not_collide(self):
        tasks = wave.planned_tasks([self.input(n=n) for n in (25, 50, 100)],
                                   [{'host': 'node', 'cpu_core': 0}], 1)
        self.assertEqual(len({t['task_id'] for t in tasks}), 15)

    def test_sibling_core_rejection(self):
        for cpu, core in ((0, 0), (1, 1), (16, 0)):
            base = self.root / f'cpu{cpu}/topology'
            base.mkdir(parents=True)
            (base / 'physical_package_id').write_text('0')
            (base / 'core_id').write_text(str(core))
            (base / 'thread_siblings_list').write_text('0,16' if core == 0 else '1,17')
        self.assertEqual(len(wave.topology_snapshot([0, 1], self.root, {0, 1, 16})), 2)
        with self.assertRaisesRegex(ValueError, 'physical core'):
            wave.topology_snapshot([0, 16], self.root, {0, 1, 16})
        with self.assertRaisesRegex(ValueError, 'actual OAR affinity'):
            wave.topology_snapshot([1], self.root, {0, 16})

    def test_backend_drift_and_missing_early_failure_evidence(self):
        task = {'runtime_key': 'highs'}
        events = [{'phase': 'exact', 'lp_backend': 'CPLEX'}]
        self.assertEqual(wave.backend_audit(task, {}, events, {})['status'], 'failed')
        events[0]['lp_backend'] = 'HiGHS'
        self.assertTrue(wave.backend_audit(task, {}, events, {})['passed'])
        events.append({'effective_backend_settings': {'lp': {'backend': 'HiGHS', 'threads': 2}}})
        self.assertEqual(wave.backend_audit(task, {}, events, {})['status'], 'failed')
        self.assertEqual(wave.backend_audit(task, {}, [], {'status': 'deadline'})['status'], 'unavailable')

    def fixture_design(self, n=100):
        checker = {'test': 'frozen-checker'}
        inputs = self.root / 'inputs'
        rel = Path(f'benchmarks/TDVRPTW/Dabia2013/n={n}/C201.vrp.json')
        instance = self.write(inputs / rel, {'instance_name': 'C201', 'num_customers': n,
                                             'td': {'atf_path': 'C201.atf.json'}})
        atf = self.write(instance.parent / 'C201.atf.json', {'identity': 'atf'})
        refs = self.root / 'references'
        solution = self.write(refs / 'solutions/C201.json', {'routes': []})
        check = self.write(refs / 'checks/C201.json', {'status': 'valid', 'routing_cost': 123.})
        entry = {'instance_name': 'C201', 'instance_path': str(rel), 'instance_sha256': sha256(instance),
            'atf_path': str(rel.parent / atf.name), 'atf_file_sha256': sha256(atf),
            'reference_cost': 123., 'reference_cost_hex': float(123).hex(),
            'solution_path': 'solutions/C201.json', 'solution_sha256': sha256(solution),
            'check_path': 'checks/C201.json', 'check_sha256': sha256(check)}
        reference = self.write(refs / 'manifest.json', {'schema_version': 1, 'checker': checker, 'instances': [entry]})
        patch_file = self.write(self.root / 'fix.patch', {'private': 'patch'})
        native_root = self.root / 'native'
        self.write(native_root / 'experiments/bp.json', {'experiments': []})
        self.write(native_root / f'instances/dabia_et_al_2013/C201_{n}.json', {'identity': 'native'})
        manifests = {}
        commit = '327e481713e1b9a3187fda2fc9f10f8d9d75b0c5'
        for key in ('cplex', 'highs', 'upstream'):
            runtime = self.write(self.root / f'{key}.binary', {'variant': key})
            manifest = {'schema_version': 1, 'checker': checker, 'backend': 'cplex' if key == 'upstream' else key,
                'source_id': 'f2c9e20, minimal reference' if key == 'upstream' else commit,
                'runtime_kind': 'native' if key == 'upstream' else 'kayros',
                'runtime_files': {str(runtime): sha256(runtime)},
                'source_patch_files': {str(patch_file): sha256(patch_file)}}
            if key == 'upstream':
                manifest['worker_environment'] = {'UPSTREAM_SOURCE_ROOT': str(native_root), 'UPSTREAM_PROBE_BINARY': str(runtime)}
                self.enterContext(patch.object(wave, 'UPSTREAM_BINARY_SHA256', sha256(runtime)))
                self.enterContext(patch.object(wave, 'UPSTREAM_MINIMAL_PATCH_SHA256', sha256(patch_file)))
            path = self.write(self.root / f'{key}.manifest.json', manifest)
            manifests[key] = {'path': str(path), 'sha256': sha256(path), 'worker_python': sys.executable}
        design = {'schema_version': 1, 'dataset': 'Dabia2013', 'purpose': 'performance',
            'kayros_source_id': commit, 'repetitions': 1, 'inputs_root': str(inputs),
            'checker_python': sys.executable, 'references': {str(n): {'path': str(reference), 'sha256': sha256(reference)}},
            'selection': [{'num_customers': n, 'instance_name': 'C201'}], 'manifests': manifests}
        return checker, design

    def test_freeze_identity_and_reject_mutated_cell(self):
        checker, design = self.fixture_design()
        args = argparse.Namespace(design=self.write(self.root / 'design.json', design),
            slots=self.write(self.root / 'slots.json', [{'host': 'node.test', 'cpu_core': 0}]), wave=self.root / 'wave')
        with patch.object(wave, 'checker_identity', return_value=checker):
            wave.prepare(args)
        spec = wave.verify_wave(args.wave)
        self.assertEqual(spec['scheduled_count'], 5)
        lera = next(t for t in spec['scheduled_cells'] if t['arm_id'] == 'lera-minimal')
        self.assertIn('native_instance_sha256', lera)
        manifest = json.loads(Path(lera['manifest_path']).read_text())
        self.assertEqual(manifest['worker_environment']['UPSTREAM_NATIVE_INPUT_SHA256'], lera['native_instance_sha256'])
        with patch.object(wave.subprocess, 'run', return_value=SimpleNamespace(returncode=17)), \
                patch.object(wave.shutil, 'which', return_value='/taskset'):
            failed = wave.execute(lera, args.wave, sys.executable)
        self.assertEqual(failed['status'], 'launch_or_audit_failure')
        stored = json.loads((args.wave / 'rows' / f"{lera['task_id']}.json").read_text())
        self.assertTrue(all(stored[k] == v for k, v in lera.items()))
        self.assertIn('exit 17', stored['reason'])
        spec['scheduled_cells'][0]['memory_mb'] = 8192
        self.write(args.wave / 'wave.json', spec)
        with self.assertRaisesRegex(ValueError, 'budget drift'):
            wave.verify_wave(args.wave)

    def test_performance_rejects_n50_before_any_launch(self):
        checker, design = self.fixture_design(50)
        directory = self.root / 'out'
        (directory / 'frozen-references').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'n=100 exclusively'):
            wave.freeze_inputs(design, directory, checker)

    def test_wrong_source_or_backend_manifest_rejected(self):
        checker, design = self.fixture_design()
        output = self.root / 'out'
        output.mkdir()
        source = Path(design['manifests']['cplex']['path'])
        manifest = json.loads(source.read_text())
        manifest['source_id'] = '0787925'
        self.write(source, manifest)
        spec = dict(design['manifests']['cplex'], sha256=sha256(source))
        with self.assertRaisesRegex(ValueError, 'source must be'):
            wave.freeze_manifest(spec, 'cplex', output, checker, design['kayros_source_id'])
        manifest['source_id'] = design['kayros_source_id']
        manifest['backend'] = 'highs'
        self.write(source, manifest)
        spec['sha256'] = sha256(source)
        with self.assertRaisesRegex(ValueError, 'another backend'):
            wave.freeze_manifest(spec, 'cplex', output, checker, design['kayros_source_id'])

    def test_mislabeled_minimal_reference_binary_is_rejected(self):
        checker, design = self.fixture_design()
        output = self.root / 'out'
        (output / 'manifests').mkdir(parents=True)
        with patch.object(wave, 'UPSTREAM_BINARY_SHA256', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'validated f2c9e20 artifact'):
                wave.freeze_manifest(design['manifests']['upstream'], 'upstream', output, checker, 'f2c9e20')


if __name__ == '__main__':
    unittest.main()
