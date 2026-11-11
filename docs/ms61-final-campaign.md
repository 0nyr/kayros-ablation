# ms61 final closure campaign: commands

This is the runbook of the final five-arm campaign of [protocol.md](protocol.md) (approved scope): 56 Dabia2013 TDVRPTW instances with 100 customers, Duration objective, 5 arms, 5 trials, 1,400 cells, 7,200 s from process launch, 16 GiB and 1 pinned core per cell. It extends the lean harness of [ms61-harness.md](ms61-harness.md). Never run builds, runners or the analysis on the frontend: the frontend only dispatches.

## Arms and cells

| Arm id | Cell | Worker arm | Seed |
| --- | --- | --- | --- |
| `lera-exact` | `kind: lera`, `lera_binary` = repaired control f3 (`/home/frascous/ms61-bpc/upstream-f3-build/bp`, sha256 `04073bef…04f2`) | Lera worker | null (timing replication) |
| `kayros-bpca-cplex` | `mode: cold`, `backend: cplex`, CPLEX venv | `kayros-bpca-cplex` | null (timing replication) |
| `kayros-bpca-highs` | `mode: cold`, `backend: highs`, HiGHS venv | `kayros-bpca-highs` | null (timing replication) |
| `kayros-heuristics` | `mode: ils`, CPLEX venv (the ILS does not use the LP backend) | `kayros-heuristics` | trial k: 42, 123, 456, 789, 2026 |
| `kayros-full-cplex` | `mode: full`, `prefix_s: 15`, CPLEX venv | `kayros-full-cplex` | trial k: same seed as the ILS arm |

Cell ids are `<instance>--<arm>--t<k>`. The runner checks the cell backend against the venv build-info before launch, and the worker checks `kayros._lera.LP_BACKEND` again at the exact phase. The ILS and full cells always pass `--seed` explicitly. The full arm's prefix is measured from process launch and charged to the single 7,200 s budget. Its exact phase receives the best in-prefix ILS incumbent (`warm_start: true` in the `exact` phase event, `exact.warm_start` and `handoff_witness` in `native.json`).

## Tools

- `scripts/ms61_final_plan.py` writes the plan. Without `--pilot` it refuses anything but the approved final shape. Cells are ordered long first by estimated duration (`configs/ms61/final-duration-hints.json`, from the ms61 archives) with the arms interleaved.
- `scripts/ms61_stage_remote.sh [PLAN ...]` copies the tools, the frozen reference manifest and the plans to `nancy:~/ms61-bpc/final-scripts/` in one ssh call and verifies every file by sha256.
- `scripts/ms61_build.sh SOURCE VENV [cplex|highs]` builds a venv on a node. `MS61_EXPECT_CORE_SHA256` makes the build fail unless the ILS `_core` module has that hash.
- `scripts/ms61_remote_launch.sh JOB PLAN OUT LANES` runs on the frontend. It reads the job from `oarstat -fJ`, freezes the scripts and the plan in `OUT/`, moves aside unfinished cells of finished jobs, starts one detached runner per host with `oarsh` in parallel, and writes `OUT/runners/launch-<epoch>.log`. The dispatch itself runs detached, so a dropped ssh session cannot leave a half launch. Runners use dynamic sharding: every runner walks the plan in order and claims the next free cell with an atomic `mkdir`, so hosts stay balanced whatever the host count. By default no runner starts a cell after job end minus the longest time limit minus grace minus 600 s (`MS61_STOP_LAUNCHING_AFTER`, `MS61_STOP_MARGIN_S`). Such cells stay pending for a later resume.
- `scripts/ms61_status.sh RUN` gives, in one ssh call, the finished cells by status and arm against the plan, the unfinished claims per host, the held cells, runner liveness per host (`pgrep` through `oarsh` while the job runs) and the last runner log lines.
- `analysis/ms61_final_analysis.py OUT` validates and analyses the run (see its docstring). It runs on a compute node with `~/ms56-ablation/experiment/.venv/bin/python` (numeric part, `--no-figures`) and locally in the Nix dev shell (`--figures-only` from the summary JSON). Outputs go to `OUT/analysis/`: `final-summary.json`, `results.md` (main table), `per-instance.md`, and `figures/` with a PDF, a PNG and a JSON plot-data file per figure (`proof-attainment`, `mean-squeezed-gap`, `goal-gap-{0,1,5}pct`, `mean-residual`, `bound-availability`, each also in log time, and `status-matrix`). Exit code 1 means soundness alerts, 3 design alerts or a failed internal cross-check.

