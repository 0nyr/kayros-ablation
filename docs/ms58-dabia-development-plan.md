# ms58 Dabia-only development and 1-hour comparison

On 2026-11-10, Onyr narrowed the immediate objective to making Kayros at least as good as, and preferably faster than, a minimally corrected Lera reference on Dabia2013 with 100 customers. Generalization to other families and more than 100 customers is deferred. Earlier cross-family evidence is preserved but supplies no new pilot cells in this pass.

## Comparison contract

All new routing-instance pilots and intermediate tests use only `TDVRPTW/Dabia2013/n=25`, `n=50` or `n=100`. Sizes 25 and 50 support correctness and startup checks. The primary performance assessment uses size 100 and includes difficult instances. Very short solves do not establish an execution improvement because loading and initialization can dominate them. Synthetic arithmetic and data-structure fixtures remain available to test source invariants without adding another benchmark family.

Every new comparative series includes the minimally corrected Lera reference `f2c9e20`, whose only additional change over the frozen upstream control is initialization of `correcting` to false. Its built binary SHA-256 is `59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783`. Historical uninitialized and diagnostic correction-on artifacts are retained as earlier evidence, not used as current reference arms. All source changes and algorithm settings are declared.

Kayros uses CPLEX in development tests. The explicitly requested cold-HiGHS arm is the only backend exception in the 1-hour comparison. All Kayros arms use the same selected corrected source revision, with separate backend builds and verified runtime identities. The comparison has 5 arms:

1. Minimally corrected Lera with CPLEX.
2. Full Kayros with CPLEX and a 5 s prefix.
3. Full Kayros with CPLEX and a 15 s prefix.
4. Cold Kayros with CPLEX.
5. Cold Kayros with HiGHS.

The 3,600 s budget begins at process launch and includes loading, initialization and the prefix. Every process has a 16 GiB address-space cap and an isolated physical core. The 12-instance size-100 development subset from the previous pilot is retained for continuity and all 6 instance classes, with 3 repetitions and paired heuristic seeds. This gives 180 cells. These are development instances already used in optimization, so the results are not independent confirmation. Size-specific results remain separate, and no size-25 or size-50 result enters the primary size-100 aggregate.

All scheduled cells remain in denominators. Deadlines, resource failures, invalid witnesses and missing incumbents remain visible. Reported computational closure requires the existing independent witness checks and integrity gates, but it is not a formal proof certificate. Primary closure summaries include the closed fraction over time and the restricted mean closure time at 3,600 s. The requested `closure-overview` plots accompany mean convergence and raw 1% goal-gap attainment plots. Raw mean gaps are shown only where mathematically defined for the full scheduled population, with incumbent coverage reported. The existing bounded gap summary retains missing incumbents throughout its denominator.

## Candidates and parallel investigations

The first integrated candidate is `327e481` on development branch `proof-speed/best`. It combines the point-addition correctness repair with column-ordered reductions at branching and cut separation. It excludes the unsupported normalizer optimization and any new tie-ranking convention. Earlier tests validated the components separately. This pass builds and validates their combination before measuring it.

Parallel source investigations examine safe correction during labeling and reuse of temporary dominance buffers. Each alternative has its own worktree and patch. Neither becomes the selected comparison candidate merely because a component benchmark is favorable. Candidate selection uses correctness gates and nontrivial size-100 solver observations against the minimally corrected Lera reference, retaining unfavorable outcomes.

The 36-cell CPLEX screen compares minimally corrected Lera, integrated candidate `327e481`, dominance scratch reuse `e27f288`, and safe correction `a63cf65` with its explicit diagnostic switch. Each of C201, RC201 and C104 has 3 repetitions, 600 s per cell and a 16 GiB limit. Runs on the same instance and repetition use the same physical core in a balanced sequential order. Job `7013804` runs this screen on `grvingt-2`, with no concurrent compilation on that node.

The first 24 completed C201 and RC201 cells favor safe correction. Mean closure times for minimally corrected Lera, integrated Kayros and safe correction are respectively 203.654 s, 145.782 s and 98.841 s on C201, and 165.349 s, 202.495 s and 159.173 s on RC201. Safe correction reduces recorded search work in all 6 pairs against integrated Kayros. Scratch reuse preserves the recorded work and has no clear speed benefit. C104 remains under observation, and these partial results do not establish parity across the development subset or the complete Dabia2013 size-100 set.

