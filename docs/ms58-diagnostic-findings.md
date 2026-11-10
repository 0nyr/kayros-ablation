# ms58 BPCA diagnostic findings

Date: 2026-11-10. Status: diagnostic pass complete, proof-speed objective still open. The main campaign remains held.

We investigate correctness, search workload and execution cost on both sides of the comparison. The historical Lera artifact is preserved, but an uninitialized algorithm setting prevents treating it as a stable algorithmic reference. A newly reproduced Kayros counterexample also prevents interpreting all reported closures as sound proofs. The author authorized minimal, explicitly documented Lera repairs for a meaningful comparison.

This pass identifies and repairs a specific Kayros pricing omission, validates a one-line initialized Lera reference, rejects normalization alone as a supported solver optimization, and demonstrates allocation-sensitive branch ranking in their shared code. The larger C104 proof-time discrepancy remains unresolved. The [development branch inventory](../configs/ms58-development-branches.json) and linked reports preserve exact changes, tested hypotheses and limitations.

## Compared implementations

| Implementation | Identity | Purpose |
|---|---|---|
| Corrected ms56 Kayros reference | `proof-speed/reference`, `0787925` | Reproduces `50eebc5` plus frozen patches `0001` through `0004`. Its rebuilt CPLEX extension is byte-identical to the archived extension. |
| In-place normalizer | `proof-speed/normalize-in-place`, production change `20625ce`, fixture `eed117b` | Avoid copies of well-formed label duration functions without changing their arithmetic. |
| Kayros first-task replay | `proof-speed/diagnostics`, `64a3e28` | Override first-call duals, capture the first pool and intentionally stop before column admission. |
| Upstream first-task replay | `proof-speed/upstream-diagnostics`, `0dbf574` | Same replay contract, with correction explicitly disabled. |
| Upstream correction control | `proof-speed/correction-control`, `92ae000` | Explicit correction settings `0` and `1`, plus focused invariant witnesses. |
| Minimally initialized upstream reference | `proof-speed/minimal-reference`, `f2c9e20` | Adds only `correcting = false;` to the frozen upstream source. |
| Kayros point-addition repair | `proof-speed/point-addition`, built `250105c`, comment-only follow-up `ea6097e` | Preserves an attainable CHOICE tail lost by legacy addition against a scalar point. |
| Column-ordered numerical reductions | `proof-speed/canonical-reductions`, `37e4801` | Orders valuation sums at branching and cut separation without changing variable identity. |
| Ordered reductions with normalization | `proof-speed/canonical-normalize`, `0bec4d4` | Tests the normalizer under the same explicit reduction convention. |

The derivative upstream manifests retain historical source-file hashes as base-source provenance and add the diagnostic patch stack. Those historical tree hashes are not final derivative tree hashes. Runtime binary hashes and exact patch digests identify every derivative. The original control binary remains unchanged.

## A confirmed Kayros correctness failure

On `TDVRPTW/Vu2020/n=59/Vu-A2-pB-d98-w100`, both the corrected reference and the normalization candidate report an optimum of `1847.239598063632` from a cold start with cuts enabled. The pinned v0.11 checker independently accepts the stored witness at `1846.8070188618972`. Input, ATF and checker identities match, so this is not a stale test constant or input drift.

| Frozen-reference control | Reported value | Observation |
|---|---:|---|
| Cold, cut limit 100 | 1847.239598063632 | Contradicts the cheaper checked witness. |
| Cold, cut limit 0 | 1846.8070188618972 | Reaches the witness value. |
| Witness warm start, cut limit 100 | 1846.8070188618972 | Reaches the witness value. |
| Witness warm start, cut limit 0 | 1846.8070188618972 | Reaches the witness value. |

The failing run closes at the root after 2 cuts and 3 finished Exact pricing calls, with no checker-infeasible priced columns recorded. Independent repricing identifies the missing route `[0,31,6,55,33,60]`, with reduced cost `-0.4325792017348249` under the final Exact context. It satisfies the active cuts. The prefix duration is inflated from the attainable `60.027219` to `69.425` when a scalar-point travel function is added to stacked departure choices, after which the label is dominated. Thus changed duals from cuts expose a PWL addition defect. Disabling cuts globally is not an accepted fix.

Production change `250105c` preserves the lost CHOICE tail only for legacy addition against an exact scalar-point operand. It leaves step-exact control flow and JUMP pairing unchanged. The captured final pricing task reproduces the reference's exact work counters and 0 returned routes. Applying the repair to the same task returns the missing checker-valid route. All 4 repaired cold/warm and cuts-on/off controls reach `1846.8070188618972`, with valid returned witnesses and no contradictory bound.

The native regression fails on the old library and passes on the repaired library. The existing regression bundle gives 60 passes and 3 opt-in skips. A matched randomized check passes on 5 actual cases in each variant, with 4 Rifki cases and 1 Dabia case. The requested sixth case selected an absent `TDVRPTW/Solomon1987/n=25` directory and remains declared missing coverage. The ILS binary and tested fixed-iteration outputs are unchanged. The repair is a correctness candidate, not an established speed improvement or a general proof of pricing completeness.

