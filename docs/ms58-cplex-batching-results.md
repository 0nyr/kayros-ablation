# MS58 correction-on CPLEX batching results

We observed shorter reported closure times with guarded CPLEX variable-domain batching in all 6 paired comparisons against correction-on Kayros. The mean reductions were 10.758% on C201 and 2.715% on RC201. The [final independent audit](../outputs/ms58/dabia-20261110/correction-on-domain-batch-screen-gros-v1-independent-audit.json) retains all 18 scheduled cells as completed and usable, including 6 fixed-reference closure credits recovered from the original checked events.

We compared correction-on Kayros `9d64b7c`, its batching integration `234d838` (production change `ef0e182`, followed by a fixture-only repair), and mandatory minimally repaired Lera `f2c9e20`. The [source inventory](../outputs/ms58/dabia-20261110/correction-on-domain-batch-package/source-inventory.json) records the exact integration tree and 7-patch reconstruction. The integrated [native gate](../outputs/ms58/dabia-20261110/correction-on-domain-batch-screen-gros-v1/native-gate/complete.json) passed all 17 cases on CPLEX 12.10 before timing.

The [frozen schedule](../outputs/ms58/dabia-20261110/correction-on-domain-batch-screen-gros-v1/schedule.json) covers only Dabia2013 C201 and RC201 at n=100, with 3 repetitions per source. We ran the 18 cold cells on `gros-40.nancy.grid5000.fr` in job `7014061`, using 3 physical cores, 1 solver thread and 16 GiB per process. Every comparison triplet ran serially on the same core, with 6 balanced source orders across the 6 triplets. Each run had a 600 s process-launch-clock budget, including loading and initialization. All runs reported closure within that budget, so the means below contain no capped observations. Both Kayros arms reported correction and lazy extension enabled.

| Instance | Correction-on `9d64b7c`, mean closure | Integrated batching, mean closure | Fixed Lera `f2c9e20`, mean closure | Reduction of mean versus `9d64b7c` |
| --- | ---: | ---: | ---: | ---: |
| C201 | 85.345 s | 76.163 s | 175.086 s | 10.758% |
| RC201 | 139.409 s | 135.624 s | 145.593 s | 2.715% |

We express each time reduction as a percentage of the control time. The preceding table uses the arithmetic means, while the following table retains every paired reduction against `9d64b7c`.

| Instance | Repetition | Correction-on closure | Batching closure | Paired reduction |
| --- | ---: | ---: | ---: | ---: |
| C201 | 1 | 84.961 s | 76.535 s | 9.917% |
| C201 | 2 | 85.761 s | 76.004 s | 11.377% |
| C201 | 3 | 85.312 s | 75.951 s | 10.973% |
| RC201 | 1 | 139.259 s | 135.095 s | 2.990% |
| RC201 | 2 | 140.155 s | 135.746 s | 3.146% |
| RC201 | 3 | 138.812 s | 136.030 s | 2.004% |

The recorded whole-run work signatures and detailed root-work signatures match in all 6 batching pairs against `9d64b7c`. These signatures cover recorded node, cut, column and constraint counts, objective and bound values, and root iteration statuses and labeling counters. We therefore observe a runtime reduction with unchanged recorded work on these cases. The signatures do not describe every operation or hidden native state. Batching also closed earlier than fixed Lera in all 6 pairs, but both work signatures differ in every comparison against Lera, so those timing differences cannot isolate batching throughput.

The [original controller completion record](../outputs/ms58/dabia-20261110/correction-on-domain-batch-screen-gros-v1/complete.json) still flags the 6 Lera cells as unusable. Its settings classifier incorrectly treated absent correction and lazy-extension fields in the fixed-reference sidecar as observed mismatches, suppressing closure credit despite completed runs. We preserved that record, every controller row and the frozen harness. The [offline re-audit](../analysis/ms58_correction_batch_audit.py) verifies the exact fixed-reference source, binary and minimal repair patch, records missing fields as unknown, and recomputes closure from the sealed events and checked candidates. It restores all 6 credits without treating source expectations as observed metadata. Explicit settings mismatches remain failures, and both Kayros arms still require observed enabled settings. The audit reports 0 proof-consistency contradictions and 0 unsupported closures. This necessary consistency check does not independently certify a proof.

These results concern exactly 2 previously used instances, 3 repetitions and 1 node. They support the isolated batching integration on C201 and RC201 under this configuration. We do not infer overall parity, general nonregression, or population-wide superiority from this screen, and we do not pool its absolute times with other cohorts.
