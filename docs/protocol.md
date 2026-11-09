# Experiment protocol

Status: design in progress. This protocol distinguishes approved choices from proposals. It contains no measured solver results.

## Approved scope

Use Kayros v1.6.1 with separately documented necessary fixes, the original upstream Lera-Romero source, and classic mamut-routing-lib v0.11.0 for all cost evaluation. Main performance comparisons use the 56 original Dabia2013 TDVRPTW instances with 100 customers and the Duration objective. Preserve the materialized original distance/speed conventions and verify representation equivalence across solver adapters.

Compare 4 arms: original exact solver, cold Kayros exact component, standalone Kayros ILS, and an ILS prefix followed by warm-started Kayros exact search. Charge the prefix and handoff to the full arm's single total budget. The full arm receives only its own in-budget incumbent. Stored reference solutions do not initialize the headline cold arms. Keep native pricing heuristics inside the exact solvers.

Add controlled comparisons matching LP backend and cut settings. Exact intermediate components require a source and patch-dependency audit. Preserve the distinction between reused methods/code, adaptations, correctness repairs and new components. Known incorrect variants can supply regression evidence but cannot receive successful proof credit.

Initial correctness tests may use n25/n50 subsets, with 1 or 3 trials. A 1-hour pilot on 12 n100 instances precedes a provisional 2-hour main campaign on all 56. Main arms have 5 trials per instance. Use 5 actual ILS seeds for heuristic and full arms, paired by trial. Record cold exact trials as timing replications where the API has no random-seed control. Do not claim that changing a metadata seed changes a deterministic algorithm. The actual seed identifiers will be frozen in the manifest.

## Proof time and validation

Record the event at which the solver declares completed optimality proof, using the declared solver clock. External validation happens afterwards. Its elapsed time is excluded from all reported times and will not receive a separate timing table or metric. A rejected proof claim remains an unsuccessful proof outcome, while any valid incumbent is retained for primal evaluation.

Solver-internal feasibility checks, numerical operations and integrity guards remain part of the computation being measured. Post-run route validation cannot establish pricing completeness. Preserve solver termination, exact-pricing evidence, bounds, witness routes and numerical parameters to support the declared acceptance checks. Store-admission protocols remain distinct from one run's proof-completion event.

The precise common clock origin remains to settle. The proposed clock includes loading/preprocessing and the complete heuristic/exact computation, but excludes post-run external checking. A native BPCA timer beginning after preprocessing must not silently be compared with another arm's broader timer. Events genuinely after the horizon do not earn success through timestamp clamping.

## Primal metrics

For incumbent cost `z` and positive reference cost `z_ref`, compute the squeezed gap `(z - z_ref) / (z + z_ref)`. It is 1 before any feasible incumbent. Retain the best-so-far envelope of checker-valid candidates. Transform each run before averaging, give instances equal weight, and average the 5 trials within each instance.

Λ is the exact step-curve integral of that squeezed gap over the common elapsed-time horizon, divided by the horizon. Use uniform time weighting, including when a plot has a logarithmic time axis. No incumbent gives Λ = 1. Improvements below the reference may yield negative gaps. Proposed raw gap goals are 0%, 1% and 5%. A zero reference gap is reference attainment, not proof.

Report mean Λ, mean final squeezed gap, feasible-run counts and reference-attainment counts. A finite final raw-gap average restricted to feasible runs must identify its conditional population. Unsuccessful runs remain in the main denominator.

## Exact metrics

The proof-attainment curve gives the fraction of all trials that complete an accepted proof by each deadline. Heuristic-only search has no proof capability. Restrict proof rankings to exact-capable arms, while retaining all 4 arms in incumbent and status comparisons.

The restricted mean time to proof is the area under the unproved fraction over the common horizon. Equivalently, average each proof time capped at the horizon, assigning the full horizon to runs without an accepted proof. Report the horizon and proof count alongside this scalar. It does not estimate eventual uncensored mean runtime and is not Λ.

A proposed bound-gap diagnostic is `(U - L) / U` for positive finite incumbent `U` and a compatible valid global lower bound `0 <= L <= U`. Use an explicit initial convention, such as residual 1, before a useful pair exists, and expose bound availability. Incomplete root pricing does not justify treating the restricted-master value as a global lower bound. Contradictions are integrity events. A near-zero displayed gap is not a substitute for solver proof completion.

Preserve fixed instance ordering in status matrices. Distinguish no valid incumbent, feasible but unproved, and accepted optimality proof, with optional reference-attainment annotation. Mark crashes, memory limits and rejected claims separately. For 5 trials, show proportions or clearly labelled trial panels, not a selected best run.

## Reproducibility requirements before a campaign

Freeze source commits, patch digests, compiler and dependency versions, binary hashes, LP parameters, inputs and materialized travel-time data, reference routes/costs, seeds, time/thread/memory limits and full arm configurations. Verify the v0.11 checker identity before any real cost computation. The current pins file does not yet satisfy these requirements.

Use explicit one-thread settings for every LP backend. HiGHS currently sets this internally, but the CPLEX wrapper must be configured explicitly. Enforce per-worker memory limits and RAM-aware parallelism. Result identity includes the manifest and parameter digest, and resumption checks identity and completion rather than filename existence alone.

Preserve streamed candidate routes and timestamps, native and checked objective values, valid global-bound events, proof completion, final status and pricing diagnostics. Warm-start proof without a new incumbent must retain the actual seed witness. Validate event recovery after forced termination before a pilot.

## Open choices

1. Clock origin: process launch with a prepared environment, solver API entry before loading/preprocessing, or a narrower native solve timer. Recommend a common end-to-end solver clock including preprocessing, with external checks outside it.
2. Reference snapshot: freeze current v0.11-validated BKS routes before the pilot, or score against a shared post-campaign reference. Recommend the pre-campaign snapshot for component attribution, permitting negative gaps.
3. Heuristic prefix: fix a duration directly or compare 15/60/180 seconds in the pilot. Recommend selecting one global duration from the pilot and freezing it before the main campaign.
4. Component granularity: reconstruct a small number of auditable addition bundles, or attempt a finer historical patch ladder. Recommend meaningful bundles after examining dependencies, with matched backend/cut comparisons retained.

The main study cannot demonstrate general exact-solver scalability or every stepwise/numerical repair. Separate correctness witnesses document changes outside the n100 panel's scope.
