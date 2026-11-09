"""Replay every frozen witness with v0.11, checking all recorded file identities."""

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform

from checker_contract import checker_identity, sha256


def verify(snapshot, source):
    identity = checker_identity()
    from mamut_routing_lib.enums import ObjectiveFunction
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import load_td_instance, check_td_solution

    manifest_path = snapshot / "manifest.json"
    expected_manifest = (snapshot / "manifest.sha256").read_text().split()[0]
    project = Path(__file__).resolve().parents[1]
    pins = json.loads((project / "configs/source_pins.json").read_text())
    campaign_pin = pins["dataset"]["reference_manifest_sha256"]
    if expected_manifest != campaign_pin or sha256(manifest_path) != campaign_pin:
        raise RuntimeError("Snapshot manifest digest mismatch")
    manifest = json.loads(manifest_path.read_text())
    if identity != manifest["checker"]:
        raise RuntimeError("Checker identity differs from the snapshot")
    if sha256(project / "uv.lock") != manifest["uv_lock_sha256"]:
        raise RuntimeError("Dependency lock differs from the snapshot environment")
    if metadata.version("pydantic") != manifest["pydantic_version"]:
        raise RuntimeError("Installed pydantic differs from the snapshot environment")
    if len(manifest["instances"]) != 56 or len({e["instance_name"] for e in manifest["instances"]}) != 56:
        raise RuntimeError("Expected exactly 56 distinct instances")
    for entry in manifest["instances"]:
        for key, digest_key, base in [("instance_path", "instance_sha256", source),
                                      ("atf_path", "atf_file_sha256", source),
                                      ("solution_path", "solution_sha256", snapshot),
                                      ("check_path", "check_sha256", snapshot)]:
            if sha256(base / entry[key]) != entry[digest_key]:
                raise RuntimeError(f"File identity mismatch: {entry[key]}")
        original = snapshot / "source-solutions" / Path(entry["source_reference_path"]).name
        if sha256(original) != entry["source_reference_sha256"]:
            raise RuntimeError(f"Source witness hash mismatch: {original.name}")
        record = json.loads((snapshot / entry["solution_path"]).read_text())
        if record["instance_name"] != entry["instance_name"] or record["objective_function"] != "Duration":
            raise RuntimeError("Reference identity/objective mismatch")
        if record["routes"] != json.loads(original.read_text())["routes"]:
            raise RuntimeError("Frozen routes differ from their original source witness")
        if record["cost"].hex() != entry["reference_cost_hex"]:
            raise RuntimeError("Frozen declared cost differs from the manifest")
        loaded = load_td_instance(source / entry["instance_path"], verify_sha256=True, validate_complete=True)
        solution = BenchmarkSolution(instance_name=record["instance_name"], routes=record["routes"], cost=record["cost"])
        result = check_td_solution(loaded, solution, ObjectiveFunction.DURATION)
        if not result.is_valid() or result.routing_cost.hex() != entry["reference_cost_hex"]:
            raise RuntimeError(f"Reference replay failed: {entry['instance_name']}")
        if result.model_dump(mode="json") != json.loads((snapshot / entry["check_path"]).read_text()):
            raise RuntimeError(f"Route-level checker evidence differs: {entry['instance_name']}")
    if sha256(snapshot / "SOURCE_LICENSE") != manifest["source_license_sha256"]:
        raise RuntimeError("Source license digest mismatch")
    print(json.dumps({"valid": 56, "reference_manifest_sha256": sha256(manifest_path),
                      "checker_commit": identity["commit"], "python_version": platform.python_version(),
                      "matches_snapshot_python": platform.python_version() == manifest["python_version"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, required=True)
    args = parser.parse_args()
    verify(args.snapshot, args.source_repo)
