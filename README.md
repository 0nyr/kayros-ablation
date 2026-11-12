# Kayros component study harness

This repository holds the experiment harness, the plans and the analysis of the component study of KAYROS 1.7.0 on the Time-Dependent Vehicle Routing Problem with Time Windows (TDVRPTW). The final campaign ran 1,400 cells on Grid'5000, with the 56 Dabia2013 instances with 100 customers under the Duration objective, 5 arms, 5 trials per arm and instance, and 7,200 s and 16 GiB per cell. The arms are the repaired Lera-Romero solver with CPLEX (`lera-exact`), the cold KAYROS exact component with CPLEX (`kayros-bpca-cplex`) and with HiGHS (`kayros-bpca-highs`), the KAYROS Iterated Local Search (ILS) alone (`kayros-heuristics`), and an ILS prefix followed by the exact component with CPLEX (`kayros-full-cplex`). The [campaign runbook](docs/ms61-final-campaign.md) gives the commands, and `analysis/ms61_final_analysis.py` is the analysis of record.

## Published items

The study relies on 3 public items:

- [KAYROS 1.7.0](https://github.com/0nyr/kayros/releases/tag/v1.7.0), the solver version of the campaign.
- [The repaired baseline](https://github.com/0nyr/lera-tdvrptw-bpc-repaired), tag `component-study-as-run`, the Lera-Romero solver as run in the campaign.
- [The dataset](https://doi.org/10.5281/zenodo.24153428), with the run records, the analysis outputs and the code used.

## Relation to the dataset

The dataset ships 24 files of this repository and the frozen reference snapshot under `references/`, in its folder `code/kayros-ablation/`. Its README cites them at commit `1b3e1a8` of this repository. Before publication, some commit messages and file contents of the development history were edited, so the corresponding commit now has the identifier `bffd7d0f88cb4615cbbe7e972e12a8c9d4f1c4f1` (see [commit-identifiers.md](docs/commit-identifiers.md)). The files shipped by the dataset are unchanged at that commit.

## Working notes

The documents under `docs/` are working notes of the development, kept as a record, and some of them describe as unreleased things that have since been released. In these notes, `$WORKSPACE` stands for the local parent directory of the development checkouts. The identifiers of commits of this repository cited in the notes predate the edit above and no longer resolve, and [commit-identifiers.md](docs/commit-identifiers.md) maps each of them to its published commit.

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

Solver binaries, licensed dependencies, full instance/ATF data and real solver-run outputs are not bundled. Reference solutions and their validation records are included. Input and reference identities are pinned in the snapshot manifest. Solver builds and instrumentation are frozen in per-wave manifests. The checked-in main proposal remains `launchable: false` and is preserved with the source-pins file as a historical pilot snapshot. The current campaign-status record supersedes their readiness wording without changing their hashes.

## Sources and attribution

- [Kayros](https://github.com/0nyr/kayros), v1.6.1 commit `50eebc57964ac4f6762719ff21033783a31c12da` for the pilots, and v1.7.0 for the final campaign.
- [Lera-Romero's networks2020 solver](https://github.com/gleraromero/networks2020), commit `cabc56ad9cdb22bed81a8a31f1671d40152055e6`.
- [MAMUT-routing-lib](https://github.com/ANR-MAMUT/MAMUT-routing-lib), v0.11.0 commit `425dff351fdd9522ea6bb8c537dd416dfcd19614`.

Code created in this repository is distributed under the [MIT license](LICENSE). Referenced software and datasets retain their own licenses.

## Development probes

The [pilot and proof specification](docs/pilot-proof-parameters.md) records the frozen 12-instance panel, matched settings and operational closure scope alongside the historical stricter proof record. `scripts/run_solver_probe.py` launches a single diagnostic cell with a pinned runtime manifest. It is not a campaign scheduler. Run the lightweight fixture tests with `python -m unittest discover -s tests`. Build and run solver probes on reserved compute nodes.