Candidate `9d64b7c53ddaea7a578d247ebd28db43ae0d4e56` makes the tested correction path the default and rejects its unsupported eager-extension combination before changing solver state. It retains the point-addition repair and ordered reductions, and excludes scratch reuse and unmeasured wrapper changes. The correction repairs preserve retired-label ownership, restore cost ordering, refresh affected merge representatives and defer correction until the new label passes the forward boundary check. This candidate is distinct from screened `a63cf65` and must pass its own native and solver validation before the 1-hour panel. Both CPLEX and HiGHS builds preserve the ILS extension hash `b527271e7224fbd1b14b6ed84de49098395e672172ab038a2cc45258572287f3`.

Job `7013882` reserves a separate `grvingt-4` node for 1 hour to build and validate the default-on candidate. CPLEX and HiGHS builds complete successfully. The native guards, checked routes, cross-arm feasible bounds and deterministic ILS handoff remain validation gates. Separate source investigations examine sampled extension costs, the inherited CPLEX domain-conversion overhead and the different internal numerical tolerances. Their patches are diagnostic alternatives and do not silently change the selected source or minimally corrected Lera control.

The original main campaign stays held while this explicitly authorized development panel proceeds. Broader-family preservation remains a source-level constraint, while new cross-family evaluations wait for the later work requested by Onyr.

## Selected candidate and launched panel

The final screen contains all 36 scheduled cells, with 24 checked closures on C201 and RC201 and 12 C104 deadlines. Its independent audit checks 168 bound claims and 24 closure claims without finding an artifact failure, contradiction or unsupported closure. No C104 cell emits a checked incumbent or global bound within 600 s. The [screen report](../outputs/ms58/dabia-20261110/screen-v1/analysis/README.md) and its figure remain a separate grvingt cohort.

Candidate `9d64b7c` passes its own 4-case Dabia validation, including both deterministic ILS handoff checks, and all native correction fixtures. Both cold solvers report checked closures on all 4 validation cases. The managing agent selects this candidate for the explicitly authorized development panel, without a broader performance or proof-completeness claim.

Onyr approves 45 gros nodes for 1 h 20 min and subsequently authorizes using available compute while forbidding comparisons of curves obtained in different environments. Job `7013928` starts at 10:12:27 CET. All 45 hosts pass a common hardware, kernel, topology and runtime preflight, with cohort SHA-256 `682d2d3bbf010438de23b8c887cd6995a678f132b3b5b32f30a947c7b687ff4d`. The inspected slots have SHA-256 `c596aed2d47ec8a69ef87a28bde4eb07968212662061d40687f7e51d232cfa69`. This evidence is copied and bound into the frozen wave. Every run receives its own physical core and 16 GiB address-space limit.

The 180-cell wave launches at 09:18:20 UTC on 2026-11-10 under `outputs/ms58/dabia-20261110/gros-five-arm-1h-v1`. All 45 node controllers start successfully. The source remains frozen for every Kayros arm. The 5 arms, references, seeds, 3,600 s horizon and complete scheduled denominators remain as specified above. Curves from this allocation will not be combined with the earlier grvingt screen.

The separate extension diagnostic finishes 6 cells on grvingt-4. In a completed difficult C104 Exact pricing call, the native log attributes 127.407 s of 232.564 s to dominance and 61.181 s to extension. Calls that fail to eliminate their candidate consume 92.199 s of the dominance total. This profile uses integrated source `327e481` with correction disabled, so it motivates a correction-enabled dominance diagnostic rather than directly characterizing selected `9d64b7c`. The completed RC201 diagnostic matches recorded control work, but the profile does not store ordered route pools. Its C104 counterpart remains censored.

Follow-ups include a fixture-validated CPLEX domain-batching candidate `cd5dee0`, a separately built Lera numerical-tolerance diagnostic `fe8e927`, and a one-line removal of discarded restriction computation in Kayros `2ea97a0`. None changes the active five-arm wave or the fixed `f2c9e20` reference. Domain-batching fixture commit `1f5102b` repairs an ambiguous test helper name only. The 3 preceding native-fixture failures remain preserved. Runtime performance of these follow-ups is still pending at this checkpoint.
