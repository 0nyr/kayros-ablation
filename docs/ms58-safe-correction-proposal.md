# Kayros correction candidate

Production changes `a63cf65` and test-only follow-up `151a4ef45595eb0d9c29dbc4df1971c978779064` are on branch `proof-speed/safe-correction` in [kayros-proof-safe-correction]($WORKSPACE/kayros-proof-safe-correction), based on integrated canonical and point-arithmetic candidate `327e481713e1b9a3187fda2fc9f10f8d9d75b0c5`. They exclude the normalization optimization. The source is prepared for review and compute-node validation. No build, fixture run or solver experiment has been performed for this candidate by its authoring agent.

The [upstream correction audit](ms58-upstream-correction-audit.md) identified a reproducible workload difference, together with stale ordering and missing ownership in the inherited correction path. The proposed candidate repairs those local invariants before considering correction as a Kayros optimization. It does not establish a complete pricing-correctness argument or a performance benefit.

## Scope and default

Correction remains disabled by default. The diagnostic environment variable `KAYROS_DIAGNOSTIC_CORRECTING` accepts only `0` or `1`. Invalid values throw before a solve proceeds. The native result's `effective_algorithm_settings.label_correction` records the selected setting. No ILS source, common evaluator, Python API, coefficient arithmetic, numerical tolerance, deadline policy or memory limit changes.

All new comparative solver series must include the minimally initialized Lera reference `f2c9e20`. The raw historical Lera binary is retained as archived evidence and is not an arm in new comparative series. Current solver experiments are restricted to Dabia2013 with n=25, n=50 or n=100 and CPLEX. Native synthetic fixtures are separate invariant and exhaustive small-pricing tests. The managing agent owns reservations, builds and launches.

## Proposed changes and invariants

1. Fully corrected labels move from `U` to a retired-label owner. They remain alive while queued children, processed descendants or merge pools can reference them. `Clean` deletes both surviving `U` labels and the retired labels exactly once. The ordinary labels stay in their existing ownership structure, and correction-off creates no retired-label allocations.
2. When partial correction changes a stored label's minimum cost, its affected `U` demand bucket is stable-sorted immediately if cost sorting is enabled. The next domination test in the same monodirectional batch therefore sees a valid ordering. With cost sorting disabled, the existing unsorted dominance behavior remains available.
3. Correction records only affected `(vertex, demand)` merge buckets. After the monodirectional batch returns, the bidirectional driver deduplicates these keys, removes empty labels from the affected merge buckets, and stable-sorts the remaining pointers by minimum cost before inserting new processed labels or exposing the index to the opposite direction. Stable sorting retains the existing relative order of exact ties.
4. Empty corrected labels are skipped when inserting into the merge index and when used as forward merge labels or final-arc parents. The direct merge routine also rejects empty operands. Removing an empty duration cannot remove a feasible departure represented by that label.
5. A new label can move beyond the current middle boundary after domination. The existing driver requeues that extension and deletes the temporary label. Correction now runs after this boundary check, so a deferred temporary label cannot prune stored labels before it is retained for processing. This deliberately differs from the inherited correction behavior and may reduce pruning.
6. Heuristic Cost pricing returns immediately from correction, matching the inherited no-op semantics of its relaxed cost check. The existing normalization and piecewise-linear dominance arithmetic are unchanged.

The `U` repair is timed within correction. Merge-index maintenance contributes to merge time. Retired-label cleanup occurs in the existing cleanup phase, before the per-call labeling timer starts, so whole-solver and pricing-callback wall times remain necessary when interpreting performance. The extra sorting and bookkeeping may outweigh the reduced search work.

## Exact patches

The incremental patch from `327e481` through the latest fixtures is `build/ms58/safe-correction-on-best.patch`, with SHA-256 `8986cdf11df313a5088c86e81814dfba79182b77975c391fab6dfbbd1080e250`. The combined patch from corrected Kayros reference `0787925` is `build/ms58/safe-correction-full.patch`, with SHA-256 `ebe0cfdbb78ede75a7e4076c49bdb98d47da4254f303eea06cf4f0149c69a64d`. Both files are available in the candidate worktree and the experiment workspace's `build/ms58` directory. The latter is a complete candidate delta only after the frozen patches leading to `0787925` have been applied.

