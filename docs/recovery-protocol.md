# Recovery audit and explicit retry semantics

## Implemented read-only audit

We audit a frozen schedule with `scripts/audit_wave_recovery.py`. The caller supplies the expected SHA-256 of `wave.json` from an independent checkpoint and the frozen reference manifest. The tool reads the recorded allocation, scheduled task identities, runtime manifests, source and binary hashes, instance and travel-function hashes, atomic results, event streams and native sidecars. It uses the same task/result identity assertion as the pilot summaries and reconstructs each checked best-so-far envelope from the stored candidate assessments.

The audit prints a JSON report. It neither writes into the wave nor starts, closes or retries any task. It preserves terminal failure outcomes. A valid final result whose summary row is absent is reported as reconstructable. An incomplete cell is reported with unknown process state because its solver may still be running. Multiple matching cells are reported as ambiguous, without selecting an outcome. The tool collects no audit timing measurements.

The envelope check establishes consistency with the complete checker evidence already saved in the result. It does not rerun route evaluation or create another proof acceptance decision. Solver-reported closure, witness validity and any separately qualified computational claim remain distinct records.

We set `RECOVERY_WAVE_SHA256` to the digest retained in the independent checkpoint before running the audit.

```bash
python scripts/audit_wave_recovery.py \
  --wave /path/to/frozen-wave \
  --expected-wave-sha256 "$RECOVERY_WAVE_SHA256" \
  --reference-manifest references/dabia2013-n100-v011-2026-11-09/manifest.json
```

Artifact paths identify the original execution environment. A full runtime/input audit requires those declared files to remain readable. Native sidecars use the relocation-safe path checked by `load_completed`. An archived environment whose runtime paths are unavailable requires an explicit relocation policy before this tool can verify those bytes.

The development schema does not include an exclusive per-task prelaunch claim ledger or per-task runtime-manifest anchors in `wave.json`. The audit reports these readiness gaps. Optional `--manifest-anchors` supplies an independently retained JSON mapping from manifest basenames to canonical manifest digests. A main schedule should embed those digests and a complete frozen source inventory in its externally anchored manifest.

## Manual recovery and retry policy

We separate a logical scheduled task from an execution attempt. The logical identity includes the experiment manifest, instance, arm, complete algorithmic parameters, horizon, prefix, trial slot and search seed. Placement and environment identify the execution attempt. Cold exact repetitions keep a null search seed. An exclusive per-task attempt ledger remains a proposed executor improvement. The future controller now takes an exclusive node-start claim and refuses existing node/task artifacts before launching, which prevents accidental re-entry from replacing summary rows. The current tool audits existing artifacts and performs no launch or repair actions.

A sealed harness final record that passes the identity, digest and envelope checks is execution-complete, regardless of the algorithmic outcome. Deadlines, memory limits, solver crashes and invalid witnesses remain primary observations. We first recover a missing row from a uniquely associated valid final record. A native sidecar alone never establishes execution completion.

We permit no automatic retries. At most 1 explicit identical-configuration retry is available for an objectively documented external job, node or controller interruption, after confirming that the original processes are inactive. A missing heartbeat alone is insufficient outage evidence. The operator retains the original attempt, records the interruption evidence and reason, and assigns a new attempt number. The experiment configuration, cluster/hardware contract, binaries, numerical parameters, seed, horizon and prefix remain fixed. A source or adapter defect requires a new manifest and a recorded invalidation/rerun scope.

The primary observation is the first execution-complete attempt by attempt number. If neither attempt is execution-complete, we retain the initial attempt under the declared failure convention. We report every reason and attempt, plus a sensitivity analysis using the original first-attempt outcomes. Interrupted traces retain only independently checked incumbent prefixes and full-horizon non-success scores. We do not merge traces across attempts or select an outcome by cost, closure time or proof status. This routine manual policy does not require an additional author-approval gate, and the audit tool does not execute it.

## Tests

The focused fixtures use tiny synthetic subprocesses and declared synthetic checker evidence. They verify file immutability, missing-row detection, incomplete-claim refusal, wave and runtime drift, task association, corrupted envelopes and preservation of failed terminal outcomes. They are harness tests and provide no solver-performance observations.

