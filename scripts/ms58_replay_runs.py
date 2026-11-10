"""Cross engines and captured first-call dual vectors without making solve claims."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_compare_pricing_traces import compare, first, read_trace
from ms58_paired_probe import candidate_manifest, frozen_existing_manifest
from ms58_prepare_first_replay import extract
from probe_runner import atomic_json


def nested_difference(left, right):
    result = {"equal": left == right, "numeric_pairs": 0, "numeric_differences": 0,
              "structural_differences": 0, "max_abs_numeric_difference": 0.0, "examples": []}

    def example(path, a, b):
        if len(result["examples"]) < 8:
            result["examples"].append({"path": path, "left": a, "right": b})

    def visit(a, b, path):
        if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
            result["numeric_pairs"] += 1
            if a != b:
                result["numeric_differences"] += 1
                result["max_abs_numeric_difference"] = max(result["max_abs_numeric_difference"], abs(a - b))
                example(path, a, b)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                result["structural_differences"] += 1
                example(path + "/length", len(a), len(b))
            for index, (aa, bb) in enumerate(zip(a, b)):
                visit(aa, bb, path + f"/{index}")
        elif isinstance(a, dict) and isinstance(b, dict):
            if a.keys() != b.keys():
                result["structural_differences"] += 1
                example(path + "/keys", sorted(a), sorted(b))
            for key in sorted(a.keys() & b.keys()):
                visit(a[key], b[key], path + f"/{key}")
        elif a != b:
            result["structural_differences"] += 1
            example(path, a, b)

    visit(left, right, "")
    return result


def compare_replay(left: Path, right: Path) -> dict:
    result = compare(left, right)
    a, _ = read_trace(left)
    b, _ = read_trace(right)
    ab, bb = first(a, "pricing_begin"), first(b, "pricing_begin")
    ac, bc = first(a, "replay_complete"), first(b, "replay_complete")
    result["replay_contract"] = {
        "left_completed": ac is not None, "right_completed": bc is not None,
        "both_intentional_non_solver_stops": bool(ac and bc and ac.get("solver_claim") is False and bc.get("solver_claim") is False),
        "both_correcting_false": bool(ab and bb and ab.get("correcting") is False and bb.get("correcting") is False),
        "effective_context_equal": bool(ab and bb and ab.get("context") == bb.get("context")),
        "original_context_equal": bool(ab and bb and ab.get("original_context") == bb.get("original_context")),
    }
    ai, bi = first(a, "replay_instance"), first(b, "replay_instance")
    if ai and bi:
        ignored = {"event", "schema_version", "monotonic_s"}
        result["preprocessed_instance"] = {
            key: nested_difference(ai.get(key), bi.get(key))
            for key in sorted((ai.keys() | bi.keys()) - ignored)
        }
    result["interpretation"] = "Pool and representation discrepancies only. Neither engine is a correctness oracle, and diagnostic times are not solver performance measurements."
    return result


def physical_core(index: int) -> int:
    unique = []
    seen = set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        key = tuple((topology / name).read_text().strip() for name in ("physical_package_id", "core_id"))
        if key not in seen:
            seen.add(key)
            unique.append(cpu)
    return unique[index]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "ms58-bpca")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--job-id", default="7013762")
    parser.add_argument("--physical-core-index", type=int, default=6)
    parser.add_argument("--instances", nargs="+", default=["C104", "R201", "RC201"])
    parser.add_argument("--horizon", type=float, default=30.0)
    args = parser.parse_args()
    if os.environ.get("OAR_JOB_ID") != args.job_id or socket.gethostname().startswith(("fnancy", "flyon")):
        parser.error("Requires the declared owned OAR compute allocation")
    if not math.isfinite(args.horizon) or args.horizon <= 0:
        parser.error("Horizon must be positive and finite")
    root = args.root.resolve()
    output = (args.output or root / "resume-20261110/replay").resolve()
    output.mkdir(parents=True, exist_ok=False)
    core = physical_core(args.physical_core_index)
    os.sched_setaffinity(0, {core})
    scripts = output / "frozen-scripts"
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    for child in ["fixtures", "manifests", "kayros-patches", "upstream-patches", "comparisons"]:
        (output / child).mkdir()
    identity = checker_identity()
    kayros = candidate_manifest(root / "venv-replay/bin/python", root / "patches-replay",
                                output / "kayros-patches", identity, "64a3e28 first-dual replay")
    upstream = frozen_existing_manifest(root / "archived-manifests/upstream.json", output / "upstream-patches",
                                        scripts, root / "experiment/patches", identity)
    old = upstream["worker_environment"]["UPSTREAM_PROBE_BINARY"]
    del upstream["runtime_files"][old]
    binary = root / "upstream-replay-build/bp"
    upstream["runtime_files"][str(binary)] = sha256(binary)
    upstream["worker_environment"]["UPSTREAM_PROBE_BINARY"] = str(binary)
    for name in ["upstream-trace.patch", "upstream-first-replay.patch"]:
        target = output / "upstream-patches" / name
        shutil.copyfile(root / "scripts" / name, target)
        upstream["source_patch_files"][str(target)] = sha256(target)
    upstream["source_id"] = "0dbf574 first-dual replay, explicit correcting=false"
    for variant, manifest in [("kayros", kayros), ("upstream", upstream)]:
        manifest["purpose"] = "Identical-dual root-pricing replay, intentional stop before column admission, no solve claim"
        atomic_json(output / "manifests" / f"{variant}.json", manifest)
    schedule, skipped = [], []
    for instance in args.instances:
        for source in ["upstream", "kayros"]:
            trace = root / "outputs/traces" / f"{instance}-{source}.jsonl"
            try:
                fixture = extract(trace)
            except (OSError, ValueError, KeyError) as error:
                skipped.append({"instance": instance, "dual_source": source, "reason": str(error)})
                continue
            fixture_path = output / "fixtures" / f"{instance}-{source}.json"
            atomic_json(fixture_path, fixture)
            for engine in ["upstream", "kayros"]:
                schedule.append({"instance": instance, "dual_source": source, "engine": engine,
                                 "fixture": str(fixture_path), "fixture_sha256": sha256(fixture_path)})
    plan = {"schema_version": 1, "purpose": "Crossed dual-source and engine first-pricing diagnostics",
            "created_utc": datetime.now(timezone.utc).isoformat(), "job_id": args.job_id,
            "host": socket.gethostname(), "cpu_core": core, "horizon_s": args.horizon,
            "scheduled": schedule, "unavailable_fixtures": skipped,
            "driver_sha256": sha256(Path(__file__))}
    atomic_json(output / "plan.json", plan)
    rows = []
    for cell in schedule:
        engine, name, source = cell["engine"], cell["instance"], cell["dual_source"]
        cell_id = f"{name}-{engine}-duals-{source}"
        trace = output / f"{cell_id}.jsonl"
        worker_python = root / "venv-replay/bin/python" if engine == "kayros" else Path(sys.executable)
        command = [sys.executable, str(scripts / "run_solver_probe.py"),
                   "--manifest", str(output / "manifests" / f"{engine}.json"),
                   "--worker-python", str(worker_python),
                   "--instance", str(Path.home() / f"ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100/{name}.vrp.json"),
                   "--output", str(output / f"{cell_id}-cell"),
                   "--arm", "lera-exact" if engine == "upstream" else "kayros-bpca-cplex",
                   "--horizon", str(args.horizon), "--memory-mb", "16384", "--cpu-core", str(core)]
        if engine == "upstream":
            command += ["--worker", str(scripts / "upstream_event_worker.py")]
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("KAYROS_")}
        environment.update(KAYROS_PRICING_TRACE=str(trace), KAYROS_FIRST_PRICING_REPLAY=cell["fixture"])
        with (output / f"{cell_id}-result.json").open("x") as stdout, (output / f"{cell_id}.stderr").open("x") as stderr:
            process = subprocess.run(command, stdout=stdout, stderr=stderr, env=environment)
        records = read_trace(trace)[0] if trace.exists() else []
        complete = first(records, "replay_complete")
        rows.append({**cell, "cell_id": cell_id, "command": command, "returncode": process.returncode,
                     "trace_sha256": sha256(trace) if trace.exists() else None,
                     "outcome": "intentional_replay_stop" if complete and complete.get("solver_claim") is False else "diagnostic_incomplete",
                     "solver_closure_claim": False})
        atomic_json(output / "rows.json", rows)
    summaries = []
    baseline_comparisons = []
    for name in args.instances:
        cases = [("same_duals_" + source, f"{name}-upstream-duals-{source}", f"{name}-kayros-duals-{source}")
                 for source in ["upstream", "kayros"]]
        cases += [("dual_sensitivity_" + engine, f"{name}-{engine}-duals-upstream", f"{name}-{engine}-duals-kayros")
                  for engine in ["upstream", "kayros"]]
        for label, left, right in cases:
            left_path, right_path = output / f"{left}.jsonl", output / f"{right}.jsonl"
            if left_path.exists() and right_path.exists():
                result = compare_replay(left_path, right_path)
                target = output / "comparisons" / f"{name}-{label}.json"
                atomic_json(target, result)
                summaries.append({"instance": name, "comparison": label, "path": str(target),
                                  "replay_contract": result["replay_contract"],
                                  "pool": result.get("first_end", {}).get("pool"),
                                  "preprocessed_instance": result.get("preprocessed_instance")})
        for engine in ["upstream", "kayros"]:
            original = root / "outputs/traces" / f"{name}-{engine}.jsonl"
            own_replay = output / f"{name}-{engine}-duals-{engine}.jsonl"
            if original.exists() and own_replay.exists():
                result = compare(original, own_replay)
                target = output / "comparisons" / f"{name}-{engine}-original-vs-own-dual-replay.json"
                atomic_json(target, result)
                baseline_comparisons.append({"instance": name, "engine": engine, "path": str(target),
                                             "pool": result.get("first_end", {}).get("pool"),
                                             "limitation": "Original upstream trace has an uninitialized correction flag. Only first-pool observables are compared."})
    atomic_json(output / "summary.json", {"scheduled": len(schedule), "completed": len(rows),
                                           "intentional_replay_stops": sum(row["outcome"] == "intentional_replay_stop" for row in rows),
                                           "unavailable_fixtures": skipped, "comparisons": summaries,
                                           "original_vs_own_replay": baseline_comparisons})
    atomic_json(output / "complete.json", {"rows": len(rows), "solver_closure_claim": False})


if __name__ == "__main__":
    main()
