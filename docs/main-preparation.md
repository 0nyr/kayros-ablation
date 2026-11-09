# Main schedule preparation

We have prepared the complete 56-instance roster from the immutable pre-pilot reference manifest and a nonlaunchable inventory of 1,400 logical cells: 56 instances, 5 arms and 5 trial slots. The main full arm has exactly 1 configuration. The historical roster template leaves its prefix and common horizon unset. Both completed full-panel pilots now select a global prefix of 15 s under the approved rule. The concrete proposal will bind that selection and the final horizon after focused review. The template assigns no hosts, runtime environments or process identities and creates no solver outputs or reservations.

The generated `configs/main-selection.json` records every instance hash and source-reference hash against reference manifest SHA-256 `ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66`. The paired heuristic seeds are 42, 123, 456, 789 and 2026 at trial IDs 1 to 5. Cold exact arms keep a null search seed and use those IDs as runtime repetitions. `configs/main-schedule-template.json` contains the exact roster and trial mapping with `launchable: false`.

```bash
python scripts/prepare_main_schedule.py --output-dir /path/to/new-preparation-directory
```

The generator checks the independently pinned reference digest and refuses to replace existing preparation artifacts. Its tests verify all 1,400 unique task identities, 25 tasks per instance, 5 arms, 5 trial slots and the paired seed mapping.

## Explicit selection and resource settings

The distributed controller now accepts `--selection-file` and `--expected-count`. Its defaults remain the frozen 12-instance pilot roster. Each future wave copies the actual selection file into `selection.json`, records its source path and SHA-256, and checks every selected instance against the frozen reference manifest before launching cells. A full-panel main invocation must explicitly select the 56-instance roster and use exactly the approved main configuration.

The controller also accepts `--memory-mb`, interpreted as MiB for the process address-space cap, with the existing default of 8,192 MiB. Each node checks the sum of simultaneous core-lane caps plus a 16 GiB reserve against its actual available RAM. The completed 8 GiB pilot retains its original limits and 2 allocation failures. At the author's request, both follow-ups use 16,384 MiB and at most 10 workers per node, requiring 176 GiB available RAM on a fully occupied node. The completed full-panel repeat and separate focused results will guide the final cap and concurrency without assuming that a 2-hour run fits the original limit. Per-process virtual-memory observations supplement RSS because the enforced limit is address space.

Capacity must be checked again after the pilot review. The official grvingt snapshot at 11:00 CET on 2026-11-09 showed about 41 clearly usable hosts including the 21 allocated to the original pilot, subject to job completion and standby availability. At 10 workers per host, this gives 410 slots for 1,400 main cells and hence 4 rounds. A provisional 7,200-second horizon would require up to 8 hours plus setup, audit and transfer margin. At least 47 hosts would reduce that bound to 3 rounds. These figures are capacity scenarios, not reservations or a fixed main-campaign plan. The completed full-panel repeat used a 33-host allocation, which has been released. Only the separate focused follow-up remains active on 6 hosts.

## Recovery and launch boundary

The read-only recovery audit and the manual retry policy are described in [recovery-protocol.md](recovery-protocol.md). The historical roster template preserves the frozen population and exact inventory without filling its pending prefix or horizon fields. The completed full-panel decision selects 15 s, while the final horizon and resource recommendation await the focused review. Neither the template nor this decision launches the main campaign. The concrete main manifest must record the chosen configuration, matched resource limits, runtime/source identities, selection provenance and the attempt-selection policy before dispatch.

After both follow-ups finish, `scripts/prepare_main_proposal.py` binds the complete full-panel summary, its eligible prefix decision and the separate focused horizon review to the frozen references. It produces a concrete 1,400-cell proposal with one selected full-arm prefix, paired trial seeds, a common horizon within the reviewed range, the same memory cap and an explicit capacity calculation. The proposal retains `launchable: false` and the author-review requirement. It creates no reservation, runtime environment or solver output. Evidence files and the generator receive SHA-256 records, and an existing proposal is never overwritten.

`analysis/resource_comparison.py` compares the two complete 1-hour panels using matched instance/configuration/trial identities and common input, seed, reference and horizon values. It keeps separate resource-budget columns and all failed observations. `analysis/resource_review.py` separately displays observed individual-process virtual-memory peaks with explicit sampling coverage. Missing observations remain missing, and worker and child address spaces are never added together. These artifacts support the final resource recommendation without attributing every cross-wave timing change to memory alone.

The accepted main raw-gap targets are frozen at 0%, 1% and 5% in [configs/main-analysis.json](../configs/main-analysis.json). This specification is carried into generated schedules and proposals. Mean compatible primal-dual residual and bound-availability figures, together with an aligned per-instance status matrix, remain main-analysis deliverables rather than pilot-completion or main-launch gates. Existing 1% pilot figures retain their narrower scope.

## Reviewed future coordinator invocation

