# Five-arm Dabia plotting contract

We audit the frozen schedule with `ms58_dabia_summary.py`, then render each customer count separately with `ms58_dabia_plots.py`. These commands read stored outcomes and never launch a solver. We keep the inputs, references, checker, minimal Lera binary and repair patch, selected Kayros source, per-cell manifests and runtime identities explicit. The figure generator waits for the complete schedule, requires the cross-run feasible-witness consistency audit and refuses unresolved artifact or identity audit failures.

From the repository root, set `MS58_DABIA_WAVE` to the synchronized directory containing `wave.json`, `rows`, `cells`, `manifests`, `frozen-patches`, `frozen-references` and `frozen-scripts`.

```bash
nix develop $WORKSPACE/nix-dev --command python analysis/ms58_dabia_summary.py --wave "$MS58_DABIA_WAVE" --output "$MS58_DABIA_WAVE/analysis/summary.json"
nix develop $WORKSPACE/nix-dev --command python analysis/ms58_dabia_plots.py --summary "$MS58_DABIA_WAVE/analysis/summary.json" --output "$MS58_DABIA_WAVE/analysis/figures"
```

Each present size receives a directory named `n25`, `n50` or `n100`, containing the following figures in PNG, PDF and SVG, together with `plot-data.json` containing exact coordinates and source hashes.

| Filename | Quantity |
| --- | --- |
| `closure-overview` | Fraction with solver-reported computational closure and an independently checked witness |
| `mean-convergence` | Mean squeezed reference gap, classic mean raw reference gap, and incumbent coverage |
| `goal-gap-1pct` | Fraction attaining a raw reference gap of at most 1% |

Every figure also has a `-log-time` version. The linear view covers the full 3,600 s horizon. The logarithmic view starts at 0.01 s while its exact data retain time 0. Both use elapsed time from process launch, including loading, initialization and the full-arm prefix. The minimal Lera arm is black, cold CPLEX orange, cold HiGHS purple, full CPLEX with a 15 s prefix dark green and full CPLEX with a 5 s prefix dashed green.

## Existing plot entrypoints

The established closure command is `python analysis/closure_plots.py --summary SUMMARY.json --output DIRECTORY`. It writes `closure-overview.png` and `closure-overview.pdf`. No separate executable named `closure-overview` was found in the PhD workspace or local command directories. The new renderer preserves this command's step curves, equal instance weights, equal trial weights within an instance and checked-witness closure rule. It additionally separates customer counts, distinguishes the new 5 s prefix and writes SVG and provenance data.

For an independent rendering of the size-100 performance wave with the existing command, run the following after the audit has completed without failures. Its default style maps the new 5 s and existing 15 s prefixes to the same green, so the extended renderer supplies the distinct styles for the final comparison.

```bash
nix develop $WORKSPACE/nix-dev --command python analysis/closure_plots.py --summary "$MS58_DABIA_WAVE/analysis/summary.json" --output "$MS58_DABIA_WAVE/analysis/established-closure"
```

The mean and target contracts follow `pilot_plots.py` and `raw_gap_plots.py`. The older classic reference-gap plotting function filters empty trajectories, so we preserve its raw-gap definition without applying that filtering to this panel.

## Interpretation

We use the frozen positive reference cost `r` and the latest independently checked incumbent cost `z`. The raw gap is `(z-r)/r`. The squeezed gap is `(z-r)/(z+r)`, with value 1 while an incumbent is missing. Below-reference values remain negative. The classic raw mean is undefined until every scheduled run in the arm has an incumbent, and the coverage panel makes that limitation explicit. If a run never obtains an incumbent, the full-population raw mean stays undefined throughout. We retain previously checked incumbents after a subsequent solver failure.

The 1% curve counts runs whose checked incumbent satisfies `(z-r)/r <= 0.01`. This is an incumbent-quality statement relative to a frozen feasible reference, not a proof-of-optimality statement. Neither a reference match nor a small gap creates a closure event. Closure requires the existing event, integrity and witness checks, and remains a solver-reported computational claim. Native guard warnings remain visible in the closure legend. Failed, censored and missing-incumbent cells remain in every scheduled denominator. The summary also reports status counts, missing incumbents and the mean closure time capped at 3,600 s.

Before accepting any closure, we compare every emitted `global_lb` and every closure's checked incumbent with the minimum cost among the frozen independently checked reference and all valid, in-budget checked candidates for that instance across the entire scheduled cohort. We verify reference solution and check files against their frozen hashes. We bind each validation payload to the instance name, input hash and ATF hash, then bind candidate routes, elapsed times and sequence numbers to their persisted events. Closure also requires a finite lower bound matching its preceding checked incumbent. A difference above the absolute tolerance of `1e-6` in objective units marks a contradiction. This is a retrospective logical check, so a better witness obtained later in another run can refute an earlier bound or closure. Late bound and closure events undergo their own identity and timestamp checks, and never add timing credit or feasible candidates. An actual late contradiction invalidates prior proof credit, while unsupported late evidence alone does not erase an otherwise supported in-budget closure. Identical names with different input or ATF hashes cannot share this audit. The shared implementation is `scripts/ms58_proof_consistency.py`, used by both analysis and candidate validation.

