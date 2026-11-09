"""Launch one real adapter through the process-clock harness, then check routes.

This is a single correctness/probe-cell entrypoint, not a campaign scheduler.
Run it with the prepared pinned-v0.11 checker Python environment.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

from checker_contract import checker_identity, sha256
from probe_runner import digest, run_probe
from validate_probe import validate_candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--worker-python", type=Path, required=True)
    parser.add_argument("--worker", type=Path, default=Path(__file__).with_name("kayros_worker.py"))
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", required=True)
    parser.add_argument("--horizon", type=float, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--prefix-seconds", type=float)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--repetition", type=int, default=0)
    args = parser.parse_args()
    heuristic_arm = args.arm in {"kayros-heuristics", "kayros-full-cplex"}
    if heuristic_arm and args.seed is None:
        parser.error("Heuristic-containing arms require an explicit --seed")
    if not heuristic_arm and args.seed is not None:
        parser.error("Cold exact arms require search_seed:null (omit --seed)")
    if args.arm == "kayros-full-cplex" and args.prefix_seconds is None:
        parser.error("Full arm requires a fixed process-launch --prefix-seconds")
    if args.arm != "kayros-full-cplex" and args.prefix_seconds is not None:
        parser.error("Only the full arm accepts --prefix-seconds")
    instance_path = args.instance.resolve()
    instance = json.loads(instance_path.read_bytes())
    atf_path = (instance_path.parent / instance["td"]["atf_path"]).resolve()
    worker = args.worker.resolve()
    worker_python = Path(os.path.abspath(args.worker_python))
    manifest = json.loads(args.manifest.read_bytes())
    if manifest.get("schema_version") != 1 or manifest.get("checker") != checker_identity():
        raise RuntimeError("Probe manifest schema or checker identity mismatch")
    runtime_files = manifest.get("runtime_files")
    patch_files = manifest.get("source_patch_files")
    if not isinstance(runtime_files, dict) or not runtime_files or not isinstance(patch_files, dict) or not patch_files:
        raise RuntimeError("Probe manifest requires runtime_files and source_patch_files hashes")
    for filename, expected in {**runtime_files, **patch_files}.items():
        if sha256(Path(filename)) != expected:
            raise RuntimeError(f"Probe artifact identity mismatch: {filename}")
    query = "import importlib.metadata as m,json; d=m.distribution('kayros'); print(json.dumps([str(d.locate_file(p).resolve()) for p in d.files if str(p).endswith(('.so','.py'))]))"
    installed_modules = json.loads(subprocess.check_output([str(worker_python), "-c", query], text=True))
    declared_paths = {str(Path(filename).resolve()) for filename in runtime_files}
    if not installed_modules or not set(installed_modules) <= declared_paths:
        raise RuntimeError("Probe manifest does not pin every installed Kayros Python module and extension")
    inputs = {"instance_name": instance["instance_name"],
              "instance_sha256": sha256(instance_path), "atf_sha256": sha256(atf_path)}
    selected_core = min(os.sched_getaffinity(0))
    environment = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                       PROBE_CPU_AFFINITY=str(selected_core))
    orchestration_files = {name: sha256(Path(__file__).with_name(name)) for name in
                           ["run_solver_probe.py", "probe_runner.py", "validate_probe.py", "checker_contract.py"]}
    parameters = {**inputs, "orchestration_files": orchestration_files, "cpu_affinity": [selected_core], "memory_cap_bytes": args.memory_mb * 1024 ** 2, "arm": args.arm, "search_seed": args.seed,
                  "repetition": args.repetition, "prefix_seconds": args.prefix_seconds,
                  "memory_mb": args.memory_mb, "worker_sha256": sha256(worker),
                  "worker_python_sha256": sha256(worker_python)}
    command = [str(worker_python), str(worker), "--instance", str(instance_path),
               "--arm", args.arm, "--memory-mb", str(args.memory_mb)]
    if args.seed is not None:
        command += ["--seed", str(args.seed)]
    if args.prefix_seconds is not None:
        command += ["--prefix-seconds", str(args.prefix_seconds)]
    native_result = args.output.resolve() / "native" / (digest({"manifest": manifest,
        "parameters": parameters, "horizon_s": args.horizon, "command": command,
        "environment_hash": digest(environment), "cwd": str(worker.parent.parent)}) + ".json")
    command += ["--native-result", str(native_result)]
    def validate_with_artifacts(events):
        result = validate_candidates(events, instance_path=instance_path,
            instance_sha256=inputs["instance_sha256"], atf_path=atf_path,
            atf_sha256=inputs["atf_sha256"], instance_name=inputs["instance_name"],
            expected_checker=manifest["checker"])
        artifact = {"path": str(native_result), "relative_path": "../native/" + native_result.name,
                    "sha256": None}
        try:
            if native_result.exists():
                artifact["sha256"] = sha256(native_result)
                json.loads(native_result.read_bytes())
        except (OSError, ValueError) as error:
            result["native_artifact_issue"] = f"{type(error).__name__}: {error}"
        result["native_result"] = artifact
        return result
    result = run_probe(output=args.output.resolve(), manifest=manifest,
                       parameters=parameters, command=command, horizon_s=args.horizon,
                       cwd=worker.parent.parent, environment=environment,
                       validate=validate_with_artifacts)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
