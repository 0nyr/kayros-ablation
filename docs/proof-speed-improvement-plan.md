# Kayros proof-speed improvement plan

On 2026-11-09, Onyr rejected the proposed main-campaign launch because the pilots show an anytime incumbent-quality benefit without the required proof-speed improvement over Lera-Romero. We will identify and close that gap, then seek evidence of improvement before resuming the main experiment. The current [campaign status](../configs/campaign-status.json) supersedes the launch recommendation in the archived pilot review. The existing results and [main proposal](../configs/main-proposal.json) remain unchanged evidence.

This document prepares development. No solver modification, new build, reservation or experiment was started during this handoff. Development branches, GPT-6.1 Sol subagents and Grid'5000 experiments are authorized for the follow-up. The manuscript and experimental appendix remain pending a validated full experiment.

## Starting evidence

The [complete 16 GiB pilot](ms56-hour-pilot-16g.md) gives the following matched comparison over 12 instances and 3 trials per configuration at a 3,600 s horizon. Each metric includes all 36 scheduled cells.

| Configuration | Reported closures | Restricted mean closure time (s) | Mean Λ | Cells with a checked incumbent |
|---|---:|---:|---:|---:|
| Original Lera CPLEX | 21 | 1696.057 | 0.387223 | 27 |
| Cold Kayros CPLEX | 18 | 1861.272 | 0.442075 | 24 |
| Full Kayros, 15 s prefix | 19 | 1872.030 | 0.005857 | 36 |

The anytime improvement is valuable, but these observations do not establish the intended proof-speed improvement. Full Kayros's restricted mean closure time is about 10.4% higher than Lera's in this panel. Choosing 15 s among the tested prefixes does not establish that the selected configuration beats Lera.

The [focused 2-hour pilot](ms56-focused-pilot-16g.md) makes C104 the first diagnostic target. Lera closes all 3 C104 trials in approximately 1,893–1,954 s. Cold Kayros CPLEX closes none by 7,200 s, while cold Kayros HiGHS closes all 3 in approximately 3,462–3,866 s. This identifies a major difference worth tracing, without establishing whether its cause is the LP backend, the column-generation trajectory, pricing implementation or their interaction. The focused C104/RC202 cohort does not supply a matched 2-hour result for the other 10 pilot instances.

The [native-work comparison](ms56-native-work-comparison.md) supplies a second lead. On RC201, measured labeling time is 129.259 s for Kayros and 84.021 s for Lera, approximately 70% of the root-time difference. Kayros also creates more labels and columns. On R201, the first recorded 3,100-column LP objectives already differ. These are observations from short diagnostic runs, not a controlled attribution of the gap to one component. Direct bridge checking outside the labeling call cannot by itself explain the measured time inside that call.

The [full-panel plots](../outputs/ms56-phase3/repeat3600-16g-seeds3/analysis/) and [focused horizon plots](../outputs/ms56-phase3/focused7200-16g-seeds3/analysis/horizon-linear-overview.png) retain the unfavorable evidence. The [instrumentation review](instrumentation-overhead.md) measures the supplemental instrumentation it toggled, rather than all costs introduced by Kayros.

## Fixed controls and development isolation

Keep the original Lera control at `cabc56ad9cdb22bed81a8a31f1671d40152055e6` with the exact audited build and instrumentation patches recorded in [the matched artifact inventory](../configs/ms56-matched-artifacts.json). Do not substitute the downstream `TDVRPTW-solver` research fork or alter the control opportunistically during optimization. Preserve the current dirty upstream build checkouts as archived preparation artifacts.

Create an isolated Kayros worktree from `50eebc57964ac4f6762719ff21033783a31c12da` and apply the frozen Kayros patches `0001` through `0004` in order. The first development commit should reproduce this corrected, instrumented reference. Suggested branches are `proof-speed/reference`, `proof-speed/diagnostics` and one `proof-speed/<hypothesis>` branch per performance change. Record the base, patch digests, build options, compiler, dependencies and binary digests. Keep the existing main checkout and release worktree separate. Reproduce a small subset against the archived binary before attributing a timing change to new code.

