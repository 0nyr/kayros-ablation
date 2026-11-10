# CPLEX variable-domain batching candidate

We prepared this source-only candidate for a later pass. It has not been compiled, executed, added to the running screen or selected for the 1-hour panel. We have no runtime parity or speed result. The existing source and mandatory minimally repaired Lera control remain unchanged.

| Identity | Value |
| --- | --- |
| Base | `327e481713e1b9a3187fda2fc9f10f8d9d75b0c5` |
| Candidate | `cd5dee0b2543e9aa65718889379bedc6181d3161` |
| Branch | `proof-speed/cplex-domain-batch` |
| Worktree | `$WORKSPACE/kayros-proof-cplex-domain-batch` |
| Patch | `patches/ms58-experimental/kayros-cplex-domain-batch.patch` |
| Patch SHA-256 | `d236383aa5e839d0cd1fa364f439e14c100847b61694bfff87d9bc78a9282b07` |
| Local validation | Source review and `git diff --check` only |

## Scope and source reasoning

The original `solve_lp` saves every variable domain through a separate checked `CPXgetctype` call, changes the problem to LP, solves and extracts results, then restores every domain through a separate checked `CPXchgctype` call. Each formulation accessor also constructs a small `std::map`. The saved domains and restoration are outside the native LP stopwatch, so a possible improvement would appear in enclosing search time rather than the reported LP solve time. This overhead exists in both implementations and may be amplified by different column counts. Its cost has not been isolated experimentally.

The new internal `VariableDomains` helper batches only an ordinary `CPXPROB_MILP` with more than 1 variable, only when its actual dynamic type is exactly `CplexFormulation`, and only when all returned types are `C`, `I` or `B`. It reads the type vector in 1 checked call. Restoration still calls the original `SetVariableDomain` for column 0, preserving its original LP-to-MILP transition and exception path, then restores columns 1 through n−1 with 1 checked batch call. No solver configuration, bound, coefficient, objective, floating-point calculation or iteration order changes in the production patch.

