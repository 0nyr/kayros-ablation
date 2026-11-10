# Reserved-compute U-filter opportunity pilot

The instrumentation source is `7d68859e8d6d8b8ae2382c1f7a88088709f099b1`, directly from a2dc. Its archive and exact patches are in `artifacts/ms58-u-filter-opportunity-7d68859e8d6d/`. The source archive SHA-256 is `88eb26d0f6d42e03562e0b2acc7431825aa4fa31f6a041ed416008fa9e81de0d`. The a2dc-to-opportunity incremental patch SHA-256 is `5cf28ef530a8dbb0d77c8515e2995d6f3360c188dddb31ee2c2e8c0358e29d0e`.

The new stage/build helpers create isolated source, patch, venv, artifact and log paths. The patch inventory contains the 4 baseline patches, selected9d full patch, existing a2dc incremental patch, and new opportunity incremental patch. Source pins include complete file inventories for all 3 sources. Existing selected9d and a2dc reserved-node libraries are reused for gates, while the new source is freshly built with CPLEX and both public profiling flags. No local Nix binary is used on Grid'5000.

The compute adapter reuses the reviewed a2dc controller and parser in private module objects. It changes only explicit source/schema pins and callbacks for the new native gate, 7-patch inventory, sampled opportunity aggregates and completed RC201 work-equivalence gate. Existing helper files and their ordinary imported module instances remain unchanged. Transfer the new adapter, stage/build helpers, configuration files and the existing experiment scripts together so that the common probe, checker and proof-consistency modules are available.

```bash
U_ROOT="$HOME/ms58-bpca"
U_SCRIPTS="$U_ROOT/experiment/scripts"
U_ART="$U_ROOT/artifacts/ms58-u-filter-opportunity-7d68859e8d6d"
U_PINS="$U_ART/source-pins.json"
U_SOURCE="$U_ROOT/sources/u-filter-opportunity"
U_CONTROL="$U_ROOT/sources/correction-on"
U_PARENT="$U_ROOT/sources/dominance-profile"
U_JOB=7014061
U_CHECKER="$HOME/ms56-ablation/experiment/.venv/bin/python"

python3 "$U_SCRIPTS/ms58_u_filter_opportunity_stage.py" --archive "$U_ART/source.tar.gz" --source-pins "$U_PINS" --patch-directory "$U_ROOT" --destination "$U_SOURCE" --patches-output "$U_ROOT/patches-u-filter-opportunity"
bash "$U_SCRIPTS/ms58_u_filter_opportunity_build.sh" "$U_ROOT" "$U_SOURCE" "$U_PINS" "$U_JOB"
```

These commands must execute inside the declared real OAR compute context. The new build log is `u-filter-build-logs/build.log`. The new worker Python is `venv-u-filter-opportunity-cplex/bin/python`. The common runtime variables remain `KAYROS_DOMINANCE_PROFILE`, `KAYROS_DOMINANCE_PROFILE_STRIDE` and `KAYROS_DOMINANCE_PROFILE_OFFSET`. The new schema is `kayros-ms58-u-filter-opportunity-v1`. The build helper performs no native fixture or routing launch.

## Reserved-node native gate

The native gate executes 5 compile/run pairs, retaining every command, stdout, stderr, result and executable hash. They cover the new standalone accounting fixture, ON and OFF 5,040-case PWL fixtures, the new 20-scenario native fixture linked to the opportunity library, and the same state fixture linked to the existing a2dc library. The expected PWL digest is `dcbfcebb1acf42ef` and the parent/new native state digest is `bdbf2a6cabb793c3`. The actual-loop fixture asserts zero membership/certificate violations and valid sampled subset-count partitions. The selected source's original correction invariants are also executed.

```bash
module load boost
U_NATIVE="$U_ROOT/dabia-20261110/native/u-filter-opportunity-v1"
"$U_CHECKER" "$U_SCRIPTS/ms58_u_filter_opportunity_compute.py" native --profile-source "$U_SOURCE" --control-source "$U_CONTROL" --parent-source "$U_PARENT" --source-pins "$U_PINS" --output "$U_NATIVE" --job-id "$U_JOB" --cores 2 4
```

Native gates are synthetic and do not solve a master LP. All gates must pass before runtime manifests are prepared. Their exact source/cache/compiler/library identities are rechecked before the pilot launches.

## Runtime manifests and fixed controls

The pilot contains C104 and RC201 from Dabia2013 n100, each with fixed minimalLera `f2c9e20`, uninstrumented selected9d correction-on, and the new opportunity source. Its independent immutable manifests retain complete runtime and patch inventories and the same classic checker. The arm key `dominance-profile` is retained for compatibility with the shared controller, while its full source pin and binary schema identify the opportunity derivative explicitly.

Supply a new runtime-pins JSON with the actual reserved-node `_core` and `_lera` hashes. Its keys are `correction-on` and `dominance-profile`, each containing `source_id`, `core_sha256` and `lera_sha256`. Their full source IDs must be selected9d and `7d68859e8d6d8b8ae2382c1f7a88088709f099b1` respectively. The selected9d control manifest must retain exactly the baseline 4 patches plus selected9d full patch. The opportunity patch directory must contain exactly the 7 lineage patches. The fixed Lera manifest retains its validated f2 binary and patch pins through the existing probe checks.

