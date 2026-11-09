"""Dry-run scheduling checks. No solver or checker is executed."""
from pathlib import Path
import sys
import json
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from development_wave import launcher_command, repetition_slots, planned_cells


class DevelopmentWaveTests(unittest.TestCase):
    def task(self, arm, repetition, seed):
        return {'cpu_core': 3, 'manifest_path': 'manifest.json', 'worker_python': 'venv/bin/python',
                'instance_path': 'C101.json', 'output_directory': 'cells', 'arm': arm,
                'horizon_s': 300, 'memory_mb': 8192, 'replicate': repetition,
                'seed': seed if arm in ['kayros-heuristics', 'kayros-full-cplex'] else None,
                'prefix_s': 15 if arm == 'kayros-full-cplex' else None}

    def test_three_slots_have_explicit_ids_and_paired_seeds(self):
        self.assertEqual(repetition_slots(3), [(1, 42), (2, 123), (3, 456)])
        for arm in ['kayros-heuristics', 'kayros-full-cplex', 'kayros-bpca-cplex', 'lera-exact']:
            commands = []
            for repetition, seed in repetition_slots(3):
                command = launcher_command(self.task(arm, repetition, seed), Path('wave'), 'python', 'taskset')
                self.assertEqual(command[command.index('--repetition') + 1], str(repetition))
                if arm in ['kayros-heuristics', 'kayros-full-cplex']:
                    self.assertEqual(command[command.index('--seed') + 1], str(seed))
                else:
                    self.assertNotIn('--seed', command)
                commands.append(tuple(command))
            self.assertEqual(len(set(commands)), 3)

    def test_frozen_panel_three_trial_full_pilot_dry_run(self):
        selection = json.loads((Path(__file__).resolve().parents[1] / 'configs/pilot-selection.json').read_bytes())
        names = [entry['instance'] for entry in selection['selected_instances']]
        cells = planned_cells(names, [15, 60, 180, 600, 1800], 3, include_upstream=True)
        self.assertEqual(len(cells), 12 * 9 * 3)
        keys = {(cell['instance_name'], cell['arm'], cell['prefix_s'], cell['replicate'], cell['seed']) for cell in cells}
        self.assertEqual(len(keys), 324)
        commands = []
        for cell in cells:
            task = self.task(cell['arm'], cell['replicate'], cell['seed'])
            task.update(instance_path=cell['instance_name'] + '.json', prefix_s=cell['prefix_s'], horizon_s=3600)
            command = launcher_command(task, Path('wave'), 'python', 'taskset')
            commands.append(tuple(command))
            if cell['arm'] in ['kayros-heuristics', 'kayros-full-cplex']:
                self.assertEqual(cell['seed'], {1: 42, 2: 123, 3: 456}[cell['replicate']])
            else:
                self.assertIsNone(cell['seed'])
        self.assertEqual(len(set(commands)), 324)
        self.assertTrue(all(sum(cell['instance_name'] == name for cell in cells) == 27 for name in names))

    def test_duplicate_prefix_refused(self):
        with self.assertRaises(ValueError):
            planned_cells(['C101'], [15, 15.0], 3)

    def test_single_trial_keeps_legacy_identity(self):
        self.assertEqual(repetition_slots(1), [(0, 42)])

    def test_unsupported_trial_count_refused(self):
        with self.assertRaises(ValueError):
            repetition_slots(2)


if __name__ == '__main__':
    unittest.main()