## Focused validation

The first [native fixture]($WORKSPACE/kayros-proof-safe-correction/tests/cpp/test_label_correction.cpp) calls the actual correction, domination, merge refresh, merge and monodirectional run methods. It checks the previously witnessed stale-order counterexample, retention of its negative reduced cost route, ownership of fully corrected parents, rejection of an empty parent's lazy child, cleanup of retired ownership, preservation of the full-only correction behavior and deferral before correction at the middle boundary. The boundary test uses a lazy queue key that predates partial correction of its parent's domain, then resumes the extension after moving the middle. Limited extension is disabled in that fixture to exercise this boundary branch. It covers both arithmetic modes and both cost-sorting settings.

The second [native fixture]($WORKSPACE/kayros-proof-safe-correction/tests/cpp/test_label_correction_pricing.cpp) performs 128 exact-pricing calls on synthetic 4-customer constant-arc networks. An independent enumerator sums arc costs over all elementary routes allowed by capacity and one forbidden arc, including a subset-row-cut dual contribution. It checks that completed pricing retains the best negative route or returns an empty pool when no negative route exists. It varies correction, partial dominance, opening/closing merge state, cost sorting and arithmetic mode, and requires positive aggregate correction coverage. Repeated calls on the same labeling object exercise cleanup between pricing tasks. These synthetic fixtures are not solver runs on an additional benchmark family.

Independent review found an inherited eager-extension hazard outside the current screen. The middle-boundary requeue creates a `LazyLabel` without initializing its eager `extension` pointer, then deletes the previous label. Resuming that queue with `lazy_extension=false` can read an indeterminate pointer. Eager queued-extension ownership on interruption also needs separate treatment. The managing agent kept production `a63cf65` unchanged for the bridge's default `lazy_extension=true` screen. This candidate does not repair, validate or claim safety for eager extension. The initial stopping eager fixture was replaced by the actual lazy deferral-and-resumption fixture in `151a4ef`.

Build the CPLEX candidate in a separate reserved-node source and build directory using the established wheel procedure. Compile each native fixture against that candidate's static `lera` library with the same compiler and module environment. A direct release-fixture command has the following form.

```bash
test -n "${OAR_JOB_ID:?}"
module load boost
cd "$HOME/ms58-bpca/sources/safe-correction"
mkdir -p build/ms58/native
for fixture in test_label_correction test_label_correction_pricing; do
    c++ -std=c++23 -O3 -DNDEBUG -ffp-contract=off \
        -Icpp/lera/goc/include -Icpp/lera/nyr/include \
        -Icpp/lera/main/include -Icpp/lera/magic_enum/include \
        "tests/cpp/$fixture.cpp" build/cplex/cpp/lera/liblera.a \
        -ldl -lm -o "build/ms58/native/$fixture"
    env -u KAYROS_DIAGNOSTIC_CORRECTING "build/ms58/native/$fixture"
done
```

Run the ownership fixture again against a separately instrumented AddressSanitizer/UndefinedBehaviorSanitizer library with leak detection enabled. This checks that deferred pointers remain valid until cleanup and that retired labels are released. Instrumenting only the fixture does not cover all library reads, so it is weaker than instrumenting the library as well.

Before correction-on timings, compare the rebuilt correction-off candidate with its `327e481` base on allowed Dabia instances and verify work signatures, checked incumbents and unchanged ILS extension identity. Every new comparative series also includes `f2c9e20`. Then compare correction-off and correction-on with fixed limits and sequential randomized order, recording correction counts, labeling work, LP and column counts, reported closure, checked incumbents, memory and failures. C104 needs a horizon that can reach its decisive pricing calls. A 300 s or 600 s timeout is not evidence of closure improvement. A full Kayros comparison must preserve the approved heuristic prefix and seeds.

The inherited dominance rules, unreachable sets, function normalization, cut-resource logic and final-arc completeness argument still require validation as a whole when correction is enabled. The local repairs do not justify enabling correction globally, assuming that all historical Lera closures were sound, or trading away Kayros's broader supported model behavior. Default activation remains pending the managing agent's correctness and performance review.
