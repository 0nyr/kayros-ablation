"""Render separate-size closure, mean convergence and 1% reference-goal curves."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

from ms58_dabia_summary import ARM_IDS
from plot_style import arm_style

LABELS = {'lera-minimal': 'Lera CPLEX, initialized',
          'kayros-full-cplex-prefix5s': 'Full CPLEX, 5 s ILS',
          'kayros-full-cplex-prefix15s': 'Full CPLEX, 15 s ILS',
          'kayros-cold-cplex': 'Cold CPLEX', 'kayros-cold-highs': 'Cold HiGHS'}


def curves(cells):
    if not cells or len({c['horizon_s'] for c in cells}) != 1:
        raise ValueError('A nonempty cohort with a common horizon is required')
    horizon = cells[0]['horizon_s']
    trials = defaultdict(int)
    for cell in cells:
        trials[cell['instance_key']] += 1
    weights = [1 / (len(trials) * trials[c['instance_key']]) for c in cells]
    changes = defaultdict(list)
    for index, cell in enumerate(cells):
        for point in cell['envelope']:
            changes[point['elapsed_s']].append((index, 'cost', point['checked_cost']))
        if cell['reported_closure_s'] is not None:
            changes[cell['reported_closure_s']].append((index, 'closure', True))
    times = sorted({0., horizon} | set(changes))
    if any(not 0 <= t <= horizon for t in times):
        raise ValueError('Observation outside the process-launch horizon')
    costs, closed = [None] * len(cells), [False] * len(cells)
    data = {key: [] for key in ['elapsed_s', 'mean_squeezed_gap', 'mean_raw_gap_percent',
                                'checked_incumbent_fraction', 'goal_1pct_fraction', 'reported_closure_fraction']}
    for time in times:
        for index, kind, value in changes[time]:
            if kind == 'cost':
                costs[index] = value
            else:
                closed[index] = True
        squeezed, raw, coverage, goal = [], [], [], []
        for index, cost in enumerate(costs):
            reference = cells[index]['reference_cost']
            present = cost is not None
            squeezed.append(weights[index] * ((cost - reference) / (cost + reference) if present else 1.))
            raw.append(weights[index] * ((cost - reference) / reference if present else 0.))
            coverage.append(weights[index] * present)
            goal.append(weights[index] * (present and (cost - reference) / reference <= .01))
        data['elapsed_s'].append(time)
        data['mean_squeezed_gap'].append(math.fsum(squeezed))
        data['mean_raw_gap_percent'].append(100 * math.fsum(raw) if all(c is not None for c in costs) else None)
        data['checked_incumbent_fraction'].append(math.fsum(coverage))
        data['goal_1pct_fraction'].append(math.fsum(goal))
        data['reported_closure_fraction'].append(math.fsum(w * c for w, c in zip(weights, closed)))
    return {**data, 'scheduled_cells': len(cells), 'instance_trial_counts': dict(trials),
            'contradicted_cells': sum(c.get('consistency_audit', {}).get('status') == 'contradicted' for c in cells),
            'unsupported_closure_cells': sum(c.get('consistency_audit', {}).get('status') == 'unsupported' for c in cells),
            'guarded_closures': sum(c.get('reported_closure_with_native_guard_warning', False) for c in cells)}


def style(cell):
    if cell['arm_id'] == 'kayros-full-cplex-prefix5s':
        return {'color': '#238b45', 'linestyle': '--', 'linewidth': 1.8}
    return arm_style(cell['arm'], cell['prefix_s'])


def render(report, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    if report['provisional'] or report['audit_failure_count']:
        raise ValueError('Comparative plots require a completed, audited scheduled population')
    if report.get('consistency_audit_completed') is not True:
        raise ValueError('Cross-run and frozen-reference proof consistency audit is required')
    grouped = defaultdict(dict)
    for size in sorted({c['num_customers'] for c in report['cells']}):
        for arm_id in ARM_IDS:
            cells = [c for c in report['cells'] if c['num_customers'] == size and c['arm_id'] == arm_id]
            if not cells:
                raise ValueError(f'Missing arm {arm_id} for n={size}')
            grouped[size][arm_id] = {'style': style(cells[0]), **curves(cells)}
    for size, groups in grouped.items():
        destination = output / f'n{size}'
        destination.mkdir(parents=True, exist_ok=True)
        for logarithmic in [False, True]:
            suffix = '-log-time' if logarithmic else ''
            for basename, fields, labels in [
                    ('closure-overview', ['reported_closure_fraction'], ['Fraction with reported closure and checked witness']),
                    ('mean-convergence', ['mean_squeezed_gap', 'mean_raw_gap_percent', 'checked_incumbent_fraction'],
                     ['Mean squeezed reference gap', 'Mean raw reference gap (%)', 'Fraction with a checked incumbent']),
                    ('goal-gap-1pct', ['goal_1pct_fraction'], ['Fraction attaining raw reference gap ≤ 1%'])]:
                fig, axes = plt.subplots(1, len(fields), figsize=(15 if len(fields) > 1 else 10, 5.6), squeeze=False)
                for arm_id, group in groups.items():
                    label = LABELS[arm_id]
                    if basename == 'closure-overview' and group['guarded_closures']:
                        label += f" [{group['guarded_closures']} guard warnings]"
                    if basename == 'closure-overview' and group['contradicted_cells']:
                        count = group['contradicted_cells']
                        label += f" [{count} contradicted run{'s' if count != 1 else ''}]"
                    if basename == 'closure-overview' and group['unsupported_closure_cells']:
                        label += f" [{group['unsupported_closure_cells']} unsupported]"
                    for ax, field, ylabel in zip(axes[0], fields, labels):
                        values = [float('nan') if value is None else value for value in group[field]]
                        ax.step(group['elapsed_s'], values, where='post', label=label, **group['style'])
                        ax.set(xlabel='Elapsed time from process launch (s)', ylabel=ylabel)
                        ax.grid(alpha=.25, which='both')
                        if logarithmic:
                            ax.set_xscale('log')
                            ax.set_xlim(.01, report['cells'][0]['horizon_s'])
                        else:
                            ax.set_xlim(0, report['cells'][0]['horizon_s'])
                        if field.endswith('fraction'):
                            ax.set_ylim(-.02, 1.02)
                role = 'primary development cohort' if size == 100 else 'size-specific pilot'
                fixture = 'SYNTHETIC FIXTURE | ' if report.get('synthetic_fixture') else ''
                fig.suptitle(f'{fixture}Dabia2013, n={size} | {role}')
                handles, names = axes[0][0].get_legend_handles_labels()
                flagged = basename == 'closure-overview' and any(
                    group['guarded_closures'] or group['contradicted_cells'] or group['unsupported_closure_cells']
                    for group in groups.values())
                fig.legend(handles, names, loc='lower center', bbox_to_anchor=(.5, .05),
                           ncol=2 if flagged else 3, frameon=False)
                note = 'All scheduled cells retained. Equal instance weights, equal trial weights within instances. Quality attainment is not proof.'
                if basename == 'mean-convergence':
                    note = 'Missing incumbent gives squeezed gap 1. Raw mean starts only at full coverage. Checked incumbents persist after failures.'
                elif basename == 'closure-overview':
                    note = 'Reported closure after feasible-witness consistency checks, not independent proof certification. Contradicted runs remain unclosed.'
                fig.text(.5, .012, note, ha='center', fontsize=8.5)
                fig.tight_layout(rect=(0, .23, 1, .94))
                for extension in ['png', 'pdf', 'svg']:
                    fig.savefig(destination / f'{basename}{suffix}.{extension}', dpi=240)
                plt.close(fig)
        payload = {'num_customers': size, 'wave_name': report['wave_name'], 'groups': groups,
                   'reference_manifests': report['reference_manifests'], 'checker': report['checker'],
                   'feasible_ceilings': report.get('feasible_ceilings'),
                   'consistency_failure_count': report.get('consistency_failure_count'),
                   'wave_sha256': report['wave_sha256'], 'summary_sha256': report['summary_sha256'],
                   'plot_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   'plot_contract_sources_sha256': {name: hashlib.sha256(
                       Path(__file__).with_name(name).read_bytes()).hexdigest()
                       for name in ['closure_plots.py', 'pilot_plots.py', 'raw_gap_plots.py', 'plot_style.py']},
                   'matplotlib_version': matplotlib.__version__,
                   'interpretation': 'Raw-gap means retain the entire fixed population and are undefined before every scheduled run has an incumbent. No arm or empty run is filtered. Below-reference gaps remain negative. Incumbent-quality and closure curves are separate.'}
        (destination / 'plot-data.json').write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.summary.read_bytes()
    report = json.loads(raw)
    report['summary_sha256'] = hashlib.sha256(raw).hexdigest()
    render(report, args.output)


if __name__ == '__main__':
    main()
