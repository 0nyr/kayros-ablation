# Kayros ablation experiments

This repository studies the contributions of Kayros's exact component and its cooperation with heuristic search for the Time-Dependent Vehicle Routing Problem with Time Windows (TDVRPTW). We compare the original Lera-Romero solver, Kayros's exact component, its standalone heuristic and a heuristic prefix followed by exact search. Controlled comparisons will distinguish changes to the LP backend and cut settings from other implementation changes.

The project is at the protocol and implementation-preparation stage. The synthetic example runs now. Campaign runners, solver instrumentation and measured results are not yet available.

## Experiment scope

The main panel contains all 56 original Dabia2013 TDVRPTW instances with 100 customers, evaluated under the Duration objective. Subsets with 25 and 50 customers will support initial correctness checks. A 12-instance n100 pilot has a 1-hour horizon. The provisional main horizon is 2 hours, subject to the pilot.

| Arm | Definition |
|---|---|
| `lera-exact` | Original upstream solver, with separately recorded build and instrumentation patches |
| `kayros-bpca` | Kayros v1.6.1 exact component, cold start |
| `kayros-heuristics` | Kayros v1.6.1 Iterated Local Search (ILS) |
| `kayros-full` | ILS prefix followed by warm-started exact search within one total budget |

The final experiment uses 5 trials per instance and arm. ILS and full Kayros use 5 random seeds. The current cold exact APIs have no seed argument, so their trials must be recorded as timing replications unless a documented randomization control is introduced. Initial checks use 1 or 3 trials. The prefix duration, precise clock boundary, reference snapshot and intermediate component configurations remain open.

## Evaluation contract

Every real solution cost and reference value must be evaluated with **mamut-routing-lib v0.11.0**, pinned to commit `425dff351fdd9522ea6bb8c537dd416dfcd19614`. Use complete solution validation, not a cost-only helper. Record the built artifact identity and verify the imported checker in each worker and analysis process. The newer checker is outside this study's contract.

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
| `outputs/` | Generated local outputs, ignored by Git |

Solver binaries, licensed dependencies, benchmark data and real run outputs are not bundled. A campaign manifest will record their identities before execution. The checked-in source-pin file is deliberately marked `design_only_not_launchable` while artifact and input hashes remain unresolved.

## Sources and attribution

- [Kayros](https://github.com/0nyr/kayros), v1.6.1 commit `50eebc57964ac4f6762719ff21033783a31c12da`.
- [Lera-Romero's networks2020 solver](https://github.com/gleraromero/networks2020), commit `cabc56ad9cdb22bed81a8a31f1671d40152055e6`.
- [MAMUT-routing-lib](https://github.com/ANR-MAMUT/MAMUT-routing-lib), v0.11.0 commit `425dff351fdd9522ea6bb8c537dd416dfcd19614`.

Code created in this repository is distributed under the [MIT license](LICENSE). Referenced software and datasets retain their own licenses.
