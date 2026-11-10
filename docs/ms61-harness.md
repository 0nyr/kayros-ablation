# ms61 development harness

This is a lean harness for BPC development screens on Grid'5000 (G5K). It reuses ms58's solver invocations without ms58's frozen-manifest and audit machinery. The scripts live in `scripts/ms61_*` and `analysis/ms61_summary.py`. Remote work lives under `~/ms61-bpc` on nancy (`sources/`, `venvs/`, `scripts/`, `runs/`, `tmp/`). Never run the runner or the build on the frontend. Both scripts refuse non-compute hostnames. For a local test of the runner logic with a stub venv, `MS61_LOCAL_TEST=1` lifts the compute-hostname check (G5K frontends stay refused).

## Workflow

### 1. Sync a source tree (Kenzae)

```bash
cd $WORKSPACE/kayros-ablation
bash scripts/ms61_sync_source.sh $WORKSPACE/kayros-ms61 my-candidate
```

The script copies the worktree without `.git`, `build/` and `__pycache__` to `~/ms61-bpc/sources/my-candidate/`. It verifies that the remote `cpp/`+`src/` content hash equals the local one and writes `SOURCE_ID` (local HEAD, dirty flag, dirty file list, content hash). The build script always deletes `build/cplex` and builds from scratch, because rsync keeps source mtimes and an incremental build could silently miss changed files.

### 2. Reserve a node and build (inside a job)

```bash
ssh nancy.g5k 'oarstat -u $USER'
ssh nancy.g5k 'cd ~/ms61-bpc && oarsub -q default -p gros -n ms61-dev -l host=1,walltime=1 "sleep infinity"'
ssh nancy.g5k 'oarstat -fj JOB | grep -E "state|assigned_hostnames"'
ssh nancy.g5k "OAR_JOB_ID=JOB oarsh gros-NN 'MS61_BUILD_JOBS=16 bash -l ~/ms61-bpc/scripts/ms61_build.sh ~/ms61-bpc/sources/my-candidate ~/ms61-bpc/venvs/my-candidate'"
```

The scripts reach `~/ms61-bpc/scripts/` through `ms61_launch.sh` (which copies them on every launch). Before the first launch, copy them by hand with `rsync -a scripts/ms61_* scripts/checker_contract.py nancy.g5k:ms61-bpc/scripts/`. The build follows `ms58_correction_batch_build.sh` without sealing: `module load boost`, CPLEX 12.10 from `~/cplex1210`, `uv build --python 3.13 --wheel` with `KAYROS_WITH_LERA=ON`, `LERA_LP_BACKEND=cplex`, `KAYROS_EXTENSION_PROFILING=OFF`, `CMAKE_EXPORT_COMPILE_COMMANDS=ON`, `build-dir=build/cplex`, then a fresh uv venv with `~/ms56-phase2/solver/requirements-checker.txt` and the wheel. It refuses an existing venv path. Side files are `<venv>.wheel/`, `<venv>.build.log` and `<venv>.build-info.json` (source dir, `SOURCE_ID`, content hash, `_lera`/`_core`/wheel sha256, compiler, CPLEX hash, build seconds, host, job). A clean build of 234d838 took 35 s with 16 jobs on gros-118.

### 3. Write a plan

```json
{"defaults": {"n": 100, "memory_mb": 16384, "time_limit_s": 600},
 "cells": [
  {"id": "C104-base-r1", "kind": "kayros", "mode": "cold", "venv": "/home/frascous/ms61-bpc/venvs/base-234d838", "env": {}, "arm": "base", "instance": "C104", "rep": 1},
  {"id": "C104-drop2-r1", "kind": "kayros", "mode": "cold", "venv": "/home/frascous/ms61-bpc/venvs/my-candidate", "env": {"KAYROS_BPC_DROPBACK_LEVEL": "2"}, "arm": "drop2", "instance": "C104", "rep": 1},
  {"id": "C104-full15-r1", "kind": "kayros", "mode": "full", "prefix_s": 15, "seed": 123, "venv": "/home/frascous/ms61-bpc/venvs/base-234d838", "arm": "full15", "instance": "C104"},
  {"id": "C104-lera-r1", "kind": "lera", "arm": "lera-f2", "instance": "C104", "rep": 1}
 ]}
```

