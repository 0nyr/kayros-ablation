"""Local scheduler and deadline checks without solver execution."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import ms58_domain_batch_run as run


class BatchScheduleTests(unittest.TestCase):
    def test_three_and_six_lanes_keep_all_balanced_cells(self):
        for count in (3, 6):
            with self.subTest(count=count):
                cores = list(range(2, 2 + count))
                triplets = run.comparison_triplets(cores)
                cells = [(t['instance'], t['replicate'], variant) for t in triplets for variant in t['order']]
                expected = {(name, rep, variant) for name in run.INSTANCES
                            for rep in range(1, 4) for variant in run.SOURCES}
                self.assertEqual(len(cells), 18)
                self.assertEqual(set(cells), expected)
                self.assertEqual([t['order'] for t in triplets], run.ORDERS)
                self.assertEqual(Counter(t['lane'] for t in triplets), {lane: 6 // count for lane in range(count)})
                for index, triplet in enumerate(triplets):
                    self.assertEqual(triplet['cpu_core'], cores[index % count])
                    self.assertEqual(triplet['lane_round'], index // count)
                for position in range(3):
                    self.assertEqual(Counter(t['order'][position] for t in triplets), {name: 2 for name in run.SOURCES})

    def test_wrong_core_shapes_reject(self):
        for cores in ([1, 2], [1, 2, 2], [1, 2, 3, 4]):
            with self.assertRaises(ValueError):
                run.comparison_triplets(cores)

    def test_prepared_schedule_has_all_cells_and_one_host(self):
        for count in (3, 6):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                native = root / 'native'
                native.mkdir()
                for name in ('complete.json', 'plan.json', 'source-identity.json', 'fixture.stdout', 'fixture.stderr'):
                    (native / name).write_text('{}')
                plan = [dict(name=name, source_id=source, manifest_path='stub', worker_python=sys.executable,
                             diagnostic_environment={}) for name, source in run.SOURCES.items()]
                plan_path = root / 'plan.json'
                plan_path.write_text(json.dumps(plan))
                for name in run.INSTANCES:
                    target = root / f'instances/dabia_et_al_2013/{name}_100.json'
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text('{}')
                def freeze(variant, output, checker):
                    name = variant['name']
                    manifest = dict(declared_source_commit=run.SOURCES[name],
                        source_patch_files={'stub': run.BEST_PATCH if name == 'best' else run.BATCH_PATCH},
                        native_gate_sha256=run.sha256(native / 'complete.json'),
                        worker_environment={'UPSTREAM_SOURCE_ROOT': str(root)},
                        runtime_files={'_lera.so': run.BEST_BINARY if name == 'best' else run.BATCH_BINARY})
                    return manifest, sys.executable, str(Path(__file__).resolve())
                args = SimpleNamespace(plan=plan_path, output=root / 'output', native_gate=native / 'complete.json',
                    inputs=root, references=root, latest_start_utc='2099-01-01T00:00:00+00:00',
                    stop_before='2099-01-01T02:00:00+00:00')
                allocated = dict(host='single-node', physical_slots=[dict(cpu_core=cpu) for cpu in range(count)])
                with (patch.object(run, 'freeze_variant', side_effect=freeze),
                      patch.object(run, 'checker_identity', return_value={}),
                      patch.object(run.shutil, 'copytree'),
                      patch.object(run, 'frozen_inputs', return_value={name: {} for name in run.INSTANCES}),
                      patch.object(run, 'freeze_references'), patch.object(run, 'hardware_snapshot', return_value={})):
                    spec = run.prepare(args, allocated, {'source_id': run.BATCH})
                cells = spec['scheduled_cells']
                self.assertEqual(len(cells), 18)
                self.assertEqual(len({t['task_id'] for t in cells}), 18)
                self.assertEqual({t['host'] for t in cells}, {'single-node'})
                self.assertEqual(spec['cohort']['node_count'], 1)
                self.assertEqual(spec['memory_required_gib'], 16 * count + 16)
                for lane in range(count):
                    lane_cells = [t for t in cells if t['lane'] == lane]
                    self.assertEqual(len(lane_cells), 18 // count)
                    self.assertEqual([t['lane_position'] for t in lane_cells], list(range(1, 18 // count + 1)))
                    self.assertEqual({t['cpu_core'] for t in lane_cells}, {lane})

    def test_both_clocks_enforce_cell_and_prepare_budgets(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=10000)
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        mono = time.monotonic()
        for deadline, monotonic_deadline in ((past, mono + 10000), (future, mono - 1)):
            with self.assertRaises(ValueError):
                run.require_cell_budget(deadline, monotonic_deadline)
            with self.assertRaises(ValueError):
                run.require_prepare_budget(deadline, monotonic_deadline)
        run.require_prepare_budget(future, mono + 10000)
        self.assertEqual(run.PREPARE_BUDGET, 4320)
        with patch.object(run, 'remaining_seconds', return_value=4319):
            with self.assertRaises(ValueError):
                run.require_prepare_budget(future, mono + 10000)

    def task(self, root):
        for name in ('rows', 'started', 'launch-logs'):
            (root / name).mkdir()
        manifest = root / 'manifest.json'
        manifest.write_text('{}')
        task = dict(task_id='synthetic', diagnostic_environment={}, manifest_path=str(manifest),
                    launcher_stdout=str(root / 'launch-logs/out'), launcher_stderr=str(root / 'launch-logs/err'))
        for name in ('instance', 'atf', 'worker_python', 'worker'):
            task[name + '_path' if name != 'worker_python' else name] = str(manifest)
            task[name + '_sha256'] = 'stub'
        task['manifest_sha256'] = 'stub'
        return task

    def test_expired_cell_retains_row_without_start_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = self.task(root)
            with patch.object(run.subprocess, 'Popen') as process:
                row = run.execute_cell(task, root, {}, datetime.now(timezone.utc), time.monotonic() - 1)
            self.assertEqual(row['status'], 'skipped_stop_before')
            self.assertFalse(row['launched'])
            self.assertFalse((root / 'started/synthetic.json').exists())
            self.assertEqual(json.loads((root / 'rows/synthetic.json').read_text()), row)
            process.assert_not_called()

    def check_launch(self, root, budgets, timeout=False):
        task = self.task(root)
        spec = dict(checker_python=sys.executable, checker={})
        with (patch.object(run, 'require_cell_budget', side_effect=budgets),
              patch.object(run, 'checked_file'), patch.object(run.shutil, 'which', return_value='/bin/taskset'),
              patch.object(run, 'launcher_command', return_value=[sys.executable, '-c', 'pass']),
              patch.object(run, 'expected_identity', return_value={}),
              patch.object(run.subprocess, 'Popen') as process,
              patch.object(run, 'terminate_tree') as terminate):
            if timeout:
                process.return_value.__enter__.return_value.wait.side_effect = [subprocess.TimeoutExpired('stub', 690), -9]
            row = run.execute_cell(task, root, spec, datetime.now(timezone.utc), 0)
            return row, process, terminate

    def test_budget_rechecked_immediately_before_process_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            row, process, _ = self.check_launch(Path(directory), [None, ValueError('expired during validation')])
            self.assertEqual(row['status'], 'skipped_stop_before')
            self.assertFalse(row['launched'])
            process.assert_not_called()

    def test_outer_timeout_terminates_tree_and_retains_failure_row(self):
        with tempfile.TemporaryDirectory() as directory:
            row, process, terminate = self.check_launch(Path(directory), [None, None], timeout=True)
            self.assertTrue(row['launched'])
            self.assertTrue(row['launcher_outer_timeout'])
            self.assertEqual(row['status'], 'launch_or_audit_failure')
            self.assertFalse(row['observation_usable'])
            self.assertEqual(process.return_value.__enter__.return_value.wait.call_args_list[0].kwargs, {'timeout': 690})
            terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
