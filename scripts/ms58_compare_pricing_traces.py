"""Compare opt-in pricing traces without turning missing records into zero work."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read_trace(path: Path) -> tuple[list[dict], dict]:
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    records = []
    truncated = False
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1 or line.endswith(b"\n"):
                raise
            truncated = True
    begun = {r["call"] for r in records if r["event"] == "pricing_begin"}
    ended = {r["call"] for r in records if r["event"] == "pricing_end"}
    return records, {
        "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "record_count": len(records), "partial_final_line": truncated,
        "calls_without_end": sorted(begun - ended),
    }


def first(records: list[dict], event: str) -> dict | None:
    return next((r for r in records if r["event"] == event), None)


def numbers(a: list, b: list) -> dict:
    differing = [i for i, (left, right) in enumerate(zip(a, b)) if left != right]
    deltas = [abs(left - right) for left, right in zip(a, b)
              if isinstance(left, (int, float)) and isinstance(right, (int, float))]
    return {"left_count": len(a), "right_count": len(b),
            "equal": a == b, "differing_indices": differing,
            "max_abs_difference": max(deltas, default=None),
            "first_differences": [{"index": i, "left": a[i], "right": b[i]}
                                  for i in differing[:10]]}


def compare(left_path: Path, right_path: Path) -> dict:
    a, am = read_trace(left_path)
    b, bm = read_trace(right_path)
    out = {"left": am, "right": bm}
    ai, bi = first(a, "initial_master"), first(b, "initial_master")
    if ai and bi:
        out["initial_master"] = {
            "same_path_order": [r["path"] for r in ai["columns"]]
                               == [r["path"] for r in bi["columns"]],
            "costs": numbers([r["master_cost"] for r in ai["columns"]],
                             [r["master_cost"] for r in bi["columns"]]),
        }
    ab, bb = first(a, "pricing_begin"), first(b, "pricing_begin")
    if ab and bb:
        out["first_begin"] = {
            "same_level": ab["level"] == bb["level"],
            "same_context_fingerprint": ab["context_fnv1a64"] == bb["context_fnv1a64"],
            "left_merge_start": ab["merge_start"], "right_merge_start": bb["merge_start"],
        }
        if "context" in ab and "context" in bb:
            ac, bc = ab["context"], bb["context"]
            out["first_begin"].update({
                "profits": numbers(ac["profits"], bc["profits"]),
                "cut_duals": numbers(ac["cuts_duals"], bc["cuts_duals"]),
                "same_cuts": ac["cuts"] == bc["cuts"],
                "same_forbidden_arcs": ac["forbidden_arcs"] == bc["forbidden_arcs"],
            })
    ae, be = first(a, "pricing_end"), first(b, "pricing_end")
    if ae and be:
        out["first_end"] = {
            "left_call": ae["call"], "right_call": be["call"],
            "left_route_count": ae["route_count"], "right_route_count": be["route_count"],
            "left_labeling": ae["labeling"], "right_labeling": be["labeling"],
        }
        if "first_call_routes" in ae and "first_call_routes" in be:
            ar, br = ae["first_call_routes"], be["first_call_routes"]
            ap, bp = [tuple(r["path"]) for r in ar], [tuple(r["path"]) for r in br]
            aset, bset = set(ap), set(bp)
            common = sorted(aset & bset)
            ac, bc = {tuple(r["path"]): r["cost"] for r in ar}, {tuple(r["path"]): r["cost"] for r in br}
            out["first_end"]["pool"] = {
                "either_truncated": ae["first_call_routes_truncated"] or be["first_call_routes_truncated"],
                "same_order": ap == bp, "same_multiset": Counter(ap) == Counter(bp),
                "common_unique_paths": len(common),
                "left_only_unique_paths": len(aset - bset), "right_only_unique_paths": len(bset - aset),
                "first_path_order_difference": next((i for i, pair in enumerate(zip(ap, bp)) if pair[0] != pair[1]), None),
                "common_path_costs": numbers([ac[p] for p in common], [bc[p] for p in common]),
                "left_only_examples": sorted(aset - bset)[:3], "right_only_examples": sorted(bset - aset)[:3],
            }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(compare(args.left, args.right), indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload, end="")
