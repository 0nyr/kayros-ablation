# ms58 Vu pricing counterexample and scalar-point repair

Date: 2026-11-10. Scope: correctness diagnosis. The performance candidate and the historical controls remain separate.

## Independently checked contradiction

The corrected Kayros reference `0787925` and normalization candidate report `Optimum` at `1847.239598063632` on `TDVRPTW/Vu2020/n=59/Vu-A2-pB-d98-w100` with cold initialization and `cut_limit=100`. The pinned MAMUT checker v0.11 accepts the stored witness at `1846.8070188618972` on identical input and ATF bytes. Thus the reported closure contradicts an independently checked feasible solution. This conclusion does not require assuming that the stored witness is optimal.

The [witness validation](../outputs/ms58/vu-witness-audit/witness-validation.json) records checker identity, routes and hashes. The [controlled runs](../outputs/ms58/vu-witness-audit/controls/) show that cold `cut_limit=0`, warm `cut_limit=100` and warm `cut_limit=0` each return `1846.8070188618972`. This establishes warm-start dependence and cut-path sensitivity. It does not establish that the subset-row cuts themselves are invalid.

## Missing improving column

The final Exact pricing task, call 15, returns `Finished` with 0 routes. Its captured duals leave the checker-valid route `[0,31,6,55,33,60]` at reduced cost `-0.4325792017348249`. The route has checker duration `103.4872189349112`, customer-dual sum `109.09142638674871` and cut-dual contribution `-5.171628250102685`. The active cuts are `{6,31,33}` and `{8,16,42}`, with duals `-5.171628250102685` and `-2.0551182657227187`. There are no forbidden arcs. The entire feasible witness satisfies both cuts at activity 1.

The [independent reduced-cost calculation](../outputs/ms58/vu-witness-audit/trace-witness-reduced-costs.json) retains every witness route and captured context. This is a concrete pricing-incompleteness witness, rather than a disagreement inferred only from final objective values.

An exhaustive check over 125 feasible resource states of one 3-customer cut found no violation of the implemented dominance allowance. A second check over 27 disjoint prefix/suffix subset pairs found no violation of the cut contribution used in the merge lower bound. These checks support the local cut algebra, without validating the complete pricing algorithm.

## First incorrect label value

The [checker timeline](../outputs/ms58/vu-witness-audit/missing-route-timeline.json) gives optimal depot departure `728.9727810650888`. The route reaches customer 6 at its earliest service time 764, leaves after service at 770, and reaches customer 55 at its latest service time 772. The ready time at customer 55 is 789. Hence the connection from 6 to 55 is feasible only at a point in this prefix's ready-time domain.

In the [forward label trace](../outputs/ms58/resume-20261110/vu-path-forward.log), the final Exact prefix `[0,31,6]` survives dominance with minimum duration `41.0272189349112` at ready time 770. Its duration function retains several stacked CHOICE pieces at that abscissa. The extension to customer 55 adds a travel/service function supported only at 770 with value 19. The resulting duration should retain `60.0272189349112`, but the native extension produces the single value `69.425`. Only after this overpricing does the Exact dominance test discard the prefix, using `[0,32,31,55]` as its dominator.

The loss therefore precedes the dominance decision. The legacy `PWLFunction` addition sweep advances both operands after processing the first CHOICE piece, exhausting the scalar-point operand and dropping the lower choices that remain stacked at the same abscissa. The retained lower endpoint `50.425` plus 19 explains the observed `69.425` exactly. The existing step-mode stacked-tail repair already prevents this class of loss, but the legacy mode used on jump-free inputs excludes consecutive vertical pieces.

This evidence does not implicate the normalization candidate's copy removal. That candidate retains the same arithmetic and reproduces the baseline counterexample.

## Candidate