Ids must be unique and simple (they become directory names). `n` may be 25, 50 or 100. `env` values must be strings. The runner removes every inherited `KAYROS_*`, `PROBE_*` and `UPSTREAM_*` variable before applying the cell's `env`, so `"env": {}` is the plain baseline, and `cell.json` records the effective solver environment. Lera cells take no `env`. Without `arm`, a label is derived from the mode, venv name and env. Example plans are in `configs/ms61/`.

### 4. Launch (Kenzae)

```bash
bash scripts/ms61_launch.sh JOB configs/ms61/my-plan.json my-run 5
```

A relative out dir lands in `~/ms61-bpc/runs/`. The launcher checks that the job is Running, copies the scripts to `~/ms61-bpc/scripts/` and to a frozen copy in `<out>/scripts/`, copies the plan to `<out>/plan.json` (it refuses a different existing plan unless `MS61_FORCE_PLAN=1`) and starts one detached runner per job host through `oarsh` (`setsid nohup`, all fds redirected). With k hosts, host i runs cells with plan index % k == i. Extra runner flags go through `MS61_RUNNER_ARGS`, for example `MS61_RUNNER_ARGS="--grace-s 20"`. The launcher makes about 10 ssh round trips and takes about 2 minutes. Relaunching the same command resumes: cells with a `cell.json` are skipped, and an unfinished cell directory from a dead runner on the same host is moved to `<id>.stale-<epoch>`. `--rerun-failed` reruns failed cells (the old directory becomes `<id>.failed-<epoch>`). A per-host flock refuses a second live runner on the same out dir.

Each runner pins itself to the physical core of CPU 0 and gives each lane its own other physical core (first hardware thread only, from `/sys/devices/system/cpu/cpu*/topology`). On gros this allows at most 17 lanes, but memory binds first: a gros node has 96 GiB, so with 16 GiB caps use at most 5 lanes (ms58 required lanes x 16 GiB + 16 GiB available). The runner only warns when MemAvailable is below lanes x memory_mb. Keep the lane count equal across the arms being compared, because concurrency changes per-core speed. Memory is capped with `RLIMIT_AS` inside the worker, as in ms58. The clock starts immediately before `Popen`, the solver gets the deadline start + `time_limit_s` through `PROBE_DEADLINE_MONOTONIC_NS` and stops by itself. The runner sends SIGKILL to the process group only at deadline + grace (default 10 s). Status is `completed` (normal exit with `native.json`), `deadline` (solver-reported time limit, or killed after grace) or `failed` (nonzero exit, missing `native.json`, memory limit). The closure time to compare with ms58 is `closure_s`, the `elapsed_s` of the first closure event. `elapsed_s` in `cell.json` is launch to process exit.

### 5. Monitor

```bash
ssh nancy.g5k 'ls ~/ms61-bpc/runs/my-run/cells/*/cell.json | wc -l; tail -n 4 ~/ms61-bpc/runs/my-run/runners/*.log'
ssh nancy.g5k 'tail -n 20 ~/ms61-bpc/runs/my-run/cells/C104-base-r1/stderr.log'
```

### 6. Rsync back, release, summarize

```bash
rsync -a nancy.g5k:ms61-bpc/runs/my-run/ $WORKSPACE/kayros-ablation/outputs/ms61/my-run/
ssh nancy.g5k 'oardel JOB; oarstat -u $USER'
cd $WORKSPACE/kayros-ablation
nix develop ../nix-dev --command python analysis/ms61_summary.py outputs/ms61/my-run
```

