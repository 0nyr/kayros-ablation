# Kayros ablation experiments

This repository studies Kayros's exact component and its cooperation with heuristic search for the Time-Dependent Vehicle Routing Problem with Time Windows (TDVRPTW). We compare the original Lera-Romero solver, Kayros's exact component with 2 Linear Programming (LP) backends, its standalone heuristic and a heuristic prefix followed by exact search. CPLEX is the main exact backend to match the original solver, independently of any presumed speed advantage. A separate cold Kayros arm uses HiGHS under otherwise matched parameters. Finer algorithmic ablations are outside the study's scope.

Correctness probes and development pilots are complete. The [full 16 GiB pilot](docs/ms56-hour-pilot-16g.md) selects a global 15 s prefix, and the [focused 2-hour pilot](docs/ms56-focused-pilot-16g.md) supports a recommended 7,200 s horizon, 16 GiB per process and 10 workers per host. The [main review](docs/main-campaign-review.md) and [1,400-cell proposal](configs/main-proposal.json) are ready for author inspection. The proposal remains nonlaunchable, and the main campaign has not run. Synthetic examples remain distinct from measured pilot results.

## Experiment scope

The main panel contains all 56 original Dabia2013 TDVRPTW instances with 100 customers, evaluated under the Duration objective. Subsets with 25 and 50 customers supported correctness checks. Completed 12-instance n100 pilots use a 1-hour horizon at separate 8 and 16 GiB budgets. The separate 2-hour C104/RC202 pilot supplies targeted horizon and resource evidence. The recommended main horizon is 2 hours.

| Arm | Definition |
|---|---|
| `lera-exact` | Original upstream solver with CPLEX, with separately recorded build and instrumentation patches |
| `kayros-bpca-cplex` | Kayros v1.6.1 exact component with CPLEX, cold start |
| `kayros-bpca-highs` | The same cold Kayros exact component with HiGHS and matched algorithmic parameters |
| `kayros-heuristics` | Kayros v1.6.1 Iterated Local Search (ILS) |
| `kayros-full-cplex` | ILS prefix followed by warm-started CPLEX exact search within one total budget |

The final experiment uses 5 trials per instance and arm, giving 1,400 cells across the 5 arms and 56 instances, with an upper budget of 2,800 solver core-hours at the recommended 2-hour horizon. ILS and full Kayros use 5 random seeds. The current cold exact APIs have no seed argument, so their trials are recorded as timing replications. Initial checks use 1 or 3 trials. The pilots compared fixed heuristic prefixes of 15, 60, 180, 600 and 1,800 seconds. Handoff occurs at the selected prefix deadline, without convergence or stagnation detection. The approved restricted mean computational-closure criterion selects 15 s globally.

## Evaluation contract

Every real solution cost and reference value must be evaluated with **mamut-routing-lib v0.11.0**, pinned to commit `425dff351fdd9522ea6bb8c537dd416dfcd19614`. Use complete solution validation, not a cost-only helper. Record the built artifact identity and verify the imported checker in each worker and analysis process. The newer checker is outside this study's contract.

The common solver clock starts at process launch with the environment prepared, including imports, instance loading, preprocessing and all solver phases. The latest available reference solutions are frozen in the [pre-pilot snapshot](references/dabia2013-n100-v011-2026-11-09/README.md). Independent replay validates all 56 references under v0.11. Repricing changes 11 source-declared costs while preserving the routes.

Incumbent evaluation uses mean squeezed-gap curves, raw gap-goal attainment, the Normalized Signed Primal Integral Λ and final gaps. Exact evaluation reports computational-closure attainment and restricted mean closure time. Compatible global primal–dual residual trajectories and aligned instance-status plots are planned main-analysis deliverables. See the [protocol](docs/protocol.md).

**Computational-closure time is recorded at solver search closure. External checking and audit times are excluded and are never reported.** Post-run validation may reject a claim, but cannot change its recorded solver completion time. Solver-internal arithmetic and integrity checks remain part of the solver computation.

Reported computational closure requires a solver closure event and an independently checked in-budget witness under the declared numerical conventions. Native guard warnings qualify affected claims. The stricter raw `accepted_proof` field is preserved separately and is not automatically promoted. Reference attainment alone is not closure. Resource failures are valid failed experimental entries, with checked incumbents and full scheduled denominator contributions retained.

## Run the synthetic example

Install [uv](https://docs.astral.sh/uv/), then run:

```bash
uv sync --locked
uv run --locked python analysis/metric_demo.py --output-dir outputs/synthetic
```

This writes a PDF, PNG and JSON summary. The costs and proof events are invented, and no solver or routing checker is called. The example shows how early incumbent discovery can coexist with slow proof completion. It checks the identity between the area under the unproved fraction and the mean proof time capped at the observation horizon.

## Layout

| Path | Contents |
|---|---|
| `docs/protocol.md` | Agreed design, timing and computational-closure scope |
| `configs/source_pins.json` | Immutable source/checker identities and current preparation status |
| `configs/main-proposal.json` | Nonlaunchable 1,400-cell proposal awaiting author inspection |
| `configs/main-analysis.json` | Frozen 0%, 1% and 5% raw-gap targets and main-analysis deliverables |
| `docs/main-campaign-review.md` | Completed pilot evidence and main recommendation |
| `analysis/` | Synthetic illustration, checked pilot summaries, closure, target, horizon and resource plots |
| `scripts/` | Checker guards and reference replay, pilot selection, process-launch probe harness and post-run validation |
| `tests/` | Small harness and checker-adapter regressions |
| `patches/` | Separately recorded source changes to pinned solvers |
| `references/` | Frozen source witnesses, v0.11 reference solutions and validation records |
| `outputs/` | Generated local outputs, ignored by Git |

Solver binaries, licensed dependencies, full instance/ATF data and real solver-run outputs are not bundled. Reference solutions and their validation records are included. Input and reference identities are pinned in the snapshot manifest. Solver builds and instrumentation are frozen in per-wave manifests. The checked-in main proposal remains `launchable: false` pending author inspection and future allocation/runtime binding.

## Sources and attribution

- [Kayros](https://github.com/0nyr/kayros), v1.6.1 commit `50eebc57964ac4f6762719ff21033783a31c12da`.
- [Lera-Romero's networks2020 solver](https://github.com/gleraromero/networks2020), commit `cabc56ad9cdb22bed81a8a31f1671d40152055e6`.
- [MAMUT-routing-lib](https://github.com/ANR-MAMUT/MAMUT-routing-lib), v0.11.0 commit `425dff351fdd9522ea6bb8c537dd416dfcd19614`.

Code created in this repository is distributed under the [MIT license](LICENSE). Referenced software and datasets retain their own licenses.

## Development probes

The [pilot and proof specification](docs/pilot-proof-parameters.md) records the frozen 12-instance panel, matched settings and operational closure scope alongside the historical stricter proof record. `scripts/run_solver_probe.py` launches a single diagnostic cell with a pinned runtime manifest. It is not a campaign scheduler. Run the lightweight fixture tests with `python -m unittest discover -s tests`. Build and run solver probes on reserved compute nodes. The main campaign remains held for author inspection of the completed pilot evidence and recommendation.
