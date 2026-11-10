"""Audit and summarize ms58 paired lanes, retaining every scheduled cell.

Use --lanes LANE... or --root DIRECTORY to find lane schedule.json files.
The output is readable JSON. No solver or route checker is executed. Checked
witness claims are taken only from hash-verified stored validation records.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import statistics

from checker_contract import CHECKER_COMMIT, CHECKER_TREE, sha256
from probe_runner import digest, load_completed


COUNTERS = ['processed_count', 'enumerated_count', 'extended_count', 'dominated_count', 'corrected_count']
WORK_FIELDS = ['nodes_closed', 'nodes_open', 'cut_count', 'root_variable_count', 'final_variable_count',
               'root_constraint_count', 'final_constraint_count', 'root_lp_value', 'best_int_value', 'best_bound']
EXPECTED_CHECKER = {'package': 'mamut-routing-lib', 'version': '0.11.0', 'commit': CHECKER_COMMIT,
                    'python_files': 23, 'installed_python_tree_sha256': CHECKER_TREE}


def sum_if_complete(values):
    return sum(values) if values and all(value is not None for value in values) else None


def native_work(raw):
    output = raw.get('exact', raw.get('native_result', {}))
    log = output.get('exact_log', output.get('Exact', {}))
    root = log.get('root_log', {})
    iterations = root.get('iterations', [])
    levels = defaultdict(list)
    signature_iterations = []
    for iteration in iterations:
        levels[iteration.get('iteration_name', 'unknown')].append(iteration)
        signature_iterations.append({key: iteration.get(key) for key in ['iteration_name', 'status']} |
            {direction: {key: iteration.get(direction, {}).get(key) for key in COUNTERS + ['status', 'count_by_length']}
             for direction in ['forward', 'backward']})
    labeling = {}
    for level, calls in levels.items():
        labeling[level] = {'calls': len(calls), 'time_s': sum_if_complete([call.get('time') for call in calls]),
                           **{key: sum_if_complete([call.get(direction, {}).get(key)
                              for call in calls for direction in ['forward', 'backward']]) for key in COUNTERS}}
    root_signature = {'iteration_count': root.get('iteration_count'), 'columns_added': root.get('columns_added'),
                      'status': root.get('status'), 'iterations': signature_iterations}
    whole_signature = {key: log.get(key) for key in WORK_FIELDS}
    return {'available': bool(log), 'status': log.get('status'),
            'whole_run': {key: log.get(key) for key in WORK_FIELDS +
                          ['time', 'root_time', 'lp_time', 'pricing_time', 'branching_time', 'cut_time']},
            'root_cg': {key: root.get(key) for key in ['status', 'iteration_count', 'columns_added', 'time', 'lp_time', 'pricing_time']},
            'root_labeling': labeling,
            'root_labeling_time_s': sum_if_complete([iteration.get('time') for iteration in iterations]),
            'root_work_signature': digest(root_signature) if root and iterations else None,
            'whole_work_signature': digest(whole_signature) if log else None}


def closure_and_resource(result, events, stderr):
    validation = result.get('validation', {})
    checks = {candidate['sequence']: candidate for candidate in validation.get('candidates', [])}
    resource = None
    if any(event.get('status') == 'MemoryLimitReached' for event in events):
        resource = 'native_memory_limit_reported'
    elif re.search(r'(^|\n)MemoryError(?::[^\n]*)?\s*$', stderr):
        resource = 'python_memory_allocation_failure'
    elif 'std::bad_alloc' in stderr:
        resource = 'native_allocation_failure_reported'
    usable = result.get('status') in ['completed', 'deadline'] and not result.get('issues') and not resource
    usable = usable and not any(event['event'] == 'integrity' for event in events)
    closure_time = None
    previous_candidate = None
    for event in events:
        if event['event'] == 'candidate':
            previous_candidate = event
        elif (event['event'] == 'closure' and usable and previous_candidate is not None
              and checks.get(previous_candidate['sequence'], {}).get('valid') is True):
            closure_time = event['elapsed_s']
            break
    return closure_time, resource


def observation(lane, plan, task):
    base = {**task, 'lane': str(lane), 'host': plan['host'], 'cpu_core': plan['cpu_core'],
            'horizon_s': plan['horizon_s'], 'memory_mb': plan['memory_mb'],
            'reported_closure_s': None, 'capped_closure_s': plan['horizon_s'],
            'native_work': {'available': False}, 'audit_ok': False}
    manifest = None
    if task.get('manifest_path'):
        manifest = json.loads((lane / 'manifests' / Path(task['manifest_path']).name).read_bytes())
        base.update(source_id=manifest.get('source_id'),
                    runtime_identity=digest(sorted(manifest['runtime_files'].values())),
                    diagnostic_environment=plan.get('diagnostic_environment', {}))
    path = lane / 'rows' / f"{task['cell_id']}.json"
    if not path.is_file():
        return {**base, 'status': 'pending'}
    row = json.loads(path.read_bytes())
    if any(row.get(key) != value for key, value in task.items()):
        raise ValueError('Scheduled row identity mismatch')
    base.update(status=row['status'], reason=row.get('reason'))
    if not row.get('run_id'):
        return {**base, 'audit_ok': True}
    cell = lane / 'cells' / row['run_id']
    identity = json.loads((cell / 'identity.json').read_bytes())
    if manifest is None:
        raise ValueError('Scheduled manifest path missing')
    if digest(manifest) != identity['manifest_hash'] or manifest.get('checker') != plan['checker']:
        raise ValueError('Scheduled manifest/checker mismatch')
    expected_task = {key: value for key, value in task.items() if key != 'manifest_path'}
    if manifest.get('paired_diagnostic_cell') != expected_task:
        raise ValueError('Manifest pair identity mismatch')
    params = identity['parameters']
    input_record = plan['inputs'][task['instance']]
    expected = {'instance_name': task['instance'], 'instance_sha256': input_record['sha256'],
                'atf_sha256': input_record['atf_sha256'], 'memory_mb': plan['memory_mb'],
                'memory_cap_bytes': plan['memory_mb'] * 1024 ** 2, 'cpu_affinity': [plan['cpu_core']],
                'repetition': task['repetition'], 'search_seed': None, 'prefix_seconds': None,
                'arm': 'lera-exact' if task['variant'] == 'upstream' else 'kayros-bpca-cplex'}
    if any(params.get(key) != value for key, value in expected.items()) or identity['horizon_s'] != plan['horizon_s']:
        raise ValueError('Scheduled parameter mismatch')
    result = load_completed(cell, expected_run_id=row['run_id'])
    validation = result.get('validation', {})
    if validation.get('checker') != plan['checker']:
        raise ValueError('Stored validation checker mismatch')
    events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
    stderr = (cell / 'stderr.log').read_text(errors='replace')
    closure, resource = closure_and_resource(result, events, stderr)
    native = validation.get('native_result', {})
    work = {'available': False}
    if native.get('sha256') is not None:
        raw = json.loads((cell / native['relative_path']).read_bytes())
        work = native_work(raw)
    root_bound = next((event for event in events if event['event'] == 'bound' and event.get('source') == 'root_complete'), None)
    root_console = stderr.split('Solving BC with existing columns')[0]
    table = re.findall(r'^\s*(\d+(?:\.\d+)?)\s+(\d+)\s+([0-9.eE+-]+)\s+(\d+)\s*$', root_console, re.M)
    guards = [event['checker_infeasible_columns'] for event in events if event.get('checker_infeasible_columns') is not None]
    raw_peak_rss = result.get('sampled_solver_peak_rss_kib')
    peak_rss = raw_peak_rss if isinstance(raw_peak_rss, (int, float)) and raw_peak_rss > 0 else None
    return {**base, 'audit_ok': True, 'status': result['status'], 'run_id': row['run_id'],
            'returncode': result.get('returncode'),
            'reported_closure_s': closure, 'capped_closure_s': closure if closure is not None else plan['horizon_s'],
            'resource_failure_kind': resource, 'raw_proof_status': validation.get('proof', {}).get('status'),
            'raw_closure_count': sum(event['event'] == 'closure' for event in events),
            'checked_cost': validation.get('best_checked_cost'),
            'invalid_candidate_count': sum(candidate.get('valid') is not True for candidate in validation.get('candidates', [])),
            'native_guard_count': max(guards) if guards else None,
            'native_guard_warning': bool(guards and max(guards)) or 'skipped checker-infeasible priced route' in stderr,
            'sampled_solver_peak_rss_kib': peak_rss, 'raw_sampled_solver_peak_rss_kib': raw_peak_rss,
            'rss_measurement': result.get('rss_measurement'),
            'rss_scope': 'python_adapter_only_native_child_excluded' if task['variant'] == 'upstream' else 'kayros_worker_including_native_extension',
            'issues': result.get('issues', []), 'native_work': work,
            'root_complete_outer_s': root_bound['elapsed_s'] if root_bound else None,
            'last_root_console_row': table[-1] if table else None,
            'events_sha256': result['events_sha256'], 'stderr_sha256': sha256(cell / 'stderr.log')}


def compare_pair(rows):
    reference = next(row for row in rows if row['variant'] == 'reference')
    other = next(row for row in rows if row['variant'] != 'reference')
    both_closed = reference['reported_closure_s'] is not None and other['reported_closure_s'] is not None
    root_reference = reference['native_work'].get('root_work_signature')
    root_other = other['native_work'].get('root_work_signature')
    root_complete = (reference['native_work'].get('root_cg', {}).get('status') == 'Optimum'
                     and other['native_work'].get('root_cg', {}).get('status') == 'Optimum')
    work_comparison = ('matching_recorded_root_work' if root_reference == root_other else 'changed_recorded_root_work') if root_complete and root_reference and root_other else 'unavailable_or_incomplete'
    reference_time = reference['native_work'].get('root_labeling_time_s')
    other_time = other['native_work'].get('root_labeling_time_s')
    ratio = reference['reported_closure_s'] / other['reported_closure_s'] if both_closed and other['reported_closure_s'] > 0 else None
    reference_rss = reference.get('sampled_solver_peak_rss_kib')
    other_rss = other.get('sampled_solver_peak_rss_kib')
    rss_comparable = (reference_rss is not None and other_rss is not None
                      and reference.get('rss_scope') == other.get('rss_scope'))
    return {'pair_id': reference['pair_id'], 'instance': reference['instance'], 'comparator': other['variant'],
            'reference_source_id': reference.get('source_id'), 'comparator_source_id': other.get('source_id'),
            'reference_runtime_identity': reference.get('runtime_identity'), 'comparator_runtime_identity': other.get('runtime_identity'),
            'memory_mb': reference.get('memory_mb'), 'diagnostic_environment': reference.get('diagnostic_environment', {}),
            'repetition': reference['repetition'], 'lane': reference['lane'], 'horizon_s': reference['horizon_s'],
            'order': [row['variant'] for row in sorted(rows, key=lambda row: row['position'])],
            'reference_status': reference['status'], 'comparator_status': other['status'],
            'reference_audit_ok': reference['audit_ok'], 'comparator_audit_ok': other['audit_ok'],
            'reference_closure_s': reference['reported_closure_s'], 'comparator_closure_s': other['reported_closure_s'],
            'reference_capped_s': reference['capped_closure_s'], 'comparator_capped_s': other['capped_closure_s'],
            'comparator_minus_reference_capped_s': other['capped_closure_s'] - reference['capped_closure_s'],
            'both_closed': both_closed, 'reference_over_comparator_closure_ratio': ratio,
            'recorded_root_work_comparison': work_comparison,
            'whole_work_counters_match': (reference['native_work'].get('whole_work_signature') == other['native_work'].get('whole_work_signature')) if both_closed and reference['native_work'].get('whole_work_signature') and other['native_work'].get('whole_work_signature') else None,
            'reference_root_labeling_s': reference_time, 'comparator_root_labeling_s': other_time,
            'reference_sampled_peak_rss_kib': reference_rss, 'comparator_sampled_peak_rss_kib': other_rss,
            'sampled_rss_scopes_comparable': rss_comparable,
            'comparator_minus_reference_sampled_peak_rss_kib': other_rss - reference_rss if rss_comparable else None,
            'root_labeling_ratio_when_recorded_work_matches': reference_time / other_time if work_comparison == 'matching_recorded_root_work' and reference_time is not None and other_time else None}


def summarize(lanes):
    observations = []
    lane_digests = {}
    for lane in lanes:
        plan = json.loads((lane / 'schedule.json').read_bytes())
        if len(plan['scheduled_cells']) != plan['scheduled_count']:
            raise ValueError('Scheduled denominator mismatch')
        if plan.get('checker') != EXPECTED_CHECKER:
            raise ValueError('Lane checker is outside the pinned v0.11 contract')
        lane_digests[str(lane)] = sha256(lane / 'schedule.json')
        seen = set()
        for task in plan['scheduled_cells']:
            if task['cell_id'] in seen:
                raise ValueError('Duplicate scheduled cell')
            seen.add(task['cell_id'])
            try:
                observations.append(observation(lane, plan, task))
            except (OSError, ValueError, KeyError, TypeError) as error:
                observations.append({**task, 'lane': str(lane), 'horizon_s': plan['horizon_s'],
                    'status': 'audit_failure', 'audit_ok': False, 'reason': f'{type(error).__name__}: {error}',
                    'reported_closure_s': None, 'capped_closure_s': plan['horizon_s'], 'native_work': {'available': False}})
    grouped_pairs = defaultdict(list)
    for row in observations:
        grouped_pairs[(row['lane'], row['pair_id'])].append(row)
    pairs = []
    for rows in grouped_pairs.values():
        if len(rows) != 2 or {row['position'] for row in rows} != {1, 2}:
            raise ValueError('Malformed pair schedule')
        pairs.append(compare_pair(rows))
    groups = defaultdict(list)
    for pair in pairs:
        groups[(pair['instance'], pair['comparator'], pair['horizon_s'],
                pair['reference_runtime_identity'] or '', pair['comparator_runtime_identity'] or '',
                pair['memory_mb'] or 0, digest(pair['diagnostic_environment']))].append(pair)
    summary = []
    for (instance, comparator, horizon, reference_runtime, comparator_runtime, memory_mb, environment), rows in sorted(groups.items()):
        ratios = [row['reference_over_comparator_closure_ratio'] for row in rows if row['reference_over_comparator_closure_ratio'] is not None]
        rss_differences = [row['comparator_minus_reference_sampled_peak_rss_kib'] for row in rows
                           if row['comparator_minus_reference_sampled_peak_rss_kib'] is not None]
        summary.append({'instance': instance, 'comparator': comparator, 'horizon_s': horizon, 'scheduled_pairs': len(rows),
            'reference_source_id': rows[0]['reference_source_id'], 'comparator_source_id': rows[0]['comparator_source_id'],
            'reference_runtime_identity': reference_runtime or None, 'comparator_runtime_identity': comparator_runtime or None,
            'memory_mb': memory_mb, 'diagnostic_environment': rows[0]['diagnostic_environment'],
            'reference_closures': sum(row['reference_closure_s'] is not None for row in rows),
            'comparator_closures': sum(row['comparator_closure_s'] is not None for row in rows),
            'both_closed_pairs': sum(row['both_closed'] for row in rows),
            'reference_mean_capped_s': statistics.mean(row['reference_capped_s'] for row in rows),
            'comparator_mean_capped_s': statistics.mean(row['comparator_capped_s'] for row in rows),
            'mean_paired_comparator_minus_reference_capped_s': statistics.mean(row['comparator_minus_reference_capped_s'] for row in rows),
            'median_reference_over_comparator_closure_ratio_both_closed_only': statistics.median(ratios) if ratios else None,
            'matching_recorded_root_work_pairs': sum(row['recorded_root_work_comparison'] == 'matching_recorded_root_work' for row in rows),
            'changed_recorded_root_work_pairs': sum(row['recorded_root_work_comparison'] == 'changed_recorded_root_work' for row in rows),
            'comparable_sampled_rss_pairs': len(rss_differences),
            'mean_comparator_minus_reference_sampled_peak_rss_kib': statistics.mean(rss_differences) if rss_differences else None,
            'unaudited_pairs': sum(not row['reference_audit_ok'] or not row['comparator_audit_ok'] for row in rows)})
    return {'schema_version': 1, 'scheduled_cells': len(observations), 'scheduled_pairs': len(pairs),
            'lane_schedule_sha256': lane_digests,
            'status_counts': dict((status, sum(row['status'] == status for row in observations)) for status in sorted({row['status'] for row in observations})),
            'limitations': ['All scheduled cells remain in capped means, including failures and pending cells.',
                'A closure requires a preceding independently checked witness and usable stored result. It remains solver-reported computational closure, not a formal certificate.',
                'No closure ratio is computed unless both members close. A timeout is not a measured completion time.',
                'Root work equality compares recorded call sequences, counters and termination statuses. It does not establish identical dual vectors or pricing tasks.',
                'Root labeling time ratios with matching counters are diagnostic only. Controlled replay is needed to attribute implementation throughput.',
                'Missing native sidecars and counters remain missing. Last console rows are partial observations, never completed exact pricing.',
                'RSS uses sampled worker-process VmHWM, so short-lived peaks can be missed. Zero means no positive sample and remains missing. Upstream adapter-only measurements exclude its native child and are not compared with Kayros worker RSS. VmSize is not collected by this harness.',
                'Short selected-panel diagnostics do not establish full-horizon proof-speed parity or general benchmark performance.'],
            'groups': summary, 'pairs': pairs, 'observations': observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--lanes', type=Path, nargs='+')
    sources.add_argument('--root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    lanes = sorted({path.resolve() for path in args.lanes}) if args.lanes else sorted({path.parent.resolve() for path in args.root.rglob('schedule.json')})
    if not lanes:
        parser.error('No lane schedule.json files found')
    result = summarize(lanes)
    result['analysis_sha256'] = sha256(Path(__file__))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status_counts': result['status_counts'], 'groups': result['groups']}, indent=2))


if __name__ == '__main__':
    main()
