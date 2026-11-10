# Reserved-compute dominance diagnostic

These independent helpers prepare a diagnostic for exact selected9d correction-on Kayros `9d64b7c53ddaea7a578d247ebd28db43ae0d4e56`, its dominance profiler `a2dcf8870c17e7edeccb472b27bee0e544eec997`, and fixed minimalLera `f2c9e20`. We use C104 and RC201 from Dabia2013 n100, CPLEX, a 600-second process-launch horizon and a 16 GiB cap per cell. The 2 physical lanes run their 3 arms serially in reverse orders. This is a mechanism diagnostic with no throughput claim.

All heavy work requires a declared real Gros OAR allocation. The helpers perform no SSH, reservation or remote launch. The parent controller owns transfer, allocation lifetime, launch, monitoring and result recovery. A fixed-witness handoff panel is independent of this diagnostic. No result from that panel is a prerequisite here.

## Source staging and build

Transfer the source archive from `artifacts/ms58-dominance-profile-a2dcf8870c17/source.tar.gz`, the source pins JSON, and the incremental patch from the same artifact directory. Also make the existing 4 baseline patches and `patches/ms58-experimental/correction-on-full.patch` available beneath the chosen patch directory. The stage helper selects hashes rather than relying on patch basenames. It requires exactly 6 distinct pinned patch identities in its output inventory. Its source extraction verifies every tracked file and preserves failed staging directories for inspection.

The source archive SHA-256 is `c455a8cbbb995f9eb3bcf7fe2ac67aeb578071dd7c5782c5d4a11b67174a21d6`. The selected9d full patch is `18b453c73422771ce0a2c6c5cbd6b934b62bcded0566c9c495d253106df28e87`. The a2dc incremental patch is `4f28744606029315c925ac8a78e96b9202a850813f1bb15815e188364925a2ff`. This stack is the 4 baseline patches, selected9d full patch and a2dc incremental patch. It does not substitute the older extension-profile full patch or the old 327 source.

Run the build helper inside the reserved compute shell. It enables `KAYROS_DOMINANCE_PROFILING=ON` publicly on `lera`, selects CPLEX, builds from the staged source and installs into a fresh `venv-dominance-profile-cplex`. It writes `dominance-build-logs/build.log` and retains failures. Its node build outputs must be used for every subsequent gate and cell. The local Nix `.so` is excluded from this recipe.

```bash
DP_ROOT="$HOME/ms58-bpca"
DP_SCRIPTS="$DP_ROOT/experiment/scripts"
DP_PINS="$DP_ROOT/experiment/configs/ms58-dominance-profile-source.json"
DP_SOURCE="$DP_ROOT/sources/dominance-profile"
DP_CONTROL="$DP_ROOT/sources/correction-on"
DP_JOB=7014010

python3 "$DP_SCRIPTS/ms58_dominance_profile_stage.py" --archive "$DP_ROOT/dominance-profile-source.tar.gz" --source-pins "$DP_PINS" --patch-directory "$DP_ROOT" --destination "$DP_SOURCE" --patches-output "$DP_ROOT/patches-dominance-profile"
bash "$DP_SCRIPTS/ms58_dominance_profile_build.sh" "$DP_ROOT" "$DP_SOURCE" "$DP_PINS" "$DP_JOB"
```

The Python controller helpers depend on the existing experiment scripts and the pinned checker Python. Transfer the updated experiment `scripts/` tree together, including `ms58_proof_consistency.py`, `checker_contract.py`, and the existing probe/worker modules. They remain independent files and do not edit the active five-arm wave.

## Native gates and runtime inventory

Use the project compiler and Boost module environment matching the reserved-node build. The native helper compiles and runs the accounting fixture, ON PWL fixture, OFF PWL fixture and native labeling fixture, retaining all compile/run records, stdout, stderr and executable hashes. The ON fixtures use the freshly built a2dc library. The OFF PWL fixture links the reserved-node selected9d library, whose source tokens match the derivative with instrumentation disabled. Both PWL executions must report exactly 5,040 cases and digest `dcbfcebb1acf42ef`. The labeling fixture also executes the selected source's original correction invariants. These are synthetic checks without a master LP solve.

```bash
DP_CHECKER="$DP_ROOT/experiment/.venv/bin/python"
DP_NATIVE="$DP_ROOT/dominance-native-v1"
module load boost
"$DP_CHECKER" "$DP_SCRIPTS/ms58_dominance_profile_native.py" --profile-source "$DP_SOURCE" --control-source "$DP_CONTROL" --source-pins "$DP_PINS" --output "$DP_NATIVE" --job-id "$DP_JOB" --cores 2 4
```

After the reserved-node build, supply `runtime-pins.json` using the actual installed `_core` and `_lera` hashes for both Kayros arms. Each path imported by its worker Python must appear with matching bytes in its manifest. No local Nix binary hash is accepted as a default. The controller checks the CPLEX backend and confirms that only the diagnostic arm contains the profiler schema marker.

```json
{
  "correction-on": {
    "source_id": "9d64b7c53ddaea7a578d247ebd28db43ae0d4e56",
    "core_sha256": "SUPPLY_G5K_HASH",
    "lera_sha256": "SUPPLY_G5K_HASH"
  },
  "dominance-profile": {
    "source_id": "a2dcf8870c17e7edeccb472b27bee0e544eec997",
    "core_sha256": "SUPPLY_G5K_HASH",
    "lera_sha256": "SUPPLY_G5K_HASH"
  }
}
```

