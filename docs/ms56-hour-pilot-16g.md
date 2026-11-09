# Completed 1-hour pilot at 16 GiB

The second full panel completed all 324 cells under a 16 GiB per-process address-space cap: 153 normal wrapper exits and 171 outer deadlines, with no allocation failures, native memory-limit outcomes or invalid retained in-budget candidates. The controller reports no node failures. It uses the same 12 instances, 9 configurations, 3 trials, paired seeds, solver bytes, checker and frozen references as the 8 GiB panel. Each execution receives 3,600 s from process launch. Placement and concurrency changed, so differences between panels are descriptive resource-setting comparisons rather than an isolated causal effect of memory.

## Measured outcomes

Every configuration retains 36 scheduled cells. Trial averages are formed within each instance before averaging instances. Lower Λ, final squeezed gap and restricted mean closure time are better. Reported closure means solver-declared computational closure with a checked witness under the documented conventions. It is separate from the stricter raw certificate-admission record.

| Configuration | Checked incumbent / 36 | Reported closure / 36 | Mean Λ | Mean final squeezed gap | Restricted mean closure (s) | Final raw gap ≤ 1% / 36 |
|---|---:|---:|---:|---:|---:|---:|
| Original Lera CPLEX | 27 | 21 | 0.387223 | 0.250040 | 1696.057 | 27 |
| Cold Kayros CPLEX | 24 | 18 | 0.442075 | 0.333337 | 1861.272 | 24 |
| Cold Kayros HiGHS | 18 | 15 | 0.629149 | 0.499988 | 2633.915 | 18 |
| Standalone ILS | 36 | Not applicable | 0.003787 | 0.002422 | Not applicable | 30 |
| Full Kayros, prefix 15 s | 36 | 19 | 0.005857 | 0.004790 | 1872.030 | 25 |
| Full Kayros, prefix 60 s | 36 | 18 | 0.005249 | 0.004270 | 1911.447 | 26 |
| Full Kayros, prefix 180 s | 36 | 20 | 0.003105 | 0.002026 | 1927.794 | 29 |
| Full Kayros, prefix 600 s | 36 | 19 | 0.002710 | 0.001349 | 2167.310 | 33 |
| Full Kayros, prefix 1800 s | 36 | 18 | 0.003259 | 0.001186 | 2775.772 | 33 |

The 148 reported closures include 3 RC105 HiGHS observations with the previously diagnosed native rejected-column warning. They retain that qualification. The remaining guard observations are timeouts and receive no closure credit.

## Prefix decision and scientific interpretation

The approved ordering again selects 15 s: restricted mean closure time first, mean Λ on an exact tie, then the shorter prefix. The ranking is 15, 60, 180, 600, 1800 s. Prefix 15 wins all 12 leave-one-instance-out comparisons and repetitions 2 and 3. Prefix 180 wins repetition 1 by 6.294 s in the restricted mean. Hence the global recommendation is stable to omitting one instance, but it is not unanimous across replications. Prefix 180 also has 20 closures versus 19 for prefix 15, so the selected prefix does not maximize closure count.

The full 15 s configuration improves mean Λ relative to Lera in 33 of 36 paired trial entries and 11 of 12 instance averages. It supplies a checked incumbent in every cell, while Lera supplies one in 27. These observations support an anytime incumbent-quality benefit. They do not support a general closure-speed advantage: Lera has more closures and a lower restricted mean closure time. At the final 1% reference-gap threshold, Lera also reaches 27 cells versus 25 for full 15 s. The main experiment must report these different outcomes rather than use a blanket superiority claim.

Prefix 600 has the lowest Λ and reaches the 1% threshold in 33 cells. Prefix 1800 has the lowest final mean squeezed gap and also reaches 33 cells. Those are meaningful quality alternatives, but the agreed rule gives closure time priority. The main recommendation therefore retains 15 s, with this trade-off explicit. No post-pilot change to the ranking criterion has been made.

Relative to the valid 8 GiB panel, the total closure count rises from 143 to 148: cold HiGHS gains 4 and full prefix180 gains 1. Lera, cold CPLEX and full15 retain their closure counts. Their capped means become 1696.057, 1861.272 and 1872.030 s respectively, compared with 1719.341, 1863.608 and 1886.102 s at 8 GiB. The two old allocation failures remain valid recorded outcomes in their own panel. They are not replaced by this wave.

## Horizon evidence still being collected

