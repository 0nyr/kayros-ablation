"""Post-run classic v0.11 witness checking, without audit timing measurements."""
from __future__ import annotations

import json
import math
from pathlib import Path

from checker_contract import checker_identity, sha256


def validate_candidates(events: list[dict], *, instance_path: Path,
                        instance_sha256: str, atf_path: Path,
                        atf_sha256: str, instance_name: str,
                        expected_checker: dict | None = None,
                        accepted_proof_audit: dict | None = None) -> dict:
    """Check every produced candidate, ignoring its native declared cost.

    Invoke only after solver exit. Native costs remain discrepancy evidence.
    A closure event alone cannot establish proof. Optional accepted audit evidence
    must name the exact closure and a separately defined acceptance protocol.
    This function does not implement that independent exact-search audit.
    """
    identity = checker_identity()  # Guard before importing any checker code.
    if expected_checker is not None and identity != expected_checker:
        raise RuntimeError("Checker identity differs from the declared manifest")
    if sha256(instance_path) != instance_sha256 or sha256(atf_path) != atf_sha256:
        raise RuntimeError("Probe input identity mismatch")
    raw_instance = json.loads(instance_path.read_bytes())
    if (raw_instance.get("instance_name") != instance_name
            or (instance_path.parent / raw_instance["td"]["atf_path"]).resolve() != atf_path.resolve()):
        raise RuntimeError("Probe instance name or ATF linkage mismatch")
    from mamut_routing_lib.enums import ObjectiveFunction
    from mamut_routing_lib.models import BenchmarkSolution
    from mamut_routing_lib.td import load_td_instance, check_td_solution

    loaded = load_td_instance(instance_path, verify_sha256=True, validate_complete=True)
    candidates = []
    envelope = []
    best_cost = None
    best_routes = None
    for event in events:
        if event.get("event") != "candidate":
            continue
        record = {"sequence": event["sequence"], "elapsed_s": event["elapsed_s"],
                  "routes": event.get("routes"), "native_cost": event.get("native_cost")}
        try:
            # Deliberately omit cost, including a native seed/store declaration.
            solution = BenchmarkSolution(instance_name=instance_name, routes=event["routes"])
            checked = check_td_solution(loaded, solution, ObjectiveFunction.DURATION)
            evidence = checked.model_dump(mode="json")
            valid = checked.is_valid()
            cost = checked.routing_cost
            if valid and (not math.isfinite(cost) or cost < 0):
                raise ValueError("Checker returned an invalid Duration cost")
            record.update(valid=valid, checked_cost=cost if valid else None,
                          checker_evidence=evidence)
            native = event.get("native_cost")
            if isinstance(native, (int, float)) and not isinstance(native, bool) and math.isfinite(native) and valid:
                record["native_minus_checked"] = native - cost
            else:
                record["native_minus_checked"] = None
            if valid and (best_cost is None or cost < best_cost):
                best_cost = cost
                best_routes = event["routes"]
                envelope.append({"elapsed_s": event["elapsed_s"], "sequence": event["sequence"],
                                 "checked_cost": cost, "routes": best_routes})
        except Exception as error:
            record.update(valid=False, checked_cost=None,
                          reason=f"{type(error).__name__}: {error}")
        candidates.append(record)
    closures = [event for event in events if event.get("event") == "closure"]
    proof = {"status": "proof_unverified" if closures else "no_closure_claim",
             "closure_sequences": [event["sequence"] for event in closures]}
    if accepted_proof_audit is not None:
        # Evidence is an input from a separately specified audit, never inferred
        # from status strings, reference attainment or a rounded native gap.
        audit = json.loads(json.dumps(accepted_proof_audit, allow_nan=False))
        matching = [event for event in closures
                    if event["sequence"] == audit.get("closure_sequence")
                    and event["run_id"] == audit.get("run_id")
                    and event["manifest_hash"] == audit.get("manifest_hash")]
        protocol = audit.get("protocol_sha256", "")
        if (audit.get("accepted") is not True or len(matching) != 1
                or best_cost is None or len(protocol) != 64
                or any(c not in "0123456789abcdef" for c in protocol)
                or not isinstance(audit.get("checks"), dict) or not audit["checks"]
                or any(value is not True for value in audit["checks"].values())):
            raise ValueError("Accepted proof audit is incomplete or mismatches this run")
        closure = matching[0]
        if not any(point["elapsed_s"] <= closure["elapsed_s"] for point in envelope):
            raise ValueError("Accepted proof closure has no preceding valid witness")
        proof.update(status="accepted", elapsed_s=closure["elapsed_s"], audit=audit)
    return {"checker": identity, "instance_name": instance_name,
            "instance_sha256": instance_sha256, "atf_sha256": atf_sha256,
            "candidates": candidates, "checked_envelope": envelope,
            "best_checked_cost": best_cost, "best_routes": best_routes, "proof": proof}
