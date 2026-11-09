"""A reviewed proposal preserves the full population without authorizing launch."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from checker_contract import sha256
from prepare_main_proposal import build_proposal


class MainProposalTests(unittest.TestCase):
    def inputs(self):
        path = ROOT / 'references/dabia2013-n100-v011-2026-11-09/manifest.json'
        reference = json.loads(path.read_bytes())
        ref_digest = sha256(path)
        full = {'provisional': False, 'wave_name': 'full', 'checker': reference['checker'],
                'reference_manifest_sha256': ref_digest,
                'cells': [{'memory_mb': 16384, 'horizon_s': 3600}]}
        decision = {'recommendation_eligible': True, 'wave_name': 'full',
                    'selected_prefix_s': 15., 'horizon_s': 3600.,
                    'selection_rule': ['time', 'Lambda', 'prefix']}
        focused = {'full_wave': 'full', 'focused_wave': 'focused', 'memory_limit_mib': 16384,
                   'recommendation_eligible': True,
                   'full_horizon_s': 3600., 'focused_horizon_s': 7200.}
        return reference, ref_digest, full, decision, focused

    def test_concrete_complete_population_is_still_nonlaunchable(self):
        proposal = build_proposal(*self.inputs(), horizon=7200, hosts=40, workers=10)
        tasks = proposal['scheduled_cells']
        analysis = proposal['main_analysis']
        self.assertEqual(analysis, json.loads((ROOT / 'configs/main-analysis.json').read_bytes()))
        self.assertEqual(analysis['raw_gap_targets'], [0.0, 0.01, 0.05])
        self.assertEqual(analysis, json.loads((ROOT / 'configs/main-schedule-template.json').read_bytes())['main_analysis'])
        self.assertFalse(proposal['launchable'])
        self.assertTrue(proposal['author_review_required'])
        self.assertEqual(len(tasks), 1400)
        self.assertEqual(len({task['task_id'] for task in tasks}), 1400)
        self.assertEqual(set(Counter(task['instance_name'] for task in tasks).values()), {25})
        self.assertEqual(set(Counter(task['arm'] for task in tasks).values()), {280})
        seeds = {1: 42, 2: 123, 3: 456, 4: 789, 5: 2026}
        for task in tasks:
            self.assertEqual(task['horizon_s'], 7200)
            self.assertEqual(task['memory_mb'], 16384)
            if task['arm'] in ('kayros-heuristics', 'kayros-full-cplex'):
                self.assertEqual(task['seed'], seeds[task['replicate']])
            else:
                self.assertIsNone(task['seed'])
            self.assertEqual(task['prefix_s'], 15. if task['arm'] == 'kayros-full-cplex' else None)
        shape = proposal['resource_shape']
        self.assertEqual(shape['maximum_rounds'], 4)
        self.assertEqual(shape['walltime_seconds_including_margin'], 30600)
        self.assertEqual(shape['solver_core_hours_upper_bound'], 2800)
        self.assertEqual(shape['node_ram_guard_mib'], 180224)

    def test_incomplete_mismatched_or_unobserved_evidence_is_refused(self):
        changes = [(2, 'provisional', True), (3, 'recommendation_eligible', False),
                   (4, 'recommendation_eligible', False),
                   (3, 'wave_name', 'another'), (4, 'full_wave', 'another'),
                   (2, 'reference_manifest_sha256', 'another'),
                   (4, 'memory_limit_mib', 8192), (4, 'full_horizon_s', 1800)]
        for index, key, value in changes:
            args = deepcopy(self.inputs())
            args[index][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_proposal(*args, horizon=7200, hosts=40, workers=10)
        with self.assertRaises(ValueError):
            build_proposal(*self.inputs(), horizon=10800, hosts=40, workers=10)


if __name__ == '__main__':
    unittest.main()
