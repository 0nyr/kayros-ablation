# Kayros ablation experiments

This repository studies Kayros's exact component and its cooperation with heuristic search for the Time-Dependent Vehicle Routing Problem with Time Windows (TDVRPTW). We compare the original Lera-Romero solver, Kayros's exact component with 2 Linear Programming (LP) backends, its standalone heuristic and a heuristic prefix followed by exact search. CPLEX is the main exact backend to match the original solver, independently of any presumed speed advantage. A separate cold Kayros arm uses HiGHS under otherwise matched parameters. Finer algorithmic ablations are outside the study's scope.

The project is at the protocol and implementation-preparation stage. The synthetic example runs now. Campaign runners, solver instrumentation and measured results are not yet available.

## Experiment scope

The main panel contains all 56 original Dabia2013 TDVRPTW instances with 100 customers, evaluated under the Duration objective. Subsets with 25 and 50 customers will support initial correctness checks. A 12-instance n100 pilot has a 1-hour horizon. The provisional main horizon is 2 hours, subject to the pilot.

| Arm | Definition |
|---|---|
| `lera-exact` | Original upstream solver with CPLEX, with separately recorded build and instrumentation patches |
| `kayros-bpca-cplex` | Kayros v1.6.1 exact component with CPLEX, cold start |
| `kayros-bpca-highs` | The same cold Kayros exact component with HiGHS and matched algorithmic parameters |
| `kayros-heuristics` | Kayros v1.6.1 Iterated Local Search (ILS) |
| `kayros-full-cplex` | ILS prefix followed by warm-started CPLEX exact search within one total budget |

The final experiment uses 5 trials per instance and arm, giving 1,400 cells across the 5 arms and 56 instances, with an upper budget of 2,800 solver core-hours at the provisional 2-hour horizon. ILS and full Kayros use 5 random seeds. The current cold exact APIs have no seed argument, so their trials are recorded as timing replications. Initial checks use 1 or 3 trials. The pilot will compare fixed heuristic prefixes of 15, 60, 180, 600 and 1,800 seconds. Handoff occurs at the selected prefix deadline, without convergence or stagnation detection. The final prefix configuration will be selected before the main campaign.

## Evaluation contract

Every real solution cost and reference value must be evaluated with **mamut-routing-lib v0.11.0**, pinned to commit `425dff351fdd9522ea6bb8c537dd416dfcd19614`. Use complete solution validation, not a cost-only helper. Record the built artifact identity and verify the imported checker in each worker and analysis process. The newer checker is outside this study's contract.

The common solver clock starts at process launch with the environment prepared, including imports, instance loading, preprocessing and all solver phases. The latest available reference solutions are frozen in the [pre-pilot snapshot](references/dabia2013-n100-v011-2026-11-09/README.md). Independent replay validates all 56 references under v0.11. Repricing changes 11 source-declared costs while preserving the routes.

Incumbent evaluation uses mean squeezed-gap curves, raw gap-goal attainment, the Normalized Signed Primal Integral Λ and final gaps. Exact evaluation adds proof-attainment curves, restricted mean time to proof, global primal–dual gap trajectories and aligned instance-status plots. See the [protocol](docs/protocol.md).

**Proof time is the time when the solver completes its proof. External checking and audit times are excluded and will not be reported.** Post-run validation may reject a claim, but cannot change its recorded solver completion time. Solver-internal arithmetic and integrity checks remain part of the solver computation.

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
| `docs/protocol.md` | Agreed design and remaining scientific choices |
| `configs/source_pins.json` | Source identities and draft campaign settings, with unresolved artifacts marked explicitly |
| `analysis/metric_demo.py` | Reproducible synthetic illustration |
| `scripts/` | Pinned-checker guard, reference snapshot creation and replay |
| `references/` | Frozen source witnesses, v0.11 reference solutions and validation records |
| `outputs/` | Generated local outputs, ignored by Git |

Solver binaries, licensed dependencies, full instance/ATF data and real solver-run outputs are not bundled. Reference solutions and their validation records are included. Input and reference identities are pinned in the snapshot manifest. The checked-in source-pin file remains `design_only_not_launchable` while solver builds, instrumentation and final campaign settings remain unfinished.

## Sources and attribution

- [Kayros](https://github.com/0nyr/kayros), v1.6.1 commit `50eebc57964ac4f6762719ff21033783a31c12da`.
- [Lera-Romero's networks2020 solver](https://github.com/gleraromero/networks2020), commit `cabc56ad9cdb22bed81a8a31f1671d40152055e6`.
- [MAMUT-routing-lib](https://github.com/ANR-MAMUT/MAMUT-routing-lib), v0.11.0 commit `425dff351fdd9522ea6bb8c537dd416dfcd19614`.

Code created in this repository is distributed under the [MIT license](LICENSE). Referenced software and datasets retain their own licenses.
