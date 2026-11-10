"""Extract an exact first-call dual vector for a bounded diagnostic replay."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def extract(path: Path) -> dict:
    records = []
    lines = path.read_text().splitlines()
    for index, raw in enumerate(lines):
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            # A timeout can truncate the final record, never an earlier one.
            if index != len(lines) - 1:
                raise
    calls = [record for record in records if record.get("event") == "pricing_begin"]
    if not calls or calls[0].get("call") != 1:
        raise ValueError("Trace has no first pricing call")
    first = calls[0]
    if first.get("level") != "Heuristic Cost" or first.get("merge_start") != 0:
        raise ValueError("Trace is not a fresh first root heuristic call")
    context = first["context"]
    for field in ("forbidden_arcs", "cuts", "cuts_duals"):
        if context[field] != []:
            raise ValueError(f"Root context has nonempty {field}")
    if not context["profits"] or not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in context["profits"]
    ):
        raise ValueError("Profits must be nonempty and finite")
    return {
        "schema_version": 1,
        "purpose": "First pricing replay only, terminated before column admission",
        "source_trace": str(path.resolve()),
        "source_trace_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_context_fnv1a64": first["context_fnv1a64"],
        "context": context,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    fixture = extract(args.trace)
    with args.output.open("x") as stream:
        json.dump(fixture, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