Keep the v0.11 checker, reference snapshot, materialized input contract, Duration objective, process-launch clock, one-thread settings, memory limits, cut settings and declared numerical tolerances fixed in paired comparisons. Preserve feasibility checks, the conservative interrupted-node bound repair and honest pricing-closure semantics. An optimization cannot earn a speedup by dropping a required check, weakening numerical settings or reporting unfinished pricing as complete. If a necessary semantic repair changes the workload, document that difference explicitly.

Resource exhaustion remains a failed experimental outcome. Retain any valid incumbent, failure status and full scheduled denominator contribution. Do not rerun a failed cell merely to obtain a favorable result. The original 8 GiB failures remain part of the archived evidence.

## Diagnostic pass

We will separate greater search work from greater time for comparable work. The first cohort should include C104, RC201 and R201, plus C101/R101 for short-run overhead and C201 as a case where Kayros already performs well. HiGHS is a diagnostic contrast on C104, while matched CPLEX remains the primary exact-engine comparison.

| Question | Evidence to collect | Decision supported |
|---|---|---|
| Where does wall time go? | Loading and conversion, preprocessing, LP solves, pricing by level, column admission/checking, cut separation, strong branching and tree processing | Select the dominant measured component |
| Why does Kayros do more work? | Columns and their costs/order, duals, pricing-level transitions, generated/processed/dominated labels, function-piece counts where available, cut and branch states | Find the first reproducible trajectory divergence |
| Does identical work run more slowly? | Replay a captured pricing task with the same duals, resources, branch/cut restrictions and route-pool state where both representations admit it | Separate implementation cost from search-path effects |
| What happens before a timeout? | Bounded per-call records and atomic partial summaries tied to the outer process deadline | Observe C104's long pricing calls without relying on a final sidecar |

Begin with source comparison and compact 300/600 s traces. C104 trial 1 has long gaps between column-generation iterations, including approximately 4,160 s to 6,855 s late in the focused run, so a short trace may not reach the decisive state. Use targeted longer runs or replay when necessary. Short diagnostic speedups cannot establish closure parity at 3,600 or 7,200 s.

Trace initial column sets and objective coefficients before blaming a downstream algorithm. Compare the first differing dual/pricing input, pool ordering, native cost and bridge-repriced coefficient. Inspect the pricing ladder, dominance and piecewise-linear operations only against an explicit failing or expensive case. Keep missing counters missing, rather than recording zero work for an interrupted call.

Improve deadline propagation and diagnostic persistence on a separate branch. The existing outer watchdog correctly enforces the process-launch budget, but a native deadline that starts after conversion can prevent final diagnostics from being emitted before the watchdog kills the worker. Propagate the absolute remaining budget and publish partial records safely. Preserve the distinction between an interrupted call and completed exact pricing. Measure the overhead of any new instrumentation before using it for headline timing.

The first source entry points are Kayros's `cpp/lera/bridge/lera_bridge.cpp`, `cpp/lera/main/src/labeling/`, `cpp/lera/main/src/bcp/bcp.cpp`, the column-generation/LP implementation and `src/kayros/lera/__init__.py`. Compare them with the fixed upstream `code/src/main_bp.cpp`, labeling and BCP code. The current evidence does not identify a single proven root cause.

## Improvement and validation loop

1. State one hypothesis and the measurable effect expected from it. Keep the code change independently reviewable and reversible.
2. Add a focused regression or invariant check where the change can affect solutions, bounds, dominance, pricing completeness or deadline handling. Preserve known difficult numerical cases and the existing interruption checks.
3. Run matched-work comparisons on the same reserved core in randomized sequential AB/BA order. Record repetitions, placement, resource limits and instrumentation mode. A seconds-per-label ratio from different search trajectories is descriptive, not a controlled throughput experiment.
4. Run paired end-to-end solver comparisons against both the frozen Kayros reference and fixed Lera. This exposes changes in column generation, branching and closure that replay cannot capture. Use 3,600/7,200 s horizons where needed, with at least the existing 3 timing repetitions on the development panel before concluding that the main gap has closed.
5. Retain negative candidates and their results. Merge a change into the candidate only when its mechanism and correctness checks are understood, then repeat the comparison for the combined candidate.