Resuming is always the same launch command: cells with a `cell.json` are skipped, an unfinished directory of a host's dead runner is moved aside to `<id>.stale-<epoch>`, and unfinished directories of finished jobs are moved aside by the launcher. A host with a live runner on the same `OUT` refuses a second one through its per-host lock (verified in the smoke).

## Commands

All Kenzae commands run from `$WORKSPACE/kayros-ablation`. Replace `K17` by the name chosen for the Kayros 1.7 source, `JOB` by the OAR job id and `NODE` by one of its hosts. `g5k` is the retrying ssh helper of the coordinating session (`g5k [-t seconds] '<remote command>'`, retries on timeouts and ssh errors), plain `ssh -S none nancy.g5k` works too. The link to nancy is lossy: wrap idempotent commands in the retrying helper (`g5k`) and run `oarsub` exactly once, then check `oarstat -u $USER` before any retry.

### 1. Sync the source (Kenzae)

```bash
bash scripts/ms61_sync_source.sh $WORKSPACE/kayros-ms61 K17
```

### 2. Plans and staging (Kenzae)

```bash
python3 scripts/ms61_final_plan.py --pilot --name ms61-final-pilot \
    --instances C101,R101,RC105,C201,R201 --trials 2 --horizon 600 --prefix 15 \
    --cplex-venv /home/frascous/ms61-bpc/venvs/K17 --highs-venv /home/frascous/ms61-bpc/venvs/K17-highs \
    --output configs/ms61/final-pilot.json
python3 scripts/ms61_final_plan.py \
    --cplex-venv /home/frascous/ms61-bpc/venvs/K17 --highs-venv /home/frascous/ms61-bpc/venvs/K17-highs \
    --output configs/ms61/final.json
bash scripts/ms61_stage_remote.sh configs/ms61/final-pilot.json configs/ms61/final.json
```

Kayros 1.7 carries the leading-arm settings as defaults, so the plans need no environment. For an older build, `--leading-env` adds `KAYROS_BPC_PACKED=1 KAYROS_BPC_HE_CUTBLIND=1 KAYROS_BPC_MERGE_PREFILTER=1 KAYROS_BPC_LAM_ITERATE=1 KAYROS_BPC_NEAR_REL=0.10` to every Kayros cell, and `--env NAME=value` adds any other variable. The runner records the effective `KAYROS_*` environment and the analysis raises a design alert when it differs from the plan.

### 3. Reserve (Kenzae, once)

```bash
timeout 120 ssh -S none -o ConnectTimeout=45 nancy.g5k 'cd ~/ms61-bpc && oarsub -q abaca -p grvingt -n ms61-final -l host=45,walltime=8:00 "sleep infinity"'
g5k 'oarstat -u $USER; oarstat -fj JOB | grep -E "state =|assigned_hostnames"'
```

Sizing: the plan estimates about 1,700 core-hours (every ILS cell and every cell of an unclosed instance at the full 7,200 s). grvingt has 192 GiB per node, so 10 lanes of 16 GiB per host. With 45 hosts (450 lanes) the work is about 3.8 h, and an 8 h walltime leaves a launch window of about 5 h 50 min (job end minus 7,200 s minus grace minus 600 s). Cells not started in the window wait for a resume in a later job. The pilot needs 2 hosts for 1:30. Check the g5k skill for the queue rules of the day before choosing the walltime.

### 4. Build both venvs (one node of the job)

```bash
g5k -t 590 "OAR_JOB_ID=JOB oarsh NODE 'MS61_BUILD_JOBS=30 bash -l ~/ms61-bpc/final-scripts/scripts/ms61_build.sh ~/ms61-bpc/sources/K17 ~/ms61-bpc/venvs/K17 cplex 2>&1 | tail -5'"
core=$(g5k "python3 -c 'import json; print(*json.load(open(\"/home/frascous/ms61-bpc/venvs/K17.build-info.json\"))[\"core_so_sha256\"].values())'")
g5k -t 590 "OAR_JOB_ID=JOB oarsh NODE 'MS61_BUILD_JOBS=30 MS61_EXPECT_CORE_SHA256=$core bash -l ~/ms61-bpc/final-scripts/scripts/ms61_build.sh ~/ms61-bpc/sources/K17 ~/ms61-bpc/venvs/K17-highs highs 2>&1 | tail -5'"
```

