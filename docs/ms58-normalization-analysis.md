# ms58 normalization and allocation-order analysis

Date: 2026-11-10. Scope: exploratory comparison, with no candidate promotion. The confirmed Vu correctness failure remains a separate blocking gate for production integration.

## Completed original comparison

All 32 scheduled cells are archived and audited, comprising 16 reference runs and 16 normalization runs. There are 27 completed runs and 5 outer-deadline outcomes. The reference reports 14 closures and the candidate reports 13. No resource failure or invalid checked incumbent is recorded in this comparison. The horizons and repetition counts differ for C104, so the table preserves the per-instance design instead of pooling unlike budgets.

| Instance | Horizon (s) | Pairs | Reference closures | Candidate closures | Reference mean capped closure (s) | Candidate mean capped closure (s) |
|---|---:|---:|---:|---:|---:|---:|
| C101 | 300 | 3 | 3 | 3 | 2.272 | 2.253 |
| R101 | 300 | 3 | 3 | 3 | 1.767 | 1.761 |
| C201 | 300 | 3 | 3 | 3 | 136.466 | 172.907 |
| R201 | 300 | 3 | 2 | 1 | 294.696 | 299.031 |
| RC201 | 300 | 3 | 3 | 3 | 196.968 | 199.688 |
| C104 | 600 | 1 | 0 | 0 | 600.000 | 600.000 |

These results show no clear end-to-end gain from normalization alone. The C101 and R101 differences are less than 0.02 s. RC201's mean closure time increases by about 1.4%, with matching recorded root and whole-work counters in all 3 pairs. Its mean root labeling time also increases from 124.677 s to 126.864 s. The small RC201 differences may include timing variation, while the larger C201 regression coincides with a repeatable tree change documented below. On R201, deadlines remain censored at 300 s, and the candidate closes 1 fewer run. Neither C104 run closes at 600 s.

On C104, sampled peak resident memory increases from 2,411,828 KiB to 2,599,436 KiB, a difference of 187,608 KiB (183.2 MiB, approximately 7.8%). Both runs last report root iteration 35 with 54,560 columns, but their unfinished pricing work is unavailable. Consequently this is an observed memory increase at a common horizon, not a controlled attribution to retained vector capacity. Sampling may miss short-lived peaks. Neither run reaches the configured 16 GiB process limit.

The [complete audited analysis](../outputs/ms58/resume-20261110/normalization-analysis.json) retains every scheduled outcome and the qualifications on closure, memory and matching work. Its unfavorable results remain part of the candidate decision.

## Completed C201 comparison

The in-place normalizer is arithmetically identical in the tested differential fixtures and avoids copies of well-formed duration functions. The 3 sequential AB/BA C201 pairs nevertheless show a repeatable end-to-end regression. Every reference run closes 23 nodes with 91,742 final columns, while every candidate run closes 27 nodes with 96,357 final columns. The 6 root LP valuations are bit-identical, and every paired root-work signature matches.

| Repetition | Order | Reference closure (s) | Candidate closure (s) | Reference root labeling (s) | Candidate root labeling (s) |
|---|---|---:|---:|---:|---:|
| 1 | Reference, candidate | 128.023 | 165.222 | 1.424 | 1.329 |
| 2 | Candidate, reference | 141.646 | 175.717 | 1.431 | 1.392 |
| 3 | Reference, candidate | 139.730 | 177.782 | 1.416 | 1.397 |

Mean reported closure time rises from 136.466 s to 172.907 s, a 26.7% increase. Mean root labeling time falls from 1.424 s to 1.373 s, approximately 3.6%, while mean total root time falls from 5.432 s to 5.178 s. Mean branching time increases from 73.533 s to 100.643 s and accounts for 27.110 s of the 36.441 s overall increase. Thus this outcome combines a modest improvement on matching recorded root work with a more expensive subsequent search.