Potential changes depend on the profile. Candidates include reducing repeated piecewise-linear computations, safe caching, dominance-data structures, avoidable column-pool work, conversion costs and an unintended ordering or coefficient difference that causes a worse search trajectory. We will not choose a broad rewrite or remove safety logic without evidence. Keep any algorithmic departure from Lera explicit, so the eventual contribution remains attributable.

## Integrating the exact engine with full Kayros

Cold CPLEX is the first development target because it exposes the exact-engine gap without the heuristic handoff. The intended final claim concerns full Kayros against Lera, with cold Kayros supplying the explanation and component comparison. First freeze an improved exact candidate, then measure full Kayros with the current 15 s prefix. Preserve the anytime benefit while checking whether warm-start columns or incumbent cutoffs alter proof performance adversely.

A fixed 15 s prefix imposes a lower bound on full-arm closure time. It cannot match an original-solver closure below 15 s under the current sequential design. Aggregate proof-speed improvement and per-instance dominance are therefore different targets. If this floor materially limits the result, discuss a shorter prefix, earlier handoff or a different cooperation design with Onyr before changing the approved fixed-prefix policy. Such a change requires a separate measured comparison, including its compute budget. It must not be introduced silently as a timing optimization.

If the improved engine warrants prefix retuning, retain the approved global ordering of restricted mean closure time, then mean Λ on a tie, then shorter prefix. Select the full configuration on development data and freeze it before confirmation. Do not choose prefixes by instance or retune against confirmation outcomes.

## Evidence required before returning to the main campaign

The primary recommendation is to require a lower restricted mean reported-closure time for full Kayros against the fixed Lera control at the same horizon and limits, supported by closure counts, paired instance differences and uncertainty. Cold-engine comparisons should show where the improvement comes from. Report anytime quality, memory, resource failures and guard qualifications alongside closure. All unsuccessful scheduled outcomes contribute the full horizon. Average trials within an instance before aggregating instances.

Agree the practically meaningful improvement criterion and uncertainty rule before confirmation, rather than selecting a threshold after seeing favorable results. This handoff does not invent an approved percentage margin or require uniform per-instance dominance. Repeated measurements quantify timing variation, while the number and range of instances determine how far the result generalizes. A repaired C104 alone is insufficient to establish the full-panel claim.

The existing 12-instance pilot is development data. One proposed design reserves a deterministic, class-stratified split of the remaining 44 instances into 22 additional development cases and 22 confirmation cases. Freeze the split, seed and selection rule before collecting new candidate results. This is a proposal for discussion, not an approved split. Confirmation means unused for this optimization effort, rather than historically unsolved. If confirmation drives another code change, disclose that reuse and reassess the validation design. The eventual 56-instance main study remains useful, but its pooled result cannot be presented as wholly independent confirmation after tuning on part of that population.

The main campaign remains held until development and confirmation support the new proof-speed objective and the resulting design is reviewed with Onyr. The old 15 s / 7,200 s / 16 GiB proposal is an archived starting point. New solver identities and any changed configuration require a new proposal and provenance records. Favorable incumbent results alone do not satisfy this objective, and the available evidence does not guarantee that optimization will achieve it.

## Execution and next resumed actions

Use reserved Grid'5000 Abaca compute nodes for builds, solver runs and medium or large tests. Kenzae handles editing, orchestration and small checks. Start with a few nodes and low contention for profiling, then size comparative waves from actual capacity and memory headroom. Preserve the 16 GiB operating point for comparability unless a separately identified resource experiment is needed. Release idle reservations, archive raw outputs with hashes and byte counts, and remove only verified workflow-created remote outputs.

Assign a solver agent to source divergence and candidate changes, a harness agent to partial diagnostics/replay and controlled execution, and a protocol agent to independent correctness/performance review. The coordinating agent owns reservations, experiment identities, integration, decisions and session logs. Parallel branches may investigate different hypotheses, but do not share mutable build directories or alter the fixed control.

The first resumed pass will create the isolated corrected reference and diagnostic worktrees, reproduce C104/RC201/R201 against the preserved artifacts, identify the first meaningful divergence, and return a profile with a ranked shortlist of changes. Subsequent passes will implement and compare those changes, expand the development panel, integrate full Kayros, and perform the agreed confirmation before revisiting the main experiment.