Each build takes about 1 minute with 30 jobs on grvingt. The HiGHS build fails (exit 6, build-info kept) when its ILS `_core` differs from the CPLEX venv's.

### 5. Pilot, then campaign (Kenzae, one ssh call each)

```bash
g5k -t 400 'bash ~/ms61-bpc/final-scripts/scripts/ms61_remote_launch.sh JOB ~/ms61-bpc/final-scripts/plans/final-pilot.json final-pilot 10'
bash scripts/ms61_status.sh final-pilot
```

After the pilot is analysed (step 6) and clean, launch the campaign in the same or a new job:

```bash
g5k -t 400 'bash ~/ms61-bpc/final-scripts/scripts/ms61_remote_launch.sh JOB ~/ms61-bpc/final-scripts/plans/final.json final 10'
bash scripts/ms61_status.sh final
```

To resume (dead runners, a new job, or cells held after the launch window), rerun the same launch command with the current job id. Do not restage different scripts during a run: the launcher refuses a different frozen copy unless `MS61_FORCE_SCRIPTS=1`, and live runners execute the workers from `OUT/scripts/`.

### 6. Analysis on a node, figures on Kenzae

```bash
g5k -t 590 "OAR_JOB_ID=JOB oarsh NODE '~/ms56-ablation/experiment/.venv/bin/python ~/ms61-bpc/final-scripts/analysis/ms61_final_analysis.py ~/ms61-bpc/runs/final --workers 30 --no-figures 2>&1 | tail -30'"
mkdir -p outputs/ms61/final/analysis
rsync -az -e 'ssh -S none' nancy.g5k:ms61-bpc/runs/final/analysis/ outputs/ms61/final/analysis/
nix develop ../nix-dev --command python analysis/ms61_final_analysis.py outputs/ms61/final --figures-only
```

The numeric pass re-checks every route set. In the smoke it took 1.5 s wall time and 76 MiB for 50 cells with 30 workers on grvingt. Its cost grows with the number of ILS incumbents, so for 1,400 cells expect minutes, not hours (an extrapolation, not a measurement). A node must still be reserved for it: run it before releasing the campaign job, or reserve one node for 30 minutes.

### 7. Archive, verify, clean up

```bash
rsync -az --exclude=/inputs/ -e 'ssh -S none' nancy.g5k:ms61-bpc/runs/final/ outputs/ms61/final/
g5k "cd ~/ms61-bpc/runs/final && find . -path ./inputs -prune -o -type f -printf '%s\n' | awk '{n++; s+=\$1} END {print n, s}'"
(cd outputs/ms61/final && find . -path ./inputs -prune -o -type f -printf '%s\n' | awk '{n++; s+=$1} END {print n, s}')
```

The two lines must agree in file count and byte total. Only then delete the remote run and release the job:

```bash
g5k 'rm -rf ~/ms61-bpc/runs/final && ls ~/ms61-bpc/runs'
g5k 'oardel JOB; oarstat -u $USER'
```

The archive keeps no `inputs/`. A local re-analysis passes `--inputs-root /home/onyr/code/phd/mamut-routing-deploy/Mamut-routing/benchmarks/TDVRPTW/Dabia2013`, whose files are checked against the instance hashes recorded in every `cell.json`.

## Validation and metrics

The analysis implements sections 4 to 6 of the ms55 design and `configs/main-analysis.json`. Every scheduled cell is in every denominator, with equal instance weights and equal trial weights within an instance. A cell without `cell.json` counts as no incumbent, no bound and no proof, and the report is then marked provisional.