Its completed 32-cell performance panel yields 14 reported closures with checked witnesses per arm, with 4 deadlines across both arms. All cells pass the artifact audit. C201 mean closure regresses from `126.687` to `153.104` s, an increase of 20.9%, with 23 reference nodes versus 25–27 repaired nodes. The root LP valuations and recorded root counters match, and root labeling becomes slightly faster. Most of the additional time is in subsequent branching. The point repair changes arithmetic semantics, so allocation-sensitive ordering remains only one possible explanation for this later divergence. RC201 has matching recorded work and mixed paired differences. R201 closes 2 of 3 in each arm by 300 s, and neither C104 arm closes by 600 s. The [point-repair performance report](ms58-point-performance.md) preserves the complete results. These observations do not establish nonregression or justify production integration.

The exact checked witness, input hashes and checker identity are in [the witness audit](../outputs/ms58/vu-witness-audit/witness-validation.json). The [mechanism and repair report](ms58-vu-point-pricing-audit.md) links the native fixture, captured-task replay and all validation outcomes. This failure predates the normalizer candidate.

## Upstream correction is an uncontrolled setting in the historical binary

The original upstream constructor and BP entrypoint leave `correcting` uninitialized. Archived fixed-control runs have positive correction counts, whereas the traced upstream binary has zero counts on the observed calls. Instrumentation therefore changed an observable algorithm setting. Its diagnostic timings cannot stand in for the historical control.

Actual-method witnesses on G5K establish that correction can leave the dominance and iterative merge structures in stale cost order. A synthetic iterative merge then misses a negative reduced cost route that is found after sorting. Fully corrected labels also escape the owning structure used by cleanup. However, final arc merging may recover an iterative omission, so these witnesses alone do not demonstrate a full-instance false closure.

The [correction audit](ms58-upstream-correction-audit.md) records the source evidence, 3 passing witnesses and the limits of their interpretation. In one paired observation per instance, explicit correction-on reduces reported closure time from 231.312 to 203.122 s on R201 and from 148.892 to 127.533 s on RC201. Its root and whole-run work signatures exactly match the archived historical control on both instances. This confirms an algorithm-setting discrepancy and changed search work, not a throughput improvement. Both settings return the same checked incumbent cost per instance, which does not establish exhaustive pricing correctness.

The minimally repaired candidate `f2c9e20` adds only `correcting = false;` to the constructor. Its C101 and RC201 smoke runs complete with zero correction counts. RC201 reproduces the explicit correction-off work signatures and checked cost exactly. This provides a clearly identified comparison candidate, supported by the upstream standalone pricing program's default, without asserting the authors' intended BP setting or selecting a weaker reference merely for favorable timing. Both explicit settings remain preserved.

The longer C104 controls both close with checked cost `96995.19799342303`. Correction-off takes `1977.638` s and correction-on takes `1737.791` s. The observed 12.1% decrease accompanies 26.4% fewer processed labels and 28.6% fewer extensions, while correction itself consumes `296.230` s. Correction-on matches the recorded work signatures of all 3 archived C104 controls. These are single simultaneous observations on distinct physical cores, so they do not establish a precise timing ratio. The approximately 33-minute correction-off closure also leaves the much larger historical Kayros gap beyond 2 hours unexplained. The earlier 600 s timeouts remain archived.

## Identical-dual replay excludes one first-pool hypothesis

On C104 and R201, swapping the captured upstream and Kayros dual vectors within each engine leaves its full ordered pool of 3,000 routes unchanged. Replaying each engine with its own captured duals also reproduces its original first pool exactly. Thus the observed dual differences of approximately `1.8e-12` do not explain the first-pool discrepancy in these cases.

| Instance | Common routes with identical duals | Routes unique to each pool | Dual sensitivity test |
|---|---:|---:|---|
| C104 | 2,287 | 713 | Both captured dual sources tested. |
| R201 | 2,472 | 528 | Both captured dual sources tested. |
| RC201 | 2,188 | 812 | Upstream duals tested in both engines. The Kayros-source fixture was unavailable after cancellation. |

The preprocessed graph, time windows, demands and capacity agree. Departure and pretravel function piece counts differ. On C104 and R201, removing zero-width pieces for inspection only aligns the remaining segments within `1e-8`. Kayros retains additional inverse-function choice representations. This is evidence of representation differences, not evidence that those pieces should be deleted. Several related Kayros arithmetic repairs are required for its wider model support.

The endpoint trace does not expose every tagged vertical image span or the internal reverse-instance tables. Therefore identical duals and aligned forward endpoints do not establish identical complete pricing states. The [replay report](ms58-first-pool-replay.md) records exact provenance and this qualification. The tested conclusion concerns these first calls, not all later dual sensitivity.

## Normalization candidate and preservation of ILS

