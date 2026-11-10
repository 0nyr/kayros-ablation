import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ms58_inspect_captured_replay import inspect
from ms58_run_captured_pair import priced_routes


class CapturedReplayTests(unittest.TestCase):
    def test_negative_route_uses_captured_cut_dual(self):
        fixture = {"context": {"profits": [0.0, 3.0, 5.0, 7.0, 0.0],
                               "cuts": [[1, 2, 3]], "cuts_duals": [-2.0]}}
        rows = priced_routes({"returned_routes": [{"path": [0, 1, 2, 3, 4], "cost": 12.0}]}, fixture)
        self.assertEqual(rows[0]["cut_coefficients"], [1])
        self.assertEqual(rows[0]["native_reduced_cost"], -1.0)
        self.assertTrue(rows[0]["negative_at_1e_6"])

    def test_candidate_workload_change_is_distinct_from_input_contract_failure(self):
        with tempfile.TemporaryDirectory() as root:
            fixture_path, trace_path = Path(root) / "fixture.json", Path(root) / "trace.jsonl"
            context = {"profits": [1.0], "cuts": [[1, 2, 3]], "cuts_duals": [-0.5], "forbidden_arcs": []}
            policy = {"correcting": False}
            end = {"event": "pricing_end", "call": 1, "level": "Exact", "route_count": 0,
                   "search_finished": True, "labeling": {"status": "Finished", "forward": {"processed_count": 4},
                                                          "backward": {"processed_count": 5}}}
            fixture = {"source_call": 15, "level": "Exact", "merge_start": 346, "closing_state": False,
                       "context": context, "policy": policy, "source_expected_end": end}
            fixture_path.write_text(json.dumps(fixture))
            begin = {"event": "pricing_begin", "call": 1, "source_call": 15, "level": "Exact", "merge_start": 346,
                     "closing_state": False, "context": context}
            done = {"event": "captured_replay_complete", "source_call": 15, "solver_claim": False, "policy": policy}
            changed = {**end, "route_count": 1}
            trace_path.write_text("".join(json.dumps(record) + "\n" for record in [begin, changed, done]))
            result = inspect(fixture_path, trace_path)
            self.assertTrue(result["contract_passes"])
            self.assertFalse(result["same_workload_as_source"])
            begin["context"]["cuts_duals"] = [-0.6]
            trace_path.write_text("".join(json.dumps(record) + "\n" for record in [begin, changed, done]))
            self.assertFalse(inspect(fixture_path, trace_path)["contract_passes"])


if __name__ == "__main__":
    unittest.main()
