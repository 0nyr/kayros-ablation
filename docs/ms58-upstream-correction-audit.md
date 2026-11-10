# ms58 upstream correction audit

Date: 2026-11-10. Scope: diagnostic controls and source-level invariants. No replacement of the historical Lera control.

## Confirmed discrepancy

The historical upstream `BidirectionalLabeling` constructor does not initialize `correcting`, and `main_bp.cpp` does not assign it before `Run` copies it into both monodirectional solvers. Reading this indeterminate Boolean is undefined behavior. Kayros explicitly initializes it to `false`. The archived root logs record positive correction counts for upstream, including 132,195 on R201 and 93,181 on RC201, whereas the earlier trace build recorded zero correction on the observed calls. These observations show that the historical and traced upstream executions cannot be assumed to use the same correction setting.

The upstream standalone pricing program reads an explicit `correcting` option with a default of `false`. This supports using explicit `false` as the first minimally repaired comparison setting, without establishing the authors' intention for every BP experiment. `symmetric` is also omitted from the constructor, but both `main_bp.cpp` and `main_pricing.cpp` explicitly assign it before running labeling. The `BP-CUTS` experiment selects `false` for `symmetric`, so that constructor omission does not leave this particular BP configuration indeterminate.

The local source at [lera-proof-correction]($WORKSPACE/lera-proof-correction) is on development branch `proof-speed/correction-control`, commit `92ae000`, based on the frozen patched reference `f931288`. It explicitly initializes correction to `false`, then permits the strict diagnostic setting `KAYROS_DIAGNOSTIC_CORRECTING=0` or `1` in `main_bp`. The native JSON and stderr record the selected value. Production behavior is unchanged from the first diagnostic commit `68f0f9a`. Later commits change only test access and witness code.

## Correction invariants

`CorrectionStep` partially removes duration pieces from previously processed labels and updates their `min_cost`. It leaves the labels in their previous positions in the dominance structure `U` and the iterative merge structure `M`. Both structures are documented as sorted, and their consumers use early `break` statements based on the costs. Thus an increase in a stored label's minimum cost can invalidate a consumer's stopping condition.

`CorrectionStep` also erases fully dominated pointers from `U` without deleting them or retaining them in another owning container. Deferring deletion is necessary while lazy children and merge pools can refer to those labels. However, `Clean` deletes only pointers still present in `U`. The erased labels therefore escape cleanup. Kayros inherits these correction-path issues, although its default `correcting=false` leaves this path inactive.

The [focused C++ witness]($WORKSPACE/lera-proof-correction/code/tests/correction_invariant_witness.cpp) exercises the actual upstream methods. It compiled and passed on the reserved G5K compute node under job `7013762`. Its 3 observations are as follows.

| Witness | Observed result | Interpretation |
|---|---|---|
| Partial correction followed by dominance | Stored costs change from `[0, 0.5]` to `[1, 0.5]`. The stale order misses a valid dominator, while sorting finds it. | Concrete violation of the dominance structure's ordering invariant. This particular error weakens pruning. |
| Iterative merging with the same stale order | The actual iterative merge method returns 0 routes with the stale order and 1 negative reduced cost route after sorting. | Concrete local route-pool omission caused by the early cost break. |
| Full correction and ownership | The corrected-away pointer is absent from the sole container visited by `Clean`. | Concrete ownership loss, corroborated by the absence of deletion or a retired-label owner in the source. |

These synthetic label-state tests do not demonstrate a false closure on a complete VRP instance. In particular, `Run` subsequently invokes `LastArcMerge` when the route pool is below its limit and time remains. That method constructs a fresh sorted index from the current backward labels and may recover routes missed by iterative merging. A full pricing counterexample or a separate completeness argument is required before attributing false optimality to this defect. A checked incumbent alone also does not verify exhaustive pricing.

The successful compute-node log is `~/ms58-bpca/resume-20261110/correction-driver-final.log`. Earlier compile and link failures remain preserved separately. The first compile failure was a missing `std::` qualification in the witness. The link failure occurred when the witness build omitted the Boost module environment used by the main build. Neither failure was a solver outcome.

## Controlled diagnostic comparison

The [diagnostic runner](../scripts/ms58_correction_runs.py) schedules one sequential pair per instance using the same explicit-control binary, with C104 at 600 s per setting and R201/RC201 at 300 s per setting. The declared orders are `0,1`, `1,0` and `0,1`, respectively. Each pair uses its own physical core and a 16 GiB process address-space limit. All 6 scheduled outcomes, launch failures, checker results and missing native sidecars remain in the denominator. These are exploratory observations, not repetitions sufficient for a timing recommendation.

The runner freezes the source patch, binary, worker, inputs and environment setting before execution. It checks the selected flag against native output when available and stderr otherwise. Native root-labeling counters remain missing when no final sidecar survives a timeout. The upstream Python adapter's sampled memory does not measure its native child's peak memory, so this diagnostic does not infer a native memory difference from that sample.

The historical frozen control remains separate from both explicit settings. The user subsequently authorized minimal upstream repairs when required for a meaningful comparison, with clear disclosure. This permits a new, explicitly initialized reference arm after review. Historical timings must retain their original identity and qualifications.

