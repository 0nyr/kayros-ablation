"""Plot the completed grvingt screen separately from every other environment."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

VARIANTS = ['minimal-lera', 'best', 'scratch', 'safe-correction']
LABELS = ['Minimal Lera', 'Canonical + point', 'Scratch reuse', 'Safe correction']
COLORS = ['#222222', '#d95f02', '#7570b3', '#006d2c']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.summary.read_bytes()
    report = json.loads(raw)
    if report['provisional'] or report['audit_failure_count'] or not report.get('consistency_audit_completed'):
        raise ValueError('Screen figures require the completed independently audited cohort')
    hosts = {cell['host'] for cell in report['cells']}
    if len(hosts) != 1 or len(report['cells']) != 36:
        raise ValueError('This figure is restricted to the original single-host 36-cell screen')
    host = next(iter(hosts)).split('.')[0]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.8), sharey=True)
    plotted = []
    for ax, instance in zip(axes, ['C201', 'RC201', 'C104']):
        for index, (variant, color) in enumerate(zip(VARIANTS, COLORS)):
            cells = sorted([c for c in report['cells'] if c['instance_name'] == instance and c['variant'] == variant],
                           key=lambda c: c['replicate'])
            if len(cells) != 3:
                raise ValueError('Every displayed arm requires all 3 scheduled trials')
            closures = []
            for offset, cell in zip([-.13, 0., .13], cells):
                closure = cell['reported_closure_s']
                censor = closure is None and cell['status'] == 'deadline'
                time = closure if closure is not None else cell['horizon_s']
                ax.scatter(time, index + offset, marker='o' if closure is not None else '>' if censor else 'x',
                           s=42, color=color, zorder=3)
                if closure is not None:
                    closures.append(closure)
                plotted.append({'instance_name': instance, 'variant': variant, 'replicate': cell['replicate'],
                    'task_id': cell['task_id'], 'status': cell['status'], 'reported_closure_s': closure,
                    'display_x': time, 'display_y': index+offset, 'deadline_censored': censor,
                    'consistency_status': cell['consistency_audit']['status']})
            if len(closures) == 3:
                ax.plot([statistics.mean(closures)] * 2, [index-.24, index+.24], color=color, linewidth=2)
        ax.set(title=instance, xlim=(0, 635), xticks=[0, 150, 300, 450, 600], yticks=range(4), yticklabels=LABELS)
        ax.grid(axis='x', alpha=.25)
    axes[0].invert_yaxis()
    fig.suptitle(f'Dabia2013, n=100 | {host} development screen | CPLEX')
    fig.supxlabel('Elapsed time from process launch (s)', y=.14)
    fig.text(.5, .075, 'Dots show reported closure after checked-witness consistency gates. Bars show means of 3 closures. Triangles show 600 s deadlines.',
             ha='center', fontsize=8.7)
    fig.text(.5, .03, 'All 36 cells retained. C104 had no checked incumbent in any run. This screen is separate from the Gros panel and is not independent proof certification.',
             ha='center', fontsize=8.5)
    fig.tight_layout(rect=(0, .2, 1, .95))
    args.output.mkdir(parents=True, exist_ok=True)
    for extension in ['png', 'pdf', 'svg']:
        fig.savefig(args.output / f'screen-closure-times.{extension}', dpi=240)
    plt.close(fig)
    (args.output / 'screen-plot-data.json').write_text(json.dumps({'host': host, 'cells': plotted,
        'summary_sha256': hashlib.sha256(raw).hexdigest(),
        'plot_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'matplotlib_version': matplotlib.__version__, 'environment_mixing': False}, indent=2) + '\n')


if __name__ == '__main__':
    main()
