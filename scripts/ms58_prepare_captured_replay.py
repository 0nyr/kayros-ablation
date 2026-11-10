"""Extract one complete pricing context with an explicitly audited policy."""
import argparse
import hashlib
import json
from pathlib import Path

from ms58_compare_pricing_traces import read_trace


def extract(trace, call, policy_path):
    records, metadata = read_trace(trace)
    begin = next(record for record in records if record.get("event") == "pricing_begin" and record.get("call") == call)
    end = next((record for record in records if record.get("event") == "pricing_end" and record.get("call") == call), None)
    policy = json.loads(policy_path.read_bytes())
    return {"schema_version": 1, "purpose": "Isolated captured labeling task, no solver closure claim",
            "source_trace": metadata, "source_call": call, "level": begin["level"],
            "merge_start": begin["merge_start"], "closing_state": begin["closing_state"],
            "solution_limit": policy["solution_limit"], "policy": policy["policy"],
            "policy_audit": policy.get("audit"),
            "policy_file_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            "context": begin["context"], "source_expected_end": end}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--call", type=int, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = extract(args.trace, args.call, args.policy)
    with args.output.open("x") as stream:
        json.dump(fixture, stream, indent=2, allow_nan=False)
        stream.write("\n")
