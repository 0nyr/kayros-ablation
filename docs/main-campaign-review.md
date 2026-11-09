# Main-campaign readiness review

Status: preparation while the 1-hour pilot runs. This is not launch authorization. Final prefix, horizon and resource recommendations await the completed 324-cell pilot and the author's inspection. No main reservation has been submitted.

## Scientific comparison

We test whether Kayros improves on the original Lera-Romero baseline on the 56 original Dabia2013 instances with 100 customers. Incumbent quality and computational closure are separate outcomes. The original solver remains the operational baseline under its declared numerical conventions. Solver-reported closure with an independently checked witness is a computational observation, consistent with the manuscript's existing scope. Source concerns are disclosed diagnostics. A demonstrated material wrong result requires investigation and repair or an explicitly revised scientific claim.

The five arms are original Lera CPLEX, cold Kayros CPLEX, cold Kayros HiGHS, standalone Kayros ILS, and one fixed ILS prefix followed by warm Kayros CPLEX. Every arm has 5 trials per instance. The two heuristic-containing arms share seeds 42, 123, 456, 789 and 2026 by trial. Cold exact trials are runtime replications with null search seeds. One global prefix applies to every instance and seed.

The matched 300 s pilot supports strong incumbent-quality benefits from heuristic-containing arms. Original Lera has the better restricted mean computational-closure time there. The main experiment must preserve this distinction and report unfavorable outcomes as well as favorable ones. The [intermediate report](ms56-phase2-results.md) and [native-work comparison](ms56-native-work-comparison.md) provide the measured evidence and its limitations.

The [RC105 guard assessment](ms56-rc105-guard.md) records a native rejected-column warning in cold HiGHS. Its independently checked terminal objective agrees with the other exact arms and the frozen reference, but the warning retains its stricter certificate-poison meaning. Report these closures with an explicit qualification. The warning is present in the 300 s archive and in all 3 completed RC105 HiGHS pilot trials. No invalid emitted incumbent or material bound/objective contradiction has been observed in those cells.

## Decisions to finalize from the completed pilot

| Item | Rule or current proposal | Evidence still required |
|---|---|---|
| Global full-arm prefix | Minimum restricted mean reported computational-closure time, then mean Λ, then shorter prefix | Complete 3-trial comparison of 15, 60, 180, 600 and 1,800 s |
| Common horizon | Provisional 7,200 s from process launch | Same-wave horizon checkpoints, last-quarter incumbent/closure progress, execution health |
| Memory cap and workers per node | Assess 8 GiB × 16 workers versus 12 GiB × 14 workers | Final per-cell native/process-tree memory observations and actual node RAM guard |
| Full schedule | 56 instances × 5 arms × 5 trials = 1,400 cells | Offline schedule identity checks and frozen runtime/source inventories |
| Main launch | Author inspection of pilot plots and recommendation | Completed pilot review |

Earlier checkpoints are censored views of the same pilot executions. They do not independently validate behavior beyond the pilot horizon. Resource observations are sampled and may miss short peaks. The independent native-child sampler began after launch, so early completed upstream cells are absent from that supplemental memory series. All such limitations accompany the recommendation.

## Resource envelope

At a 2-hour horizon, the design has at most 2,800 solver core-hours. Total job walltime must include all rounds, preparation and post-run validation. Early closures can shorten actual completion, but reservation sizing does not assume them. Candidate shapes below use homogeneous grvingt hosts and a 30-minute walltime margin. They are capacity calculations, not reservations or predictions of current availability.

| Hosts | Workers per host | Concurrent slots | Maximum rounds | Walltime upper bound with margin |
|---:|---:|---:|---:|---:|
| 44 | 16 | 704 | 2 | 4 h 30 min |
| 50 | 14 | 700 | 2 | 4 h 30 min |
| 40 | 16 | 640 | 3 | 6 h 30 min |
| 40 | 14 | 560 | 3 | 6 h 30 min |

Each process receives one distinct allowed CPU, one backend thread and the declared address-space limit. The node guard checks the configured caps plus its RAM reserve against actual memory. The final shape must use current free capacity and the real OAR nodefile. A smaller worker count can increase memory headroom without increasing the number of rounds when enough hosts are available. We will release the pilot reservation after archival and release main resources as soon as their assigned work is complete.

The official grvingt status API at approximately 11:00 CET reported 38 alive/busy nodes (including the 21 pilot hosts), 6 alive/free, 14 standby/free, 1 free with best-effort work, 3 dead and 2 absent. Counting the pilot hosts after their release gives approximately 41 clearly usable hosts before other allocations change. The 40-host, 3-round calculation is therefore more realistic at this checkpoint than assuming 50 hosts will immediately be free. Capacity will be checked again after the author review. No allocation is implied by this snapshot.

## Execution and archival

Freeze the complete task schedule, source and patch inventory, runtime manifests, numerical settings, reference snapshot digest, input digests and resource settings before dispatch. Verify the schedule in an offline dry-run. The main scheduler must explicitly select all 56 reference identities rather than accidentally retain the 12-instance development panel.

Keep the original failure outcomes and every execution attempt. Recover an intact final result whose summary row is missing before considering a new execution. The [recovery protocol](recovery-protocol.md) defines read-only checks and the explicit infrastructure-retry policy. Never select attempts by solution quality or speed, merge trajectories across retries, or retry an algorithmic failure merely to obtain a favorable outcome.

Use the same immutable v0.11 reference snapshot and independently checked candidate envelopes for every metric. Missing incumbents remain at squeezed gap 1, and unsuccessful closure outcomes contribute the full horizon to restricted mean time. Average trials within each instance before averaging instances. Report native/checker discrepancies, closure diagnostics, crashes, limits, retries and incomplete outcomes alongside performance.

After each wave, sync raw outputs, verify recursive file hashes and byte counts, and remove only verified workflow-created remote outputs. Retain source/runtime caches for reproducibility. Manuscript and appendix changes remain deferred until the validated full experiment supplies the intended evidence.
