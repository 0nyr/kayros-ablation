"""Validate a captured replay contract and compare its observable workload."""
import argparse
import hashlib
import json
from pathlib import Path

from ms58_compare_pricing_traces import first, read_trace


def workload(record):
    if record is None:
        return None
    labeling = record["labeling"]
    result = {"level": record["level"], "route_count": record["route_count"],
              "search_finished": record["search_finished"], "status": labeling["status"]}
    for direction in ["forward", "backward"]:
        result[direction] = {key: value for key, value in labeling[direction].items()
                             if key.endswith("_count") or key in ["count_by_length", "status"]}
    return result


def inspect(fixture_path, trace_path):
    fixture = json.loads(fixture_path.read_bytes())
    records, metadata = read_trace(trace_path)
    begin, end = first(records, "pricing_begin"), first(records, "pricing_end")
    complete = first(records, "captured_replay_complete")
    checks = {
        "intentional_non_solver_stop": bool(complete and complete.get("solver_claim") is False),
        "source_call_matches": bool(begin and begin.get("source_call") == fixture["source_call"]),
        "full_context_matches": bool(begin and begin.get("context") == fixture["context"]),
        "level_matches": bool(begin and begin.get("level") == fixture["level"]),
        "merge_start_matches": bool(begin and begin.get("merge_start") == fixture["merge_start"]),
        "closing_state_matches": bool(begin and begin.get("closing_state") == fixture["closing_state"]),
        "declared_policy_matches": bool(complete and complete.get("policy") == fixture["policy"]),
        "pricing_end_present": end is not None,
    }
    expected, observed = workload(fixture.get("source_expected_end")), workload(end)
    return {"schema_version": 1, "fixture_path": str(fixture_path),
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "trace": metadata, "contract_checks": checks, "contract_passes": all(checks.values()),
            "source_workload": expected, "replay_workload": observed,
            "same_workload_as_source": expected == observed if expected is not None else None,
            "returned_routes": end.get("first_call_routes") if end else None,
            "solver_closure_claim": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inspect(args.fixture, args.trace), indent=2, allow_nan=False) + "\n")
