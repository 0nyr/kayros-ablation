"""Run a baseline and candidate on one captured Exact task, with no solve claim."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

from checker_contract import sha256
from ms58_inspect_captured_replay import inspect
from ms58_replay_runs import physical_core
from probe_runner import atomic_json


def utc():
    return datetime.now(timezone.utc).isoformat()


def source_inventory(source):
    files = []
    for subtree in [source / "cpp", source / "src"]:
        for directory, children, names in os.walk(subtree):
            children[:] = sorted(name for name in children if name not in ["build", "__pycache__", ".git", ".venv"])
            files.extend(Path(directory) / name for name in sorted(names))
    files.extend(source / name for name in ["CMakeLists.txt", "pyproject.toml", "setup.py", "setup.cfg"]
                 if (source / name).is_file())
    result = {str(path.relative_to(source)): sha256(path) for path in sorted(set(files)) if path.is_file()}
    if not result:
        raise ValueError(f"Missing source inventory at {source}")
    return {"path": str(source), "scope": "All cpp/src files plus root build/package configuration",
            "files": result, "inventory_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
            "cmake_cache_sha256": sha256(source / "build/cplex/CMakeCache.txt")
            if (source / "build/cplex/CMakeCache.txt").is_file() else None}


def runtime_inventory(python):
    query = ("import importlib.metadata as m,json; d=m.distribution('kayros'); "
             "print(json.dumps({'version':d.version,'files':[str(d.locate_file(p).resolve()) for p in d.files "
             "if str(p).endswith(('.so','.py'))]}))")
    found = json.loads(subprocess.check_output([str(python), "-c", query], text=True))
    if not any(Path(name).name.startswith("_lera.") for name in found["files"]):
        raise ValueError("Missing exact engine extension")
    return {"python": str(python), "python_sha256": sha256(python), "version": found["version"],
            "runtime_files": {name: sha256(Path(name)) for name in found["files"]}}


def priced_routes(audit, fixture):
    context = fixture["context"]
    result = []
    for route in audit.get("returned_routes") or []:
        path = route["path"]
        visited = set(path)
        cut_coefficients = [int(len(visited.intersection(cut)) >= 2) for cut in context["cuts"]]
        reduced_cost = (route["cost"] - sum(context["profits"][vertex] for vertex in path)
                        - sum(coefficient * dual for coefficient, dual in zip(cut_coefficients, context["cuts_duals"])))
        result.append({"path": path, "native_duration": route["cost"], "native_reduced_cost": reduced_cost,
                       "cut_coefficients": cut_coefficients, "negative_at_1e_6": reduced_cost < -1e-6})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "ms58-bpca")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--job-id", default="7013766")
    parser.add_argument("--physical-core-index", type=int, default=4)
    args = parser.parse_args()
    if os.environ.get("OAR_JOB_ID") != args.job_id or socket.gethostname().startswith(("fnancy", "flyon")):
        parser.error("Captured pair requires the declared reserved compute allocation")
    root = args.root.resolve()
    output = (args.output or root / "resume-20261110/captured-pair").resolve()
    output.mkdir(parents=True, exist_ok=False)
    core = physical_core(args.physical_core_index)
    os.sched_setaffinity(0, {core})
    scripts = output / "frozen-scripts"
    shutil.copytree(Path(__file__).resolve().parent, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    fixture_path = output / "fixture.json"
    shutil.copyfile(root / "resume-20261110/captured-fixtures/vu-cold-call15.json", fixture_path)
    fixture = json.loads(fixture_path.read_bytes())
    fixture_hash = sha256(fixture_path)
    if fixture_hash != "fe4e4b91802b07a4c016287a5e2032184d91a7ee416fe2ed4ca8b1fe13a4b69d":
        raise ValueError("Captured Vu fixture does not match the frozen task")
    instance = Path.home() / "kayros-sweeps/MAMUT-routing/benchmarks/TDVRPTW/Vu2020/n=59/Vu-A2-pB-d98-w100.vrp.json"
    witness = Path(str(instance).replace(".vrp.json", ".bks.Duration.json"))
    payload = json.loads(instance.read_bytes())
    atf = (instance.parent / payload["td"]["atf_path"]).resolve()
    variants = [("baseline", "task-replay", "00158011921c7675663a51b6adfedc3a782bbc85"),
                ("candidate", "point-replay", "db5312f3b431517f45c31685d9143d1f4bc9664f")]
    identities = {}
    for name, variant, commit in variants:
        identities[name] = {"source_commit": commit, "source": source_inventory(root / "sources" / variant),
                            "runtime": runtime_inventory(root / f"venv-{variant}/bin/python")}
    plan = {"schema_version": 1, "created_utc": utc(), "oar_job_id": args.job_id,
            "host": socket.gethostname(), "cpu_core": core, "order": [name for name, _, _ in variants],
            "outer_horizon_s": 60, "address_space_cap_bytes": 16 * 1024 ** 3, "native_memory_limit_mb": 16384,
            "fixture_sha256": fixture_hash, "instance": str(instance), "instance_sha256": sha256(instance),
            "atf": str(atf), "atf_sha256": sha256(atf), "witness": str(witness), "witness_sha256": sha256(witness),
            "variants": identities, "script_files": {str(path.relative_to(output)): sha256(path)
                for path in sorted(scripts.rglob("*.py"))}, "solver_closure_claim": False,
            "timing_scope": "Single sequential diagnostic pair including startup, checking and trace I/O. No throughput estimate."}
    atomic_json(output / "plan.json", plan)
    launcher = ("import os,resource,sys; os.sched_setaffinity(0,{int(sys.argv[1])}); "
                "resource.setrlimit(resource.RLIMIT_AS,(16*1024**3,16*1024**3)); "
                "os.execv(sys.argv[2],sys.argv[2:])")
    rows = []
    baseline_valid = False
    for name, variant, commit in variants:
        trace = output / f"{name}.jsonl"
        command = [sys.executable, "-c", launcher, str(core), str(root / f"venv-{variant}/bin/python"),
                   str(scripts / "ms58_vu_witness_probe.py"), "--instance", str(instance),
                   "--witness", str(witness), "--output", str(output / name), "--source-id", commit,
                   "--horizon", "60", "--cut-limit", "100", "--memory-mb", "16384"]
        environment = {key: value for key, value in os.environ.items() if not key.startswith("KAYROS_")}
        environment.update(KAYROS_CAPTURED_PRICING_REPLAY=str(fixture_path), KAYROS_PRICING_TRACE=str(trace),
                           OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
        started = time.monotonic()
        row = {"variant": name, "command": command, "started_utc": utc(), "outer_timeout": False}
        with (output / f"{name}.log").open("x") as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=environment,
                                       cwd=scripts.parent, start_new_session=True)
            try:
                process.wait(timeout=max(0.0, 60.0 - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                row["outer_timeout"] = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        row.update(returncode=process.returncode, elapsed_s=time.monotonic() - started,
                   finished_utc=utc(), log_sha256=sha256(output / f"{name}.log"),
                   trace_sha256=sha256(trace) if trace.is_file() else None)
        try:
            audit = inspect(fixture_path, trace)
        except (OSError, ValueError, KeyError) as error:
            audit = {"contract_passes": False, "error": str(error)}
        atomic_json(output / f"{name}-audit.json", audit)
        witness_result = output / name / "witness.json"
        witness_valid = witness_result.is_file() and json.loads(witness_result.read_bytes()).get("valid") is True
        deliberate = (not row["outer_timeout"] and process.returncode != 0 and audit["contract_passes"]
                      and "MS58_CAPTURED_PRICING_REPLAY_COMPLETE" in (output / f"{name}.log").read_text())
        if name == "baseline":
            baseline_valid = deliberate and witness_valid and audit.get("same_workload_as_source") is True
        routes = priced_routes(audit, fixture)
        row.update(intentional_replay_stop=deliberate, witness_valid=witness_valid,
                   contract_passes=audit["contract_passes"], same_workload_as_source=audit.get("same_workload_as_source"),
                   baseline_source_counter_match=baseline_valid,
                   candidate_interpretation_allowed=baseline_valid and deliberate and witness_valid,
                   returned_route_count=len(routes), native_negative_route_count=sum(route["negative_at_1e_6"] for route in routes),
                   routes=routes, solver_closure_claim=False,
                   route_validation_scope="Native durations and captured duals only. Independent route feasibility is not asserted here.")
        rows.append(row)
        atomic_json(output / "rows.json", rows)
    atomic_json(output / "complete.json", {"rows": rows, "baseline_source_counter_match": baseline_valid,
                                           "solver_closure_claim": False, "finished_utc": utc()})
    print(json.dumps({"output": str(output), "baseline_source_counter_match": baseline_valid,
                      "native_negative_route_counts": {row["variant"]: row["native_negative_route_count"] for row in rows}}), flush=True)


if __name__ == "__main__":
    main()
