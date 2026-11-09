# Experiment protocol

On 2026-11-09, Onyr rejected the pilot-based main launch because Kayros's incumbent-quality improvement does not meet the intended proof-speed objective. The main campaign is held for solver improvement under the [follow-up plan](proof-speed-improvement-plan.md) and [current status record](../configs/campaign-status.json). The [main review](main-campaign-review.md) and [1,400-cell proposal](../configs/main-proposal.json) remain historical preparation artifacts. The proposal is nonlaunchable, and no main results exist. The [full 16 GiB report](ms56-hour-pilot-16g.md) and [focused report](ms56-focused-pilot-16g.md) retain the measured evidence. The protocol below records the existing comparison contract, which remains the starting point for development.

## Preparation status

The 5 arms and fixed prefix candidates are agreed, and the 56-reference snapshot is frozen, checked and published. The repository records the immutable v0.11 checker source and installed-source identity, the analysis dependency lock and the exact reference manifest digest. Reference checks are preparation evidence, not solver-performance results.

The main campaign remains unlaunched. Source-pinned builds, process-launch instrumentation, interrupted-bound repair, closure diagnostics, the 12 pilot identities and matched effective backend settings are implemented and frozen per wave. The approved criterion below selects 15 s among the pilot prefixes. The historical proposal records 7,200 s and 16 GiB while retaining `launchable: false`. An improved solver must receive new source/runtime identities and comparative validation before a revised main design is reviewed. Neither the source-pins snapshot nor the archived proposal authorizes execution. The computational-closure scope at the end of this document governs interpretation of the earlier proof terminology.

## Approved scope

Use Kayros v1.6.1 with separately documented necessary fixes, the original upstream Lera-Romero source, and classic mamut-routing-lib v0.11.0 for all cost evaluation. Main performance comparisons use the 56 original Dabia2013 TDVRPTW instances with 100 customers and the Duration objective. Preserve the materialized original distance/speed conventions and verify representation equivalence across solver adapters.

Compare 5 arms: original Lera-Romero exact solver with CPLEX, cold Kayros exact with CPLEX, cold Kayros exact with HiGHS, standalone Kayros ILS, and an ILS prefix followed by warm-started Kayros exact with CPLEX. Charge the prefix and handoff to the full arm's single total budget. The full arm receives only its own in-budget incumbent. Stored reference solutions do not initialize the headline cold arms. Keep native pricing heuristics inside the exact solvers.

Use CPLEX for the main exact comparisons to match the original solver's backend. This is a choice of experimental control, not a claim that CPLEX is faster. The additional cold Kayros HiGHS arm isolates backend replacement with matched algorithmic parameters, cut settings, thread limits and comparable numerical settings. Record backend-specific settings and any unavoidable differences. No algorithmic addition bundles, finer component ablations or separate cut-setting interventions are planned. Provenance documentation still distinguishes reused methods/code, adaptations, correctness repairs and new components. Known incorrect variants can supply regression evidence but cannot receive successful proof credit.

Initial correctness tests may use n25/n50 subsets, with 1 or 3 trials. A 1-hour pilot on 12 n100 instances precedes a provisional 2-hour main campaign on all 56. Main arms have 5 trials per instance. The configuration fixes the ILS seeds to 42, 123, 456, 789 and 2026, paired between heuristic and full arms. Record cold exact trials as timing replications where the API has no random-seed control. Do not claim that changing a metadata seed changes a deterministic algorithm.

With 5 arms, 56 instances and 5 trials, the final design has 1,400 cells and at most 2,800 solver core-hours at a 2-hour horizon. This counts one selected full-arm prefix configuration. The pilot's prefix comparison is additional development work.

## Proof time and validation

Record the event at which the solver declares completed optimality proof, using the declared solver clock. External validation happens afterwards. Its elapsed time is excluded from all reported times and will not receive a separate timing table or metric. A rejected proof claim remains an unsuccessful proof outcome, while any valid incumbent is retained for primal evaluation.

Solver-internal feasibility checks, numerical operations and integrity guards remain part of the computation being measured. Post-run route validation cannot establish pricing completeness. Preserve solver termination, exact-pricing evidence, bounds, witness routes and numerical parameters to support the declared acceptance checks. Store-admission protocols remain distinct from one run's proof-completion event.

The common clock starts at process launch with the environment prepared. It includes imports, instance loading, preprocessing and the complete heuristic/exact computation, and excludes post-run external checking. Rebase native BPCA timestamps that begin after preprocessing onto this common clock. Events genuinely after the horizon do not earn success through timestamp clamping.

## Reference snapshot and heuristic prefix

Freeze the latest available reference solutions before the pilot, after complete validation and cost evaluation under the pinned v0.11 checker. Retain reference routes, original records, instance identities, input hashes and checker provenance. Use the same immutable snapshot across all arms, prefix candidates and deadlines. Later improvements may give negative primal gaps. Stored optimality annotations remain provenance separate from the primal reference cost and do not prove a new run's optimality. The [snapshot](../references/dabia2013-n100-v011-2026-11-09/README.md) is constructed and independently replayed: all 56 references are valid, with 11 source-declared costs repriced and every route unchanged. Its manifest SHA-256 is `ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66`. Campaign consumers must pin that digest rather than accepting a replacement manifest.

Compare fixed heuristic prefixes of 15, 60, 180, 600 and 1,800 seconds in the pilot. Handoff to CPLEX BPCA occurs at the selected prefix deadline. No convergence or stagnation detector changes this schedule. Pair ILS seeds across prefix candidates and standalone heuristic runs, and choose one final full-arm configuration before the main campaign.

