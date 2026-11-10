"""Offline screen contract checks. No solver or G5K command is executed."""
from collections import Counter
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from checker_contract import sha256
from probe_runner import atomic_json, digest
import ms58_dabia_screen as screen


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def variants(self):
        return [dict(name=name, source_id=source, manifest_path='/manifest',
                     worker_python=sys.executable, diagnostic_environment={})
                for name, source in [('lera-minimal', screen.LERA), ('best', screen.BEST)]]

    def test_balanced_positions_and_pair_order_for_all_panel_sizes(self):
        for n in (2, 3, 4):
            names = list('abcd')[:n]
            orders = screen.balanced_orders(names)
            self.assertEqual(len(orders), 9)
            self.assertTrue(all(sorted(order) == names for order in orders))
            for position in range(n):
                counts = Counter(order[position] for order in orders)
                self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
            for a, b in itertools.combinations(names, 2):
                count = sum(order.index(a) < order.index(b) for order in orders)
                self.assertIn(count, (4, 5))

    def test_required_controls_and_explicit_isolated_switches(self):
        plan = self.variants()
        screen.validate_plan(plan)
        for bad in [plan[:1], [plan[0], dict(plan[1], source_id='0787925')],
                    [plan[0], dict(plan[1], diagnostic_environment={'KAYROS_ANY': '1'})],
                    plan + [dict(plan[1], name='scratch', source_id='1234567',
                                 diagnostic_environment={'LD_PRELOAD': '/bad'})]]:
            with self.assertRaises(ValueError):
                screen.validate_plan(bad)
        screen.validate_plan(plan + [dict(plan[1], name='scratch', source_id='1234567',
                                         diagnostic_environment={'KAYROS_PWL_SCRATCH': '1'})])

    def fixture(self):
        checker = {'test': 'checker'}
        plan = self.variants()
        native = self.write('native-binary', {'bytes': 'minimal'})
        patch_file = self.write('minimal.patch', {'correcting': False})
        best_patch = self.write('best.patch', {'repairs': 'point canonical'})
        python_module = self.write('runtime.so', {'bytes': 'candidate'})
        native_root = self.root / 'upstream'
        self.write('upstream/experiments/bp.json', {'experiments': []})
        inputs = self.root / 'inputs/benchmarks/TDVRPTW/Dabia2013/n=100'
        for name in screen.INSTANCES:
            self.write(f'upstream/instances/dabia_et_al_2013/{name}_100.json', {'name': name})
            self.write(inputs / f'{name}.vrp.json', {'instance_name': name, 'num_customers': 100,
                                                    'td': {'atf_path': f'{name}.atf.json'}})
            self.write(inputs / f'{name}.atf.json', {'time': []})
        for item in plan:
            is_native = item['name'] == 'lera-minimal'
            runtime = native if is_native else python_module
            source_patch = patch_file if is_native else best_patch
            manifest = {'schema_version': 1, 'checker': checker, 'backend': 'CPLEX',
                'runtime_kind': 'native' if is_native else 'kayros', 'source_id': item['source_id'],
                'runtime_files': {str(runtime): sha256(runtime)},
                'source_patch_files': {str(source_patch): sha256(source_patch)}, 'diagnostic_environment': {}}
            if is_native:
                manifest['worker_environment'] = {'UPSTREAM_PROBE_BINARY': str(native),
                                                   'UPSTREAM_SOURCE_ROOT': str(native_root)}
            item['manifest_path'] = str(self.write(f"{item['name']}.manifest.json", manifest))
        args = SimpleNamespace(plan=self.write('plan.json', plan), output=self.root / 'out', inputs=inputs, seed=20261110)
        allocation = {'host': 'compute.test', 'physical_slots': [{'cpu_core': n} for n in range(9)]}
        with patch.object(screen, 'LERA_BINARY', sha256(native)), patch.object(screen, 'LERA_PATCH', sha256(patch_file)), \
                patch.object(screen, 'BEST_PATCH', sha256(best_patch)), \
                patch.object(screen.subprocess, 'check_output', return_value='CPLEX\n'):
            spec = screen.prepare(args, allocation, checker)
        return args.output, spec

    def test_freeze_schedule_and_retain_launcher_failures_in_denominator(self):
        output, spec = self.fixture()
        self.assertEqual(spec['scheduled_count'], 18)
        self.assertEqual(len({task['task_id'] for task in spec['scheduled_cells']}), 18)
        self.assertEqual(Counter(t['cpu_core'] for t in spec['scheduled_cells']), Counter({n: 2 for n in range(9)}))
        for task in spec['scheduled_cells']:
            self.assertEqual(task['horizon_s'], 600.)
            self.assertEqual(task['memory_mb'], 16384)
        task = spec['scheduled_cells'][0]
        with patch.object(screen.subprocess, 'run', return_value=SimpleNamespace(returncode=17)), \
                patch.object(screen.shutil, 'which', return_value='/taskset'):
            row = screen.execute(task, output, spec)
        self.assertEqual(row['status'], 'launch_or_audit_failure')
        self.assertIn('exit 17', row['reason'])
        self.assertFalse(row['audit_ok'])
        monitor = screen.monitor(output)
        self.assertEqual(monitor['scheduled_cells'], 18)
        self.assertEqual(monitor['completed_rows'], 1)
        self.assertEqual(len(monitor['pending_cells']), 17)
        self.assertEqual(monitor['failed_or_unusable_cells'], [task['task_id']])
        with self.assertRaises(FileExistsError):
            screen.immutable_json(output / 'rows' / f"{task['task_id']}.json", {'status': 'success'})
        self.assertEqual(json.loads((output / 'rows' / f"{task['task_id']}.json").read_text())['status'], row['status'])

    def test_schedule_tampering_is_rejected(self):
        output, spec = self.fixture()
        spec['scheduled_cells'][0]['memory_mb'] = 8192
        atomic_json(output / 'schedule.json', spec)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            screen.monitor(output)

    def test_declared_environment_changes_expected_identity(self):
        output, spec = self.fixture()
        task = next(t for t in spec['scheduled_cells'] if t['runtime_key'] == 'cplex')
        manifest = json.loads(Path(task['manifest_path']).read_bytes())
        plain = screen.expected_identity(task, output, manifest, {})
        changed = screen.expected_identity(task, output, manifest, {'KAYROS_PWL_SCRATCH': '1'})
        self.assertNotEqual(digest(plain), digest(changed))
        self.assertEqual(plain['parameters']['repetition'], task['replicate'])
        self.assertEqual(plain['parameters']['cpu_affinity'], [task['cpu_core']])

    def test_wrong_minimal_binary_rejected_before_launch(self):
        output, spec = self.fixture()
        plan = json.loads((output / 'plan.json').read_text())
        other = self.root / 'other'
        (other / 'frozen-scripts').mkdir(parents=True)
        (other / 'manifests').mkdir()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            screen.freeze_variant(plan[0], other, spec['checker'])

    def test_smt_siblings_rejected_as_separate_physical_lanes(self):
        for cpu in (0, 16):
            directory = self.root / f'cpu{cpu}/topology'
            directory.mkdir(parents=True)
            for name, value in [('physical_package_id', '0'), ('core_id', '0'), ('thread_siblings_list', '0,16')]:
                (directory / name).write_text(value)
        with self.assertRaisesRegex(ValueError, 'physical core'):
            screen.topology_snapshot([0, 16], self.root, {0, 16})

    def test_safe_correction_requires_its_explicit_source_and_switch(self):
        safe = dict(self.variants()[1], name='safe-correction', source_id=screen.SAFE_CORRECTION,
                    diagnostic_environment={'KAYROS_DIAGNOSTIC_CORRECTING': '1'})
        screen.validate_plan(self.variants() + [safe])
        for bad in [dict(safe, diagnostic_environment={}),
                    dict(safe, source_id='1234567'),
                    dict(safe, diagnostic_environment={'KAYROS_DIAGNOSTIC_CORRECTING': '0'})]:
            with self.assertRaises(ValueError):
                screen.validate_plan(self.variants() + [bad])

    def test_correction_native_evidence_and_lazy_setting_audit(self):
        safe = {'source_id': screen.SAFE_CORRECTION}
        def payload(**settings):
            return {'exact': {'effective_algorithm_settings': settings}}
        observed = screen.algorithm_setting_audit(safe, payload(label_correction=True))
        self.assertEqual(observed['label_correction']['status'], 'passed')
        self.assertEqual(observed['lazy_extension']['status'], 'unknown')
        for raw in [payload(label_correction=False), payload(), payload(label_correction=1),
                    payload(label_correction=True, lazy_extension=False)]:
            self.assertEqual(screen.algorithm_setting_audit(safe, raw)['status'], 'failed')
        missing = screen.algorithm_setting_audit(safe, None)
        self.assertEqual(missing['status'], 'unknown')
        self.assertIsNone(missing['label_correction']['observed'])
        self.assertFalse(missing['native_sidecar_present'])
        self.assertEqual(screen.algorithm_setting_audit(safe,
            payload(label_correction=True, lazy_extension=True))['status'], 'passed')
        default = screen.algorithm_setting_audit({'source_id': screen.BEST}, payload())
        self.assertFalse(default['label_correction']['expected'])
        self.assertEqual(default['label_correction']['status'], 'unknown')
        self.assertIsNone(default['label_correction']['observed'])


if __name__ == '__main__':
    unittest.main()
