# Native work in the matched 300-second wave

R201 is the sixth instance that original Lera closes while cold Kayros CPLEX remains open at 300 seconds. Both return the same v0.11-checked incumbent cost, 22,389.299831918728. The other large difference is RC201: both close, but Kayros reaches its reported closure approximately 65 seconds later. These 2 instances contribute approximately 112.18 seconds of the 114.44-second net difference in summed capped closure times, or approximately 98%. The 12-instance restricted mean difference is 9.537 seconds. This is 1 development trial per arm and instance under the [declared comparison conventions](pilot-proof-parameters.md).

We used existing events, native sidecars and stderr only. The [comparison script](../analysis/compare_native_work.py) verifies event/native digests and produces `matched300-revised/analysis/native-work-comparison.json`. No solver run or route evaluation is added by this analysis. The [phase 2 report](ms56-phase2-results.md) owns the wave-level results.

## Shared closure outcomes

All values are seconds except column/node counts. Each pair is original Lera / cold Kayros CPLEX. Outer closure time starts at process launch. Native search time is the internal BCP timer, used here to distinguish search from loading and bridge work.

| Instance | Outer closure | Native search | Root columns | Closed nodes |
|---|---:|---:|---:|---:|
| C101 | 1.747 / 2.497 | 1.213 / 1.152 | 13,123 / 9,610 | 1 / 1 |
| C201 | 139.379 / 131.208 | 138.802 / 129.836 | 39,629 / 30,349 | 21 / 25 |
| R101 | 1.074 / 1.934 | 0.549 / 0.586 | 11,067 / 8,611 | 1 / 1 |
| RC105 | 108.360 / 117.183 | 107.604 / 115.774 | 23,426 / 22,282 | 31 / 27 |
| RC201 | 136.335 / 201.304 | 135.652 / 199.907 | 92,701 / 118,212 | 1 / 1 |

Kayros is faster on C201 despite exploring more nodes. Its LP and strong-branching time are lower there, while its aggregate pricing time is higher. On C101, Kayros's native search is slightly faster, but the process-launch result is later. The difference outside the native search is approximately 1.345 seconds for Kayros and 0.534 seconds for Lera. For R101, the corresponding differences are approximately 1.348 and 0.525 seconds. Imports, loading, payload construction and preprocessing are charged by the approved clock and matter visibly on these short searches.

## R201, the missing closure

Lera completes its root at outer time 121.066 seconds and reports closure at 252.788 seconds, after 3 closed nodes. Kayros completes its root at 167.369 seconds, finds its checked incumbent at 172.134 seconds, enters branching and then reaches the outer deadline without a closure event or final native sidecar.

| Root evidence | Original Lera | Kayros CPLEX |
|---|---:|---:|
| Internal root time | 120.357 s | 165.954 s |
| Root bound | 22,381.5715073654 | 22,381.571507365406 |
| Root columns | 65,849 | 83,061 |
| Root cuts | 61 | 65 |
| Reported root CG iterations | 78 | 75 |
| Exact labeling calls at the root | 21 | 20 |

Kayros reaches essentially the same displayed root bound with approximately 26% more root columns. Similar CG/Exact call counts therefore do not mean identical work per call. The first post-pricing restricted-master values already differ: with 3,100 columns, Lera prints 48,089.8 and Kayros 47,893.4. The later search trajectories cannot be interpreted as a shared sequence of LPs plus a constant checking delay.

The missing Kayros native sidecar limits the comparison. Its root columns, cuts and CG count come from the completed root console section, and Exact-call counts come from the root-bound event. The logs do not separate the remaining 128 seconds after its incumbent into strong branching, descendant pricing and other work. No final node count or detailed Kayros root labeling breakdown can be reconstructed from the available records. In particular, absence of a later frontier event is not evidence that no child work occurred.

## RC201, where complete timing evidence is available

Both solvers close at the root with the same checked optimum. Kayros nevertheless generates more root columns and spends more time inside labeling itself.

| Root labeling level | Calls, Lera / Kayros | Processed labels, Lera / Kayros | Labeling time, Lera / Kayros |
|---|---:|---:|---:|
| Heuristic Cost | 36 / 32 | 143,346 / 122,203 | 2.552 / 2.908 s |
| Heuristic Elementarity | 21 / 30 | 289,581 / 591,278 | 13.888 / 36.310 s |
| Exact | 19 / 19 | 925,931 / 1,030,386 | 67.581 / 90.041 s |
| Total | 76 / 81 | 1,358,858 / 1,743,867 | 84.021 / 129.259 s |