Branch `proof-speed/point-addition`, commit `250105c`, is based on `0787925` in [kayros-proof-point-addition]($WORKSPACE/kayros-proof-point-addition). Its patch is `build/ms58/point-addition.patch`. The change is confined to addition in legacy mode. When one entire operand is a single nonvertical point, the sweep retains it across a CHOICE-to-CHOICE tail at exactly the same abscissa. The existing ordinary-segment-to-CHOICE handling remains in place. Multiplication, composition, JUMP attainment rules, reverse normalization and step-mode arithmetic are unchanged.

Comment-only follow-up `ea6097e` updates the arithmetic-mode header to describe the repair and removes the obsolete claim of bit-identical legacy behavior. The tested production binary and patch remain identified by `250105c`.

The [native fixture]($WORKSPACE/kayros-proof-point-addition/tests/cpp/test_point_addition.cpp) checks the captured loss mechanism in both operand orders under legacy and step modes, plus tight-arc composition, disjoint domains, ordinary point addition, ordinary-segment-to-CHOICE boundaries and unrelated JUMP behavior. The [validation driver](../scripts/ms58_point_validation.py) runs the same fixture against the baseline and candidate libraries, then the existing 5-file regression bundle, focused ILS/BPC coupling and the 4 Vu controls on reserved compute job `7013766`.

The reserved-node native fixture reproduces the expected failure against the frozen reference library and passes against the candidate library. All 4 candidate combinations of cold or warm initialization and `cut_limit=0` or `cut_limit=100` now report `1846.8070188618972`, return checker-valid routes at the same cost and report no bound above the witness. Thus the targeted addition repair resolves the observed full-instance contradiction as well as the local arithmetic loss.

The focused coupling checks on Dabia C101 and Rifki-1 preserve identical ILS costs and routes before and after a BPC call. The candidate `_core` binary is byte-identical to the reference, with SHA-256 `b527271e7224fbd1b14b6ed84de49098395e672172ab038a2cc45258572287f3`. These checks support separation of the modified BPC arithmetic from ILS, within the tested deterministic cases.

## Captured-task causal check

The [captured replay](../outputs/ms58/resume-20261110/captured-pair/complete.json) restores Exact call 15 with identical duals, cuts, forbidden arcs, merge point and closing state. Both replay contracts pass. The reference reproduces every source task workload counter and returns 0 routes. The repaired variant returns exactly `[0,31,6,55,33,60]`, with native duration `103.4872189349112` and reduced cost `-0.4325792017348249`. The [witness match](../outputs/ms58/vu-witness-audit/replay-witness-match.json) confirms that this is the independently checked missing route, at the same cost. Backward work is unchanged, while the repaired forward pass processes 433 labels instead of 431 and preserves the improving column. Both replay processes stop deliberately before column admission and make no solver-closure claim.

## Validation scope

The [reserved-node validation](../outputs/ms58/resume-20261110/point-validation/complete.json) passes every candidate process. The 5-file regression bundle reports 60 passed tests and 3 expected opt-in fuzz skips. The native baseline fixture fails for the expected lost-choice assertion, while the repaired fixture passes. The full-instance controls and coupling checks described above also pass.

The [matched fuzz batches](../outputs/ms58/resume-20261110/point-fuzz/complete.json) use the [existing parameterized test](../scripts/ms58_point_fuzz.py) with seeds 7, 11 and 23 and 2 requested cases per seed. Each variant passes all 3 pytest parameters and actually executes 5 cases, comprising 4 Rifki cases, including 1 full instance with 10 customers, and 1 Dabia case. Both variants report identical costs and closure statuses on those cases, with no soundness violation. The sixth request, seed 23 case 0, selects `TDVRPTW/Solomon1987/n=25`, which is absent from the remote benchmark checkout. The existing generator returns no case for that selection, so this result provides 5 matched cases rather than 6. The driver explicitly records the missing coverage.

The candidate is not integrated into production. The arithmetic counterexample, matched pricing replay and complete Vu controls establish the cause and repair of this specific false closure. The broader tests provide bounded regression evidence without establishing general correctness or a proof-speed improvement. We recommend retaining the repair as a separate correctness candidate and using a validated corrected Kayros baseline before evaluating independent execution optimizations such as normalization copy removal.
