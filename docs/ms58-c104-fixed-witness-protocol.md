# C104 outcome-selected fixed-witness mechanism case

We prepare a separate C104 mechanism case before drawing any global conclusion about the handoff policy. The witness comes from the original `9d64b7c` Full 15 s cell with seed 456 and repetition 3, which later reported closure at 3158.282500852 s on the main process clock. This is explicitly an outcome-selected case. Its source closure time identifies the selected case and is not a timing comparator for the new allocation.

The selected handoff has 11 routes, covers all 100 customers and independently costs 97829.81555195048. Its ordered-route SHA256 is `f4a851bf2682977d4fda6383bdf0e77a6f962d1dd07d4473f4d923d2b5c84281`. No singleton route appears, so the deduplicated treatment should add all 11 handoff routes and skip 0 singleton duplicates. The source run's better checked incumbent and the original independently checked frozen n100 reference are retained as external proof-consistency ceilings. They never replace the selected treatment routes or upper bound.

We use the same 5 CPLEX arms as the short fixed-witness pilot, with 3 repetitions each. The arms are selected `9d64b7c` warm, `74ed814` UB-only, `391954a` deduplicated, selected `9d64b7c` cold and fixed minimal `f2c9e20` Lera. Every repetition uses the same frozen handoff. No ILS is executed. This diagnostic measures the exact stage, not full-process timing, and does not provide an unbiased performance estimate or a proof-completeness certificate.

## Distribution and budgets

The 15 cells occupy 15 isolated physical cores on 4 Gros hosts, with lane counts 4/4/4/3. Host roles A–D correspond to the sorted actual allocation hostnames. Every arm spans 3 distinct hosts and each repetition spans all 4 hosts.

| Arm | Repetition 1 | Repetition 2 | Repetition 3 |
| --- | --- | --- | --- |
| Current warm | A | B | C |
| UB-only | B | C | D |
| Deduplicated | C | D | A |
| Current cold | D | A | B |
| Fixed Lera | A | B | C |

Each cell receives a 3600 s exact stage and a 16 GiB address-space cap. Witness checking, instance loading and Python payload preparation precede the stage. Its kernel alarm and native internal deadline share one absolute stage deadline while native events retain the original process timestamp origin. The separate process cap is 3660 s, and the launcher cap is 3680 s including post-run checking.

All 15 slots start concurrently. Launch requires at least 3800 s before the declared timezone-qualified stop. Every node rechecks wall and monotonic time immediately before starting each cell and requires 3680 s plus a 30 s final audit margin. A reservation of 1 h 20 min leaves 1000 s for allocation inspection and launch preparation. A 2 h reservation provides more margin. The node with 3 concurrent cells has a different load from the 3 nodes with 4 concurrent cells, which remains a qualification on small timing differences.

## Concrete preparation

The local no-solver proposal validates all 4 stored gate/manifest bindings, their source patches, the exact original source cell and both external feasible references. It reruns the pinned v0.11 checker on the original handoff IDs before any native indexing, including customer coverage, capacity, full-model feasibility, finite route costs and canonical total cost. Its artifact records the outstanding live-runtime and allocation checks. No reservation or solver is required to create it.

The following reservation line is a concrete shape for the root agent to use under the existing experiment authorization. It is not executed by this protocol preparation.

```bash
oarsub -q default -p gros -n ms58-c104-fixed -l host=4,walltime=1:20:00 "sleep infinity"
```

After reserving 4 Gros hosts, use the pinned checker Python. `EXP` names the experiment checkout and `ROOT` names the remote `ms58-bpca` root. Copy the reviewed source helpers to the experiment checkout only. Leave the active short pilot's `frozen-scripts`, plan, launch records and cells untouched.

```bash
"$CHECKER_PYTHON" "$EXP/scripts/ms58_c104_fixed_witness.py" \
  --mode design \
  --variants "$ROOT/dabia-20261110/warm-gates-v1/variants.json" \
  --source-wave "$ROOT/dabia-20261110/gros-five-arm-1h-v1" \
  --inputs "$HOME/kayros-sweeps/MAMUT-routing/benchmarks/TDVRPTW/Dabia2013/n=100" \
  --output "$ROOT/dabia-20261110/c104-fixed-witness-design.json"

"$CHECKER_PYTHON" "$EXP/scripts/ms58_c104_fixed_witness.py" \
  --mode prepare \
  --design "$ROOT/dabia-20261110/c104-fixed-witness-design.json" \
  --output "$ROOT/dabia-20261110/c104-fixed-witness-v1"

"$CHECKER_PYTHON" "$ROOT/dabia-20261110/c104-fixed-witness-v1/frozen-scripts/ms58_c104_fixed_witness.py" \
  --mode preflight --job-id "$OAR_JOB_ID" \
  --output "$ROOT/dabia-20261110/c104-fixed-witness-v1"

"$CHECKER_PYTHON" "$ROOT/dabia-20261110/c104-fixed-witness-v1/frozen-scripts/ms58_c104_fixed_witness.py" \
  --mode launch --job-id "$OAR_JOB_ID" \
  --output "$ROOT/dabia-20261110/c104-fixed-witness-v1" \
  --stop-before "$C104_STOP_UTC"
```

Run preflight and launch inside the real OAR context with its inherited nodefile. The allocation inspector uses `oarsh`, checks actual CPU affinity and physical topology, verifies 80 GiB available RAM on each 4-lane host and 64 GiB on the 3-lane host, and compares CPU, kernel, imported extension and dynamic-library identities. Every cell binds the exact plan, allocation, host, physical CPU, source, binary, input and witness hashes. It cannot use the short pilot's 600 s cell entrypoint.

## Analysis guardrails

Preserve all 15 scheduled outcomes, including deadlines, missing native sidecars, invalid candidates, resource failures, skipped cells and node failures. Native root labeling and column-generation work, whole-run LP and pricing times, branching work, reported closure, returned checked costs and handoff-policy metadata are retained separately. Compare these measurements only within this new homogeneous allocation cohort. Main-panel and short-pilot timing curves remain separate.

Warm arms retain and emit the independently checked caller-held witness before native execution, so a finite native value with an empty route list does not discard the witness. Cold and fixed Lera receive no handoff. After-stage candidates are excluded from the feasible candidate pool, while observed late bound and closure contradictions remain available to refute earlier claims. The shared consistency audit uses the pooled new checked candidates and both frozen external feasible witnesses. Optional unavailable termination bounds remain explicit under the reviewed shared-helper contract.

The first decision is whether UB-only or deduplication retains the handoff benefit on this selected hard case while preserving checked solutions and consistent bounds. A result from these 3 repetitions remains a mechanism observation. It cannot establish a global policy recommendation, and reported closure after necessary consistency checks remains distinct from independent proof certification.
