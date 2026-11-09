# Intermediate pilot results

Status: 60 s and 300 s development waves complete. The prespecified 1-hour pilot is running on Abaca job 7012808, with 324 cells on 21 grvingt hosts. The main campaign remains held for review.

## Scope and protocol

Both waves use the 12 frozen n100 identities and classic mamut-routing-lib v0.11 validation against the fixed reference snapshot. The 60 s wave uses earlier native settings and 4 Kayros configurations. The matched 300 s wave uses revised explicit numerical settings and adds original Lera plus multiple full-arm prefixes. Compare configurations within each wave. Differences between waves combine horizon and implementation-setting changes.

The 300 s wave has 84 cells: 12 instances × 7 configurations, 1 paired heuristic seed (42) and one runtime replication for the cold exact arms. Every scheduled cell remains in the denominator. Λ integrates the checked best-so-far squeezed gap over uniform elapsed time from process launch. Missing incumbents have gap 1. Reported closure means solver-declared completion under the documented numerical conventions with the latest incumbent witness independently validated. It is distinct from the earlier stricter raw audit field, which remains unchanged in archived records.

## Matched 300 s observations

| Configuration | Valid incumbents / 12 | Reported closures / 12 | Mean Λ | Restricted mean reported closure (s) |
|---|---:|---:|---:|---:|
| Original Lera CPLEX | 6 | 6 | 0.583820 | 203.307 |
| Cold Kayros CPLEX | 6 | 5 | 0.615792 | 212.844 |
| Cold Kayros HiGHS | 3 | 3 | 0.779250 | 244.789 |
| Standalone ILS | 12 | Not applicable | 0.010046 | Not applicable |
| Full Kayros, prefix 15 s | 12 | 5 | 0.011055 | 229.137 |
| Full Kayros, prefix 60 s | 12 | 5 | 0.010525 | 242.495 |
| Full Kayros, prefix 180 s | 12 | 2 | 0.009863 | 280.277 |

There were no launch failures, crashes, invalid candidates or validation/integrity failures. All 6 upstream closures had matching complete-pricing counters and zero recorded restricted-master-infeasibility/unbranched-fractional warnings. Numerical native-versus-checker differences are retained in the raw records. Their maximum absolute magnitude in this wave was below 5e-10.

The results support a strong incumbent-quality benefit from the heuristic-containing arms at this horizon. They do not support blanket superiority of Kayros's exact component. Original Lera closes more selected instances and has lower restricted mean reported-closure time. Among the tested full-arm prefixes, 15 s has the lowest restricted mean reported-closure time, while 180 s has the lowest Λ and 60 s the lowest final mean squeezed gap. This is a single-trial development observation, not the final prefix choice.

## Resources and limitations

All 84 cells ran concurrently on 6 grvingt hosts with 14 distinct cores per host and 8 GiB process address-space limits. Maximum sampled Kayros RSS was approximately 2.05 GiB. Independent late-start native-child sampling observed 7 longer upstream processes, with maximum sampled RSS approximately 2.126 GiB. The ordinary harness RSS field measures the upstream Python wrapper and must not be used as the native solver's memory footprint. Short upstream processes completed before that supplemental sampler started, so its evidence is incomplete.

The full 56-instance representation audit found exact agreement in checked metadata and a maximum arrival-function discrepancy of approximately 3.64e-12 across 10,340,020 finite union-knot/midpoint samples. No excluded native-domain sample arrives within the common depot horizon. These are numerical representation checks under the implemented model, not formal real-arithmetic equivalence proofs.

The next pilot retains all 5 proposed prefixes and 3 paired trials under a common 3,600 s horizon. Its purpose is to assess prefix stability, native closure progression, incumbent saturation and memory growth before choosing a main horizon. Numerical settings and source/runtime identities are frozen for that wave. Main-manuscript and appendix revisions await validated full-experiment results.

## Prefix and horizon decision analysis

The [decision analysis](../analysis/pilot_decision.py) requires a completed wave and retains every scheduled cell. It ranks the full-arm prefixes by restricted mean computational-closure time, then mean Λ, then prefix duration. Ties are exact ties of the computed aggregates, with no post-hoc equivalence margin. Per-replication and leave-one-instance-out rankings describe sensitivity and do not change that ordering. The pilot panel is selected for design, so these are descriptive checks rather than population confidence intervals.

The same analysis evaluates trajectories at 25%, 50%, 75% and 100% of the common horizon and records incumbent improvements and closures in the last quarter. These are censored views of the same executions. A prefix longer than an early checkpoint has not reached exact search there. Lack of late progress cannot establish behavior beyond the observed horizon.

Concrete failed executions or invalid candidates hold the recommendation while preserving their diagnostic ranking and denominator contributions. Closure-census source concerns alone do not impose an additional formal-proof gate. A clean recommendation remains subject to author review before main launch. The matched 300 s replay passes these checks and ranks 15 s first, but the final choice will use the completed 1-hour, 3-trial pilot.

The plotting scripts produce incumbent, computational-closure and prefix trade-off figures, using consistent arm colors. Figure points in the prefix comparison are per-replication averages, not confidence intervals. JSON and CSV companions retain the plotted quantities.

## Native guard visibility correction

The archived 300 s RC105 cold HiGHS cell already records 1 rejected checker-infeasible priced column and the native certificate-poison warning. The earlier summary omitted this field. Updated summaries and closure plots expose the warning without changing the raw artifacts or the qualified solver-reported closure count. Its final emitted incumbent is independently v0.11-valid at 23,376.812597162243, and its CG closure census is 35/35 with zero unresolved closures. The [source and reporting assessment](ms56-rc105-guard.md) explains why this remains a flagged operational closure rather than an unqualified clean computational proof. It also separates the observed rejected column from any hypothetical pricing-completeness failure.
