# ms58 point-addition repair performance

The completed C201 pairs show a performance regression for the scalar-point addition repair. Mean reported closure time increases from 126.687 s to 153.104 s, or 20.9%. The repaired runs explore more nodes and generate more columns after a matching root calculation. These results do not establish nonregression and do not support promotion on performance grounds.

All 32 scheduled cells are archived and pass the independent archived-result audit. There are 28 completed runs and 4 outer-deadline outcomes, with 14 reported closures out of 16 runs in each arm. We record no resource failure, invalid checked incumbent, native guard warning or integrity issue. These are solver-reported closures with checked witnesses, not independently certified proofs. The [final panel analysis](../outputs/ms58/resume-20261110/point-comparison-analysis.json) is authoritative, and the [independent final audit](../outputs/ms58/resume-20261110/point-final-panel-audit.json) confirms its observations and groups from the archived cells. The earlier [31-cell checkpoint](../outputs/ms58/resume-20261110/point-comparison-checkpoint.json) remains historical evidence.

The reference is `0787925`, with runtime identity `0534bea70c99ff5cae51c778df038a9aba7da1460c0a1db08f2cf225b54d7b53`. The point-repair candidate is `250105c`, with runtime identity `714cacb25dc20b903db14cc5e11877931659f4b4635c5915feca9bed1bdaaea7`. Both arms use a 16 GiB process limit. We retain the per-instance horizons and repetition counts below, without pooling unlike budgets. An outer deadline contributes its horizon to the capped mean and does not measure a completion time.

| Instance | Horizon (s) | Pairs | Reference closures | Candidate closures | Reference mean capped closure (s) | Candidate mean capped closure (s) |
|---|---:|---:|---:|---:|---:|---:|
| C101 | 300 | 3 | 3 | 3 | 2.719 | 2.792 |
| R101 | 300 | 3 | 3 | 3 | 1.733 | 1.753 |
| C201 | 300 | 3 | 3 | 3 | 126.687 | 153.104 |
| R201 | 300 | 3 | 2 | 2 | 286.078 | 287.991 |
| RC201 | 300 | 3 | 3 | 3 | 167.162 | 165.584 |
| C104 | 600 | 1 | 0 | 0 | 600.000 | 600.000 |

R201 closes 2 of 3 runs in each arm, with a 1.913 s increase in mean capped time. C104 reaches the 600 s outer deadline in both arms. Its last recorded root row differs, with iteration 35 and 54,560 columns for the reference versus iteration 36 and 55,015 columns for the candidate. Those partial observations do not establish completed pricing work or a proof-speed improvement. C104 sampled peak resident memory differs by +200 KiB. The equal panel closure counts do not cancel the C201 slowdown or establish overall nonregression.

## C201 search and pricing

| Repetition | Order | Reference closure (s) | Candidate closure (s) | Reference nodes / final columns | Candidate nodes / final columns |
|---|---|---:|---:|---:|---:|
| 1 | Reference, candidate | 135.430 | 156.577 | 23 / 74,118 | 27 / 84,239 |
| 2 | Candidate, reference | 125.246 | 155.310 | 23 / 74,118 | 27 / 84,239 |
| 3 | Reference, candidate | 119.383 | 147.425 | 23 / 74,118 | 25 / 77,639 |

All 6 root LP valuations agree bit for bit across their 23 stored entries. Every run records 52 root iterations, 30,249 added columns and the same root labeling counters. The [C201 and RC201 analysis](../outputs/ms58/resume-20261110/point-c201-rc201-analysis.json) preserves the valuation comparison and detailed work records. Mean root labeling time falls from 1.517 s to 1.414 s, with heuristic-cost time 0.521 → 0.493 s, heuristic-elementarity time 0.957 → 0.885 s and exact time 0.039 → 0.036 s. Mean root column-generation pricing time falls from 2.417 s to 2.253 s, and mean total root time falls from 5.651 s to 5.299 s. Timing variation remains possible, especially because the first reference run is slower than its later repetitions. These observations supply no evidence that slower root pricing explains the end-to-end regression.

Mean branching time instead rises from 73.648 s to 92.332 s, accounting for 18.684 s of the 26.418 s closure increase. Mean LP time rises from 30.452 s to 34.993 s, and whole-run pricing time rises from 12.883 s to 13.403 s. The observed regression accompanies changed search work, so these totals cannot isolate arithmetic throughput. The candidate also changes its node and column counts between repetitions.

The mean sampled peak resident-memory difference is -503 KiB, with paired differences of -196, +48 and -1,360 KiB. We observe no material C201 memory increase in these runs. Peak memory reflects different searches, and sampling can miss brief peaks, so this is not a general memory guarantee.

## RC201 matching recorded work

All 6 RC201 runs close at the root with 118,212 columns and 48 cuts. Their root and whole-run work signatures match. Mean closure time changes from 167.162 s to 165.584 s, a 0.9% decrease, but the paired candidate-minus-reference differences are -6.212, +1.407 and +0.072 s. The favorable mean is driven by the first pair and establishes no robust speedup.

Mean root labeling time changes from 105.096 s to 104.695 s. Its heuristic-cost, heuristic-elementarity and exact components change from 2.756 → 2.732 s, 30.089 → 29.997 s and 72.251 → 71.966 s, respectively. Whole-run pricing time changes from 108.654 s to 108.137 s. Mean sampled peak resident memory differs by -1,464 KiB, with mixed paired differences. These measurements do not establish a general resource or performance improvement.

## Interpretation and proposed comparison

The [separate normalization diagnostic](ms58-normalization-analysis.md) demonstrates that pointer-dependent valuation traversal can change floating reductions and the strong-branching candidate pool. That mechanism is a plausible explanation for some C201 search variability here, but this point-repair panel contains no first-branch trace or comparison with canonical reductions. In addition, the point repair intentionally preserves previously lost attainable arithmetic values, whereas the normalizer was arithmetically identical on its differential fixtures. Matching root values and counters cannot rule out a legitimate repair-induced pricing change later in the search, another allocation-sensitive path or an unrelated numerical effect. We do not attribute this regression to allocation order alone.

The reference runtime files are byte-identical in the point-repair and normalization panels, as verified by their archived file names and hashes. Nevertheless, the C201 reference records 74,118 final columns in this panel and 91,742 in the normalization panel, with 23 closed nodes in both. These cohorts run on `grvingt-34` and `grvingt-54`, respectively. The observation establishes sensitivity to the changed execution context, without proving a specific cause. It reinforces the need to audit work within each panel and to avoid causal claims from absolute timing comparisons across panels.

The repair addresses the independently reproduced [Vu pricing defect](ms58-vu-point-pricing-audit.md). That correctness evidence remains separate from its unfavorable C201 performance measurement. The next proposed test is the point repair under the same ordered reductions in both arms. This combination is currently unbuilt and unmeasured. The complete 32-cell panel supports no overall nonregression or promotion claim, and no additional code change or experiment is part of this analysis.
