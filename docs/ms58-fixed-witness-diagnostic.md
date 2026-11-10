# Fixed-witness CPLEX handoff diagnostic

We compare 5 exact-stage treatments on C201, R201, RC201 and RC105 at `TDVRPTW/Dabia2013/n=100`. The treatments are the selected `9d64b7c` warm start, `74ed814` incumbent-only handoff, `391954a` deduplicated handoff, selected `9d64b7c` cold search and the fixed minimally corrected `f2c9e20` Lera control. Each instance supplies exactly 1 previously checked witness from its original main-panel Full 5 s, seed 123, repetition 2 cell. No ILS runs in this diagnostic.

The experiment has 20 cells on 4 physical cores in one reserved compute environment. Each instance occupies one lane and runs all 5 arms sequentially, with the arm order rotated across instances. This small panel diagnoses how a fixed handoff changes the exact-search path. It does not support a global recommendation to use incumbent-only handoff. A later confirmation must include the closing C104 Full 15 s witness and a 1 h exact-search budget.

## Preparation and launch

Run the commands with the pinned checker Python. `EXP` identifies the remote experiment checkout containing the reviewed scripts, `ROOT` identifies the remote `ms58-bpca` root, and `CHECKER_PYTHON` names its pinned checker interpreter. The source main panel remains unchanged. The `variants.json` file comes from the completed warm-handoff native and actual ILS-to-BPC integration gates. All 4 gate outcomes must pass, including the fixed Lera control.

```bash
"$CHECKER_PYTHON" "$EXP/scripts/ms58_fixed_witness.py" \
  --mode design \
  --variants "$ROOT/dabia-20261110/warm-gates-v1/variants.json" \
  --source-wave "$ROOT/dabia-20261110/gros-five-arm-1h-v1" \
  --inputs "$HOME/kayros-sweeps/MAMUT-routing/benchmarks/TDVRPTW/Dabia2013/n=100" \
  --output "$ROOT/dabia-20261110/fixed-witness-design.json"

"$CHECKER_PYTHON" "$EXP/scripts/ms58_fixed_witness.py" \
  --mode prepare \
  --design "$ROOT/dabia-20261110/fixed-witness-design.json" \
  --output "$ROOT/dabia-20261110/fixed-witness-v1"

"$CHECKER_PYTHON" "$ROOT/dabia-20261110/fixed-witness-v1/frozen-scripts/ms58_fixed_witness.py" \
  --mode launch \
  --output "$ROOT/dabia-20261110/fixed-witness-v1" \
  --job-id "$OAR_JOB_ID" --cores 0 1 2 3 \
  --stop-before '2026-11-10T11:36:00+00:00'
```

The 4 core numbers are placeholders for the actual allocated physical CPU IDs. Launch validates membership in the inherited OAR affinity, excludes SMT siblings, checks the real OAR nodefile and requires 80 GiB available RAM. The plan, 4 runtime manifests, source patches, native/integration gate reports, source witness provenance, copied inputs, independent checker evidence and scripts are frozen before launch. Every cell binds the launched plan and its independently derived worker identity.

## Witness and clock contracts

Preparation and the worker both validate every original route ID before any demand lookup, integer coercion or native indexing. IDs must be ordinary integers in `1..100`, without depots. The routes must be nonempty and partition all customers exactly once. Explicit capacity and vehicle-count checks precede the independent full-model v0.11 checker. Every route must have finite nonnegative duration and finite departure time, and the canonical route sum must exactly match the independently checked total and the original checked candidate cost.

Warm arms emit the independently checked caller-held witness as an in-budget candidate before invoking native search. This retains the witness when native search returns a finite value with an empty route list. Cold Kayros and fixed Lera receive no initial routes or upper bound. They record the same witness as external feasible-ceiling provenance, without treating it as a solver discovery or native warm start.

The 600 s exact-stage clock starts after witness checking, instance loading and Python payload preparation. The worker records its origin and arms a kernel `SIGALRM` hard stop. The native solver also receives a 600 s limit. The separate process cap is 660 s, and the cell launcher has a 680 s cap including post-run checking. A stage alarm is classified as a deadline only when its observed process duration reaches the recorded stage deadline. Missing native work after a hard stop remains unavailable.

The initial launch preflight requires `5 × 680 + 120 = 3520` s before the declared timezone-qualified stop. Immediately before every cell launch, wall and monotonic clocks must both leave 680 s plus a 30 s final audit margin. A cell that no longer fits remains in the schedule as `skipped_stop_before`. Preparation time therefore reduces the available launch budget.

## Retained evidence and interpretation

Each row retains the native work counters, root column-generation and labeling work, warm-start metadata, checked candidates, raw bound and closure claims, resource outcomes and artifact hashes. The UB-only audit requires 0 inserted warm columns and a valid caller-held UB. The deduplicated audit requires an added-column count matching the nonsingleton routes and a duplicate count matching the singleton routes. In particular, the C201 witness must set the UB from all 4 routes while adding 3 columns and skipping its 1 singleton duplicate.

Candidate and closure times are projected onto the exact-stage clock. Prepared caller-held witnesses have time 0. Candidates after 600 s are excluded from the feasible candidate pool. Observed after-stage bounds and closures remain raw consistency evidence, so a genuine late contradiction can still refute an earlier claim. The shared pooled consistency audit compares all 5 arms against every retained independently checked feasible witness. Null optional termination bounds follow the reviewed shared-helper semantics. No observation is silently filtered from the denominator.

Any metadata, backend, default-setting, handoff-policy, candidate-cost or artifact audit failure removes accepted closure credit while preserving raw claims and checked evidence. Reported closure after these necessary checks is not independent proof certification. This exact-stage diagnostic must remain separate from the main full-process timing panel and from curves measured in other environments.

The offline tests invoke no solver. They cover malformed original IDs before indexing, route partition and capacity failures, actual independent checking of all 4 source witnesses, worker identity binding, caller-held support for empty-native-route closure, audit-failure closure suppression, and rejection of after-stage candidates while retaining contradictory late bounds.
