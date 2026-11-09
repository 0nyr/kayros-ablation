"""Rank fixed prefixes and describe horizon sensitivity in a completed pilot."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

from pilot_summary import score_trace


def configuration(cell):
    return cell['arm'] + (f"/prefix={cell['prefix_s']}" if cell.get('prefix_s') is not None else '')


def equal_instance_mean(cells, field):
    grouped = defaultdict(list)
    for cell in cells:
        grouped[cell['instance_name']].append(cell[field])
    return statistics.mean(statistics.mean(values) for values in grouped.values())


def prefix_ranking(cells):
    grouped = defaultdict(list)
    for cell in cells:
        if cell['arm'] == 'kayros-full-cplex':
            grouped[cell['prefix_s']].append(cell)
    rows = [{'prefix_s': prefix,
             'restricted_mean_reported_closure_s': equal_instance_mean(rows, 'capped_reported_closure_s'),
             'mean_lambda': equal_instance_mean(rows, 'lambda'),
             'reported_closures': sum(row['reported_closure_s'] is not None for row in rows),
             'scheduled': len(rows)} for prefix, rows in grouped.items()]
    return sorted(rows, key=lambda row: (row['restricted_mean_reported_closure_s'], row['mean_lambda'], row['prefix_s']))


def decide(report, wave):
    if report['provisional']:
        raise ValueError('Complete all scheduled cells before prefix selection')
    cells = report['cells']
    horizons = {cell['horizon_s'] for cell in cells}
    if len(horizons) != 1:
        raise ValueError('A common horizon is required')
    horizon = horizons.pop()
    ranking = prefix_ranking(cells)
    if not ranking:
        raise ValueError('No full-arm prefix candidates')
    replications = sorted({cell['replicate'] for cell in cells})
    instances = sorted({cell['instance_name'] for cell in cells})
    sensitivity = {
        'by_replication': {str(rep): prefix_ranking([c for c in cells if c['replicate'] == rep]) for rep in replications},
        'leave_one_instance_out': {name: prefix_ranking([c for c in cells if c['instance_name'] != name]) for name in instances},
    }
    checkpoints = sorted({horizon * fraction for fraction in (0.25, 0.5, 0.75, 1.0)})
    checkpoint_rows = []
    last_quarter = []
    blocking_cells = []
    for cell in cells:
        result_path = wave / 'cells' / cell['run_id'] / 'result.json' if cell['run_id'] else None
        reasons = []
        result = {}
        if cell.get('status') not in ('completed', 'deadline'):
            reasons.append('summary_status_not_clean')
        if result_path is None or not result_path.is_file():
            reasons.append('missing_result')
        else:
            try:
                result = json.loads(result_path.read_text())
                if not isinstance(result, dict):
                    reasons.append('malformed_result')
                    result = {}
            except (OSError, ValueError) as error:
                reasons.append('unreadable_result:' + type(error).__name__)
        if result_path is not None and result_path.is_file():
            if result.get('status') not in ('completed', 'deadline'):
                reasons.append('result_status_not_clean')
            if result.get('status') == 'completed' and result.get('returncode') != 0:
                reasons.append('completed_with_nonzero_returncode')
            if result.get('status') == 'deadline' and result.get('returncode') not in (0, -9):
                reasons.append('deadline_with_unexpected_returncode')
            if result.get('returncode', 0) is not None and result.get('returncode', 0) > 0:
                reasons.append('worker_exit_failure')
            if result.get('issues'):
                reasons.append('result_issues')
            if result.get('validation_failure'):
                reasons.append('validation_failure')
        validation = result.get('validation', {})
        if not isinstance(validation, dict):
            reasons.append('malformed_validation')
            validation = {}
        invalid_count = max(cell.get('invalid_candidate_count', 0),
                            sum(candidate.get('valid') is not True for candidate in validation.get('candidates', [])))
        if invalid_count:
            reasons.append('invalid_candidates')
        if reasons:
            blocking_cells.append({'task_id': cell['task_id'], 'configuration': configuration(cell),
                'instance_name': cell['instance_name'], 'replicate': cell['replicate'],
                'run_id': cell['run_id'], 'summary_status': cell.get('status'),
                'result_status': result.get('status'), 'result_issues': result.get('issues', []),
                'invalid_candidate_count': invalid_count, 'reasons': reasons})
        envelope = validation.get('checked_envelope', [])
        cutoff = 0.75 * horizon
        before = [point for point in envelope if point['elapsed_s'] <= cutoff]
        after = [point for point in envelope if cutoff < point['elapsed_s'] <= horizon]
        final = envelope[-1]['checked_cost'] if envelope else None
        previous = before[-1]['checked_cost'] if before else None
        last_quarter.append({
            'task_id': cell['task_id'], 'configuration': configuration(cell),
            'instance_name': cell['instance_name'], 'replicate': cell['replicate'],
            'first_feasible_in_last_quarter': previous is None and final is not None,
            'strict_improvement_in_last_quarter': bool(after) and (previous is None or final < previous),
            'relative_improvement_from_quarter_start': (previous - final) / previous if previous and final is not None else None,
            'last_checked_improvement_s': envelope[-1]['elapsed_s'] if envelope else None,
            'closure_in_last_quarter': cell['reported_closure_s'] is not None and cell['reported_closure_s'] > cutoff,
        })
        for checkpoint in checkpoints:
            prefix = [point for point in envelope if point['elapsed_s'] <= checkpoint]
            score = score_trace(prefix, cell['reference_cost'], checkpoint)
            closure = cell['reported_closure_s']
            checkpoint_rows.append({**cell, **score, 'checkpoint_s': checkpoint,
                                    'capped_reported_closure_s': closure if closure is not None and closure <= checkpoint else checkpoint,
                                    'closure_by_checkpoint': closure is not None and closure <= checkpoint})
    checkpoint_groups = []
    for checkpoint in checkpoints:
        grouped = defaultdict(list)
        for cell in checkpoint_rows:
            if cell['checkpoint_s'] == checkpoint:
                grouped[configuration(cell)].append(cell)
        for name, rows in sorted(grouped.items()):
            checkpoint_groups.append({
                'checkpoint_s': checkpoint, 'configuration': name,
                'mean_lambda': equal_instance_mean(rows, 'lambda'),
                'mean_final_squeezed_gap': equal_instance_mean(rows, 'final_squeezed_gap'),
                'feasible': sum(c['final_checked_cost'] is not None for c in rows),
                'reported_closures': sum(c['closure_by_checkpoint'] for c in rows),
                'restricted_mean_reported_closure_s': equal_instance_mean(rows, 'capped_reported_closure_s') if rows[0]['arm'] != 'kayros-heuristics' else None,
                'scheduled': len(rows),
            })
    return {
        'wave_name': report['wave_name'], 'horizon_s': horizon,
        'selection_rule': ['minimum restricted mean reported closure time', 'minimum mean Lambda', 'shorter prefix'],
        'tie_rule': 'Exact equality of computed aggregate values. Sensitivity analyses do not override the approved ordering.',
        'recommendation_eligible': not blocking_cells,
        'blocking_cells': blocking_cells,
        'native_guard_review_warnings': [
            {'task_id': cell['task_id'], 'configuration': configuration(cell),
             'reported_closure_s': cell['reported_closure_s'],
             'native_rejected_priced_columns': cell.get('native_rejected_priced_columns'),
             'stderr_warning_count': cell.get('native_guard_stderr_warning_count', 0)}
            for cell in cells if cell.get('native_guard_warning')],
        'diagnostic_best_prefix_s': ranking[0]['prefix_s'],
        'selected_prefix_s': ranking[0]['prefix_s'] if not blocking_cells else None,
        'prefix_ranking': ranking,
        'sensitivity': sensitivity, 'checkpoints': checkpoint_groups, 'last_quarter_cells': last_quarter,
        'limitations': [
            'The 12-instance pilot is selected for design and is not a random sample of all instances.',
            'Replication and leave-one-instance-out rankings describe stability, not confidence intervals.',
            'Earlier checkpoints are censored views of the same executions, not independent experiments.',
            'Prefixes longer than an earlier checkpoint have not entered exact search at that checkpoint.',
            'Observed stagnation within this horizon does not establish behavior beyond it.',
            'The selected prefix is a design recommendation, not authorization to launch the main campaign.',
            'All scheduled cells remain in diagnostic rankings. Concrete failures or invalid candidates block the recommendation until review and any required reruns.',
            'Source-level closure-census diagnostics alone do not block a recommendation.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    decision = decide(json.loads(args.summary.read_text()), args.wave)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision, indent=2, allow_nan=False) + '\n')
    print(json.dumps({key: decision[key] for key in ['recommendation_eligible', 'selected_prefix_s', 'diagnostic_best_prefix_s', 'blocking_cells', 'prefix_ranking']}, indent=2))


if __name__ == '__main__':
    main()