## Primal metrics

For incumbent cost `z` and positive reference cost `z_ref`, compute the squeezed gap `(z - z_ref) / (z + z_ref)`. It is 1 before any feasible incumbent. Retain the best-so-far envelope of checker-valid candidates. Transform each run before averaging, give instances equal weight, and average the 5 trials within each instance.

Λ is the exact step-curve integral of that squeezed gap over the common elapsed-time horizon, divided by the horizon. Use uniform time weighting, including when a plot has a logarithmic time axis. No incumbent gives Λ = 1. Improvements below the reference may yield negative gaps. The main raw-gap goals are frozen at 0%, 1% and 5% in [the main-analysis specification](../configs/main-analysis.json). A checked incumbent attains a goal when its raw gap `(z - z_ref) / z_ref` is at most that goal. Missing incumbents attain no goal and remain in the denominator. Existing 1% pilot plots remain scoped pilot summaries. A zero reference gap is reference attainment, not proof.

Report mean Λ, mean final squeezed gap, feasible-run counts and reference-attainment counts. A finite final raw-gap average restricted to feasible runs must identify its conditional population. Unsuccessful runs remain in the main denominator.

## Exact metrics

The proof-attainment curve gives the fraction of all trials that complete an accepted proof by each deadline. Heuristic-only search has no proof capability. Restrict proof rankings to the 4 exact-capable arms, while retaining all 5 arms in incumbent and status comparisons.

The restricted mean time to proof is the area under the unproved fraction over the common horizon. Equivalently, average each proof time capped at the horizon, assigning the full horizon to runs without an accepted proof. Report the horizon and proof count alongside this scalar. It does not estimate eventual uncensored mean runtime and is not Λ.

A proposed bound-gap diagnostic is `(U - L) / U` for positive finite incumbent `U` and a compatible valid global lower bound `0 <= L <= U`. Use an explicit initial convention, such as residual 1, before a useful pair exists, and expose bound availability. Incomplete root pricing does not justify treating the restricted-master value as a global lower bound. Contradictions are integrity events. A near-zero displayed gap is not a substitute for solver proof completion.

Preserve fixed instance ordering in status matrices. Distinguish no valid incumbent, feasible but unproved, and accepted optimality proof, with optional reference-attainment annotation. Mark crashes, memory limits and rejected claims separately. For 5 trials, show proportions or clearly labelled trial panels, not a selected best run.

The mean compatible primal-dual residual, explicit bound availability and aligned per-instance status matrix are main-analysis deliverables. Their implementation is not a pilot-completion or main-launch gate. The frozen analysis specification travels with the nonlaunchable main schedule and concrete proposal.

## Reproducibility requirements before a campaign

Freeze source commits, patch digests, compiler and dependency versions, binary hashes, LP parameters, inputs and materialized travel-time data, reference routes/costs, seeds, time/thread/memory limits and full arm configurations. Verify the v0.11 checker identity before any real cost computation. The current pins file does not yet satisfy these requirements.

Use explicit one-thread settings for every LP backend. Both matched pilot backends now set and read back the effective one-thread configuration. Enforce per-worker memory limits and RAM-aware parallelism. Result identity includes the manifest and parameter digest, and resumption checks identity and completion rather than filename existence alone.

Preserve streamed candidate routes and timestamps, native and checked objective values, valid global-bound events, proof completion, final status and pricing diagnostics. Warm-start proof without a new incumbent must retain the actual seed witness. Validate event recovery after forced termination before a pilot.

## Open choices

1. Select the global prefix from the completed pilot by minimum restricted mean computational-closure time, then lower mean Λ on a tie, then the shorter prefix. This ordering is author-approved. Computational closure follows the declared-convention scope below.
2. Decide the common main horizon and resource shape from completed pilot trajectories, memory observations and available capacity. Review the results with the author before launch.

The prefix deadline is measured from process launch, as confirmed by the author. All setup and handoff work remains inside the common process-launch horizon.

Process-launch timing, the pre-pilot reference policy, the 5-arm design, fixed prefix scheduling and exclusion of finer algorithmic ablations are settled. The reference snapshot is frozen and independently replayed. Campaign consumers must retain its pinned identity.

The main study cannot demonstrate general exact-solver scalability or every stepwise/numerical repair. Separate correctness witnesses document changes outside the n100 panel's scope.

## Implementation ruling (ms56)

The fixed heuristic prefix deadline is measured from process launch. Loading and initialization consume part of the prefix, and all phases share the total process-launch deadline. This origin was confirmed before prefix probes. The main campaign remains held for author inspection of probe results and recommendations.

## Computational closure scope (ms56 phase 2)

The baseline comparison follows the declared Lera-Romero/Kayros numerical and algorithmic conventions. Its scientific question does not require formally verified proof objects. The manuscript defines a computational certificate as search closure under canonical binary64 route evaluation, backend LP tolerances and vendored pricing epsilon comparisons. Post-run route checking verifies feasibility and cost, not the search tree.

Following the author's clarification, intermediate analyses report solver-reported closure with a checked in-budget witness, alongside closure census and integrity diagnostics. This observational series is distinct from the stricter independent audit field in early probe artifacts. A source-level suspicion is recorded and investigated without automatically blocking pilots. A demonstrated major wrong result, invalid witness, contradictory bound or known unsafe execution requires repair and re-evaluation before the main campaign. Do not erase or conceal such evidence to support the superiority hypothesis. Manuscript and appendix claims must retain this computational scope.