The summary prints one block per instance and one row per cell: status, closure time, launch-to-exit time, final value, final lower bound, B&B nodes, root CG iterations, root columns, root cuts, and root pricing calls/seconds for the Heuristic Cost, Heuristic Elementarity and Exact levels. It also lists the root iterations slower than 5 s (`--slow-s`) from the stderr table as (iter, dt, LP value, #cols), marking rows that follow a `Cuts:` line because their dt includes separation. When `native.json` is absent the row falls back to events and the stderr table. Validation re-checks every candidate route set and the native final routes with the pinned checker (mamut-routing-lib 0.11.0 at 425dff3, identity verified by `scripts/checker_contract.py`) on the instance copies in `<out>/inputs/`. It requires `cg_unresolved_final_pricing == 0` and `checker_infeasible_columns == 0` for Kayros closures, and zero unbranched-fractional and RMP-infeasible counts for Lera closures. Per instance, every claimed optimum and every final lower bound must not exceed any checked feasible cost (including the frozen reference from `references/dabia2013-n100-v011-2026-11-09`) by more than 1e-6 relative, and all closures must agree. Violations print `SOUNDNESS ALERT`, the exit code becomes 1, and `<out>/summary.json` keeps the full machine-readable record. These checks are necessary consistency checks, not a proof certificate. Clean up remote run dirs once verified.

## Verified facts (2026-11-10, ms61 smoke)

Remote paths that exist and are used: `~/ms58-bpca/upstream-minimal-build/bp` (sha256 `59ce565d…0783`, identical to ms58's pinned f2 executable), `~/ms56-phase2/upstream/source` (native `instances/dabia_et_al_2013/<name>_<n>.json` for n = 25, 50 and 100, and `experiments/bp.json`), `~/ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n={25,50,100}` (MAMUT instances and ATFs), `~/ms56-ablation/experiment/.venv/bin/python` (Python 3.13.12 with the pinned checker, used for the runner and the Lera worker), `~/ms58-correction-domain-batch/venv-correction-on-domain-batch-cplex` (Kayros 234d838, `_lera` `5d20db5b…38d6`, `_core` `b527271e…87f3`), `~/ms56-phase2/solver/requirements-checker.txt`, `~/cplex1210` and `~/.local/bin/uv`. Nothing had to be restored.

Lera f2 invocation (`ms61_lera_worker.py`, same as ms58 `upstream_event_worker.py`). The binary runs with no arguments. Its stdin is three JSON lines: the `BP-CUTS` entry of `experiments/bp.json` with `time_limit` replaced by the remaining seconds (cut limit 500, partial, limited extension, unreachable strengthened, sort by cost, iterative merge, lazy extension, exact labeling, not symmetric), the native instance JSON, and `{}`. The environment carries `PROBE_START_MONOTONIC_NS`, `PROBE_DEADLINE_MONOTONIC_NS` (mandatory, because the patched binary reads its time limit from it) and `UPSTREAM_EXPECTED_END_DEPOT=n+1`. Stdout carries JSON event lines, and the last one, `native_final`, contains the native result that the worker stores in `native.json` under `native_result`. Stderr is the console log with the root iteration table.

Kayros invocation (`ms61_kayros_worker.py`, the ms58 `kayros_worker.py` unchanged apart from logging the `KAYROS_*` environment) runs `<venv>/bin/python ms61_kayros_worker.py --instance <vrp.json> --arm kayros-bpca-cplex|kayros-full-cplex --memory-mb M --native-result <cell>/native.json [--seed S --prefix-seconds P]`, under `taskset -c <core>`. The worker loads the instance with `load_td_instance(verify_sha256=True)` and calls `kayros.lera.solve_duration(loaded, time_limit_s=remaining, cut_limit=500, memory_limit_mb=M, initial_routes=witness, ...)`. The witness is the ILS incumbent at the fixed prefix in full mode and `None` in cold mode.