IBM documents `CPXchgctype` as a bulk operation and treats binary bounds as a separate responsibility. We therefore add no `CPXchgbds` call and no automatic clipping, including for unusual binary bounds. IBM also documents that converting a mixed-integer problem to a continuous problem discards its type vector, and that a MILP containing only continuous variables remains a MILP unless its problem type is explicitly changed. These semantics justify preserving the original first restoration mutation rather than introducing a new problem-type conversion. ([CPXchgctype](https://www.ibm.com/docs/en/icos/22.1.1?topic=c-cpxxchgctype-cpxchgctype), [CPXchgprobtype](https://www.ibm.com/docs/en/icos/22.1.2?topic=c-cpxxchgprobtype-cpxchgprobtype))

Zero-column and single-column cases, subclasses with virtual domain accessors, non-MILP problem types and semi-continuous or semi-integer columns use the original per-column loops. In particular, this candidate does not repair or reinterpret the existing treatment of unsupported domain types or quadratic/fixed problem types. The original `CPXPROB_LP` conversion remains in place. The fixture records such fallback behavior against the old method rather than assuming that the old method preserves every input problem type.

All CPLEX operations still use the existing checked wrappers. A nonzero API return still throws `std::runtime_error` with the existing function name and CPLEX error message. Restoration remains explicit after result extraction, so we add no restoration during exception unwinding and no catch-and-retry fallback. The fast snapshot uses approximately 5n bytes of vector element storage, compared with approximately 4n for the original enum vector on the intended platform. We have not measured allocator or peak-memory effects.

## Validation limits and native fixture

The source review supports this restricted candidate but does not establish native equivalence. IBM does not promise identical internal cache or basis effects for 1 batch and n separate modifications. Nor does its API contract establish identical partially modified state after an allocation failure. We therefore keep the source isolated and require the native fixture before any performance run or integration. If the requirement is literal equality of intermediate states under all possible allocation failures, we cannot establish it and should retain the original restoration path.

The native fixture in `tests/cpp/test_cplex_variable_domains.cpp` compares the actual helper with copies of the original accessor loops. It covers 13 model cases, including each possible first C/I/B type, a continuous MILP, fixed and nonstandard binary bounds, signed-zero and infinite bounds, 1-column and empty models, semi-types and a MIQP fallback with an empty quadratic matrix. Nonempty cases undergo 3 LP cycles followed by MIP optimization when the restored type permits it. It compares objective, primal, dual and reduced-cost bit patterns, bounds, domains, problem type, basis states and query availability codes. The mixed model additionally has known LP and MIP objective values of 15 and 14.75.

Additional checks compare explicit-LP snapshot behavior and fixed-MILP restoration exceptions, preserve virtual accessor calls for a derived formulation, and exercise the changed production `solve_lp` before a subsequent MIP solve. The fixture records completed cases and its failure reason if any comparison fails. It makes no timing claim. A passing fixture would cover these cases on the tested CPLEX build, not all hidden native states or allocation-failure scenarios.

## Build and validation handoff

The CMake target `ms58_cplex_domain_fixture` is available only in a CPLEX build and is excluded from the default build. These are proposed commands for a reserved compute node after the parent has staged this distinct source and configured its separate `build/cplex` directory using the established build procedure. They have not been executed.

```bash
cmake --build build/cplex --target ms58_cplex_domain_fixture --parallel 8
build/cplex/cpp/lera/ms58_cplex_domain_fixture > cplex-domain-fixture.json 2> cplex-domain-fixture.stderr
```

Retain both output files and the exit code, CPLEX library/header identity, compiler/build flags and candidate source/patch identity. If compilation or any parity check fails, retain the failure and revise or abandon the candidate before a timing run. If it passes, the next decision belongs to a separate paired experiment against `327e481`, with the same inputs, backend, solver settings, checker and resource envelope. Search work must be compared alongside runtime because native state changes could affect later degeneracy even when the small fixture passes.

## Reconstructed source package

The staged archive `outputs/ms58/dabia-20261110/cplex-domain-batch-source.tar.gz` has SHA-256 `c9d76179f02077e43576aa51ecc1dc56e4858fdf7e4fc511196fecd71b570881`. Its source identity record is `outputs/ms58/dabia-20261110/cplex-domain-batch-source-identity.json`. We reconstructed the original `50eebc5` source with frozen patches `0001` through `0004` and verified exact Git tree equality with `07879253091fe9e895cc8071aa69ccd0b2310634`, then applied `patches/ms58-experimental/cplex-domain-batch-full.patch` and verified exact equality with candidate tree `41f37d1dec61f264520f359c6ab1daa4363477d0`. The full patch SHA-256 is `057fe3dec78bbbbf38cb629994a78c6624fafd85e9177fdf29f5adca976a455c`. The archive was independently extracted and matched the reconstructed file hashes and Git tree, including file modes.

The complete 5-patch directory is `build/ms58/staging-nextpasses/patches-cplex-domain-batch`. The incremental patch listed above applies to `327e481`, while this full patch applies to `0787925`. They must not both be applied in the same reconstruction. The staging script and records remain in `build/ms58/prepare-nextpass-sources.py` and `build/ms58/staging-nextpasses/complete.json`. The same process verified the independent extension-profile source `4643f21`. Profiling is enabled by the build script only for the `extension-profile` variant.

## Proposed native and solver gates

This is a source-only protocol. Its machine-readable design is `configs/ms58-cplex-domain-batch-gates.json`, which does not authorize itself to launch. The parent owns resource allocation, transfer, builds and execution.

1. Verify the archive, all 5 source patches and every staged source-file hash. Build in a fresh candidate directory with CPLEX, the same compiler and flags as `327e481`, and no extension profiling. Record the CMake cache, compile commands, `liblera.a`, wheel, installed Python/native files and CPLEX library/header hashes. Reject inherited `KAYROS_`, `UPSTREAM_` or `PROBE_` overrides. Verify that the independently built ILS `_core` artifact matches the reference when the build environment and flags match. A mismatch requires explanation before a claim that the ILS build is unchanged.
2. Compile and execute the native target on reserved compute, preserving the command, resource identity, exit code, JSON and stderr in a new directory. Require exit code 0, `passed=true`, all 17 recorded cases and the expected CPLEX version. Any basis, type, bound, solution or exception mismatch blocks solver timings. Keep the source record unvalidated until this gate actually passes.
3. Reuse the existing Dabia-only correctness and ILS/BPC coupling checks with this candidate before selection. The change does not touch ILS code, but the native fixture alone does not validate the full bridge. Any invalid checked route, unexplained native/checker cost discrepancy or unsupported closure blocks promotion. These checks remain separate from timed cells and use their own identities and resource records.
4. If those gates pass, prepare 18 cold CPLEX cells from 2 instances (Dabia2013 n100 C201 and RC201), 3 repetitions and 3 sources. The sources are best `327e481`, batch `cd5dee0` and mandatory fixed Lera `f2c9e20`. Each cell has a proposed 600 s outer budget from process launch, no heuristic prefix, no solver seed, 16 GiB process cap and 1 physical core. Each instance/repetition is a serial triplet on the same host/core. Freeze 6 balanced orders across the 6 triplets before launch, pair positions within instances, and finish all compilation before timing. Each arm occupies each position once per instance. Six simultaneous lanes require at least 96 GiB for processes plus the established node reserve. A smaller allocation may execute triplets sequentially.
5. Audit every scheduled cell and retained failure. Keep the fixed quality-reference snapshot, independently checked incumbents and feasible-witness proof-consistency check. Compare within-panel paired closure/capped time, initialization, root pricing and LP timing, whole-tree and per-level work, columns/cuts/labels, bounds, candidate costs and sampled RSS. Compare the first workload divergence if signatures differ. A throughput interpretation requires matching recorded work and is still limited by the observed scheduling context. Deadlines remain censored observations, and earlier panels cannot supply absolute timing controls. This 2-instance development test cannot establish overall nonregression or select the 1-hour source by itself.

The Lera manifest must retain binary SHA-256 `59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783` and minimal repair patch SHA-256 `ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf`. The generic older `ms58_paired_probe.py` currently rewrites the upstream source label to its historical control description, so it must not be used unchanged for this new triplet series. A dedicated gate can reuse `development_wave.launcher_command`, the frozen `run_solver_probe.py` and the existing checker/backend audit functions while preserving explicit `f2c9e20` identities. No existing running schedule or frozen script is modified by this design.
