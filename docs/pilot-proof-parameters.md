# Pilot selection, computational closure and matched settings

Status: matched implementations are prepared and the 1-hour, 3-trial pilot is running. The main campaign remains held for review. The author approved process-launch prefix deadlines and ranking by restricted mean reported computational-closure time, then mean primal Λ, then shorter prefix. Per-wave manifests are authoritative for executed cells.

The public design authority is [the protocol](protocol.md), with immutable source and reference identities in [source pins](../configs/source_pins.json).

## Deterministic pilot identities

Run `nix develop base --command python3 scripts/select_pilot.py --output configs/pilot-selection.json` from this repository. The script verifies the immutable reference-manifest digest and every original source-reference hash before reading historical labels. It consults no new runtime observations. For each of C1/C2/R1/R2/RC1/RC2, reserve RC105 in RC1, choose a lexicographically first instance from each missing historical stratum, then fill lexicographically to 2 identities. A stratum is `metadata.optimality.proven is true` or its complement. The complement means no frozen declaration, not evidence that optimality is unknown in every source.

The frozen panel is C101, C104, C201, C202, R101, R112, R201, R202, RC105, RC104, RC201 and RC202. `configs/pilot-selection.json` carries per-instance input/source-reference hashes and complete stratum counts. C1, R1 and RC1 each contain both strata. C2, R2 and RC2 contain no declared-proven records and therefore use the explicit fallback. RC105 supplies the requested numerical corner without selecting on current outcomes. Historical proven labels are neither current proof acceptance nor a measured easy/hard classification.

## Source-based closure audit and stricter proof record

### Current operational observation

The author clarified the numerical scope before the RC105 guard-counter investigation: baseline comparison follows the declared Lera/Kayros computational conventions, without requiring formal proof objects. `reported_closure_with_checked_witness` records a source-reported search closure within the common horizon whose latest preceding incumbent passes the independent pinned v0.11 checker. The summary also requires a usable completed/deadline record with no harness integrity issues. An outer deadline after an already recorded closure does not move that event's timestamp. A deadline, memory limit, node limit or interrupted search alone supplies no closure event.

Every event remains bound to its immutable cell, input/ATF hashes, runtime/patch manifests, repetition, actual seed, prefix and process-launch horizon. Native and checked objectives remain separate. The existing algorithmic EPS of 1e-6 and backend LP primal/dual tolerances of 1e-7 describe the computation. Small native/checker differences are retained without inventing another acceptance tolerance or clipping a contradictory bound. A demonstrated material wrong result, invalid terminal witness or material bound/objective contradiction requires investigation before the affected headline interpretation or main launch. A source concern alone does not establish such a defect.

Execution diagnostics accompany the observation. These include exact-pricing and CG closure censuses, restricted-master infeasibility, unbranched fractional nodes, rejected priced columns, numerical statuses and resource exits. They are not silently converted into formal proof obligations or erased when a run closes. In particular, a nonzero `checker_infeasible_columns` value is an explicit native certificate-poison guard. A closure carrying it is a flagged operational observation, not an unqualified clean computational proof. Its final headline treatment requires a source/route assessment. The [RC105 guard note](ms56-rc105-guard.md) separates the observed rejection from any unproved completeness hypothesis.

Warm exact search may close without returning improved routes. The full arm then retains its actual in-budget prefix witness, including routes, and checks that witness afterwards. An upper-bound scalar without the witness does not satisfy the observation criterion. The independent checker establishes feasibility and cost, while pricing completeness and tree exhaustion remain properties of the recorded implementation. The manuscript's separate 4-run publication/store-admission protocol is not imposed on each pilot observation.

### Historical stricter `accepted_proof` record

The preparation specification also defined a stricter per-run audit. Its raw `accepted_proof`/`proof_unverified` records remain unchanged. The operational observation above does not retrospectively satisfy that record or override a native certificate-poison flag. The stricter checks were:

1. Verify the immutable cell, complete input and runtime provenance, objective and effective numerical parameters.
2. Require a genuine in-horizon native search-closure event with the corresponding complete witness and preserved logs. Post-run checking supplies no solver event time.
3. Validate the full witness using pinned mamut-routing-lib v0.11, retaining warm-start routes when exact search returns no new routes, and audit the relationship between native and checked values.
4. Audit the global bound's provenance, including the active node, completed pricing and native numerical comparisons. Reference attainment or a small displayed gap alone supplies no search-closure evidence.
5. Check compact CG closure evidence against the pinned control flow: matching CG-optimum/certified-final-pricing counts, no unresolved final-pricing closures, and a completed Exact pass on true duals for the final unchanged formulation. Native no-new-column, duplicate and epsilon semantics remain explicit.
6. Require zero checker-infeasible priced columns where that guard is exposed. This preserves the native stricter certificate-poison requirement. Review integrity failures, numerical failures, skipped columns and other unresolved source diagnostics.
7. Preserve the audit outcome and reasons without validation timings. A single per-run audit does not automatically satisfy the separate 4-run store-admission gate.

