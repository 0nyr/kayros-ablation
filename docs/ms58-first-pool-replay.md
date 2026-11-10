# ms58 first-pool replay

The controlled replay rejects the tiny initial-dual differences as the explanation for the first priced-pool discrepancy on C104 and R201. Replacing either engine's own captured dual vector with the other engine's vector leaves its complete ordered 3,000-route pool unchanged. The engines still produce different pools when given identical vectors.

This is a diagnostic result from the first root Heuristic Cost call, with correction explicitly disabled in both implementations. It does not establish which pool is preferable, whether either solver's bound is sound, or why the later proof times differ. Neither implementation is a correctness oracle.

## Method and identities

Kayros diagnostic commit `64a3e28` and upstream diagnostic commit `0dbf574` add an opt-in `KAYROS_FIRST_PRICING_REPLAY` mode. Each run records its original and effective pricing context, preprocessed instance tables, first ordered route pool and a `replay_complete` event, then deliberately terminates before column admission. No run contributes a solver-closure claim. The frozen controls remain unchanged.

The runs used G5K OAR allocation `7013762` on `grvingt-54`, a common reserved physical core, a 30 s process horizon and a 16 GiB limit. Each pair shared the exact same fixture file. The completed 10-cell schedule comprises both dual sources crossed with both engines on C104 and R201, plus the upstream dual source in both engines on RC201. RC201's Kayros source trace was unavailable after the earlier cancellation, so that missing crossed comparison was retained explicitly.

The [findings inventory](../outputs/ms58/first-pool-replay-findings.json) records the exact fixture, trace, manifest, binary, source-patch and comparison hashes. The [run plan](../outputs/ms58/replay-inspection/plan.json) and [rows](../outputs/ms58/replay-inspection/rows.json) preserve the commands and outcomes. All 10 runs reached the intentional diagnostic stop. The replay contract passed 3 Python tests and a standalone C++ test covering the dual override, root-context guards, single-call use and non-solver completion.

## Observations

| Instance | Identical dual source | Shared paths out of 3,000 | Paths unique to each engine | Effect of swapping dual sources within one engine |
|---|---|---:|---:|---|
| C104 | Upstream or Kayros | 2,287 | 713 | Same complete ordered pool in both engines |
| R201 | Upstream or Kayros | 2,472 | 528 | Same complete ordered pool in both engines |
| RC201 | Upstream | 2,188 | 812 | Kayros-source fixture unavailable |

Every engine's replay with its own captured duals reproduces its original first pool in the same order. This supplies an observable stability check for the replay instrumentation and explicit correction setting at this first stage. It does not remove the undefined-behavior qualification from later calls of the original upstream diagnostic binary.

The preprocessed graph successor order, time windows, capacity, demands and horizon agree. Forward travel and arrival endpoints differ by at most a few times `1e-12`. Departure and pretravel tables have different piece counts in 1,629 C104 entries, 1,206 R201 entries and 1,330 RC201 entries. Large numeric differences from comparing these arrays by piece index are consequences of misaligned pieces and must not be interpreted as functional errors.

The [function-shape analysis](../outputs/ms58/replay-function-analysis.json) resolves the C104 and R201 count discrepancies. Every differing entry has the same sequence of nonzero-width pieces within `1e-8` after zero-width pieces are excluded from the comparison. C104 departure tables contain 56,830 pieces in upstream and 58,459 in Kayros, including 23 and 1,652 zero-width pieces respectively. R201 contains 5,007 and 6,213 pieces, including 207 and 1,413 zero-width pieces. The extra zero-width pieces exactly account for those count differences. They were excluded only to align the analysis, never removed from either solver.

## Source interpretation and next tests

Upstream `PWLFunction::Inverse` constructs the maximum envelope of separately inverted pieces. A constant piece becomes a single latest-departure representative. Kayros's monotone inverse explicitly preserves a plateau's full departure-choice span as a tagged vertical. Kayros also uses direct reversal transforms and reverse-arrival normalization with documented correctness counterexamples against removing that normalization. These differences are plausible sources of the observed representation and search divergence. This replay does not isolate their individual effects.

The next useful task is a captured-function comparison at actual arrival plateaus and inverse endpoints, retaining domain, image, slope, intercept and vertical kind. Evaluate the latest-departure representative and departure-choice semantics independently before comparing extension and dominance results for the same captured label. The current PWL JSON contains evaluated endpoints, which can conceal vertical image spans, and it does not expose the reversed-instance tables.

A safe performance direction remains reducing allocations while preserving every piece and arithmetic operation, such as the separate in-place normalizer candidate. Removing choice verticals, reverting normalization or equating upstream's pool with a correctness target would not follow from these results. Correctness checks must cover the broader supported instance families before any candidate is promoted.
