# Zero-width pieces and label-query audit

The apparent duplicate inverse points are overwhelmingly genuine departure-choice intervals. Deleting them would discard model semantics. A separate proposal to collapse label-duration choices to their minimum also lacks a sufficient implementation invariant, so neither simplification was implemented. The existing scratch-storage candidate remains independent of this audit.

This source-only investigation uses already captured C104, R201 and RC201 n=100 traces and integrated Kayros source `327e481`. It launches no solver, modifies no production source and changes no numerical tolerance. The [audit inventory](../outputs/ms58/zero-width-audit.json) preserves input hashes, source hashes, classification counts and examples.

## Preprocessing and inverse pieces

| Instance | Nonzero departure-choice spans inferred from arrival plateaus | Scalar inverse points | Observed zero-width inverse pieces |
|---|---:|---:|---:|
| C104 | 1,651 | 1 | 1,652 |
| R201 | 1,413 | 0 | 1,413 |
| RC201 | 1,600 | 0 | 1,600 |

The inference follows the exact breakpoint decisions in `PWLFunction::Inverse`. Each equal arrival-time pair with distinct departure times creates a CHOICE vertical. Equal arrival and departure endpoints create a scalar point. Together these cases account for every observed zero-width departure piece in the 3 captures. The endpoint JSON alone conceals the vertical's image span, so it is insufficient for deciding redundancy.

For example, C104 arc `0 -> 4` has arrival time 8,170 for every departure in `[0, 7161.400217199565]`. Its inverse therefore contains that full departure-choice interval at arrival time 8,170. The trace serializer prints the attained latest-departure representative twice, making it look like a scalar point already present on the next segment. Removing that apparent duplicate would lose the other departures.

The only inferred scalar point is C104 arc `95 -> 55`, whose entire arrival function is the point `(900, 2480)` and whose entire departure function is `(2480, 900)`. It has no adjacent nonzero segment to cover it. Removing it would empty the arc function. Hence this audit found no removable scalar redundancy in the inspected inverse tables.

## Conditional label-minimum argument and its implementation limit

For a mathematically exact dynamic program, retaining only the minimum duration at a fixed complete state can be valid. The state must include the current time, path-dependent resources and every quantity affecting feasible continuations. Every retained duration must be attainable. If a common continuation adds duration `c`, then `min(d + c) = min(d) + c`. This argument applies to duration costs, not to an inverse arc's departure-time choices, whose different departure times can encounter different prefix-duration values.

Kayros's `Label` comment describes `duration(t)` as a minimum duration, but the actual implementation retains several distinct semantic values. `MinValueAt` and the candidate side of domination use a CHOICE span's minimum. The dominator side uses its attained representative. `completion_base` uses the maximum image endpoint among all pieces touching the label's final time. These consumers cannot share one minimum-valued replacement.

A simple code-level example shows the difference. For a CHOICE span `[2, 7]` with representative 7, the candidate query returns 2 while the dominator query and completion base can return 7. Replacing the span by its minimum changes a comparison against candidate value 5 from failing to succeeding. It also lowers the completion beyond the original domain. This demonstrates that the edit is not neutral for the current predicates, without claiming that the abstract minimum-duration theorem is false.

The source contains stronger historical evidence. `tests/test_lera_stepwise_soundness.py` records the dominator-side choice-min experiment over-certifying a C102 subset at approximately 7,667.4 against the checked value `7525.999728501456`, and keeps a regression guard for it. That historical off-size subset is source context only and was not rerun here. The current representation, reverse normalization and partial-domination operations do not establish that every image minimum can act as a feasible dominating continuation at every later time. A label lower-envelope optimization therefore needs a proof of that invariant and regression evidence before implementation.

## Repeated-query opportunities

The representative query is a single `LinearFunction::Value` evaluation and does not scan the piece collection. Caching it is not presently motivated. `completion_base` scans backward through the pieces touching the final abscissa for every eligible dominance comparison. The same processed label can serve as a dominator many times, so this query may repeat.

A per-label completion-base cache would preserve the exact maximum if its validity were tied to duration mutation. With `correcting=false`, a processed label's duration remains stable within the pricing task. With correction enabled, that assumption fails. Such a cache would also add persistent memory to labels, which matters under the 16 GiB limit. There is no useful per-comparison cache because the helper is called once in each comparison. A local cache inside `CorrectionStep` could reuse the fixed new dominator across its comparisons, but current Kayros runs with correction disabled, so that scope would not help the present configuration.

Endpoint `MinValueAt` queries in the waiting branch of `Merge` have a simpler safe scope. `IterativeMerge` compares one fixed label `l` against multiple labels, and `LastArcMerge` does likewise for each popped frontier. A lazy local cache for `l`'s clamped endpoint minimum would live only during that loop, while `Merge` changes neither duration. It would retain CHOICE/JUMP pieces and require no global PWL mutation invalidation. The other label varies across comparisons. No implementation was added because the size of this opportunity is still unmeasured.

## Profile before caching

The existing 60 s C104 trace contains 36 completed pricing calls. Their recorded extension time totals 32.034 s, domination time 11.327 s and merge time 1.599 s, against 53.714 s of measured pricing-call wall time. These counters have millisecond granularity and omit the interrupted next call. They suggest prioritizing extension and dominance work over merge endpoint caching for this prefix, but they do not characterize the long later calls responsible for the proof-speed gap.

The next diagnostic should count calls and pieces examined separately for `PWLFunction::Value`, `MinValueAt` and `completion_base`, plus the number of waiting versus overlap merges. Counts for addition and composition piece pairs would help explain the larger extension component. Emit snapshots at pricing entry and completion, with bounded periodic snapshots for long interrupted calls. Keep representative, minimum and completion-maximum counters separate. Avoid a per-query timer because its cost can dominate a small query.

Run that instrumentation only in identified diagnostic builds and require a completed captured-task replay to reproduce the uninstrumented baseline's route pool and workload. Timings from instrumented builds must not become headline speed measurements. Any new comparative solver series must use Dabia2013 n=25, n=50 or n=100 with CPLEX and include minimally corrected Lera `f2c9e20`, together with the direct Kayros control. Keep the full-function semantics intact while testing storage or query reuse.