The last quarter of the full pilot contains 6 additional HiGHS closures, 1 full15 closure, 2 full180 closures and 1 full600 closure. Standalone ILS improves its checked incumbent in 4 cells during that quarter. These observations show that the full 1-hour panel is not uniformly stagnant, but they cannot establish what happens after its deadline.

The separate 54-cell C104/RC202 pilot continues to 7,200 s under the same 16 GiB cap. Its completed trajectories will distinguish genuine second-hour improvements and closures from the dilution of startup cost in normalized Λ. It will remain separate from the complete 12-instance aggregates. The common main horizon and final resource recommendation await this evidence. Main launch remains subject to author inspection.

## Memory observations

The bounded virtual-memory observer supplies usable individual-process peaks for 210 of 324 cells. Sampling began after launch and can miss processes that end before an observation. The other 114 cells remain missing rather than becoming zero-memory measurements. The recorded address-space limits match the declared cap in every observed process, with no unmatched process-to-cell associations.

Five observed cells exceed 8 GiB in lifetime virtual-memory peak. The largest is RC202 full/prefix60/trial2 at 10.734093 GiB, followed by RC202 cold CPLEX trials 2 and 3 at 10.536182 GiB. RC202 full/prefix15/trial2 reaches 9.594265 GiB, and C104 full/prefix15/trial1 reaches 9.342289 GiB. All remain below 16 GiB. These are observed individual-process VmPeak values, not sums over worker and child processes. They are not guaranteed full-cohort maxima because sampling can miss exited processes and later peaks. RSS and sampled current virtual size are retained separately in the machine-readable data.

## Numerical guard observations

Six cells have observed native guard warnings. RC105 cold HiGHS trials 1, 2 and 3 each have 1 rejected priced route and report closure with a valid checked final witness. RC202 full prefixes 600 and 1800 in trial 3 each retain 1 stderr warning and time out. Their previously diagnosed path encounters a strict time-window boundary by 1 ULP.

R202 full/prefix180/trial3 has 11 distinct rejected generated routes and times out without closure. All share the customer prefix `[3, 33, 47, 42, 14, 96, 92]`. Pinned-v0.11 diagnostics find a composed arrival of 1810.0000000000002 at customer 92 against due time 1810, while scalar evaluation arrives at exactly 1810. Loads and customer uniqueness pass. This is a floating-point composition boundary, with no demonstrated wrong terminal witness. The [dedicated assessment](ms56-r202-guard.md) preserves the stricter native certificate-poison interpretation and the raw artifact hashes. No numerical tolerance or live solver was changed. Timeout-side final guard counters remain unknown, and stderr warning counts are reported as observed evidence.

## Artifacts and reservation

Abaca job 7012887 used 33 grvingt hosts with at most 10 workers each. At 12:32:26 CET, the sealed wave had 324 results, zero workers/native children and complete RSS/virtual observers. The allocation was then released. The separate 6-host focused job 7012889 remains active. Main analysis and plots ran on unused core 62 of reserved grvingt-25, outside the focused solver cores.

The raw wave is `outputs/ms56-phase3/repeat3600-16g-seeds3/`, with wave SHA-256 `42bf4a5b64db44e1666a85ca7b08a4a3d4e2cacd96794f9c5f278fb762a6d254`. All 3,093 raw files and 81,988,388 bytes are locally verified by SHA-256 and size, separately from derived `analysis/` extras. The adjacent `.local-verification.json` and `.recovery-audit.json` receipts record zero transfer mismatches and all 324 terminal identities passing recovery checks, with no orphaned cells or runtime-manifest errors. One unreadable partial artifact had its original mode recorded before owner-read permission was added for archival. No content was changed. The verified remote raw wave and its transferred tar were then deleted. The focused wave and required runtime/input/checker caches remain intact.

The [incumbent-quality figure](../outputs/ms56-phase3/repeat3600-16g-seeds3/analysis/pilot-overview.png), [closure figure](../outputs/ms56-phase3/repeat3600-16g-seeds3/analysis/closure-overview.png), [prefix trade-off](../outputs/ms56-phase3/repeat3600-16g-seeds3/analysis/prefix-overview.png) and [virtual-memory figure](../outputs/ms56-phase3/repeat3600-16g-seeds3/analysis/resource-vmpeak.png) have PDF counterparts and exact JSON data alongside them. The directory also contains the checked summary, per-configuration CSV, selection decision, separate 8/16 GiB comparison, source hashes and memory-coverage records. Independent review reproduced all group means and closure endpoints from the 324 cell records.
