"""Process-launch-clock harness. Child stdout is JSONL; diagnostics go to stderr.

Workers inherit PROBE_START_MONOTONIC_NS and PROBE_DEADLINE_MONOTONIC_NS.
They must emit production timestamps on that clock, before external validation.
This module contains no solver adapter and makes no proof-acceptance decisions.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Callable

SCHEMA_VERSION = 1
EVENT_TYPES = {"phase", "candidate", "bound", "closure", "resource", "integrity"}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _validation_without_times(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            banned = {
                "audit_time", "audit_time_s", "audit_elapsed_s", "audit_duration_s",
                "validation_time", "validation_time_s", "validation_elapsed_s", "validation_duration_s",
                "external_validation_time_s", "external_validation_elapsed_s", "external_validation_duration_s",
                "checker_validation_time_s", "checker_validation_elapsed_s", "checker_validation_duration_s",
            }
            if key.lower() in banned:
                raise ValueError("external validation must not record timing fields")
            _validation_without_times(item)
    elif isinstance(value, list):
        for item in value:
            _validation_without_times(item)
    canonical(value)


def run_probe(*, output: Path, manifest: dict, parameters: dict,
              command: list[str], horizon_s: float, cwd: Path,
              environment: dict[str, str],
              validate: Callable[[list[dict]], object]) -> dict:
    """Run one immutable cell, then validate witnesses after the child exits.

    Environment preparation/building is the caller's responsibility. Environment
    is a complete explicit mapping and contributes to identity. Existing cells are
    never overwritten or silently resumed. Failed runs retain prior candidates.
    Validation receives events with their original production timestamps. Its
    output must contain outcomes only, without timing measurements.
    """
    if not math.isfinite(horizon_s) or horizon_s <= 0:
        raise ValueError("horizon must be finite and positive")
    if not command:
        raise ValueError("empty command")
    manifest_hash = digest(manifest)
    identity = {"schema_version": SCHEMA_VERSION, "manifest_hash": manifest_hash,
                "parameters": parameters, "command": command,
                "horizon_s": horizon_s, "cwd": str(cwd.resolve()),
                "environment_hash": digest(environment)}
    # Detach caller-owned nested structures before launching.
    identity = json.loads(canonical(identity))
    run_id = digest(identity)
    output.mkdir(parents=True, exist_ok=True)
    cell = output / run_id
    cell.mkdir()  # Fail closed on existing or incomplete artifacts.
    atomic_json(cell / "identity.json", {"run_id": run_id, **identity, "manifest": manifest})
    events: list[dict] = []
    late_events: list[dict] = []
    issues: list[str] = []
    status = "completed"
    returncode = None
    with (cell / "events.jsonl").open("xb") as event_file, (cell / "stderr.log").open("xb") as stderr, (cell / "late-events.jsonl").open("xb") as late_file:
        # This timestamp directly precedes Popen, charging process creation too.
        start_ns = time.monotonic_ns()
        deadline_ns = start_ns + int(horizon_s * 1e9)
        env = dict(environment, PROBE_START_MONOTONIC_NS=str(start_ns),
                   PROBE_DEADLINE_MONOTONIC_NS=str(deadline_ns), PROBE_RUN_ID=run_id,
                   PROBE_MANIFEST_HASH=manifest_hash)
        last_production_elapsed = -1.0
        def consume(line: bytes) -> None:
            nonlocal last_production_elapsed
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("event") not in EVENT_TYPES:
                    raise ValueError("unknown event type")
                elapsed = event["elapsed_s"]
                if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed):
                    raise ValueError("invalid production timestamp")
                if elapsed < 0 or elapsed > (time.monotonic_ns() - start_ns) / 1e9:
                    raise ValueError("production timestamp outside process horizon")
                if elapsed < last_production_elapsed:
                    raise ValueError("nonmonotone event timestamp")
                if any(key in event for key in ("run_id", "manifest_hash", "schema_version", "sequence")):
                    raise ValueError("worker attempted to replace event identity")
                last_production_elapsed = elapsed
                event = {**event, "run_id": run_id, "manifest_hash": manifest_hash,
                         "schema_version": SCHEMA_VERSION, "sequence": len(events)}
                encoded = canonical(event)
                if elapsed > horizon_s:
                    late = {**event, "sequence": len(late_events), "excluded_reason": "after_horizon"}
                    late_file.write(canonical(late) + b"\n")
                    late_file.flush()
                    os.fsync(late_file.fileno())
                    late_events.append(late)
                    return
                event_file.write(encoded + b"\n")
                event_file.flush()
                os.fsync(event_file.fileno())
                events.append(event)
                if event["event"] == "integrity":
                    issues.append("worker integrity event: " + str(event.get("reason", event.get("status", "unspecified"))))
            except (ValueError, TypeError, KeyError) as error:
                issues.append(str(error))
        process = None
        def terminate_group() -> None:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                       stderr=stderr, start_new_session=True)
            assert process.stdout is not None
            os.set_blocking(process.stdout.fileno(), False)
            pending = b""
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = (deadline_ns - time.monotonic_ns()) / 1e9
                    if remaining <= 0:
                        status = "deadline"
                        terminate_group()
                        break
                    for key, _ in selector.select(min(remaining, 0.05)):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        pending += block
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            consume(line)
                        if len(pending) > 16 * 1024 * 1024:
                            raise ValueError("event line exceeds size limit")
                # A worker may close stdout while continuing computation.
                if status != "deadline":
                    try:
                        process.wait(timeout=max(0, (deadline_ns - time.monotonic_ns()) / 1e9))
                    except subprocess.TimeoutExpired:
                        status = "deadline"
                        terminate_group()
                if status == "deadline":
                    # Kill all group members before draining. Already-flushed
                    # predeadline evidence may still be buffered in the pipe.
                    terminate_group()
                    process.wait()
                    drained = 0
                    while drained < 16 * 1024 * 1024:
                        try:
                            block = os.read(process.stdout.fileno(), 65536)
                        except BlockingIOError:
                            break
                        if not block:
                            break
                        drained += len(block)
                        pending += block
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            consume(line)
                    if drained >= 16 * 1024 * 1024:
                        issues.append("deadline drain exceeds size limit")
                if pending:
                    issues.append("unterminated event line")
            returncode = process.wait()
            if returncode != 0 and status == "completed":
                status = "failed"
        except Exception as error:
            status = "failed"
            issues.append(f"{type(error).__name__}: {error}")
        finally:
            if process is not None:
                # Also remove descendants that outlived their group leader.
                terminate_group()
                returncode = process.wait()
                process.stdout.close()
        solver_elapsed_s = (time.monotonic_ns() - start_ns) / 1e9
    if issues:
        status = "integrity_failure"
    # No clock is read around external validation and no audit duration is stored.
    try:
        validation = validate(json.loads(canonical(events)))
        _validation_without_times(validation)
    except Exception as error:
        validation = {"accepted": False, "reason": f"{type(error).__name__}: {error}"}
        status = "validation_failure"
    result = {"schema_version": SCHEMA_VERSION, "run_id": run_id,
              "manifest_hash": manifest_hash, "identity_hash": digest(identity),
              "status": status, "returncode": returncode,
              "solver_elapsed_s": solver_elapsed_s,
              "timeout_overshoot_s": max(0.0, solver_elapsed_s - horizon_s), "issues": issues,
              "event_count": len(events), "late_event_count": len(late_events),
              "late_events_sha256": hashlib.sha256((cell / "late-events.jsonl").read_bytes()).hexdigest(),
              "events_sha256": hashlib.sha256((cell / "events.jsonl").read_bytes()).hexdigest(),
              "validation": validation}
    atomic_json(cell / "result.json", result)
    return result


def load_completed(cell: Path, *, expected_run_id: str) -> dict:
    """Verify immutable identity, atomic completion and event integrity before reuse.

    A caller must derive expected_run_id from its current manifest and full
    parameters, rather than trusting a directory name from an earlier campaign.
    Failure statuses are complete observations, never successful solver claims.
    """
    identity_record = json.loads((cell / "identity.json").read_bytes())
    manifest = identity_record.pop("manifest")
    recorded_id = identity_record.pop("run_id")
    if (recorded_id != expected_run_id or digest(identity_record) != expected_run_id
            or identity_record.get("schema_version") != SCHEMA_VERSION
            or digest(manifest) != identity_record.get("manifest_hash")):
        raise ValueError("run identity mismatch")
    result = json.loads((cell / "result.json").read_bytes())
    if (result.get("run_id") != expected_run_id
            or result.get("identity_hash") != expected_run_id
            or result.get("manifest_hash") != identity_record["manifest_hash"]
            or result.get("schema_version") != SCHEMA_VERSION
            or result.get("status") not in {"completed", "deadline", "failed", "integrity_failure", "validation_failure"}):
        raise ValueError("invalid completion record")
    late_raw = (cell / "late-events.jsonl").read_bytes()
    if (hashlib.sha256(late_raw).hexdigest() != result.get("late_events_sha256")
            or len(late_raw.splitlines()) != result.get("late_event_count")):
        raise ValueError("late event digest or count mismatch")
    raw = (cell / "events.jsonl").read_bytes()
    if hashlib.sha256(raw).hexdigest() != result.get("events_sha256"):
        raise ValueError("event digest mismatch")
    events = [json.loads(line) for line in raw.splitlines()]
    if len(events) != result.get("event_count"):
        raise ValueError("event count mismatch")
    last = 0.0
    for sequence, event in enumerate(events):
        elapsed = event.get("elapsed_s")
        if (event.get("run_id") != expected_run_id
                or event.get("manifest_hash") != identity_record["manifest_hash"]
                or event.get("schema_version") != SCHEMA_VERSION
                or event.get("sequence") != sequence
                or event.get("event") not in EVENT_TYPES
                or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                or not math.isfinite(elapsed) or not last <= elapsed <= identity_record["horizon_s"]):
            raise ValueError("invalid persisted event")
        last = elapsed
    _validation_without_times(result["validation"])
    native = result["validation"].get("native_result") if isinstance(result["validation"], dict) else None
    if native and native.get("sha256") is not None:
        relative = native.get("relative_path")
        if not isinstance(relative, str):
            raise ValueError("native artifact relative path missing")
        artifact = (cell / relative).resolve()
        if not artifact.is_relative_to(cell.parent.resolve()):
            raise ValueError("native artifact path escapes output directory")
        try:
            actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError("native artifact missing or unreadable") from error
        if actual != native["sha256"]:
            raise ValueError("native artifact digest mismatch")
    return result
