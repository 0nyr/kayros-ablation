# Frozen Dabia2013 n100 reference solutions

This snapshot fixes the primal reference for the experiment before its pilot. It contains 56 original n100 TDVRPTW route witnesses, each fully checked and repriced with classic mamut-routing-lib v0.11.0. All 56 are valid. The reference costs of 11 instances differ from the current source-declared costs, by at most approximately `7.28e-12`, because the source was repriced with v0.12.0. The routes are unchanged. This snapshot does not introduce new optimality claims.

The source is [MAMUT-routing commit 8e3aa713](https://github.com/ANR-MAMUT/MAMUT-routing/tree/8e3aa713032a3d99c220c87551535213856458a0/benchmarks/TDVRPTW/Dabia2013/n%3D100). All selected instance, ATF and BKS bytes were checked against that commit. The original benchmark definitions and original solution sources retain their provenance and rights. See [SOURCE_LICENSE](SOURCE_LICENSE).

| Path | Role |
|---|---|
| `solutions/` | Primal reference routes and v0.11 costs to use in experiment scoring |
| `source-solutions/` | Byte-exact original BKS files, including historical metadata that is not a new campaign outcome |
| `checks/` | Complete v0.11 validation results, including route durations and departure times, without validation timings |
| `manifest.json` | Input/ATF/source/solution hashes, checker identity, reference costs and exact hexadecimal floating-point values |
| `manifest.sha256` | Digest of the immutable manifest |

Manifest SHA-256: `ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66`.

Checker commit: `425dff351fdd9522ea6bb8c537dd416dfcd19614`. Its installed 23 Python source files have aggregate SHA-256 `e22c65ea7b13c342653b947b6066b78b45313e517ad8ac3af09efaaa66e91334`. The guard checks version, Git installation provenance, package content and import location before importing the evaluation functions. Dependency versions are fixed by the repository's `uv.lock`, whose digest is in the manifest.

The instance definitions and ATF sidecars are pinned by Git commit and hashes rather than duplicated here. To replay the checks from the experiment repository, supply a MAMUT-routing checkout containing the recorded input bytes:

```bash
uv sync --locked --extra checker
uv run --locked --extra checker python scripts/verify_references.py \
  --snapshot references/dabia2013-n100-v011-2026-11-09 \
  --source-repo /path/to/MAMUT-routing
```

The verifier requires exact cost agreement. The snapshot generation script refuses an existing destination and aborts if any witness, input identity or checker identity is invalid. Regeneration under the same environment writes a separate destination, never overwrites the campaign reference. Independent replay of all 56 witnesses also confirmed source-route equality, exact cost equality, complete checker-record equality and file hashes.