```bash
U_MANIFESTS="$U_ROOT/dabia-20261110/u-filter-artifacts-v1"
"$U_CHECKER" "$U_SCRIPTS/ms58_u_filter_opportunity_compute.py" manifest --profile-source "$U_SOURCE" --control-source "$U_CONTROL" --source-pins "$U_PINS" --output "$U_MANIFESTS" --native-gate "$U_NATIVE/complete.json" --runtime-pins "$U_ROOT/u-filter-runtime-pins.json" --control-python "$U_ROOT/venv-correction-on-cplex/bin/python" --control-manifest "$U_CONTROL_MANIFEST" --profile-python "$U_ROOT/venv-u-filter-opportunity-cplex/bin/python" --profile-patches "$U_ROOT/patches-u-filter-opportunity" --lera-python "$U_LERA_PYTHON" --lera-manifest "$U_LERA_MANIFEST" --lera-worker "$U_SCRIPTS/upstream_event_worker.py" --job-id "$U_JOB" --cores 2 4
```

The generated `plan.json` is the concrete CPLEX runtime manifest for the 3 arms. `configs/ms58-u-filter-opportunity-pilot.template.json` documents the source pins, 6-cell matrix and required result gate before runtime paths and hashes are known. Worker interpreter paths preserve venv symlinks.

## Freeze and bounded launch

Preparation freezes 6 cells without launching solvers. Each of the 2 physical lanes runs 3 arms serially, with reverse order across C104 and RC201. Each cell has a 600-second process-launch horizon and 16 GiB cap. The sampler uses stride `1009` and offset `0`, and only the diagnostic cells receive distinct exclusive output paths. There are no algorithm overrides. Selected9d and the opportunity source expect correction and lazy extension on, with observed metadata checked when available.

```bash
U_OUTPUT="$U_ROOT/dabia-20261110/u-filter-opportunity-gros-v1"
U_REFERENCES="$U_ROOT/experiment/references/dabia2013-n100-v011-2026-11-09/manifest.json"
"$U_CHECKER" "$U_SCRIPTS/ms58_u_filter_opportunity_compute.py" pilot --mode prepare --output "$U_OUTPUT" --plan "$U_MANIFESTS/plan.json" --native-gate "$U_NATIVE/complete.json" --profile-source "$U_SOURCE" --control-source "$U_CONTROL" --source-pins "$U_PINS" --job-id "$U_JOB" --cores 2 4 --references "$U_REFERENCES"
```

The controller writes `stop-before-real-budget.json` with zero solver cells started. A separate launch binds the exact schedule SHA, rechecks the allocation, source/native/checker identities and requires at least 3 × (600 + 90) + 180 seconds before the declared stop-before time. Skipped cells remain in the denominator. Set stop-before conservatively inside the actual reservation expiry. The value below is for root's Gros40 reservation 7014061 ending at 12:25 UTC.

```bash
U_SCHEDULE_SHA=$(sha256sum "$U_OUTPUT/schedule.json" | cut -d ' ' -f 1)
"$U_CHECKER" "$U_SCRIPTS/ms58_u_filter_opportunity_compute.py" pilot --mode launch --output "$U_OUTPUT" --job-id "$U_JOB" --release-real-budget "$U_SCHEDULE_SHA" --stop-before '2026-11-10T12:23:00+00:00'
"$U_CHECKER" "$U_SCRIPTS/ms58_u_filter_opportunity_compute.py" pilot --mode monitor --output "$U_OUTPUT"
```

The controller retains the shared checker, fixed references, native status and pooled feasible-witness/global-bound gates. Completed RC201 must have both native searches at Optimum and equal recorded root and whole-search work signatures against uninstrumented selected9d before interpretation. Missing sidecars, a time-limited RC201 run, unequal work or unusable proof observations fail this result gate. C104 may remain a censored mechanism sample. An unmatched pricing begin stays unfinished and never becomes solver completion.

## Opportunity interpretation

The adapter validates sampled denominators, zero membership/certificate violations, `subset_pass + subset_reject == actual_u_tests`, `covered_actual_rejects <= subset_reject`, and equal actual-test denominators across the 4 granularities. Its output separates returned pricing from unfinished last snapshots and aggregates each granularity separately. It never divides sampled coverage by unsampled parent counters.

Masks cover every full entry group, including cost-ineligible and unvisited labels. Correction retains original membership after retirement. Cost-break labels receive separate loop-entry counts and zero U-test coverage. Whole-bucket and block results overlap and cannot be added. Bypass flags may overlap. Production mask maintenance is unmeasured, sampled timings are perturbed by the new measurements, and no runtime benefit or net traversal saving is inferred.

The local no-solver adapter suite covers private-module isolation, both build flags and new log paths, 7-patch lineage, actual source staging, sampled coverage aggregation, malformed counter rejection, nonadditive bypass flags and completed-RC201 work-equivalence requirements. Run `python scripts/ms58_u_filter_opportunity_compute_test.py`. Existing frozen helpers and experiment results must not be overwritten when creating these new artifacts.
