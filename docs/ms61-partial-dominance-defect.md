# ms61: upstream partial-dominance defect (pricing incompleteness)

Date: 2026-11-10. Status: fixed in the Kayros candidate `86f74ad` (worktree `kayros-ms61`), and in a separately identified upstream control "f3". Found by the ms61 coordinating agent.

## The defect

`PWLDominationFunction::DominatePieces` removes from a candidate label the parts of its duration function that a stored label dominates. In case D, the dominated interval `[ld, rd]` lies strictly inside one linear piece `p1`, which must be split into a left part and a right part. The right part was built as

```cpp
auto right_piece = LinearFunction({rd, p1.Value(rd)}, {p1.domain.right, p1.image.right});
```

`image` is the interval `[min, max]` of the values of the piece. `p1.image.right` is therefore the maximum of the piece, which equals its value at the right end only when the piece is non-decreasing. On a decreasing piece, the common situation where a later arrival means less waiting, the maximum is the value at the left end. The rebuilt right part then rises to that maximum instead of continuing to fall. The label's duration is over-estimated on that part, and a later comparison can erase it although it is not dominated. A route whose best schedule uses that part is lost by the labeling.

The same line is in upstream Lera-Romero's code (`code/src/labeling/pwl_domination_function.cpp`, line 133 in the audited tree). It is not a Kayros modification and not an ms61 change. The corrected line evaluates the piece at its right end:

```cpp
auto right_piece = LinearFunction({rd, p1.Value(rd)}, {p1.domain.right, p1.Value(p1.domain.right)});
```

## Consequence

The Exact pricing level can finish without reporting a route of negative reduced cost that exists. The restricted master is then declared optimal too early, the node bound can exceed the true optimum, and the search can close on a suboptimal value. The error only ever removes labels, so every column the solver does find is still feasible and exactly priced. Incumbents remain valid. Bounds and optimality claims are what can be wrong.

## Reproducer

Dabia2013, 50 customers, RC107, cold BPC with CPLEX, Kayros candidate `a1d6363` (any build before the fix), `KAYROS_BPC_DROPBACK_LEVEL=0`. The run closes at the root with value 10632.144948203571 after 34 cuts and 11 Exact passes. Five other settings of the same build, including all switches off, prove 10614.011179781175. Adding cut-blind heuristics to the failing setting gives a third value, 10625.511174131196.

The diagnostic switch `KAYROS_BPC_VERIFY_FINAL` re-runs an Exact pass that found nothing, with one labeling feature changed, before the node is declared optimal. Bits 1, 2, 4, 8 and 32 (correction off, opening merge state, no cost sorting, weak unreachable sets, no limited extension) leave the wrong closure in place. Bit 16 (no partial dominance) recovers 2 columns, `[0, 31, 34, 32, 33, 26, 27, 51]` with duration 1603.5 and `[0, 29, 34, 30, 26, 33, 32, 51]` with duration 1581.92, and the run then proves 10614.011179781175.

The path trace of the first route shows the mechanism. The forward label `0,31,34` has, among others, a decreasing piece on `[1122.2, 1266.199]` with values from 642.2 down to 546.2. The stored label `0,34` dominates it on a middle interval ending at 1175.501. The right part should run from 606.665 at 1175.501 down to 546.2 at 1266.199. The trace shows it rebuilt as `[1175.501, 1266.199]` with values from 606.665 up to 642.2, and the next comparison then erases it entirely. With partial dominance the merged route is priced at 1654.31 (reduced cost 47.26, rejected). Without it the same route is priced at 1603.50 (reduced cost -3.55, added).

## Validation of the fix

- Both failing settings return 10614.011179781175, and the verification pass (bit 16) finds nothing.
- Dabia2013 n=25, all 56 instances, 3 settings with verification on: 305 verification passes, 0 catches, 0 alerts, and no optimal value differs from the pre-fix baseline.
- No other computational use of a piece's image bounds as an endpoint value exists in either source tree. Upstream has this single site. The remaining Kayros sites read the span of a vertical piece or print.
- The repository's BPC soundness suite passes on the fixed build: 56 passed, 3 opt-in randomized tests skipped. `tests/test_lera_partial_dominance.py` pins the RC107 reproducer under 4 ladders.
- Larger validation (n=50 with verification, the opt-in randomized sweep, n=100 against fixed Lera) is in progress and recorded in the ms61 session log.

## What it means for the comparison

Fixed Lera `f2c9e20` and every Kayros build up to `a1d6363` share the defect. A closure reported by either is a claim made by an incomplete pricer. In the ms58 and ms61 screens every closure agreed across arms and with the checked reference costs, so no wrong closure was observed at n=100, but agreement between solvers that share a defect is not a proof. The repaired upstream control "f3" is `f2c9e20` plus this single line. It is committed locally as `1001ff2b9f4a1a8df513b763f36f83f386bd63d3` on branch `proof-speed/minimal-reference-f3` (worktree `lera-proof-f3`). The remote build source `~/ms61-bpc/sources/upstream-f3` has the same 131 C++ files (aggregate SHA-256 `18094757…9f0e` on both sides), and the binary has SHA-256 `04073bef66d50273d018df9381e2a86b7a6cd744a68951b83fc60d04952e04f2`. The defect removes labels, so the unrepaired solvers also do less work than a correct solver. Timing comparisons must therefore be redone with the fix on both sides.
