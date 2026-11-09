# Completed 1-hour pilot at 8 GiB

The original 12-instance pilot finished all 324 scheduled cells on Abaca job 7012808: 147 normal exits, 175 deadline exits and 2 allocation failures, with zero node-controller failures. All emitted candidates passed the pinned v0.11 checker. The resource failures remain in every denominator and retain their available checked incumbent histories. This wave compares 9 configurations with 3 trials each on the frozen 12-instance panel, using a 3,600 s process-launch horizon and 8 GiB per-process address-space cap.

## Measured outcomes

Every row has 36 scheduled cells. Reported closure is solver-declared closure with a checked witness under the documented conventions. It is not the stricter raw certificate-admission field. Λ and capped closure time give instances equal weight after averaging their 3 trials. Lower is better for both scalars.

| Configuration | Valid incumbent / 36 | Reported closure / 36 | Mean Λ | Mean final squeezed gap | Restricted mean closure (s) |
|---|---:|---:|---:|---:|---:|
| Original Lera CPLEX | 27 | 21 | 0.402182 | 0.250040 | 1719.341 |
| Cold Kayros CPLEX | 24 | 18 | 0.450370 | 0.333337 | 1863.608 |
| Cold Kayros HiGHS | 17 | 11 | 0.636564 | 0.527766 | 2709.377 |
| Standalone ILS | 36 | Not applicable | 0.003753 | 0.002422 | Not applicable |
| Full Kayros, prefix 15 s | 36 | 19 | 0.005847 | 0.004790 | 1886.102 |
| Full Kayros, prefix 60 s | 36 | 18 | 0.005206 | 0.004275 | 1912.958 |
| Full Kayros, prefix 180 s | 36 | 19 | 0.003147 | 0.002163 | 1963.957 |
| Full Kayros, prefix 600 s | 36 | 19 | 0.002691 | 0.001359 | 2182.389 |
| Full Kayros, prefix 1800 s | 36 | 18 | 0.003222 | 0.001252 | 2792.389 |

The cold HiGHS row includes 3 RC105 closures with the native rejected-column warning described in the [guard assessment](ms56-rc105-guard.md). Two further warnings occur in RC202 full-arm timeouts, at prefixes 600 and 1800 s in trial 3, and carry no closure credit. Missing final counters remain unknown. The summaries also inspect the preserved exact stderr diagnostic, so an interrupted run cannot conceal a warning merely because it lacks a native final sidecar.

## Interpretation and prefix sensitivity

Heuristic-containing arms supply valid incumbents in every cell and substantially lower mean Λ than the cold exact arms at this horizon. Original Lera has the fastest restricted mean closure time and the most reported closures. These data support an incumbent-quality advantage from Kayros's heuristic stage, while they do not support a general closure-speed advantage over Lera.

The approved diagnostic prefix ordering is 15, 60, 180, 600, then 1800 s by restricted mean closure time. Prefix 15 ranks first in each replication and in all 12 leave-one-instance-out checks. Those are descriptive stability checks, not confidence intervals. The quality trade-off is substantial: prefix 600 has the lowest Λ, while prefix 1800 has the lowest mean final squeezed gap. The approved selection rule retains closure time as its primary criterion.

The decision script holds a final recommendation because this wave contains concrete allocation failures. It preserves the diagnostic 15 s winner instead of silently selecting a successful subset or ignoring the failures. The author authorized a complete 16 GiB repeat, which will provide the matched panel for the main prefix decision. The 8 GiB observations remain a separate resource-setting result.

## Resource failures and follow-ups

C104 full/prefix15/trial1 failed with `MemoryError: std::bad_alloc` at measured worker termination time 3072.107300064 s. Its 8 emitted candidates remain valid, with retained best cost 98446.01457149195. RC202 cold CPLEX/trial1 failed with the same allocation error at 3437.666088687 s, with no emitted incumbent. Neither has a closure event. These terminal times are not timestamps of the exact failed allocation, which was not separately logged.

The corresponding harness RSS high-water samples are approximately 4.818 and 5.571 GiB. A separate live-process snapshot observed virtual-memory peaks up to 7.114 GiB under the 8 GiB address-space limit. That maximum belongs to another observed process and is not attributed to either failed worker. RSS is therefore insufficient to assess remaining address-space headroom. The larger 16 GiB cap is a resource-design response, without changing solver arithmetic, tolerances or the checker.

Two disjoint follow-up allocations are running: job 7012887 repeats all 324 cells at 16 GiB for 1 hour on 33 hosts, and job 7012889 runs all 54 C104/RC202 cells at 16 GiB for 2 hours on 6 other hosts. Both allow at most 10 workers per node. Their source, runtime, checker, reference and seed identities match this wave. Memory cap, placement and concurrent load differ, so cross-wave runtime differences cannot be attributed solely to memory. The focused cohort was selected from the observed high-memory cases and is reported separately. No main campaign has launched.

## Artifacts and verification

All 2,930 remote raw files (61,245,678 bytes) match the local archive file-by-file by SHA-256 and size. The authoritative local wave is `outputs/ms56-phase2/pilot3600-seeds3/`, with receipt `outputs/ms56-phase2/pilot3600-seeds3.local-verification.json`. Derived `analysis/` files are additional local artifacts, not part of the raw count. Final summary, decision and figures were computed on reserved grvingt-25 core 62 and then copied locally. The provisional summary produced during an incomplete transfer was replaced by the complete replay and supplied no reported comparison.

The analysis directory contains `summary.json`, `groups.csv`, `decision.json`, the incumbent and closure plot data, and PNG/PDF versions of `pilot-overview`, `closure-overview` and `prefix-overview`. The closure legend explicitly flags the 3 guarded HiGHS closures. The prefix figure records that the final recommendation is held. Source hashes for the analysis are preserved alongside these outputs. Manuscript and appendix revisions still await a validated full experiment.
