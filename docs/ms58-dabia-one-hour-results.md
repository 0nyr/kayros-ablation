# MS58 Dabia first one-hour panel

This development panel covers 12 previously used Dabia n=100 instances, with 3 repetitions per arm, a 3600 s limit and 16 GiB per run on the same Gros cohort. Kayros uses source `9d64b7c53ddaea7a578d247ebd28db43ae0d4e56` and the fixed Lera control uses `f2c9e205bd1ecc9afb45d5efe7fcb0df881f2b33`. Job 7013928 was released. These 12 instances are not the full 56-instance population and do not support generalization to it.

The Lera repair initializes its previously undefined correction flag to false. Kayros includes the scalar-point addition repair, ordered numerical reductions and the repaired correction path enabled by default. All 4 Kayros arms share this source, and their ILS extension is unchanged. CPLEX arms use the pinned CPLEX 12.10 library with 1 solver thread. The separate cold HiGHS arm is the requested backend comparison. Times include process loading and initialization, and the Full prefixes consume part of the common 3600 s budget. The [source inventory](../configs/ms58-development-branches.json) and frozen per-run manifests identify the exact patches, binaries and settings.

The proof-speed objective remains unmet in this panel. Full Kayros retains a checked incumbent in every run, but its 19 or 18 closures remain below Lera's 21. No production integration follows from these results.

| Arm | Reported closures / 36 | Checked incumbent coverage / 36 | Within 1% / 36 | Mean closure time capped at 3600 s | Final mean raw gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Lera minimal CPLEX | 21 | 27 | 27 | 1726.603 s | Undefined |
| Kayros cold CPLEX | 18 | 22 | 22 | 1857.833 s | Undefined |
| Kayros cold HiGHS | 12 | 15 | 15 | 2668.717 s | Undefined |
| Kayros Full CPLEX, 5 s prefix | 18 | 36 | 24 | 1885.412 s | 1.5923% |
| Kayros Full CPLEX, 15 s prefix | 19 | 36 | 25 | 1872.270 s | 0.9608% |

The raw mean gap is undefined when any scheduled run lacks a checked incumbent. The 1% target uses the checked reference for each instance. Reported closure and checked feasible witnesses are separate observations. Consistency with witnesses does not independently certify a proof.

On C104, Lera reported closure in 3 of 3 runs with mean 2018.578 s. Full 15 s reported closure in 1 of 3 at 3158.283 s, while cold CPLEX, cold HiGHS and Full 5 s each reported 0 of 3. The remaining Full 15 s checked incumbents were about 1.20% and 1.25% above the reference, and Full 5 s incumbents were about 1.22% to 1.66% above it. C101 and R101 are short cases and do not support a runtime conclusion.

The requested figures show [mean convergence](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1/analysis/figures/n100/mean-convergence.png), [the 1% target](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1/analysis/figures/n100/goal-gap-1pct.png), and [reported closure](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1/analysis/figures/n100/closure-overview.png). The [full figure directory](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1/analysis/figures/n100/) also contains logarithmic-time versions and plot data. The table comes from the [summary](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1/analysis/summary.json) and [plot data](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1/analysis/figures/n100/plot-data.json).

The corrected audit retains 7 null optional termination bounds as unavailable observations and reports 0 numeric contradictions. The initial analysis incorrectly classified those absent bounds as claims. Correcting that classification changed no timing, quality or closure metric, and the original analysis is preserved. The 3 HiGHS infeasible-column guard warnings remain flagged. The [sync verification](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1-sync-verification.json) records 2248 remote files and 29,692,991 bytes verified against inventory SHA-256 `04ef85b960bf07c153d74fec1b617d7a72927bfe8a81bb46dff953c77e5f03b7`.

The [C104 work and handoff review](../outputs/ms58/dabia-20261110/gros-five-arm-1h-v1/analysis/c104-warm-gates-eps-review-20261110.md) separates completed native work from censored observations. Lera spends about 97% of C104 native time in exact pricing, with dominance tests accounting for about 61%. The sole completed Full 15 s repetition has more recorded work and additional correction, extension and dominance time. Those different pricing contexts do not establish a causal per-operation speed comparison. Ongoing fixed-witness and selected-source profiling experiments address those mechanisms separately from this full-process panel.
