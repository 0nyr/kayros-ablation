import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ms58_prepare_first_replay import extract
from ms58_replay_runs import compare_replay, nested_difference


class FirstReplayTests(unittest.TestCase):
    def test_fixture_preserves_tiny_profit_difference_and_rejects_constraints(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "source.jsonl"
            begin = {"event": "pricing_begin", "call": 1, "level": "Heuristic Cost", "merge_start": 0,
                     "context_fnv1a64": 12,
                     "context": {"profits": [1141.5006037515082], "cuts": [], "cuts_duals": [], "forbidden_arcs": []}}
            path.write_text(json.dumps(begin) + "\n" + '{"event":')
            self.assertEqual(extract(path)["context"]["profits"], [1141.5006037515082])
            begin["context"]["forbidden_arcs"] = [[1, 2]]
            path.write_text(json.dumps(begin) + "\n")
            with self.assertRaises(ValueError):
                extract(path)

    def test_preprocessed_structure_and_numerical_differences_stay_distinct(self):
        delta = nested_difference([[1.0, 2.0], [3.0]], [[1.0, 2.0 + 1e-12], []])
        self.assertEqual(delta["numeric_differences"], 1)
        self.assertEqual(delta["structural_differences"], 1)
        self.assertLess(delta["max_abs_numeric_difference"], 2e-12)

    def test_replay_requires_explicit_non_solver_completion(self):
        with tempfile.TemporaryDirectory() as root:
            left, right = Path(root) / "a.jsonl", Path(root) / "b.jsonl"
            context = {"profits": [1.0], "cuts": [], "cuts_duals": [], "forbidden_arcs": []}
            begin = {"event": "pricing_begin", "call": 1, "level": "Heuristic Cost", "merge_start": 0,
                     "context_fnv1a64": 12, "context": context, "original_context": context,
                     "correcting": False, "replay": True}
            done = {"event": "replay_complete", "call": 1, "solver_claim": False}
            left.write_text(json.dumps(begin) + "\n" + json.dumps(done) + "\n")
            right.write_text(json.dumps(begin) + "\n")
            contract = compare_replay(left, right)["replay_contract"]
            self.assertTrue(contract["effective_context_equal"])
            self.assertFalse(contract["both_intentional_non_solver_stops"])
            self.assertFalse(contract["right_completed"])


if __name__ == "__main__":
    unittest.main()
