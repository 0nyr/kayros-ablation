"""Validator control-flow fixtures. Real checker replay runs separately."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import validate_probe as validator


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.instance = self.root / "fixture.json"
        self.instance.write_text(json.dumps({"instance_name": "fixture", "td": {"atf_path": "atf.json"}}))
        self.atf = self.root / "atf.json"
        self.atf.write_text("{}")
        self.constructed = []
        def solution(**kwargs):
            self.constructed.append(kwargs)
            return kwargs
        def check(loaded, sol, objective):
            valid = sol["routes"] != [[99]]
            cost = float(sum(sum(route) for route in sol["routes"]))
            return types.SimpleNamespace(is_valid=lambda: valid, routing_cost=cost,
                                         model_dump=lambda **_: {"valid": valid, "duration": cost, "departure_time": 0})
        modules = {
            "mamut_routing_lib": types.ModuleType("mamut_routing_lib"),
            "mamut_routing_lib.enums": types.SimpleNamespace(ObjectiveFunction=types.SimpleNamespace(DURATION="Duration")),
            "mamut_routing_lib.models": types.SimpleNamespace(BenchmarkSolution=solution),
            "mamut_routing_lib.td": types.SimpleNamespace(load_td_instance=lambda *a, **k: object(), check_td_solution=check),
        }
        self.modules_patch = mock.patch.dict(sys.modules, modules)
        self.modules_patch.start()
        self.guard_patch = mock.patch.object(validator, "checker_identity", return_value={"version": "fixture"})
        self.guard_patch.start()

    def tearDown(self):
        self.guard_patch.stop()
        self.modules_patch.stop()
        self.temporary.cleanup()

    def validate(self, events, **kwargs):
        return validator.validate_candidates(events, instance_path=self.instance,
            instance_sha256=validator.sha256(self.instance), atf_path=self.atf,
            atf_sha256=validator.sha256(self.atf), instance_name="fixture", **kwargs)

    def event(self, sequence, routes, cost=999):
        return {"event": "candidate", "sequence": sequence, "elapsed_s": sequence + 0.1,
                "routes": routes, "native_cost": cost, "run_id": "run", "manifest_hash": "manifest"}

    def test_cost_omitted_and_best_checked_envelope_preserved(self):
        result = self.validate([self.event(0, [[5]]), self.event(1, [[99]]),
                                self.event(2, [[6]]), self.event(3, [[3]])])
        self.assertTrue(all("cost" not in sol for sol in self.constructed))
        self.assertEqual([p["checked_cost"] for p in result["checked_envelope"]], [5, 3])
        self.assertEqual(result["candidates"][0]["native_minus_checked"], 994)
        self.assertFalse(result["candidates"][1]["valid"])
        self.assertEqual(result["best_routes"], [[3]])

    def test_closure_is_not_proof_and_original_timestamp_retained(self):
        events = [self.event(0, [[5]]), {"event": "closure", "sequence": 1, "elapsed_s": 0.7}]
        result = self.validate(events)
        self.assertEqual(result["proof"]["status"], "proof_unverified")
        self.assertEqual(result["checked_envelope"][0]["elapsed_s"], 0.1)

    def test_explicit_audit_is_bound_to_closure_identity(self):
        closure = {"event": "closure", "sequence": 1, "elapsed_s": 0.7,
                   "run_id": "run", "manifest_hash": "manifest"}
        audit = {"accepted": True, "closure_sequence": 1, "run_id": "run",
                 "manifest_hash": "manifest", "protocol_sha256": "a" * 64,
                 "checks": {"independent_exact_audit": True}}
        result = self.validate([self.event(0, [[5]]), closure], accepted_proof_audit=audit)
        self.assertEqual(result["proof"]["elapsed_s"], 0.7)
        with self.assertRaises(ValueError):
            self.validate([self.event(0, [[5]]), closure], accepted_proof_audit={**audit, "run_id": "other"})

    def test_checker_guard_prevents_evaluation(self):
        with mock.patch.object(validator, "checker_identity", side_effect=RuntimeError("wrong checker")):
            with self.assertRaisesRegex(RuntimeError, "wrong checker"):
                self.validate([self.event(0, [[5]])])
        self.assertEqual(self.constructed, [])


if __name__ == "__main__":
    unittest.main()