A contradiction removes closure credit, sets the capped closure time to the horizon and marks the proof observation unusable. The raw controller row, status, claims, original closure time and valid quality trajectory remain available. Contradicted runs remain in the closure denominator and are counted in its legend. The feasible ceiling only supports this necessary proof-consistency check. It does not replace the frozen reference used for gap plots, and passing it does not establish independent proof certification. Partial summaries may change as additional checked witnesses arrive, so candidate selection waits for the complete cohort.

We report the 12 size-100 development instances separately from the size-25 and size-50 pilots. The existing selection contains 2 instances from each of the 6 classes, which supports class coverage and continuity with earlier diagnostics. Its equal-instance aggregate is therefore also an equal-class aggregate. The 56-instance collection has unequal class sizes, so the 12-instance result does not estimate its equally weighted average. These instances have already informed development and do not provide independent confirmation. We should freeze any expansion before inspecting the new outcomes, preferably adding the remaining size-100 instances rather than selecting additions by favorable or unfavorable results. Even a complete 56-instance comparison would remain a descriptive development assessment after this tuning history.

## Validation

The 17 metric and integrity tests cover missing-incumbent denominators, unequal repetition counts, negative below-reference gaps, the full-coverage requirement for raw means, customer-count identity collisions, native input identity, backend thread drift, controller audit failures, late evidence and cross-run proof contradictions. A read-only regression on 8 preserved diagnostic artifacts rejects the known false closure and retains 7 noncontradicted outcomes, with source hashes in `outputs/ms58/dabia-20261110/consistency-regression.json`. This reuses existing evidence and adds no benchmark run. The renderer was exercised on explicitly labeled synthetic fixtures, producing all 18 PNG/PDF/SVG figures plus exact plot data. These fixture artifacts are under `build/ms58/plot-fixture` and are not experiment evidence.

```bash
nix develop $WORKSPACE/nix-dev --command env PYTHONPATH=analysis:scripts python -m unittest discover -s tests -p test_ms58_dabia_analysis.py
```

## Bounded candidate screen

The separate screen audit covers all 36 scheduled cells from `screen-v1`, comprising minimal Lera `f2c9e20`, integrated best `327e481`, scratch reuse `e27f288` and safe correction `a63cf65`, with C201, RC201 and C104 at size 100, 3 repetitions and a 600 s horizon. It verifies the immutable schedule seal and frozen files, the started and completed run identities, the backend, the declared correction setting and the same feasible-witness consistency gate. Its fixed reference manifest has SHA-256 `ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66`.

```bash
nix develop $WORKSPACE/nix-dev --command python analysis/ms58_dabia_screen_summary.py --screen outputs/ms58/dabia-20261110/screen-v1 --output outputs/ms58/dabia-20261110/screen-v1/analysis/summary.json
```

The summary reports all scheduled rows, pending cells, per-instance status and capped closure summaries, and matched lane comparisons against integrated best. A ratio is reported only when both observations have usable closure. Joint censoring does not establish equal speed. Native work signatures help distinguish changed search work from execution changes, but they cover recorded fields rather than a complete instruction trace. The Lera RSS measurement covers its adapter process and excludes the native child, so it cannot be used as a directly comparable whole-solver memory measurement. The script never promotes a candidate automatically.

## Final candidate artifact preparation

The separate helper `scripts/ms58_dabia_final_design.py` prepares correction-on source `9d64b7c53ddaea7a578d247ebd28db43ae0d4e56` after its revised coupled validation and native fixture gates pass. It requires the exact full patch, pinned CPLEX and HiGHS extensions, unchanged ILS binary, original reference snapshot and preserved 12-instance selection. The validation report must identify the shared consistency helper and successful per-case pooled-witness checks, so an earlier report without those checks cannot authorize preparation. The output contains runtime manifests, `artifact-gates.json` and `design.json` for 180 cells. It assigns no slots and launches no solver.

After syncing the current scripts, reference directory and selection config, the managing agent can run the following on the declared reserved compute node with the pinned checker environment. Resource allocation and slot selection remain separate decisions.

```bash
"$HOME/ms56-ablation/experiment/.venv/bin/python" "$HOME/ms58-bpca/experiment/scripts/ms58_dabia_final_design.py" --job-id "$OAR_JOB_ID" --references "$HOME/ms58-bpca/experiment/references/dabia2013-n100-v011-2026-11-09/manifest.json" --pilot-selection "$HOME/ms58-bpca/experiment/configs/pilot-selection.json" --output "$HOME/ms58-bpca/dabia-20261110/final-artifacts-correction-on"
```
