"""Describe the separate focused horizon pilot without pooling it into the panel.

First feasibility also improves the missing-incumbent state, so first-feasible and strict-improvement counts overlap and must not be added.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

from pilot_summary import score_trace


def logical_key(cell):
    return cell['instance_name'], cell['arm'], cell.get('prefix_s'), cell['replicate']


def checkpoint(envelope, reference, elapsed):
    return score_trace([point for point in envelope if point['elapsed_s'] <= elapsed], reference, elapsed)


def interval_mean_gap(envelope, reference, start, end):
    if not 0 < start < end:
        raise ValueError('Expected a positive ordered interval')
    first = checkpoint(envelope, reference, start)
    last = checkpoint(envelope, reference, end)
    return (end * last['lambda'] - start * first['lambda']) / (end - start)


def read_envelope(wave, cell):
    if not cell['run_id']:
        return []
    result = json.loads((wave / 'cells' / cell['run_id'] / 'result.json').read_bytes())
    if result['run_id'] != cell['run_id']:
        raise ValueError('Result identity mismatch')
    return result.get('validation', {}).get('checked_envelope', [])


def review(full, focused, full_wave, focused_wave):
    if full['provisional'] or focused['provisional']:
        raise ValueError('Both cohorts must be complete before a horizon comparison')
    if (full['checker'] != focused['checker'] or
            full['reference_manifest_sha256'] != focused['reference_manifest_sha256']):
        raise ValueError('Checker or reference differs between cohorts')
    full_cells, focus_cells = full['cells'], focused['cells']
    full_map = {logical_key(cell): cell for cell in full_cells}
    if len(full_map) != len(full_cells) or len({logical_key(cell) for cell in focus_cells}) != len(focus_cells):
        raise ValueError('Repeated logical cell within a cohort')
    starts = {cell['horizon_s'] for cell in full_cells}
    ends = {cell['horizon_s'] for cell in focus_cells}
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError('Each cohort must have a common horizon')
    start, end = starts.pop(), ends.pop()
    if not 0 < start < end:
        raise ValueError('Focused horizon must exceed the full-panel horizon')
    caps = {cell['memory_mb'] for cell in full_cells + focus_cells}
    if len(caps) != 1:
        raise ValueError('Horizon review requires the same declared memory cap')
    full_instances = {cell['instance_name'] for cell in full_cells}
    focus_instances = {cell['instance_name'] for cell in focus_cells}
    if not focus_instances < full_instances:
        raise ValueError('Expected a separate proper subset of the full panel')
    expected_keys = {key for key in full_map if key[0] in focus_instances}
    if {logical_key(cell) for cell in focus_cells} != expected_keys:
        raise ValueError('Focused cohort must retain every configuration and trial for its instances')
    rows = []
    for cell in focus_cells:
        other = full_map.get(logical_key(cell))
        if other is None or (cell['seed'], cell['instance_sha256'], cell['reference_cost']) != (
                other['seed'], other['instance_sha256'], other['reference_cost']):
            raise ValueError('Focused cell lacks its exact panel counterpart')
        envelope = read_envelope(focused_wave, cell)
        original = read_envelope(full_wave, other)
        a = checkpoint(original, cell['reference_cost'], start)
        b = checkpoint(envelope, cell['reference_cost'], start)
        c = checkpoint(envelope, cell['reference_cost'], end)
        closure = cell['reported_closure_s']
        rows.append({
            'instance_name': cell['instance_name'], 'arm': cell['arm'],
            'prefix_s': cell.get('prefix_s'), 'replicate': cell['replicate'],
            'full_run_id': other['run_id'], 'focused_run_id': cell['run_id'],
            'full_status': other['status'], 'focused_status': cell['status'],
            'full_horizon_mean_gap': a['lambda'],
            'focused_first_interval_mean_gap': b['lambda'],
            'focused_second_interval_mean_gap': interval_mean_gap(envelope, cell['reference_cost'], start, end),
            'focused_full_horizon_mean_gap': c['lambda'],
            'focused_gap_at_first_horizon': b['final_squeezed_gap'],
            'focused_gap_at_end': c['final_squeezed_gap'],
            'focused_first_valid_s': c['first_valid_s'],
            # First feasibility is included in strict primal improvement.
            'focused_first_feasible_in_second_interval': b['final_checked_cost'] is None and c['final_checked_cost'] is not None,
            'focused_strict_improvement_in_second_interval': c['final_checked_cost'] is not None and (
                b['final_checked_cost'] is None or c['final_checked_cost'] < b['final_checked_cost']),
            'focused_closure_s': closure,
            'focused_closed_by_first_horizon': closure is not None and closure <= start,
            'focused_closed_in_second_interval': closure is not None and start < closure <= end,
            'focused_native_guard_warning': cell.get('native_guard_warning', False),
        })
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['instance_name'], row['arm'], row['prefix_s']].append(row)
    groups = []
    averages = ['full_horizon_mean_gap', 'focused_first_interval_mean_gap',
                'focused_second_interval_mean_gap', 'focused_full_horizon_mean_gap',
                'focused_gap_at_first_horizon', 'focused_gap_at_end']
    counts = ['focused_first_feasible_in_second_interval', 'focused_strict_improvement_in_second_interval',
              'focused_closed_by_first_horizon', 'focused_closed_in_second_interval', 'focused_native_guard_warning']
    for (instance, arm, prefix), values in sorted(grouped.items()):
        groups.append({'instance_name': instance, 'arm': arm, 'prefix_s': prefix,
                       'scheduled_trials': len(values),
                       **{key: statistics.mean(value[key] for value in values) for key in averages},
                       **{key: sum(value[key] for value in values) for key in counts}})
    return {
        'full_wave': full['wave_name'], 'focused_wave': focused['wave_name'],
        'full_horizon_s': start, 'focused_horizon_s': end, 'memory_limit_mib': caps.pop(),
        'focused_instances': sorted(focus_instances), 'groups': groups, 'cells': rows,
        'global_prefix_selection': 'Not performed here. Use the complete full-panel selection analysis.',
        'interpretation': [
            'Focused cases were selected from the observed allocation failures and are reported separately.',
            'First-interval comparisons across waves are separate executions with different placement and concurrent load.',
            'Within-focused first/second intervals describe the same retained execution trajectory.',
            'A lower normalized full-horizon Lambda can arise from diluting startup cost without any later improvement.',
            'Second-interval mean gap and strict improvement/closure counts expose actual later progress.',
            'First-feasible cells also count as strict improvements from the missing-incumbent state. These overlapping counts must not be added.',
            'No focused result is substituted into the full-panel aggregate. Failed runs remain in every denominator.',
            'A clean result on these cases does not guarantee that every main instance will fit the cap.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['full-summary', 'focused-summary', 'full-wave', 'focused-wave', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = review(json.loads(args.full_summary.read_bytes()), json.loads(args.focused_summary.read_bytes()),
                    args.full_wave, args.focused_wave)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