The mean sampled peak resident-memory difference is only 132 KiB. This observation supplies no evidence of a material C201 memory regression from retained vector capacity, but it does not establish a general memory bound. Full-run peak memory also reflects the different trees and column counts. The isolated component benchmark constructs copied inputs with compact capacities and does not exercise the candidate's retained-capacity risk adequately.

The [audited C201 analysis](../outputs/ms58/resume-20261110/c201-normalization-analysis.json) preserves all 6 outcomes, exact runtime identities, resource observations and work signatures. Reference source `0787925` is compared with normalizer production change `20625ce` and fixture `eed117b`. These are operational reported-closure measurements with checked incumbents, not independent proof certificates.

## Allocation-dependent numerical reductions

The current `Valuation` stores variables in `std::unordered_map<Variable, double>`. `Variable` identity and its hash use the address of the index object. `BranchNode` iterates this map when summing LP route values into arc fractions, then ranks arcs using raw floating comparisons of their distance from 0.5. `SeparateCuts` likewise sums values in the map's iteration order. Avoiding temporary allocations can change this traversal order without changing any route coefficient or root LP value. This pattern is inherited from upstream Lera and can affect either implementation. The container behavior is legal, unlike the separately discovered uninitialized-flag defect.

This is a concrete mechanism by which an allocation optimization can alter subsequent search through rounding and ties. The first-branch experiment below directly demonstrates the effect of reduction order on the candidate pool at a fixed LP state. It does not establish the first divergence or the complete cause of the original C201 regression. The normalization change can also affect other allocation-sensitive code, and matching root counters alone does not establish complete internal-state equality. No memory address is itself an algorithmic input that we intend to tune.

Changing the variable hash to the current column index would be incorrect because column indices can change while variables remain keys in existing containers. The diagnostic instead sorts a temporary view by current column index only at the 2 numerical reductions. It preserves variable identity and container semantics.

## Controlled diagnostic variants

Development branch `proof-speed/canonical-reductions` at `37e4801`, in `kayros-proof-canonical`, adds column-index ordering at branching and cut reductions. Development branch `proof-speed/canonical-normalize` at `0bec4d4`, in `kayros-proof-canonical-normalize`, adds the original normalizer optimization on top. Their comparison isolates normalization under the same explicit reduction order, subject to measured search-work agreement. The ordering change intentionally changes floating rounding relative to the historical algorithm and is therefore a separately identified reproducibility experiment.

`KAYROS_FIRST_BRANCH_TRACE` optionally records the first raw valuation traversal, candidate arcs from canonical and legacy accumulation orders, and the selected branch. It writes to an exclusive new file and does no file IO when unset. Instrumented observations remain separate from timing runs. The native property fixture applies 128 insertion permutations to actual valuations carrying captured C201 fractional values and checks invariant canonical reductions while retaining evidence that raw reductions can differ. This property fixture passed on the reserved G5K compute node.

The [targeted runner](../scripts/ms58_canonical_pairs.py) requires an explicit validation record, disallows inherited Kayros diagnostic settings and freezes 3 sequential C201 pairs on 1 allowed core. The test must establish stable work from its outputs, rather than assume that 2 canonical reductions remove every source of search variability.

## Completed canonical comparison

All 6 cells are complete and pass the archived-result audit. They run sequentially on core 5 of `grvingt-34` with tracing disabled. All 6 close 23 nodes with 79,735 final columns and share the same recorded root-work signature and whole-work signature. The latter hashes the reported aggregate node, column, constraint, cut and bound fields, so its equality does not establish an identical complete node trace or every pricing task. The [final canonical analysis](../outputs/ms58/resume-20261110/canonical-analysis.json) supersedes the retained 3-cell checkpoint.

| Repetition | Order | Canonical reference closure (s) | Canonical normalization closure (s) | Candidate minus reference (s) |
|---|---|---:|---:|---:|
| 1 | Reference, candidate | 121.241 | 121.882 | +0.640 |
| 2 | Candidate, reference | 126.164 | 121.702 | -4.462 |
| 3 | Reference, candidate | 128.105 | 126.994 | -1.111 |
| Mean | | 125.170 | 123.526 | -1.644 |