Processed-label totals sum the forward and backward counters. They do not measure polynomial-piece complexity or the cost of every dominance test. The 28% larger total processed-label count and 28% larger master accompany a 54% larger labeling time, so a raw seconds-per-label ratio would not be a controlled implementation-throughput comparison.

The native root-time difference is 64.254 seconds. Approximately 45.238 seconds, or 70%, appears inside the labeling timers. LP time adds 12.208 seconds (31.641 versus 43.849 seconds), and cut separation adds 3.411 seconds (16.041 versus 19.452 seconds). The remaining root pricing-callback time after subtracting labeling and cuts is 1.498 versus 3.858 seconds. That residual includes column admission, master insertion, dual interpretation and other callback work. It does not isolate route checking. Timers are rounded to milliseconds and this decomposition is descriptive.

## Interpretation and limits

The source confirms an additional direct cost in Kayros: every newly admitted route is repriced and feasibility-checked by its internal checker-exact evaluator, while upstream admits the labeling's route directly. Kayros also constructs route fingerprints. These operations run outside `lbl.Run()`, so their direct execution cannot account for the 45.238-second RC201 labeling-time increase. They are plausible contributors to the larger non-label callback residual. Repriced coefficients can also affect later LP duals and pricing trajectories indirectly, but this analysis does not identify that causal contribution.

Both arms use CPLEX 12.10, 1 thread, explicit LP tolerances of 1e-7, the 1e-6 algorithmic comparison EPS, a 500-cut cap and `epsilon_bigger(violation, 0.1)`. Their selected native code and arithmetic are still different. The full-panel representation audit found arrival discrepancies up to approximately 3.64e-12, and reported incumbent cost discrepancies in this wave remain below 5e-10. Small coefficient differences can select different LP bases in a degenerate model and thereby change generated columns and cuts. The observed early LP divergence, column counts and pricing-level mix are consistent with this possibility. They do not establish that epsilon arithmetic is the cause. Adapted PWL/labeling code, route-pool ordering and different numeric conventions also remain possible contributors.

The labeling and master records therefore support a workload-dependent explanation of the timing differences. They do not identify a single universal slowdown in Kayros. C201's faster Kayros search and RC201's much larger Kayros root workload illustrate that dependence. The measurements are single trials on different cores/hosts within a concurrent wave, so shared-resource effects and ordinary runtime variation remain additional limits on causal attribution. The repeated 1-hour pilot can assess whether these observed rankings persist, while retaining solver-reported closure and checker validation as separate evidence.

## Process-launch and prefix timing review

The current source applies a common outer clock. [The harness](../scripts/probe_runner.py) records the monotonic start immediately before `Popen`, derives the absolute horizon, passes both timestamps to the worker and kills the complete process group at that deadline. The launcher's input identity checks and prepared-runtime verification happen before this clock. Solver imports, actual instance loading, any repeated in-worker checks, preprocessing and native-child launch happen afterwards. External route validation is called after worker exit and has no recorded duration.

[The Kayros worker](../scripts/kayros_worker.py) derives every remaining budget from that inherited deadline. For full Kayros, it subtracts loading and native-instance conversion from the process-launch prefix allowance, retains only witnesses produced within that allowance, and waits until the fixed boundary if ILS returns early. A native ILS overrun can delay the handoff, but late prefix candidates cannot enter the full arm's trajectory or warm start. The 36 matched-wave handoffs all start after their selected boundary. Lateness ranges from 2.779 to 9.388 ms, with per-prefix medians of approximately 4.0–4.5 ms. No timestamp is clamped to the chosen prefix.

[The upstream worker](../scripts/upstream_event_worker.py) inherits the same start/deadline across its native subprocess. Its patched C++ `ms56::remaining()` reads the absolute inherited monotonic deadline, including parsing and preprocessing. The native ILS implementation starts its steady-clock timer at function entry. These mechanisms support consistent charging of loading across arms, while the amount and representation of loading work differ between their native interfaces.

Kayros's exact API retains an internal timing limitation: the worker supplies a residual relative duration before `to_lera_payload` and native preprocessing, but `BCP::Run` starts its own relative deadline afterwards. Its internal deadline can therefore extend beyond the common outer deadline by intervening setup time. The outer watchdog still enforces the approved horizon, and all after-horizon events remain excluded. This affects graceful termination and availability of final native diagnostics, rather than giving Kayros additional scored search time. It is consistent with the missing final sidecars of timed-out Kayros cells. Passing the absolute outer deadline through this native layer would improve diagnostic completion in a future separately identified change. No active pilot runtime was changed by this review.
