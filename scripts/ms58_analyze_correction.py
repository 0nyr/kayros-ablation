"""Summarize all scheduled explicit correction cells without rerunning solvers."""
import argparse
import json
from pathlib import Path

from checker_contract import sha256
from ms58_analyze_pairs import native_work
from probe_runner import atomic_json, load_completed


def relative_change(off, on):
    if off is None or on is None or off == 0:
        return None
    return 100.0 * (on - off) / off


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--archive-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    schedule = json.loads((args.root / 'schedule.json').read_bytes())
    archived = {}
    for item in json.loads(args.archive_audit.read_bytes())['rows']:
        if item.get('arm') != 'lera-exact' or not item.get('native_sidecar'):
            continue
        path = Path(item['native_sidecar'])
        if sha256(path) != item['sha256']:
            raise ValueError(f'Archived sidecar digest mismatch: {path}')
        archived[item['instance']] = native_work(json.loads(path.read_bytes()))
    observations, pairs = [], []
    for lane in schedule['lanes']:
        by_setting = {}
        for setting in lane['order']:
            cell_id = f"{lane['instance']}-correcting-{setting}"
            row_path = args.root / 'rows' / f'{cell_id}.json'
            if not row_path.exists():
                observations.append({'cell_id': cell_id, 'status': 'pending'})
                continue
            row = json.loads(row_path.read_bytes())
            if row['instance'] != lane['instance'] or row['correcting'] is not bool(setting):
                raise ValueError(f'Scheduled row mismatch: {cell_id}')
            row['row_sha256'] = sha256(row_path)
            if row.get('run_id'):
                cell = args.root / 'cells' / row['run_id']
                result = load_completed(cell, expected_run_id=row['run_id'])
                native = result['validation'].get('native_result', {})
                if native.get('sha256'):
                    native_path = (cell / native['relative_path']).resolve()
                    if sha256(native_path) != native['sha256']:
                        raise ValueError(f'Native digest mismatch: {cell_id}')
                    actual = native_work(json.loads(native_path.read_bytes()))
                    if actual != row['native_work']:
                        raise ValueError(f'Stored native summary mismatch: {cell_id}')
                    row['native_sha256'] = native['sha256']
                    previous = archived.get(lane['instance'], {})
                    row['archived_root_work_matches'] = actual.get('root_work_signature') == previous.get('root_work_signature')
                    row['archived_whole_work_matches'] = actual.get('whole_work_signature') == previous.get('whole_work_signature')
            observations.append(row)
            by_setting[setting] = row
        pair = {'instance': lane['instance'], 'order': lane['order'], 'cpu': lane['cpu'],
                'horizon_s': lane['horizon_s'], 'complete_pair': len(by_setting) == 2,
                'scope': 'One exploratory sequential pair, no timing uncertainty estimate'}
        if all(setting in by_setting and by_setting[setting].get('native_work', {}).get('available')
               for setting in [0, 1]):
            off, on = by_setting[0], by_setting[1]
            pair['same_checked_cost'] = off['checked_cost'] == on['checked_cost']
            metrics = {}
            for name, off_value, on_value in [
                ('reported_closure_s', off['reported_closure_s'], on['reported_closure_s']),
                ('root_labeling_time_s', off['native_work']['root_labeling_time_s'], on['native_work']['root_labeling_time_s']),
                *[(field, off['native_work']['whole_run'].get(field), on['native_work']['whole_run'].get(field))
                  for field in ['root_time', 'root_variable_count', 'final_variable_count', 'cut_count', 'lp_time', 'pricing_time']],
                *[(field, sum(level[field] for level in off['native_work']['root_labeling'].values()),
                   sum(level[field] for level in on['native_work']['root_labeling'].values()))
                  for field in ['calls', 'processed_count', 'extended_count', 'enumerated_count', 'corrected_count']]
            ]:
                metrics[name] = {'off': off_value, 'on': on_value,
                                 'on_minus_off': on_value - off_value if off_value is not None and on_value is not None else None,
                                 'on_relative_to_off_percent': relative_change(off_value, on_value)}
            pair['metrics'] = metrics
        pairs.append(pair)
    atomic_json(args.output, {'scheduled_count': schedule['scheduled_count'],
        'observations': observations, 'pairs': pairs,
        'qualification': 'Reported closure with checked incumbents does not independently establish exhaustive pricing. Search trajectories differ. Timing changes are not matched-work throughput estimates.'})


if __name__ == '__main__':
    main()
