# Main-campaign readiness review

Status: both complete 1-hour panels select prefix 15 s under the approved rule. The [16 GiB report](ms56-hour-pilot-16g.md) covers all 324 cells, with no allocation failures or invalid retained candidates. Its 33-host allocation is released. The separate 54-cell, 2-hour follow-up remains active at 16 GiB on 6 hosts. These are separate experiments, not repairs or replacements of the valid 8 GiB observations. Expected resource failures remain measured outcomes and do not automatically veto readiness. Main launch awaits the focused horizon evidence and the originally required author inspection. No main reservation has been submitted.

## Authorized resource follow-ups

The original C104 full/prefix15/trial1 and RC202 cold CPLEX/trial1 runs failed with `std::bad_alloc` under their 8 GiB address-space caps. All 8 candidates emitted by the first remain checker-valid, while the second emitted none. Preserve both as resource-failed observations in the [completed original wave](ms56-hour-pilot-8g.md), with their metrics and capped closure contributions. Neither failure by itself invalidates that experiment or requires a favorable rerun. A separate live-process snapshot found virtual-memory peaks as high as 7.114 GiB, so RSS alone understated proximity to the address-space cap.

The full 12-instance repeat retains all 9 configurations and 3 trials at 3,600 s, giving 324 cells. Its 16 GiB resource setting supplies an additional matched full-panel prefix comparison for that proposed main operating point. The completed 8 GiB panel retains its own valid ranking under the same approved rule. Abaca job 7012887 requests 33 grvingt hosts for 90 minutes, with 10 workers per host. The focused C104/RC202 follow-up retains all configurations and trials at 7,200 s, giving 54 cells. Job 7012889 requests 6 separate grvingt hosts for 150 minutes, also with 10 workers per host. Its purpose is horizon and resource sensitivity on the 2 instances with observed allocation failures. It is not pooled with the other 10 original instances to manufacture a matched full-panel result.

Both allocations use `--memory-mb 16384`, corresponding to a 17,179,869,184-byte per-process address-space cap. Their solver patches and runtime bytes match the prepared phase 2 implementations. The orchestration driver now accepts explicit selection and memory parameters, with its own frozen hashes in each wave. The unchanged node guard requires 176 GiB available RAM for 10 such workers plus its 16 GiB reserve. The superseded queued 24-node/12 GiB request, job 7012876, was cancelled. No result from the original 8 GiB wave is overwritten or silently replaced.

## Scientific comparison

We test whether Kayros improves on the original Lera-Romero baseline on the 56 original Dabia2013 instances with 100 customers. Incumbent quality and computational closure are separate outcomes. The original solver remains the operational baseline under its declared numerical conventions. Solver-reported closure with an independently checked witness is a computational observation, consistent with the manuscript's existing scope. Source concerns are disclosed diagnostics. A demonstrated material wrong result requires investigation and repair or an explicitly revised scientific claim.

The five arms are original Lera CPLEX, cold Kayros CPLEX, cold Kayros HiGHS, standalone Kayros ILS, and one fixed ILS prefix followed by warm Kayros CPLEX. Every arm has 5 trials per instance. The two heuristic-containing arms share seeds 42, 123, 456, 789 and 2026 by trial. Cold exact trials are runtime replications with null search seeds. One global prefix applies to every instance and seed.

The 16 GiB, 1-hour panel confirms the anytime incumbent-quality benefit of heuristic-containing arms. Full15 has incumbents in 36/36 cells and mean Λ 0.005857, compared with Lera's 27/36 and 0.387223. Lera leads on reported closure count/time (21 and 1696.057 s, versus 19 and 1872.030 s) and reaches the final 1% reference-gap threshold more often (27 versus 25). Full600 reaches that threshold in 33 cells and has the lowest Λ, but the approved primary closure-time criterion selects 15 s. The main experiment must preserve these distinctions and report unfavorable outcomes as well as favorable ones. The [complete report](ms56-hour-pilot-16g.md) and [native-work comparison](ms56-native-work-comparison.md) provide the measured evidence and its limitations.

The [RC105 guard assessment](ms56-rc105-guard.md) records a native rejected-column warning in cold HiGHS. Its independently checked terminal objective agrees with the other exact arms and the frozen reference, but the warning retains its stricter certificate-poison meaning. Report these closures with an explicit qualification. The warning is present in the 300 s archive and in all 3 completed RC105 HiGHS pilot trials. No invalid emitted incumbent or material bound/objective contradiction has been observed in those cells.