The completed R201 and RC201 pairs are summarized in the [analysis artifact](../outputs/ms58/resume-20261110/correction-analysis.json), which verifies the stored result identities, native sidecar hashes and summaries. All 4 cells reported closure and returned the same checked incumbent cost within each instance, with no recorded resource failure. The correction-on runs reproduce both archived non-timing work signatures for R201 and RC201, including the per-call root counters and the selected whole-run work fields. The correction-off signatures differ. This confirms that the explicit setting reproduces a material feature of the historical workload, while leaving the correctness qualifications above in force.

| Observation | R201 off | R201 on | RC201 off | RC201 on |
|---|---:|---:|---:|---:|
| Reported closure time (s) | 231.312 | 203.122 | 148.892 | 127.533 |
| Root labeling time (s) | 71.790 | 65.278 | 93.782 | 78.306 |
| Root processed labels | 1,493,498 | 1,481,955 | 1,751,496 | 1,358,858 |
| Root extended labels | 17,703,848 | 16,758,727 | 25,359,803 | 18,130,717 |
| Root columns | 72,733 | 65,849 | 106,919 | 92,701 |
| Whole-run LP time (s) | 42.329 | 24.944 | 33.646 | 29.856 |

Correction-on reduces observed closure time by 12.2% on R201 and 14.3% on RC201 in these single pairs. The mechanisms differ. R201 has nearly unchanged processed-label count, but fewer columns and substantially less LP time. RC201 has 22.4% fewer processed labels and 28.5% fewer extensions. The changed trajectories prevent interpreting these timings as an execution-speed measurement for identical work. Both C104 cells reached the 600 s deadline without a checked incumbent or final native sidecar. Those outcomes remain preserved.

## Longer C104 controls

Both additional 3,600 s C104 controls reported closure with the same checked incumbent cost of 96,995.19799342303. They ran simultaneously on distinct physical cores 10 and 11 of the same reserved node, with 16 GiB per process. This is 1 exploratory observation per setting, not a randomized sequential timing comparison. The [independent artifact audit](../outputs/ms58/resume-20261110/reference-controls-audit.json) verifies identities, event hashes, manifests, frozen patch and script hashes, checker identity, input metadata, parameters, native sidecar hashes, actual correction settings and the stored closure evidence. Remote runtime bytes were checked by the launch harness and remain identified by their stored manifest hashes.

| C104 observation | Correction off | Correction on |
|---|---:|---:|
| Reported closure time (s) | 1977.638 | 1737.791 |
| Root labeling time (s) | 1964.740 | 1724.875 |
| Time inside correction (s) | 0.000 | 296.230 |
| Root processed labels | 12,051,673 | 8,865,282 |
| Root extended labels | 197,696,894 | 141,063,055 |
| Root enumerated labels | 487,204,262 | 366,797,967 |
| Fully corrected labels | 0 | 355,920 |
| Root columns | 59,770 | 58,513 |
| Whole-run LP time (s) | 3.362 | 3.819 |

Correction-on has a 12.1% lower observed closure time, with 26.4% fewer processed labels and 28.6% fewer extensions. Almost all the time difference occurs inside labeling. LP time remains below 4 s in both cells. Correction itself costs 296.230 s, so the reduction in other labeling work must be considered alongside this overhead.

The correction-on root and whole-run work signatures match all 3 historical focused C104 Lera trials exactly. Those historical native solver times were 1910.362 s, 1951.961 s and 1891.690 s. The new correction-on timing therefore reflects a different execution context for the same recorded search workload. It must not be presented as a search improvement over those historical runs.

Correction-off still reports closure in approximately 33 min here, whereas archived cold Kayros CPLEX remained unfinished after 2 h. Consequently, this setting discrepancy does not by itself explain the full historical Kayros gap. The tests identify a real workload difference and an improvement hypothesis, while preserving the unresolved pricing-correctness questions and the need to investigate the other trajectory differences.

## Minimal repaired reference candidate

Development branch `proof-speed/minimal-reference` at `f2c9e20` in [lera-proof-minimal-reference]($WORKSPACE/lera-proof-minimal-reference) contains exactly 1 added line relative to `f931288`, assigning `correcting = false` in the bidirectional constructor. It preserves the original end-of-file bytes and adds no environment selector, logging, witness access or build changes. Its patch is `build/ms58/upstream-minimal-reference.patch`. The resulting binary has SHA-256 `59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783`.

The [minimal-reference smoke](../outputs/ms58/resume-20261110/minimal-reference/complete.json) completed both scheduled cells under job `7013766`, with no inherited Kayros settings, 300 s horizons and 16 GiB limits. C101 reported closure at 1.646 s and RC201 at 140.822 s, each with a checked incumbent and zero recorded corrections. RC201's root and whole-run work signatures match the explicit correction-off diagnostic exactly, with the same checked incumbent cost. The independent artifact audit verified both smoke results as well as the longer C104 controls. This validates the intended correction-off behavior on these 2 cases. It does not independently prove pricing completeness or select a reference for headline comparisons.

## Improvement implications

Enabling the inherited correction path is not a justified Kayros performance change at this stage. A candidate should first retain ownership of all processed labels until dependent pointers are no longer used, and restore or avoid the invalid ordering assumptions in `U` and `M`. That candidate should then be tested against independently checked small instances, pricing witnesses and the broader stepwise cases supported by Kayros, followed by paired full-engine comparisons. The observed upstream timing benefit does not establish a safe or general Kayros improvement or explain the full C104 gap.

Explicitly disabling upstream correction provides a controlled way to test the discrepancy while preserving Kayros's existing disabled default. The observed correction effect should be reported as an algorithm-setting effect under the repaired controls. It should not be credited to a Kayros execution optimization.