The current comparison keeps these stricter records alongside operational observations. Neither the summary nor a call to Kayros's `optimality_metadata()` helper silently turns a source-reported status into that stronger audit outcome.

## Verified matched settings

The [artifact index](../configs/ms56-matched-artifacts.json) identifies the binaries and patches shared by the matched 300 s wave and the 1-hour pilot. Full per-wave manifests bind the Python modules, extensions/native executable, original native input files, runtime libraries, orchestration, checker and environment. The [preparation audit](ms56-probe-status.md), [upstream implementation](upstream-preparation.md) and [native work comparison](ms56-native-work-comparison.md) retain the effective-setting and representation evidence. The 1-hour prelaunch audit confirmed the matched-wave runtime and patch bytes were unchanged.

| Control | Executed setting | Evidence and scope |
|---|---|---|
| CPLEX library | CPLEX 12.10 for upstream, cold Kayros and full Kayros | Runtime readback and pinned build/library inventories. |
| HiGHS library | Source release v1.14.0 selected by the pinned Kayros build | Resolved source/build identity retained in its inventory. This is the additional cold backend arm. |
| Threads and affinity | 1 solver thread and 1 fixed allowed CPU per cell | Explicit backend configuration and launch identity. |
| Subset-row cuts | Cap 500, `epsilon_bigger(best_violation, 0.1)` | Both raw pinned upstream and Kayros use 0.1. The earlier recommendation to restore a positive-zero threshold was superseded by the [raw source audit](../configs/upstream-source-audit.json). |
| Algorithmic settings | Same Kayros cold CPLEX/HiGHS labeling, cut, pruning and native pricing-heuristic controls | Solution limit 3,000, NG pricing off, dual stabilization off. Full-arm exact settings match cold Kayros CPLEX apart from its own witness and remaining budget. |
| Numerical settings | Algorithmic EPS 1e-6, LP primal/dual tolerances 1e-7, MIP integrality/feasibility setting 1e-6, MIP relative gap 0 | Effective readback follows internal overrides. Backend parameter semantics remain documented rather than claimed identical. |
| LP method and preprocessing | Native backend method, presolve, reduction, scaling and related settings recorded | CPLEX LP reduction is explicitly read back as 0. HiGHS retains its persistent LP session. These backend-specific implementation differences remain visible. |
| Randomness | Paired ILS seeds 42/123/456 for trial IDs 1/2/3 | Cold exact search seed is null. Native backend seed settings are recorded separately. A trial ID does not inject randomness into exact search. |
| Resource shape | Common 8,192 MiB address-space cap and fixed single-core affinity in the matched pilot | Per-node concurrency is RAM-checked. Native child RSS is sampled separately from the Python worker's memory. |
| Budget | 3,600 s from process launch, including imports/loading/setup, prefix, handoff and exact search | Prefix deadlines also originate at process launch. External route checking occurs after solving and has no recorded or reported duration. |

The full 56-instance native IGP/materialized-ATF representation audit passed under the pinned checker loader, with approximately 3.64e-12 maximum sampled arrival discrepancy across more than 10 million union-knot/midpoint samples. Late native-domain exclusions cannot arrive within the shared TDVRPTW horizon. This supports the executed numerical comparison without asserting bit-identical route accumulation in upstream.

## Approved global prefix selection

Compare 15, 60, 180, 600 and 1,800 s with 3 paired ILS seeds (42, 123, 456), 12 fixed identities and one 3,600 s total horizon per cell. First invalidate every affected candidate configuration if a correctness defect is discovered. Repair and rerun the paired affected development cells under a new manifest before ranking.

Rank valid candidates lexicographically by (1) minimum restricted mean reported computational-closure time over all 36 cells, treating every cell without a qualifying reported closure as 3,600 s, (2) minimum mean primal Λ over the same denominator, then (3) shorter prefix duration. Calculate within-instance means before the equal-instance aggregate. No feasible-only denominator or per-instance prefix choice is allowed. Keep final squeezed gap and feasibility as descriptive secondary outcomes. Report operational closure counts and native guard diagnostics alongside restricted mean reported-closure time, without using counts as a primary ranking key. The raw stricter proof fields remain separate. This rule prioritizes computational closure speed while making incumbent quality a prespecified tie-break. It does not claim independent tuning-validation evidence. Select the final prefix after pilot review under this approved rule. Prefix deadlines are measured from process launch, including setup.
