"""Compare serialized function shapes without discarding solver semantics."""
import argparse
import hashlib
import json
from pathlib import Path


def first_instance(path):
    with path.open() as stream:
        for line in stream:
            record = json.loads(line)
            if record["event"] == "replay_instance":
                return record
    raise ValueError(f"No replay instance in {path}")


def analyze(left_path, right_path, tolerance=1e-8):
    a, b = first_instance(left_path), first_instance(right_path)
    result = {"left": {"path": str(left_path), "sha256": hashlib.sha256(left_path.read_bytes()).hexdigest()},
              "right": {"path": str(right_path), "sha256": hashlib.sha256(right_path.read_bytes()).hexdigest()},
              "comparison_tolerance": tolerance,
              "limitation": "Endpoint serialization hides tagged vertical image spans. Excluding zero-width pieces here is an analysis alignment, never a solver change.",
              "tables": {}}
    for key in ["travel_times", "arrival_times", "departure_times", "pretravel_times"]:
        aa, bb = a[key], b[key]
        if len(aa) != len(bb) or any(len(ar) != len(br) for ar, br in zip(aa, bb)):
            raise ValueError(f"Matrix dimension mismatch in {key}")
        out = {"piece_counts": [0, 0], "zero_width_piece_counts": [0, 0],
               "entries_with_different_piece_counts": 0,
               "differing_count_entries_with_matching_nonzero_pieces": 0,
               "max_abs_aligned_nonzero_endpoint_difference": 0.0, "examples": []}
        for i, (ar, br) in enumerate(zip(aa, bb)):
            for j, (af, bf) in enumerate(zip(ar, br)):
                clean = []
                for side, function in enumerate([af, bf]):
                    out["piece_counts"][side] += len(function)
                    out["zero_width_piece_counts"][side] += sum(p[0][0] == p[1][0] for p in function)
                    clean.append([p for p in function if p[0][0] != p[1][0]])
                if len(clean[0]) == len(clean[1]):
                    delta = max((abs(x - y) for ap, bp in zip(*clean)
                                 for ae, be in zip(ap, bp) for x, y in zip(ae, be)), default=0.0)
                    out["max_abs_aligned_nonzero_endpoint_difference"] = max(out["max_abs_aligned_nonzero_endpoint_difference"], delta)
                else:
                    delta = None
                if len(af) != len(bf):
                    out["entries_with_different_piece_counts"] += 1
                    if delta is not None and delta <= tolerance:
                        out["differing_count_entries_with_matching_nonzero_pieces"] += 1
                    if len(out["examples"]) < 3:
                        out["examples"].append({"arc": [i, j], "left": af, "right": bf})
        result["tables"][key] = out
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(analyze(args.left, args.right), indent=2, allow_nan=False) + "\n")
