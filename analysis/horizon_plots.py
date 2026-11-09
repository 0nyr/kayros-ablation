"""Plot checked focused-pilot gaps without pooling them into the full panel."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


FIRST = '#0072B2'
SECOND = '#D55E00'
DETAIL_LINEAR_THRESHOLD = 1e-3
ARM_ORDER = {'lera-exact': 0, 'kayros-bpca-cplex': 1, 'kayros-bpca-highs': 2,
             'kayros-heuristics': 3, 'kayros-full-cplex': 4}


def configuration_key(row):
    return row['arm'], row.get('prefix_s')


def configuration_order(key):
    arm, prefix = key
    return ARM_ORDER.get(arm, 99), -1 if prefix is None else prefix


def configuration_label(key):
    arm, prefix = key
    labels = {'lera-exact': 'Lera CPLEX', 'kayros-bpca-cplex': 'Cold Kayros CPLEX',
              'kayros-bpca-highs': 'Cold Kayros HiGHS', 'kayros-heuristics': 'ILS'}
    if arm == 'kayros-full-cplex':
        return f'Full CPLEX, {prefix:g} s'
    return labels.get(arm, arm)


def time_label(seconds):
    return f'{seconds / 3600:g} h' if seconds % 3600 == 0 else f'{seconds:g} s'


def checked_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Plot values must be finite checked scalar gaps')
    return value


def prepare_plot_data(report):
    start, end = report['full_horizon_s'], report['focused_horizon_s']
    if not 0 < start < end:
        raise ValueError('Expected ordered focused-pilot horizons')
    cells = defaultdict(list)
    for row in report['cells']:
        cells[row['instance_name'], configuration_key(row)].append(row)
    groups = {}
    for row in report['groups']:
        key = row['instance_name'], configuration_key(row)
        if key in groups:
            raise ValueError('Duplicate plotted configuration')
        groups[key] = row
    if set(cells) != set(groups):
        raise ValueError('Trial and group configuration identities differ')
    instances = []
    for name in report['focused_instances']:
        configurations = []
        keys = sorted((key for instance, key in groups if instance == name), key=configuration_order)
        if len(keys) != 9:
            raise ValueError('Focused plots require all 9 configurations')
        for key in keys:
            group = groups[name, key]
            trials = sorted(cells[name, key], key=lambda row: row['replicate'])
            if (len(trials) != 3 or group['scheduled_trials'] != 3
                    or len({row['replicate'] for row in trials}) != 3):
                raise ValueError('Every plotted configuration requires all 3 trials')
            endpoint_trials, interval_trials = [], []
            for trial in trials:
                first_valid = trial['focused_first_valid_s']
                identity = {'replicate': trial['replicate'],
                            'focused_run_id': trial['focused_run_id'],
                            'full_run_id': trial['full_run_id']}
                endpoint_trials.append({**identity,
                    'first': checked_number(trial['focused_gap_at_first_horizon']),
                    'second': checked_number(trial['focused_gap_at_end']),
                    'first_no_incumbent': first_valid is None or first_valid > start,
                    'second_no_incumbent': first_valid is None or first_valid > end})
                # An incumbent first available at an interval endpoint supplies
                # no positive-duration feasible portion inside that interval.
                interval_trials.append({**identity,
                    'first': checked_number(trial['focused_first_interval_mean_gap']),
                    'second': checked_number(trial['focused_second_interval_mean_gap']),
                    'first_no_incumbent': first_valid is None or first_valid >= start,
                    'second_no_incumbent': first_valid is None or first_valid >= end})
            configurations.append({'arm': key[0], 'prefix_s': key[1],
                'label': configuration_label(key), 'scheduled_trials': 3,
                'endpoint': {'first_mean': checked_number(group['focused_gap_at_first_horizon']),
                             'second_mean': checked_number(group['focused_gap_at_end']),
                             'trials': endpoint_trials},
                'interval': {'first_mean': checked_number(group['focused_first_interval_mean_gap']),
                             'second_mean': checked_number(group['focused_second_interval_mean_gap']),
                             'trials': interval_trials}})
        instances.append({'instance_name': name, 'configurations': configurations})
    return {'full_wave': report['full_wave'], 'focused_wave': report['focused_wave'],
            'first_horizon_s': start, 'second_horizon_s': end,
            'memory_limit_mib': report['memory_limit_mib'],
            'instances': instances,
            'display': {'detail_scale': 'symlog', 'detail_linear_threshold': DETAIL_LINEAR_THRESHOLD,
                        'no_incumbent_gap': 1,
                        'raw_trial_vertical_jitter': [-0.06, 0, 0.06]},
            'interpretation': [
                'Only the focused executions are plotted. Full-panel counterpart IDs are retained for provenance, without pooling their values.',
                'Every configuration retains all 3 trials. Filled marks are their mean and hollow marks show individual trials.',
                'Black crosses mark individual trials with no incumbent at the endpoint or over the entire corresponding interval, using gap 1.',
                'The signed detail scale preserves zero and negative gaps without clipping or changing the metrics.',
                'Interval means describe the same execution trajectory. Startup dilution alone does not establish later improvement.',
                'No global prefix selection or full-panel horizon inference is performed by these plots.']}


def draw_panel(ax, configurations, metric, detail):
    if detail:
        ax.set_xscale('symlog', linthresh=DETAIL_LINEAR_THRESHOLD, linscale=1)
    ax.axvline(0, color='0.45', linewidth=0.8, linestyle=':', zorder=0)
    ax.axvline(1, color='0.65', linewidth=0.8, linestyle='--', zorder=0)
    for index, configuration in enumerate(configurations):
        values = configuration[metric]
        ax.plot([values['first_mean'], values['second_mean']],
                [index - 0.09, index + 0.09], color='0.65', linewidth=1, zorder=1)
        for period, color, marker, offset in [('first', FIRST, 'o', -0.25),
                                               ('second', SECOND, 's', 0.25)]:
            for trial, jitter in zip(values['trials'], (-0.06, 0, 0.06)):
                x, y = trial[period], index + offset + jitter
                if trial[period + '_no_incumbent']:
                    if x != 1:
                        raise ValueError('A missing-incumbent trial must use gap 1')
                    ax.scatter([x], [y], marker='x', c='black', s=28, linewidths=1, zorder=4)
                else:
                    ax.scatter([x], [y], marker=marker, facecolors='none',
                               edgecolors=color, s=20, alpha=0.65, linewidths=0.8, zorder=2)
            ax.scatter([values[period + '_mean']],
                       [index + (-0.09 if period == 'first' else 0.09)],
                       marker=marker, color=color, edgecolors='white', s=42, linewidths=0.4, zorder=3)
    ax.set_yticks(range(len(configurations)), [row['label'] for row in configurations], fontsize=9)
    ax.set_ylim(len(configurations) - 0.5, -0.5)
    ax.grid(axis='x', alpha=0.2)
    ax.margins(x=0.07)
    ax.tick_params(axis='x', labelsize=9)
    ax.set_xlabel('Squeezed gap to frozen reference', fontsize=10)


def render(data, output, detail):
    instances = data['instances']
    if not instances:
        raise ValueError('No focused instances to plot')
    first, second = time_label(data['first_horizon_s']), time_label(data['second_horizon_s'])
    fig, axes = plt.subplots(len(instances), 2, figsize=(16, 4.6 * len(instances) + 1.4), squeeze=False)
    for index, instance in enumerate(instances):
        for column, metric in enumerate(('endpoint', 'interval')):
            ax = axes[index, column]
            draw_panel(ax, instance['configurations'], metric, detail)
            title = (f'Gap at {first} and {second}' if metric == 'endpoint'
                     else f'Mean gap in 0–{first} and {first}–{second}')
            ax.set_title(instance['instance_name'] + ' | ' + title, fontsize=11)
    scale = ('signed detail scale, linear within ±0.001' if detail else 'linear overview')
    fig.suptitle(f"Focused {data['memory_limit_mib'] / 1024:g} GiB horizon comparison | {scale}", fontsize=13)
    handles = [Line2D([], [], marker='o', color=FIRST, linestyle='none', markersize=7,
                      label=f'Mean: {first} endpoint / first interval'),
               Line2D([], [], marker='s', color=SECOND, linestyle='none', markersize=7,
                      label=f'Mean: {second} endpoint / second interval'),
               Line2D([], [], marker='o', markerfacecolor='none', color='0.45', linestyle='none',
                      markersize=5, label='Individual trial (3 per configuration)'),
               Line2D([], [], marker='x', color='black', linestyle='none', markersize=6,
                      label='No incumbent: individual gap 1')]
    fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(0.5, 0.055), ncol=2, frameon=False)
    fig.text(0.5, 0.016,
             'Focused trajectories only. All trials retained. Zero/negative gaps preserved. Interval averages are not independent runs or evidence of late progress by themselves.',
             ha='center', fontsize=9)
    fig.tight_layout(rect=(0, 0.13, 1, 0.95))
    stem = 'horizon-signed-detail' if detail else 'horizon-linear-overview'
    for suffix in ('png', 'pdf'):
        fig.savefig(output / f'{stem}.{suffix}', dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.review.read_bytes()
    data = prepare_plot_data(json.loads(raw))
    data['source_review_sha256'] = hashlib.sha256(raw).hexdigest()
    data['plot_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'horizon-plot-data.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    render(data, args.output, detail=False)
    render(data, args.output, detail=True)


if __name__ == '__main__':
    main()