The preparation helper needs the existing selected9d CPLEX manifest and fixed minimalLera manifest. Their checker identity, runtime bytes and patch inventories are reverified. The example variables below must name those existing manifests and their correct worker Python paths.

```bash
DP_ARTIFACTS="$DP_ROOT/dominance-runtime-artifacts-v1"
"$DP_CHECKER" "$DP_SCRIPTS/ms58_dominance_profile_prepare.py" --output "$DP_ARTIFACTS" --native-gate "$DP_NATIVE/complete.json" --source-pins "$DP_PINS" --runtime-pins "$DP_ROOT/runtime-pins.json" --profile-source "$DP_SOURCE" --control-source "$DP_CONTROL" --control-python "$DP_ROOT/venv-correction-on-cplex/bin/python" --control-manifest "$DP_CONTROL_MANIFEST" --profile-python "$DP_ROOT/venv-dominance-profile-cplex/bin/python" --profile-patches "$DP_ROOT/patches-dominance-profile" --lera-python "$DP_LERA_PYTHON" --lera-manifest "$DP_LERA_MANIFEST" --lera-worker "$DP_SCRIPTS/upstream_event_worker.py" --job-id "$DP_JOB" --cores 2 4
```

## Freeze, stop, then release the concrete budget

The run controller's prepare mode freezes the 6 cells, source/runtime manifests, source pins, scripts, inputs, checked reference artifacts and native gate. It then writes `stop-before-real-budget.json` with `solver_cells_started=0`. No solver can start in this mode. Inspect `schedule.json` and retain its exact SHA for the separate launch command.

```bash
DP_OUTPUT="$DP_ROOT/dominance-pilot-v1"
DP_REFERENCES="$DP_ROOT/experiment/references/dabia2013-n100-v011-2026-11-09/manifest.json"
"$DP_CHECKER" "$DP_SCRIPTS/ms58_dominance_profile_run.py" --mode prepare --output "$DP_OUTPUT" --plan "$DP_ARTIFACTS/plan.json" --native-gate "$DP_NATIVE/complete.json" --profile-source "$DP_SOURCE" --control-source "$DP_CONTROL" --source-pins "$DP_PINS" --job-id "$DP_JOB" --cores 2 4 --references "$DP_REFERENCES"
```

Launch requires the reviewed schedule SHA, the same real job and host, revalidated native/source identities, the same checker and sufficient actual remaining budget. The stop-before time must precede the real OAR expiry. The conservative time below belongs to reservation 7014010 and must be adjusted if the actual allocation evidence differs. The controller reserves 3 × (600 + 90) + 180 seconds before starting. It checks both wall and monotonic time before every cell and retains any skipped cell in the denominator. A separate launcher timeout prevents checker or adapter tails from running indefinitely.

```bash
DP_SCHEDULE_SHA=$(sha256sum "$DP_OUTPUT/schedule.json" | cut -d ' ' -f 1)
"$DP_CHECKER" "$DP_SCRIPTS/ms58_dominance_profile_run.py" --mode launch --output "$DP_OUTPUT" --job-id "$DP_JOB" --release-real-budget "$DP_SCHEDULE_SHA" --stop-before '2026-11-10T11:55:00+00:00'
"$DP_CHECKER" "$DP_SCRIPTS/ms58_dominance_profile_run.py" --mode monitor --output "$DP_OUTPUT"
```

The runtime variables are `KAYROS_DOMINANCE_PROFILE`, `KAYROS_DOMINANCE_PROFILE_STRIDE` and `KAYROS_DOMINANCE_PROFILE_OFFSET`. The controller assigns distinct exclusive output paths only to the 2 diagnostic cells. Defaults are stride `1009` and offset `0`. Both uninstrumented selected9d and minimalLera run without diagnostic overrides. The true source correction mode is on for selected9d and a2dc. Available Kayros native sidecars must report correction and lazy extension as on. Missing sidecars retain unknown observed metadata, including after an outer deadline.

## Interpretation and local checks

Every cell uses the pinned shared checker and fixed n100 reference snapshot. The audit binds checked candidates to in-budget events, verifies late-event identity, and applies the existing pooled feasible-witness/global-bound consistency helper across all 6 cells. Contradictory bounds and unsupported in-budget closure claims block proof usability. Work signatures remain descriptive, and missing sidecars leave work equivalence unknown.

Profile parsing separates returned pricing calls from unfinished calls, preserves explicit native status and accepts unfinished sampled episodes in progress snapshots. Each returned episode must partition its sampled nanoseconds across construction, scan and materialization. Surviving candidates may still have shrink/split mutations. An unmatched pricing begin or truncated final record never becomes a completion claim.

The local helper suite executes no solvers. It checks exact source/patch staging, source-mode audits, all 6 frozen cells and balanced order, unique diagnostic paths, sample outcome accounting, unfinished/truncated records, malformed timing rejection, schedule-SHA binding and skipped-cell retention. Run `python scripts/ms58_dominance_profile_test.py` for those checks. Reserved-node native gates and routing pilots remain separate actions.
