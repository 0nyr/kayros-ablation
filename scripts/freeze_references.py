"""Freeze and fully validate the 56 n100 references using the classic checker."""

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import tempfile

from checker_contract import checker_identity, sha256

REPOSITORY = "https://github.com/ANR-MAMUT/MAMUT-routing"
SOURCE_COMMIT = "8e3aa713032a3d99c220c87551535213856458a0"
FAMILY_PATH = Path("benchmarks/TDVRPTW/Dabia2013")
EXPECTED_NAMES = {f"{group}{i:02}" for group, count in
                  [("C1", 9), ("C2", 8), ("R1", 12), ("R2", 11), ("RC1", 8), ("RC2", 8)]
                  for i in range(1, count + 1)}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def source_bytes(root, relative):
    return subprocess.check_output(["git", "-C", str(root), "show", f"{SOURCE_COMMIT}:{relative.as_posix()}"])


def assert_pinned_file(root, relative):
    path = root / relative
    data = path.read_bytes()
    if data != source_bytes(root, relative):
        raise RuntimeError(f"Source differs from pinned Git object: {relative}")
    return data


def freeze(root, output):
    identity = checker_identity()
    from importlib import metadata
    from mamut_routing_lib.enums import ObjectiveFunction
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import check_td_solution, load_td_instance

    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise RuntimeError("Snapshot destination already exists; refuse to overwrite")
    directory = root / FAMILY_PATH / "n=100"
    names = {p.name.removesuffix(".vrp.json") for p in directory.glob("*.vrp.json")}
    references = {p.name.removesuffix(".bks.Duration.json") for p in directory.glob("*.bks.Duration.json")}
    if names != EXPECTED_NAMES or references != EXPECTED_NAMES:
        raise RuntimeError("The source does not contain the exact 56-instance/reference population")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".reference-stage-", dir=output.parent) as temp:
        stage = Path(temp) / "snapshot"
        for directory_name in ["solutions", "source-solutions", "checks"]:
            (stage / directory_name).mkdir(parents=True, exist_ok=True)
        entries = []
        for name in sorted(names):
            instance_rel = FAMILY_PATH / "n=100" / f"{name}.vrp.json"
            source_rel = instance_rel.with_name(f"{name}.bks.Duration.json")
            instance_bytes = assert_pinned_file(root, instance_rel)
            reference_bytes = assert_pinned_file(root, source_rel)
            instance = json.loads(instance_bytes)
            source = json.loads(reference_bytes)
            if (instance["instance_name"] != name or instance["num_customers"] != 100
                    or source["instance_name"] != name or source["objective_function"] != "Duration"):
                raise RuntimeError(f"Identity/contract mismatch: {name}")
            atf_rel = instance_rel.parent / instance["td"]["atf_path"]
            if atf_rel.parent != instance_rel.parent:
                raise RuntimeError(f"Unexpected ATF path: {atf_rel}")
            atf_bytes = assert_pinned_file(root, atf_rel)
            expanded = gzip.decompress(atf_bytes) if atf_rel.suffix == ".gz" else atf_bytes
            atf_digest = hashlib.sha256(expanded).hexdigest()
            if atf_digest != instance["td"]["atf_sha256"]:
                raise RuntimeError(f"ATF declaration mismatch: {name}")
            loaded = load_td_instance(root / instance_rel, verify_sha256=True, validate_complete=True)
            candidate = BenchmarkSolution(instance_name=name, routes=source["routes"], cost=None)
            check = check_td_solution(loaded, candidate, ObjectiveFunction.DURATION)
            if (not check.is_valid() or check.routing_cost is None
                    or not math.isfinite(check.routing_cost) or check.routing_cost <= 0):
                raise RuntimeError(f"Invalid reference {name}: {check.model_dump_json()}")
            # Store only primal reference semantics. Historical proof claims stay in source-solutions.
            solution = {"instance_name": name, "objective_function": "Duration",
                        "routes": source["routes"], "cost": check.routing_cost,
                        "metadata": {"reference_role": "pre-campaign primal reference, not a new proof",
                                     "checker_commit": identity["commit"],
                                     "source_repository": REPOSITORY, "source_commit": SOURCE_COMMIT,
                                     "source_path": source_rel.as_posix()}}
            solution_path = stage / "solutions" / f"{name}.bks.Duration.json"
            write_json(solution_path, solution)
            (stage / "source-solutions" / source_rel.name).write_bytes(reference_bytes)
            check_path = stage / "checks" / f"{name}.json"
            write_json(check_path, check.model_dump(mode="json"))
            original_cost = source["cost"]
            entries.append({"instance_name": name, "instance_path": instance_rel.as_posix(),
                            "instance_sha256": hashlib.sha256(instance_bytes).hexdigest(),
                            "atf_path": atf_rel.as_posix(), "atf_file_sha256": hashlib.sha256(atf_bytes).hexdigest(),
                            "atf_content_sha256": atf_digest, "source_reference_path": source_rel.as_posix(),
                            "source_reference_sha256": hashlib.sha256(reference_bytes).hexdigest(),
                            "solution_path": solution_path.relative_to(stage).as_posix(),
                            "solution_sha256": sha256(solution_path),
                            "check_path": check_path.relative_to(stage).as_posix(), "check_sha256": sha256(check_path),
                            "source_declared_cost": original_cost, "reference_cost": check.routing_cost,
                            "reference_cost_hex": check.routing_cost.hex(),
                            "cost_changed": original_cost != check.routing_cost,
                            "cost_delta": check.routing_cost - original_cost})
            print(f"{name}: valid, cost_changed={original_cost != check.routing_cost}", flush=True)
        license_bytes = assert_pinned_file(root, FAMILY_PATH / "LICENSE")
        (stage / "SOURCE_LICENSE").write_bytes(license_bytes)
        lock = Path(__file__).resolve().parents[1] / "uv.lock"
        manifest = {"schema_version": 1, "snapshot_id": "dabia2013-n100-v011-2026-11-09",
                    "reference_policy": "pre-campaign fixed primal references",
                    "source_repository": REPOSITORY, "source_commit": SOURCE_COMMIT,
                    "checker": identity, "python_version": platform.python_version(),
                    "pydantic_version": metadata.version("pydantic"), "uv_lock_sha256": sha256(lock),
                    "source_license_sha256": hashlib.sha256(license_bytes).hexdigest(),
                    "instances": entries,
                    "summary": {"expected": 56, "valid": len(entries),
                                "repriced": sum(e["cost_changed"] for e in entries)}}
        write_json(stage / "manifest.json", manifest)
        (stage / "manifest.sha256").write_text(f"{sha256(stage / 'manifest.json')}  manifest.json\n")
        stage.rename(output)
    print(json.dumps(manifest["summary"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze(args.source_repo, args.output)