## Decisions to finalize from the completed pilot

| Item | Rule or current proposal | Evidence still required |
|---|---|---|
| Global full-arm prefix | Selected 15 s by restricted mean closure time, then mean Λ, then shorter prefix | Complete at both 8 and 16 GiB. At 16 GiB, 15 wins all 12 leave-one-instance-out checks and 2 of 3 replications. |
| Common horizon | Provisional 7,200 s from process launch | Same-wave horizon checkpoints, last-quarter incumbent/closure progress, execution health |
| Memory cap and workers per node | Follow-ups use 16 GiB × 10 workers | Full-panel observed VmPeak reaches 10.734 GiB, with 5 observed cells above 8 GiB. Await the separate 2-hour resource trajectory. |
| Full schedule | 56 instances × 5 arms × 5 trials = 1,400 cells | Offline schedule identity checks and frozen runtime/source inventories |
| Main launch | Author inspection of pilot plots and recommendation | Completed pilot review |

Earlier checkpoints are censored views of the same pilot executions. They do not independently validate behavior beyond the pilot horizon. Resource observations are sampled and may miss short peaks. The independent native-child sampler began after launch, so early completed upstream cells are absent from that supplemental memory series. All such limitations accompany the recommendation.

## Resource envelope

At a 2-hour horizon, the design has at most 2,800 solver core-hours. Total job walltime must include all rounds, preparation and post-run validation. Early closures can shorten actual completion, but reservation sizing does not assume them. Candidate shapes below use homogeneous grvingt hosts and a 30-minute walltime margin. They are capacity calculations, not reservations or predictions of current availability.

| Hosts | Workers per host | Concurrent slots | Maximum rounds | Walltime upper bound with margin |
|---:|---:|---:|---:|---:|
| 47 | 10 | 470 | 3 | 6 h 30 min |
| 50 | 10 | 500 | 3 | 6 h 30 min |
| 40 | 10 | 400 | 4 | 8 h 30 min |
| 33 | 10 | 330 | 5 | 10 h 30 min |

Each process receives one distinct allowed CPU, one backend thread and the declared address-space limit. The node guard checks the configured caps plus its RAM reserve against actual memory. The final shape must use current free capacity and the real OAR nodefile. A smaller worker count can increase memory headroom without increasing the number of rounds when enough hosts are available. Completed reservations are released after process/observer checks. Shared-home archival does not require retaining idle compute nodes.

The official grvingt status API at approximately 11:00 CET reported 38 alive/busy nodes (including the 21 pilot hosts), 6 alive/free, 14 standby/free, 1 free with best-effort work, 3 dead and 2 absent. Counting the pilot hosts after their release gives approximately 41 clearly usable hosts before other allocations change. With the updated 10-worker shape, 40 hosts require 4 worst-case main rounds. Capacity will be checked again after the author review. No main allocation is implied by this snapshot.

## Execution and archival

Freeze the complete task schedule, source and patch inventory, runtime manifests, numerical settings, reference snapshot digest, input digests and resource settings before dispatch. Verify the schedule in an offline dry-run. The main scheduler must explicitly select all 56 reference identities rather than accidentally retain the 12-instance development panel.

Keep the original failure outcomes and every execution attempt. Recover an intact final result whose summary row is missing before considering a new execution. The [recovery protocol](recovery-protocol.md) defines read-only checks and the explicit infrastructure-retry policy. Never select attempts by solution quality or speed, merge trajectories across retries, or retry an algorithmic failure merely to obtain a favorable outcome.

Use the same immutable v0.11 reference snapshot and independently checked candidate envelopes for every metric. Missing incumbents remain at squeezed gap 1, and unsuccessful closure outcomes contribute the full horizon to restricted mean time. Average trials within each instance before averaging instances. Report native/checker discrepancies, closure diagnostics, crashes, limits, retries and incomplete outcomes alongside performance.

After each wave, sync raw outputs, verify recursive file hashes and byte counts, and remove only verified workflow-created remote outputs. Retain source/runtime caches for reproducibility. Manuscript and appendix changes remain deferred until the validated full experiment supplies the intended evidence.