- Soundness: every candidate and the final route set are re-checked with the pinned checker, native and checked costs must agree, Kayros closures need `cg_unresolved_final_pricing == 0` and `checker_infeasible_columns == 0`, Lera closures need zero unbranched-fractional and RMP-infeasible counts, and per instance every claimed optimum, final bound and streamed bound must not exceed any checked feasible cost (all arms and trials, plus the frozen reference), with all closures agreeing. The reference manifest must have the pinned digest `ea731086…af66`.
- Design conformance: plan cell equals the cell that ran, worker arm, recorded `KAYROS_*` environment, LP backend of the exact phase, ILS seed, prefix length and end, exact start after the prefix, warm start consistent with the prefix witness, no ILS candidate after the prefix, Lera binary hash (f3) and metadata equivalence.
- A proof counts when the run completed normally, its first closure event lies within the horizon and no soundness or design alert touches the cell. Rejected claims stay in the denominator as unproved.
- Primal: squeezed gap with value 1 before the first checked incumbent, Λ by exact step integration over uniform time, goal attainment at 0 %, 1 % and 5 %. Exact: proof attainment for the 4 exact-capable arms (the ILS arm shows "no proof capability"), restricted mean time to proof (closure capped at the horizon), the mean residual (U - L) / U with value 1 until both a checked incumbent and a streamed bound exist, bound availability, and the status matrix (5 arm rows by 56 instance columns, trial proportions of proved, feasible and no valid incumbent, markers M memory, C crash, I integrity, K killed).
- Internal cross-checks: the squeezed-gap, 1 % goal, coverage and closure curves are compared with ms58's `curves()`, Λ with `pilot_summary.score_trace` (locally only, its imports are not staged), and the restricted mean with the area under 1 - F.

Offline regression: on the archived wave `broad3` the analysis reproduces the documented 32 closures and 919.8 s for `lera-f3` and 31 and 963.7 s for `F-pk`, with zero alerts. On the ms58 five-arm panel (converted by `analysis/ms61_adapt_ms58_panel.py`) it reproduces ms58's closure counts, restricted means and Λ for Lera, cold CPLEX and both full arms. For cold HiGHS it rejects the 3 RC105 closures that carry `checker_infeasible_columns = 1`, where ms58 only flagged them as guard warnings.

## Smoke of 2026-11-11 (job 7014937, grvingt-7 and grvingt-8)

Plan `configs/ms61/final-smoke.json`: C101, R101, RC105, C201 and R201, all 5 arms, 2 trials, 600 s, 15 s prefix, 10 lanes per host, source `ms61-c10` (CPLEX venv `ms61-c10`, HiGHS venv `ms61-c10-highs` built in this smoke) with the leading-arm environment. All 50 cells finished, none failed. Archive: `outputs/ms61/final-smoke/` (278 files, 6,760,375 bytes, identical counts and totals on both sides before the remote deletion).

| Arm | Accepted closures | Restricted mean time to proof (s) | Mean Λ | Reference attained |
| --- | ---: | ---: | ---: | ---: |
| `lera-exact` | 10 / 10 | 108.0 | 0.05201 | 10 / 10 |
| `kayros-bpca-cplex` | 10 / 10 | 132.1 | 0.10045 | 10 / 10 |
| `kayros-bpca-highs` | 4 / 10 (2 rejected) | 361.5 | 0.40429 | 8 / 10 |
| `kayros-heuristics` | no proof capability | no proof capability | 0.00617 | 5 / 10 |
| `kayros-full-cplex` | 9 / 10 | 162.5 | 0.00277 | 10 / 10 |

Every closure of every arm, including the 2 rejected ones, matches the frozen reference (C101 97901.1137, R101 26857.5262, RC105 23376.8126, C201 96115.8211, R201 22389.2998). The 2 soundness alerts are the 2 cold HiGHS closures of RC105, which report `checker_infeasible_columns = 1` (the ms61 rule rejects them, ms58 flagged the same case on RC105 as a guard warning). Every full cell ends its prefix at 15.00 s, starts the exact phase within 7 ms with `warm_start: true` and the handoff witness, and its native `exact.warm_start` reports the given routes. Every ILS cell runs to 600.1 s with its plan seed and 5 to 31 checked candidates. Every HiGHS cell reports `LP_BACKEND` HiGHS. The remote launcher dispatched both runners in 0.6 s and 0.9 s (parallel `oarsh`), the whole launch ssh call took 23 s, and a second launch while the runners were live was refused by both per-host locks. That launch call exited 1 with `LAUNCH FAILED` after a successful dispatch, because a quoting bug in the report step (fixed in `598d7dd`) broke the per-host runner summary. The fixed launcher was then run end to end only locally, with stub `hostname`, `oarstat` and `oarsh` (`LAUNCH COMPLETE`, stale claim of a finished job moved aside). The `results.md` main table also counts the rejected closures whose only defect is the checker-infeasible column guard, so the coordinator can qualify them instead of rejecting them without a re-analysis.
