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

The original main campaign stays held while this explicitly authorized development panel proceeds. Broader-family preservation remains a source-level constraint, while new cross-family evaluations wait for the later work requested by Onyr.
