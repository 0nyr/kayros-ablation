import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ms58_compare_pricing_traces import numbers, read_trace


class TracePersistenceTests(unittest.TestCase):
    def test_killed_final_record_preserves_unfinished_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text('{"event":"pricing_begin","call":1}\n{"event":"pricing_end"')
            records, identity = read_trace(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(identity["calls_without_end"], [1])
            self.assertTrue(identity["partial_final_line"])

    def test_corrupt_completed_line_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text('{"event":"pricing_begin"\n')
            with self.assertRaises(json.JSONDecodeError):
                read_trace(path)

    def test_dual_difference_is_not_hidden_by_tolerance(self):
        result = numbers([1.0, 2.0], [1.0 + 1e-12, 2.0])
        self.assertFalse(result["equal"])
        self.assertEqual(result["differing_indices"], [0])
        self.assertGreater(result["max_abs_difference"], 0.0)


if __name__ == "__main__":
    unittest.main()
