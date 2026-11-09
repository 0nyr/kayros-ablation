"""Plot the approved prefix criterion and the incumbent-quality trade-off."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from plot_style import arm_style


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--decision', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    decision = json.loads(args.decision.read_text())
    rows = sorted(decision['prefix_ranking'], key=lambda row: row['prefix_s'])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    for ax, field, title in zip(axes, ['restricted_mean_reported_closure_s', 'mean_lambda'],
                                ['Primary criterion: capped closure time (s)', 'Secondary criterion: mean Λ']):
        for x, row in enumerate(rows):
            color = arm_style('kayros-full-cplex', row['prefix_s'])['color']
            ax.bar(x, row[field], color=color, width=.65, alpha=.75)
            rep_values = [next(item[field] for item in ranking if item['prefix_s'] == row['prefix_s'])
                          for ranking in decision['sensitivity']['by_replication'].values()]
            ax.scatter([x] * len(rep_values), rep_values, color='#222222', s=17, zorder=3)
        ax.set_xticks(range(len(rows)), [f"{row['prefix_s']:g}" for row in rows])
        ax.set_xlabel('Fixed prefix from process launch (s)')
        ax.set_title(title, fontsize=11)
        ax.grid(axis='y', alpha=.2)
    selected = decision['selected_prefix_s']
    selected_text = f"Rule-selected prefix: {selected:g} s" if selected is not None else 'Recommendation held for recorded issues'
    fig.suptitle(f"Pilot horizon {decision['horizon_s']:g} s | {selected_text}", fontsize=12)
    fig.text(.5, .035, 'Bars: equal-instance averages over all trials. Points: per-replication averages, not confidence intervals.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .08, 1, .94))
    args.output.mkdir(parents=True, exist_ok=True)
    for extension in ('png', 'pdf'):
        fig.savefig(args.output / f'prefix-overview.{extension}', dpi=180)


if __name__ == '__main__':
    main()