The existing distributed driver accepts the main population through explicit arguments. It does not ingest the concrete proposal. After the author inspection, ordinary execution preparation must compare the generated invocation and task inventory against that proposal, including all 1,400 task keys, paired seeds and exact-arm repetitions, horizon, cap, selected prefix and frozen analysis specification. Bind the actual allocation and runtime manifests at that stage. Preserve `configs/main-analysis.json` with the future wave's analysis inputs and record its hash. This manual comparison is sufficient and does not require a new adapter or authorization flow.

The following template illustrates the provisional 7,200 s, 16 GiB configuration with prefix 15 s. Replace the prepared-source, output-root, upstream-manifest and unique wave-name placeholders with the reviewed paths. Run only inside the actual attached OAR compute allocation, after the required author inspection. The prepared upstream manifest must be retained outside cleanup-prone output directories and pin the approved upstream executable, native inputs, patch and worker environment.

```bash
mapfile -t main_hosts < <(awk '!seen[$0]++' "$OAR_NODEFILE")
main_source="$HOME/<main-prepared-source>"
main_root="$HOME/<main-output-root>"
main_upstream_manifest="$HOME/<main-prepared-upstream-manifest>.json"

"$HOME/ms56-ablation/experiment/.venv/bin/python" \
  "$main_source/scripts/development_wave.py" \
  --mode coordinator \
  --root "$main_root" \
  --source "$main_source" \
  --selection-file "$main_source/configs/main-selection.json" \
  --expected-count 56 \
  --inputs "$HOME/ms56-ablation/inputs" \
  --patch-dir "$main_source/patches" \
  --checker-python "$HOME/ms56-ablation/experiment/.venv/bin/python" \
  --cplex-python "$HOME/ms56-phase2/solver/venv-cplex/bin/python" \
  --highs-python "$HOME/ms56-phase2/solver/venv-highs/bin/python" \
  --cplex-patch 0001-kayros-bound-events.patch \
  --cplex-patch 0002-cplex-single-thread.patch \
  --cplex-patch 0003-kayros-node-closure-census.patch \
  --cplex-patch 0004-kayros-effective-backend-settings.patch \
  --highs-patch 0001-kayros-bound-events.patch \
  --highs-patch 0002-cplex-single-thread.patch \
  --highs-patch 0003-kayros-node-closure-census.patch \
  --highs-patch 0004-kayros-effective-backend-settings.patch \
  --workers-per-host 10 \
  --memory-mb 16384 \
  --horizon 7200 \
  --full-prefixes 15 \
  --repetitions 5 \
  --hosts "${main_hosts[@]}" \
  --upstream-manifest "$main_upstream_manifest" \
  --upstream-worker "$HOME/ms56-phase2/upstream/upstream_event_worker.py" \
  --wave-name '<unique-main-wave>' \
  --label 'approved main campaign'
```

Explicit flags are essential. Without `--hosts`, the driver uses only the first 3 allocated hosts. The default selection contains 12 instances, the default repetition count is 1, and omission of `--upstream-manifest` produces only 4 arms. The defaults for workers, memory and horizon are 16, 8,192 MiB and 60 s. The default patch lists omit the later closure-census and effective-setting patches. The complete command above yields 56 instances, 5 configurations and 5 trials, with exactly 1 full-arm prefix. The HiGHS inventory retains the CPLEX-only patch as source provenance, matching the reviewed pilot inventory.

The existing upstream worker selects the approved binary at `~/ms56-phase2/upstream/build/bp` and native source/input tree at `~/ms56-phase2/upstream/source` through its manifest environment. The launcher fixes OMP, OpenBLAS and MKL thread counts to 1 and applies the recorded per-process address-space cap and CPU affinity. Rehash the prepared runtime inventory when binding the actual future wave, using the approved pilot identities as the comparison anchor.

The driver shuffles tasks with seed 20261109, assigns hosts round-robin and advances core lanes after each host cycle. Each lane executes its assigned tasks serially, preventing simultaneous reuse of its core. The maximum number of cells per lane is `ceil(1400 / (10 * hosts))`. Thus 47 hosts require at most 3 solver rounds, 40 require 4 and 33 require 5. At 7,200 s these are 6, 8 and 10 solver hours before coordination, checking and transfer margin. Post-run checking is excluded from solver metrics but occupies the lane before its next cell, so the reservation must allow for that work. The node guard checks actual available memory against the caps for occupied lanes plus its 16 GiB reserve, giving 176 GiB for 10 lanes.

Health, RSS and V4 virtual-memory observers require separate startup. The coordinator does not start them automatically. Assign observer CPUs outside the solver lanes, preserve observer source hashes and sampling coverage, and stop them after the wave. This is ordinary execution preparation alongside the actual OAR nodefile, runtime binding and archive checks. It does not alter the current nonlaunchable proposal or authorize a main reservation.
