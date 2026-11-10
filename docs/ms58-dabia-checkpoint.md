# MS58 Dabia checkpoint

The main goal remains active. The selected final candidate is source `9d64b7c53ddaea7a578d247ebd28db43ae0d4e56`. The `screen-v1` analysis reports 36 audited rows, with 24 completed runs on C201 and RC201 and 12 C104 runs reaching the deadline. Safe correction is promising on this limited screen. These results do not establish performance parity across n=100. See [screen summary](../outputs/ms58/dabia-20261110/screen-v1/analysis/summary.json) and [completion report](../outputs/ms58/dabia-20261110/screen-v1/complete.json).

The [correction-on validation](../outputs/ms58/dabia-20261110/validation-correction-on/complete.json) reports all checks passed for n=25 C201, n=50 R201, n=100 C101 and n=100 RC201. The [native correction-on report](../outputs/ms58/dabia-20261110/native/correction-on/complete.json) also reports all checks passed for the selected source. Checked feasible witnesses can expose contradictions in bound and closure claims, but they do not independently certify a proof or full pricing completeness. The ILS core binary remains `b527271e` according to the recorded runtime identities.

Extension profiling and CPLEX domain batching are separate experiments and remain pending. The retained domain-batch reports record a fixture compilation failure in the [first attempt](../outputs/ms58/dabia-20261110/native/cplex-domain-batch/complete.json) and an unavailable Ninja executable in [v2](../outputs/ms58/dabia-20261110/native/cplex-domain-batch-v2/complete.json). These failures are not evidence against the selected correction candidate.

At assignment, reservation 7013804 was on grvingt-2 with expiry at 09:53 UTC, reservation 7013882 was on grvingt-4 with expiry at 09:39 UTC, and reservation 7013928 for 45 gros nodes for 1 h 20 min was approved and submitted at 09:08 UTC. The panel had not been launched at assignment. These resource and launch states were supplied at assignment and were not independently refreshed for this checkpoint.

Available compute may be used. The experiment scope is all Dabia instances at sizes 25, 50 and 100, with CPLEX except for the requested cold HiGHS runs and a fixed f2 Lera baseline. Do not compare curves across environments.

## 2026-11-10 development checkpoint

Job 7013966 started at 09:37:02 UTC on gros-122 and gros-124 for 2 h. The 18-cell domain-batch screen completed at 09:58:54 UTC with all cells usable. Against source `327e4817`, batching won 6 of 6 same-work pairs, with median paired reductions of 9.63% on C201 and 3.27% on RC201. EPS v2 was running on gros-124. Both warm builds were assigned to gros-122 after the batch completed.

Job 7014010 started at 09:57:53 UTC on gros-117 for 2 h, submitted with `oarsub -q default -p gros -n ms58-handoff-dev -l host=1,walltime=2 "sleep infinity"`. It builds the A2 profiler and the `2ea97a0` restriction candidate. Job 7013928 remained running at this checkpoint. The A2 build completed at 10:11:11 UTC with `_lera` SHA-256 `27036ca15d8db61827c691208782009be633f03d1eb2636db23e4e9307154bd9`, with native checks pending. The warm incumbent-only build completed at 10:11:24 UTC with `_lera` SHA-256 `37aabd56e1cfffc406db047a515385dbe4c894c7e5737a0030c2946a0643e01a`. Its ILS core remained `b527271e`.

The warm candidates are `74ed81486d2cd365061bdbce02264f6845d8ad30` and `391954aef4c131ad759d2862afc28458c3144026`, both based on `9d64b7c`. Each passed 7 local unit checks and Astra review. The A2 profiler is `a2dcf8870c17e7edeccb472b27bee0e544eec997`, whose 5040 local gates had the same OFF and ON digest. The correction-on domain-batch production source is `ef0e18288883dd7b4c2a5941105176d6ff312cfd`, with fixture-only follow-up `234d838b4128eda40f11cb53f66005d533cf24af`. Runtime evaluation of these new variants remains pending.
