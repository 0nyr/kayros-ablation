"""Synthetic illustration of incumbent quality and proof speed."""

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=Path("outputs/synthetic"))
OUT = parser.parse_args().output_dir
OUT.mkdir(parents=True, exist_ok=True)
HORIZON = 60.0
REFERENCE = 100.0
# Each arm has 3 equally weighted synthetic instances. Costs are invented.
ARMS = {
    "A: early incumbents, later proofs": [
        {"incumbents": [(2, 100)], "proof": 45},
        {"incumbents": [(3, 100)], "proof": math.inf},
        {"incumbents": [(4, 100)], "proof": math.inf},
    ],
    "B: later incumbents, earlier proofs": [
        {"incumbents": [(10, 100)], "proof": 15},
        {"incumbents": [(12, 100)], "proof": 20},
        {"incumbents": [(15, 100)], "proof": math.inf},
    ],
}


def gap(run, time):
    costs = [z for t, z in run["incumbents"] if t <= time]
    if not costs:
        return 1.0
    value = min(costs)
    return (value - REFERENCE) / (value + REFERENCE)


def area(times, values):
    return sum((right - left) * value
               for left, right, value in zip(times, times[1:], values))


fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
summary = {"kind": "synthetic illustration, not experiment results", "horizon": HORIZON}
for label, runs in ARMS.items():
    times = sorted({0.0, HORIZON}
                   | {t for r in runs for t, _ in r["incumbents"]}
                   | {r["proof"] for r in runs if math.isfinite(r["proof"])})
    means = [sum(gap(r, t) for r in runs) / len(runs) for t in times]
    targets = [sum(gap(r, t) <= 0 for r in runs) / len(runs) for t in times]
    proofs = [sum(r["proof"] <= t for r in runs) / len(runs) for t in times]
    unsolved = [1 - value for value in proofs]
    score = area(times, means) / HORIZON
    restricted = area(times, unsolved)
    direct = sum(min(r["proof"], HORIZON) for r in runs) / len(runs)
    assert math.isclose(restricted, direct)
    assert targets[-1] == 1.0 and proofs[-1] < 1.0
    for ax, values in zip(axes.flat, [means, targets, proofs, unsolved]):
        ax.step(times, values, where="post", label=label)
    summary[label] = {"Lambda": score, "restricted_mean_proof_seconds": restricted,
                      "proved": sum(math.isfinite(r["proof"]) for r in runs),
                      "runs": len(runs)}

# Ranking reversal is deliberate: incumbent quality and proof speed differ.
assert summary[next(iter(ARMS))]["Lambda"] < summary[list(ARMS)[1]]["Lambda"]
assert summary[next(iter(ARMS))]["restricted_mean_proof_seconds"] > summary[list(ARMS)[1]]["restricted_mean_proof_seconds"]
for ax, title in zip(axes.flat, [
    "Mean squeezed reference gap (lower is better)",
    "Reference attained (higher is better)",
    "Proof attained (higher is better)",
    "Unproved fraction (area = restricted mean proof time)",
]):
    ax.set(title=title, xlabel="Elapsed seconds", xlim=(0, HORIZON), ylim=(-0.03, 1.03))
    ax.grid(alpha=0.2)
axes[0, 0].set_ylabel("Mean squeezed gap")
for ax in [axes[0, 1], axes[1, 0], axes[1, 1]]:
    ax.set_ylabel("Fraction of all 3 runs")
axes[1, 1].legend(loc="upper right", fontsize=8)
fig.suptitle("SYNTHETIC DESIGN EXAMPLE: finding the reference and proving optimality", fontsize=14)
fig.savefig(OUT / "metric_demo.pdf")
fig.savefig(OUT / "metric_demo.png", dpi=150)
(OUT / "metric_demo.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