The candidate removes return-by-value copies from the duration normalizer and preserves its boundary scan and repair arithmetic. All 2,004 differential cases pass bitwise comparison. In a 48-measurement component benchmark, median reference-to-candidate time ratios range from 1.72 to 3.41 on the unchanged-function path and from 1.02 to 1.15 on the repair path. These component ratios are not solver speedups.

The 32-cell sequential AB/BA comparison yields 14 reference and 13 candidate reported closures with checked witnesses out of 16 scheduled cells per arm. All remaining outcomes are deadlines and all cells pass the artifact audit. The mixed 300/600 s horizons are retained per instance, so no pooled closure-time claim is made.

| Instance | Reference mean capped closure time (s) | Normalization candidate (s) | Qualification |
|---|---:|---:|---|
| C101 | 2.272 | 2.253 | 3 paired repetitions, same recorded root work. |
| R101 | 1.767 | 1.761 | 3 paired repetitions, same recorded root work. |
| C201 | 136.466 | 172.907 | All reference trees close 23 nodes, all candidate trees close 27. |
| RC201 | 196.968 | 199.688 | 3 paired repetitions, same recorded work. |
| R201 | 294.696 | 299.031 | Reference closes 2 of 3, candidate 1 of 3 by 300 s. |
| C104 | 600.000 | 600.000 | Neither arm closes in the single 600 s pair. |

The candidate therefore shows no clear solver benefit and a repeatable C201 regression. C201 root LP valuations and recorded root work agree exactly, but later branching differs. Source inspection identifies pointer-hashed valuation iteration in branching and cut sums, inherited from upstream, as an allocation-sensitive mechanism. At a fixed captured LP state, changing only reduction order shifts 2 arc fractions by 1 ULP and replaces 4 of the 10 strong-branching candidates. The selected canonical arc is absent from the raw-order pool. This directly demonstrates a mechanism that changes the available branch decision, but does not locate the original normalizer's first tree divergence.

The separate column-index-ordered experiment completes 3 C201 pairs, all with matching recorded root and whole-run work, 23 closed nodes and 79,735 final columns. Mean closure time is `125.170` s for ordered reductions and `123.526` s with normalization added. The mixed paired differences and small 1.3% mean change establish no robust execution gain. This cohort runs on a different node from the original comparison, so its absolute times do not measure a speedup from canonicalization. Ordering intentionally changes floating-point accumulation and remains a separately identified reproducibility proposal.

The candidate may retain spare vector capacity that the original copies compacted. C104 sampled peak RSS rises from 2,411,828 to 2,599,436 KiB, an increase of 183.2 MiB or 7.8%. This is one censored pair, and unfinished pricing work is unavailable, so memory growth cannot yet be attributed solely to retained capacity. The [normalization analysis](ms58-normalization-analysis.md) preserves per-pair evidence and all unfavorable outcomes.

The native ILS `_core` binary is byte-identical across reference and candidate. Fixed-iteration ILS outputs agree before and after BPC on the tested continuous and stepwise instances, and the focused warm-start witnesses pass. The remaining 15 soundness gates pass, while the confirmed Vu gate fails in both builds. Random fuzz is opt-in and was not enabled by this validation bundle.

## Decision and next checks

The point-addition change is a concrete correctness repair with a reproduced mechanism and captured-task counterexample, but its measured C201 regression prevents a nonregression recommendation. The normalizer alone is not recommended for promotion either. Ordered reductions are a concrete reproducibility proposal that requires broader validation and the same declared convention on any repaired upstream comparator. The next candidate comparison should apply the point repair with the same ordered reductions in both arms, while retaining all current unfavorable outcomes. That combination has not been built or measured in this pass.

The next proof-speed investigation should profile identical captured pricing tasks with full tagged forward and reverse function state, then separate label generation, dominance, merging and function arithmetic costs. First-pool dual perturbations and upstream correction alone do not explain the large C104 gap. Preserve the model-generalization repairs and the historical control with their exact identities. Any later upstream correction-order or ownership repair must remain a separate declared variant and be evaluated for pricing completeness before its speed is interpreted.

The ILS implementation remains unchanged, but BPC timing and memory can affect full Kayros through the handoff and shared resources. A full Kayros recommendation therefore still requires controlled end-to-end comparisons. This diagnostic pass does not establish proof-speed parity or authorize the held main campaign.

## Preserved evidence

The local raw archive is [outputs/ms58/remote-final](../outputs/ms58/remote-final). Its 2,446 files, totaling 91,604,363 bytes, match the completed remote output directories by relative path, file type, size and SHA-256. The [verification record](../outputs/ms58/resource-audit/archive-verification.json) and full local and remote inventories are preserved beside the resource audit. Derived analyses remain in `outputs/ms58/resume-20261110`, with the first canceled pass also retained in `outputs/ms58/nov09-remote`.

Exact patches are committed with the [branch inventory](../configs/ms58-development-branches.json). Built wheels, native executables, source trees and environments remain in the `nancy.g5k:~/ms58-bpca` build cache for reproducibility. They are not part of the local raw-output inventory. Only verified workflow output directories are removed remotely. No production integration or new main-campaign launch has occurred.
