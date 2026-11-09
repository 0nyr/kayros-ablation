# Completed 2-hour focused pilot at 16 GiB

The focused C104/RC202 pilot completed all 54 scheduled cells with 20 normal completions, 34 outer deadlines and no allocation failures, native memory-limit outcomes or invalid retained candidates. All 947 retained candidates pass the pinned v0.11 checker. There are 42 cells with a checked incumbent and 12 without one. Every scheduled cell remains in the metrics. The 2 allocation failures in the separate 8 GiB full panel remain valid results and are not replaced by this wave.

We recommend a common 7,200 s main horizon from process launch, a 16 GiB per-process address-space cap and at most 10 workers per grvingt host. The global prefix remains 15 s, selected by the approved rule on the complete 12-instance, 1-hour panel. The 2 focused instances were selected after allocation failures, so their results provide targeted horizon and resource evidence. They neither replace the full-panel observations nor select a different global prefix.

## What the second hour adds

The following counts compare checkpoints of the same 2-hour executions. They are not differences between independently executed 1-hour and 2-hour waves. Each row has 3 C104 trials.

| Configuration | C104 closures by 3,600 s | C104 closures by 7,200 s | C104 trials improving after 3,600 s |
|---|---:|---:|---:|
| Original Lera CPLEX | 3 | 3 | 0 |
| Cold Kayros CPLEX | 0 | 0 | 0 |
| Cold Kayros HiGHS | 1 | 3 | 2 |
| Standalone ILS | Not applicable | Not applicable | 1 |
| Full Kayros, prefix 15 s | 1 | 2 | 1 |
| Full Kayros, prefix 60 s | 0 | 3 | 3 |
| Full Kayros, prefix 180 s | 1 | 3 | 2 |
| Full Kayros, prefix 600 s | 1 | 3 | 2 |
| Full Kayros, prefix 1800 s | 0 | 3 | 3 |

The second hour adds 13 reported computational closures, increasing the total from 7 to 20. All occur on C104, with closure times up to 5,436.23 s. This is direct evidence that a 1-hour horizon would censor meaningful exact-search progress. The selected full15 configuration gains 1 closure, while its first trial still times out. Original Lera has already closed all 3 C104 trials before the first checkpoint. These observations support the longer opportunity window without claiming that Kayros closes faster than Lera.

Every RC202 run reaches the common deadline, and no exact arm reports closure. All 3 cold arms still lack a checked incumbent. Standalone ILS improves in all 3 trials during the second hour, reducing its mean endpoint squeezed gap from 0.004854 to 0.002972. All full-arm RC202 endpoint gaps remain unchanged during that interval. In particular, full15 stays at 0.022021, whereas prefixes 600 and 1800 stay at 0.004854. These are separate outcomes from computational closure.

Across both instances, 17 cells improve during the second hour. This count includes 2 HiGHS cells obtaining their first checked incumbent, so the 2 counts must not be added. The [linear horizon overview](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/horizon-linear-overview.png) retains missing-incumbent gap 1. The [signed detail view](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/horizon-signed-detail.png) expands the smaller gaps while preserving zero and negative values. Each figure separates endpoint gaps from interval-average gaps and displays all 3 trials.

A lower full-horizon Λ alone does not establish later search progress. Lera's C104 second-hour gap is zero because it already closed during the first hour. RC202 full-arm scores can fall simply because startup and earlier larger gaps occupy a smaller fraction of the longer horizon. The horizon review therefore reports actual later improvements and closures alongside the second-hour mean gap. No focused observation is pooled with the other 10 pilot instances to manufacture a 12-instance, 2-hour panel.

## Resource and numerical observations

Usable virtual-memory observations cover all 54 cells. The largest observed individual-process VmPeak is 11,322,988 KiB (10.798443 GiB), on RC202 full15/trial3. Every sampled address-space limit matches 16 GiB, with no unmatched observations or limit warnings. The [memory figure](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/resource-vmpeak.png) preserves worker and native-child identities without adding their virtual address spaces. Lifetime peaks are sampled before process exit, so coverage does not guarantee that every final peak was observed. A clean result on these 2 instances does not guarantee that all 56 main instances fit the cap.

The 16 GiB cap provides a practical tested operating point alongside the complete 16 GiB full panel, where the largest observed peak was 10.734093 GiB. At 10 workers, the node guard requires 176 GiB available RAM, comprising the declared process caps and a 16 GiB reserve. Any resource failure in the main campaign remains a failed experimental outcome with retained checked incumbents and full scheduled denominator contributions.

The 20 reported closures all have checked witnesses and consistent recorded closure censuses, without observed native rejected-column warnings. The raw stricter `accepted_proof` fields remain unchanged and are not promoted. The largest observed native/checked objective discrepancy is approximately 1.31e-10. Two native rejected-column warnings recur on RC202 full600/trial3 and full1800/trial3, both timeouts without closure. Their path `[37, 42, 3, 7, 65]` is the previously diagnosed 1-ULP strict-composition boundary. The [existing assessment](ms56-rc105-guard.md) retains its numerical and native certificate-poison interpretation. No tolerance or solver runtime was changed.

## Reproducibility and main recommendation

Wave `focused7200-16g-seeds3` has ID `800f94098f3c4878a84639d97c54da52` and manifest SHA-256 `cd752f9c610dcd1c96b725ed89402767d4486d7d4c2827ad2083c1b3925332d6`. It ran on 6 hosts in Abaca job 7012889 with the same solver, patch, checker, reference and seed identities as the complete 16 GiB panel. All 54 records sealed, with zero workers/native children and all 6 RSS plus 6 virtual-memory observers complete. The final analysis ran on reserved grvingt-25 core 62, and independent recovery auditing used grvingt-24 core 62. Neither analysis reran solvers or reported external validation times.

The raw archive contains 572 files and 22,382,906 bytes. Local SHA-256, byte counts and modes match the remote inventory, and all 54 terminal identities pass the recovery audit without orphaned cells or runtime-manifest errors. Archive SHA-256 is `300df5f2571aa63d78fbcf908ef11b926954099c90134fdd3e856e6427ff8709`. Derived analysis extras are kept separately from that inventory. The verified remote raw directory and transfer archive have been removed after all readers finished. Runtime, source, checker and input caches remain available.

The [horizon review](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/horizon-review.json) has SHA-256 `5b538a3df517dfbead2d46e770ef2d0e279ffd5bcc6db1c29e00a1d79e7c5b70`, and the [checked summary](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/summary.json) has SHA-256 `177c9049c193d0094067bd482d5c75275fb7083f33209a32fb3255859212d441`. The analysis directory also contains [closure curves](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/closure-overview.png), [0%, 1% and 5% target curves](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/raw-gap-targets/raw-gap-attainment.png), PDF counterparts, exact plot coordinates and source hashes. Independent review confirms the 13 additional closures, 17 improving cells, all scheduled denominators and the memory coverage.

The [main proposal](../configs/main-proposal.json) contains 1,400 cells for 56 instances, 5 arms and 5 trials, with prefix 15 s, horizon 7,200 s and cap 16 GiB. The current capacity scenario uses 47 hosts and 10 workers per host, giving 470 slots, at most 3 rounds and a 6 h 30 min reservation including margin. Its `launchable` field remains false. No further pilot is needed to support this design recommendation. The original author-inspection requirement remains in force, and no main reservation or execution has been submitted. Manuscript and appendix integration remain deferred until the validated full experiment.