The candidate's mean closure time is 1.3% lower, with mixed paired differences. These 3 pairs establish no robust execution gain. Mean root labeling time increases from 1.329 s to 1.359 s, mean total pricing time changes from 12.436 s to 12.418 s, and mean LP time falls from 27.879 s to 26.874 s. The small overall difference therefore cannot be attributed to faster normalization. Mean sampled peak resident memory differs by -85 KiB, which supplies no evidence of a material memory change in this diagnostic. The isolated component improvement does not establish an end-to-end improvement because the proportion of solver time saved by normalization remains unmeasured.

The original normalization-only and canonical comparisons use different nodes. Their absolute times must not be compared as a measured canonicalization speedup. Their distinct final-column counts also show that canonicalization changes the historical search. Our reproducibility conclusion is restricted to the recorded agreement among these 6 C201 runs.

## First-branch reduction evidence

The 2 separate 30 s probes on core 10 complete the first-branch trace before their outer deadlines. Both stored results and all archived patch hashes pass audit. The traces compare the actual ordered accumulation with a recomputation in the captured map traversal order at the same root LP state. They provide no timing or proof claim. Their raw traversal orders differ between the canonical reference and canonical normalization, but all 23 indexed LP values agree exactly. The canonical candidate lists, the legacy candidate lists and the selected canonical branch each agree across these 2 variants.

Within either trace, changing only reduction order changes the near-0.95 fractions below by 1 unit in the last place (ULP), equal to approximately 1.11e-16. The raw floating ranking and its ties then select a different 10-arc pool for strong branching.

| Arc | Column-ordered fraction | Raw-order fraction | Difference |
|---|---:|---:|---:|
| 86 → 83 | 0.94999999999999973 | 0.94999999999999984 | -1 ULP |
| 84 → 86 | 0.94999999999999984 | 0.94999999999999996 | -1 ULP |

Only 6 of the 10 candidates are common. The ordered pool adds arcs 99 → 100, 98 → 7, 97 → 92 and 5 → 75, while the raw-order pool contains 100 → 97, 91 → 88, 89 → 91 and 88 → 84 instead. The actual selected canonical arc is 99 → 100, with estimate 96035.935334110589. That arc is absent from the raw-order pool. This directly establishes that the reduction order can affect the available branch decision at the same LP state, without changing the LP solution itself. The legacy strong-branch estimates and selected legacy arc were not executed.

The scope of the causal evidence matters. Both current raw-order pools happen to agree despite different raw traversal orders, so these traces do not prove that normalization changes the very first branch. The original normalization-only binaries did not record their first branch, and the canonical and trace code can change later allocation state. We therefore cannot locate the original 23-node versus 27-node divergence from this evidence. The supported conclusion is a demonstrated rounding-to-candidate-pool mechanism, together with restored agreement of recorded work in this canonical comparison. The [machine-readable findings](../outputs/ms58/resume-20261110/canonical-branch-findings.json) preserve the complete ranked lists, binary floating representations, trace hashes, audits and limitations.

## Candidate decision

We retain the original 32 outcomes, including the C201 regression and C104 memory increase, and do not recommend promoting normalization on these results. Column-ordered reductions are a concrete reproducibility proposal for the shared Branch & Price & Cut code. They merit evaluation alongside the correctness repairs and with the same declared convention on any minimally repaired Lera comparator. A deterministic secondary arc key for exactly equal ranking scores is a separate possible refinement, which has not been implemented or measured here. Neither proposal should change variable hashing or remove support for broader Kayros instances.

The canonical normalizer remains a candidate. It changes no ILS code, but these BPC-only diagnostics do not establish the absence of an effect on full Kayros through timing, resources or the heuristic handoff. Any later integration still requires correctness validation and controlled full-solver comparisons. No additional production change or experiment follows from this analysis.
