"""Tiny harness fixtures only. These are not solver-performance probes."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "probe_runner.py"
spec = importlib.util.spec_from_file_location("probe_runner", MODULE)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ProbeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_child(self, source, **overrides):
        options = dict(output=self.directory, manifest={"source": "fixture"},
                       parameters={"repetition": 0, "search_seed": None},
                       command=[sys.executable, "-c", source], horizon_s=1,
                       cwd=self.directory, environment=dict(os.environ),
                       validate=lambda events: {"accepted": True, "witness_count": len(events)})
        options.update(overrides)
        return runner.run_probe(**options)

    def test_flushed_events_and_validation_after_child_exit(self):
        marker = self.directory / "done"
        def validate(events):
            self.assertTrue(marker.exists())
            self.assertEqual(events[0]["routes"], [[0, 1, 0]])
            time.sleep(0.02)
            return {"accepted": True}
        result = self.run_child(
            "import json, os, time, pathlib; "
            "t=(time.monotonic_ns()-int(os.environ['PROBE_START_MONOTONIC_NS']))/1e9; "
            "print(json.dumps(dict(event='candidate', elapsed_s=t, routes=[[0,1,0]])), flush=True); "
            "pathlib.Path('done').touch()", validate=validate)
        self.assertEqual(result["status"], "completed")
        cell = self.directory / result["run_id"]
        event = json.loads((cell / "events.jsonl").read_text())
        self.assertEqual(event["run_id"], result["run_id"])
        self.assertEqual(json.loads((cell / "result.json").read_text()), result)
        self.assertEqual(result["validation"], {"accepted": True})

    def test_resume_checks_completion_and_event_digest(self):
        result = self.run_child("pass")
        cell = self.directory / result["run_id"]
        self.assertEqual(runner.load_completed(cell, expected_run_id=result["run_id"]), result)
        (cell / "events.jsonl").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "digest"):
            runner.load_completed(cell, expected_run_id=result["run_id"])

    def test_resume_verifies_saved_native_sidecar(self):
        native = self.directory / "native" / "fixture.json"
        native.parent.mkdir()
        native.write_text("{}")
        def validate(events):
            import hashlib
            return {"native_result": {"relative_path": "../native/fixture.json",
                    "sha256": hashlib.sha256(native.read_bytes()).hexdigest()}}
        result = self.run_child("pass", validate=validate)
        cell = self.directory / result["run_id"]
        runner.load_completed(cell, expected_run_id=result["run_id"])
        native.write_text("changed")
        with self.assertRaisesRegex(ValueError, "native artifact digest"):
            runner.load_completed(cell, expected_run_id=result["run_id"])
        native.unlink()
        with self.assertRaisesRegex(ValueError, "native artifact missing"):
            runner.load_completed(cell, expected_run_id=result["run_id"])

    def test_deadline_preserves_candidate(self):
        result = self.run_child(
            "import json,time; print(json.dumps(dict(event='candidate',elapsed_s=0,routes=[])),flush=True); time.sleep(5)",
            horizon_s=0.15)
        self.assertEqual(result["status"], "deadline")
        self.assertEqual(result["event_count"], 1)
        self.assertLess(result["solver_elapsed_s"], 2)

    def test_deadline_drains_flushed_unread_events(self):
        # Hold the reader until the deadline so the witness remains in the pipe.
        with mock.patch.object(runner.selectors.DefaultSelector, "select",
                               side_effect=lambda timeout: (time.sleep(0.25) or [])):
            result = self.run_child(
                "import json,time; print(json.dumps(dict(event='candidate',elapsed_s=0,routes=[])),flush=True); time.sleep(5)",
                horizon_s=0.2)
        self.assertEqual(result["status"], "deadline")
        self.assertEqual(result["event_count"], 1)
        self.assertGreater(result["timeout_overshoot_s"], 0)

    def test_late_closure_is_retained_but_excluded(self):
        with mock.patch.object(runner.selectors.DefaultSelector, "select",
                               side_effect=lambda timeout: (time.sleep(0.3) or [])):
            result = self.run_child(
                "import json,time; time.sleep(0.22); print(json.dumps(dict(event='closure',elapsed_s=0.22)),flush=True); time.sleep(5)",
                horizon_s=0.2)
        self.assertEqual(result["status"], "deadline")
        self.assertEqual(result["event_count"], 0)
        self.assertEqual(result["late_event_count"], 1)
        late = json.loads((self.directory / result["run_id"] / "late-events.jsonl").read_text())
        self.assertEqual(late["elapsed_s"], 0.22)
        self.assertEqual(late["excluded_reason"], "after_horizon")

    def test_route_duration_and_time_window_diagnostics_allowed(self):
        evidence = {"accepted": True, "route_duration": 12, "time_windows": [[0, 10]]}
        result = self.run_child("pass", validate=lambda _: evidence)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["validation"], evidence)

    def test_closed_stdout_still_obeys_deadline(self):
        result = self.run_child("import os,time; os.close(1); time.sleep(5)", horizon_s=0.1)
        self.assertEqual(result["status"], "deadline")

    def test_identity_includes_parameters_and_existing_cell_refused(self):
        first = self.run_child("pass")
        with self.assertRaises(FileExistsError):
            self.run_child("pass")
        second = self.run_child("pass", parameters={"repetition": 1})
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_invalid_events_fail_closed(self):
        for i, event in enumerate((
            {"event": "candidate", "elapsed_s": -1},
            {"event": "candidate", "elapsed_s": 0, "run_id": "spoof"},
            {"event": "unknown", "elapsed_s": 0},
        )):
            result = self.run_child(f"print({json.dumps(json.dumps(event))})", parameters={"case": i})
            self.assertEqual(result["status"], "integrity_failure")
            self.assertEqual(result["event_count"], 0)

    def test_validation_timing_fields_rejected(self):
        result = self.run_child("pass", validate=lambda _: {"nested": {"audit_duration_s": 1}})
        self.assertEqual(result["status"], "validation_failure")
        self.assertNotIn("audit_duration_s", json.dumps(result))

    def test_integrity_event_fails_run_and_retains_candidate(self):
        source = "import json; print(json.dumps(dict(event='candidate',elapsed_s=0,routes=[])),flush=True); print(json.dumps(dict(event='integrity',elapsed_s=0,reason='invalid native route')),flush=True)"
        result = self.run_child(source)
        self.assertEqual(result["status"], "integrity_failure")
        self.assertEqual(result["event_count"], 2)
        self.assertEqual(result["validation"]["witness_count"], 2)

    def test_crash_retains_prior_witness(self):
        result = self.run_child("import json; print(json.dumps(dict(event='candidate',elapsed_s=0,routes=[])),flush=True); raise RuntimeError('fixture')")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
