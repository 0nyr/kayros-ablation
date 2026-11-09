"""Plot archived checker-valid development traces, without solver-performance claims."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from checker_contract import checker_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checker = checker_identity()
    selection = json.loads((args.source / 'final-authoritative-cells.json').read_text())
    styles = {
        'kayros-bpca-cplex': ('Cold CPLEX (15 s)', '#1764ab', '-', 'o'),
        'kayros-bpca-highs': ('Cold HiGHS (15 s)', '#d86c00', '--', 's'),
        'kayros-heuristics': ('ILS (15 s)', '#21834d', '-.', '^'),
        'kayros-full-cplex': ('Full CPLEX (15 s prefix, 30 s total)', '#8c49ad', ':', 'D'),
    }
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    points = []
    for cell in selection['cells']:
        folder = args.source / cell['source_directory'] / cell['run_id']
        result = json.loads((folder / 'result.json').read_text())
        identity = json.loads((folder / 'identity.json').read_text())
        assert result['run_id'] == cell['run_id']
        assert result['validation']['checker'] == checker
        assert hashlib.sha256((folder / 'events.jsonl').read_bytes()).hexdigest() == result['events_sha256']
        assert result['validation']['proof']['status'] != 'accepted'
        envelope = result['validation']['checked_envelope']
        times = [p['elapsed_s'] for p in envelope]
        costs = [p['checked_cost'] for p in envelope]
        horizon = identity['horizon_s']
        assert times and all(0 <= t <= horizon for t in times)
        label, color, style, marker = styles[cell['arm']]
        ax = axes[0 if cell['n'] == 25 else 1]
        ax.step(times + [horizon], costs + [costs[-1]], where='post', label=label,
                color=color, linestyle=style, linewidth=1.8, alpha=.85)
        ax.scatter(times, costs, color=color, marker=marker, s=32, facecolors='none', zorder=5)
        points.append({'n': cell['n'], 'arm': cell['arm'], 'run_id': cell['run_id'],
                       'horizon_s': horizon, 'checked_envelope': envelope})
    for n, ax in zip((25, 50), axes):
        ax.set_title(f'C101, {n} customers')
        ax.set_xscale('log')
        ax.set_xlabel('Elapsed time from process launch (s, log scale)')
        ax.set_ylabel('Best checker-valid Duration cost')
        ax.ticklabel_format(axis='y', style='plain', useOffset=False)
        ax.grid(True, alpha=.25)
        ax.axvline(15, color='grey', linewidth=.8, alpha=.5)
    fig.suptitle('ms56 correctness probes: 1 repetition, 0 accepted proofs', fontsize=13)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', bbox_to_anchor=(.5, .07), ncol=2, frameon=False)
    fig.text(.5, .015, 'No curve before first valid incumbent. Flat tails retain the incumbent to the configured horizon.\n'
             'Different budgets and a tiny correctness panel: these traces do not rank solver performance.',
             ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .21, 1, .94))
    args.output.mkdir(parents=True, exist_ok=True)
    for ext in ('png', 'pdf'):
        fig.savefig(args.output / f'probe-overview.{ext}', dpi=180)
    (args.output / 'probe-overview.json').write_text(json.dumps({'checker': checker, 'cells': points}, indent=2)+'\n')


if __name__ == '__main__':
    main()
