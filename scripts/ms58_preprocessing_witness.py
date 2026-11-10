#!/usr/bin/env python3
"""Check whether the earliest_departure typo can affect this pilot's rule 2.

This is a sufficient-condition data check, not a solver or a floating-point
emulation of native preprocessing. Both implementations retain every
capacity-feasible customer-to-end-depot arc until after rule 2. If that
arc's earliest transformed arrival exceeds the depot's lower window, its
earliest_departure returns the customer's current lower bound. This
successor alone makes the min-departure tightening inactive. Upstream's
other successors return either 1 or that same lower bound, so its typo is
also inactive whenever customer lower bounds are at least 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mamut_routing_lib.td.artifacts import load_td_instance


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(path: Path) -> dict:
    loaded = load_td_instance(path, verify_sha256=True)
    inst = loaded.instance
    depot_lower = inst.time_windows[0][0] + inst.service_times[0]
    rows = []
    for customer in range(1, inst.num_customers + 1):
        ready = inst.time_windows[customer][0] + inst.service_times[customer]
        arc = loaded.atfs.arcs.get((customer, 0))
        conditions = {
            "customer_lower_at_least_one": ready >= 1.0,
            "return_arc_exists": arc is not None,
            "return_arc_survives_capacity": (
                inst.demands[customer] + inst.demands[0] <= inst.vehicle_capacity
            ),
            "ready_in_return_domain": bool(arc and arc.xs[0] <= ready <= arc.xs[-1]),
            "nonnegative_return_travel": bool(
                arc and all(y >= x for x, y in zip(arc.xs, arc.ys))
            ),
            "fifo_return_arc": bool(
                arc and all(a <= b for a, b in zip(arc.ys, arc.ys[1:]))
            ),
            "positive_depot_arrival_margin": ready - depot_lower > 1e-6,
        }
        rows.append({
            "customer": customer,
            "transformed_lower_window": ready,
            "return_arrival_lower_margin": ready - depot_lower,
            "conditions": conditions,
            "witness_passed": all(conditions.values()),
        })
    return {
        "instance": inst.instance_name if hasattr(inst, "instance_name") else path.name,
        "instance_path": str(path.resolve()),
        "instance_sha256": sha256(path),
        "atf_sha256": sha256(path.with_name(path.name.replace(".vrp.json", ".atf.json.gz"))),
        "customers": len(rows),
        "depot_transformed_lower_window": depot_lower,
        "min_transformed_customer_lower": min(r["transformed_lower_window"] for r in rows),
        "min_depot_arrival_margin": min(r["return_arrival_lower_margin"] for r in rows),
        "all_rule2_inactive_witnesses_pass": all(r["witness_passed"] for r in rows),
        "customer_witnesses": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instances", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selection-manifest", type=Path)
    args = parser.parse_args()
    results = [check(path) for path in args.instances]
    if args.selection_manifest:
        selected = {
            row["instance"]: row["instance_sha256"]
            for row in json.loads(args.selection_manifest.read_text())["selected_instances"]
        }
        observed = {row["instance"].split(".")[0]: row["instance_sha256"] for row in results}
        if observed != selected:
            raise ValueError("Input identity set differs from the selected pilot identities")
    root = Path(__file__).resolve().parents[1]
    source_files = [
        "build/ms56-phase2-upstream/code/src/preprocess/preprocess_time_windows.cpp",
        "build/phase2/cpp/lera/main/src/preprocess/preprocess_utils.cpp",
        "build/phase2/cpp/lera/main/src/preprocess/preprocess_time_windows.cpp",
        "build/phase2/cpp/lera/main/src/preprocess/preprocess_service_waiting.cpp",
    ]
    report = {
        "schema_version": 1,
        "script_sha256": sha256(Path(__file__)),
        "selection_manifest_sha256": sha256(args.selection_manifest) if args.selection_manifest else None,
        "hypothesis": "upstream earliest_departure boolean return explains initial trajectory divergence",
        "method": "sufficient-condition analytic witness checked against hashed raw ATFs and instance data",
        "scope": "Rule 2 only. This does not establish equality of other preprocessing, PWL arithmetic, seeds, duals or pricing.",
        "source_paths": {
            "upstream": "build/ms56-phase2-upstream/code/src/preprocess/preprocess_time_windows.cpp:49",
            "kayros": "build/phase2/cpp/lera/main/src/preprocess/preprocess_utils.cpp:55",
        },
        "source_sha256": {name: sha256(root / name) for name in source_files},
        "instances": results,
        "hypothesis_excluded_on_checked_instances": all(
            r["all_rule2_inactive_witnesses_pass"] for r in results
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "instances": len(results),
        "customer_witnesses": sum(r["customers"] for r in results),
        "hypothesis_excluded_on_checked_instances": report["hypothesis_excluded_on_checked_instances"],
        "output": str(args.output),
    }))


if __name__ == "__main__":
    main()
