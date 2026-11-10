"""Read-only dominance snapshots. Incomplete pricing remains unfinished."""
from collections import Counter
import json
from pathlib import Path

from checker_contract import sha256
from probe_runner import digest

SCHEMA = 'kayros-ms58-dominance-profile-v1'
BASE = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
KINDS = ('dominance', 'correction', 'merge_maintenance')
OUTCOMES = ('survived', 'eliminated')


def validate_snapshot(record):
    for direction in ('forward', 'backward'):
        for kind in KINDS:
            operation = record[direction][kind]
            attempts, sampled = operation['attempts'], operation['sampled_attempts']
            finished = sampled_finished = 0
            if not 0 <= sampled <= attempts:
                raise ValueError('Invalid attempt sampling counts')
            for outcome in OUTCOMES:
                value = operation[outcome]
                if (not 0 <= value['sampled_finished'] <= value['finished']
                        or value['sampled_ns'] != sum(value['sampled_stage_ns'].values())
                        or any(v < 0 for v in value['counters'].values())):
                    raise ValueError('Invalid outcome counts or overlapping sampled stages')
                finished += value['finished']
                sampled_finished += value['sampled_finished']
            if not finished <= attempts or not sampled_finished <= sampled:
                raise ValueError('Finished episodes exceed attempts')
            if record['event'] == 'pricing_end' and (finished != attempts or sampled_finished != sampled):
                raise ValueError('Returned pricing contains unfinished profiler episodes')


def summarize_profile(path):
    path = Path(path)
    if not path.is_file():
        return {'available': False, 'status': 'missing', 'path': str(path), 'throughput_claim': False}
    lines = path.read_bytes().splitlines(keepends=True)
    fragment = len(lines.pop()) if lines and not lines[-1].endswith(b'\n') else 0
    records = [json.loads(line) for line in lines]
    if not records or records[0].get('event') != 'profile_open' or records[0].get('base_source') != BASE:
        raise ValueError('Missing opening record or selected9d source identity')
    opening = records[0]
    calls, active = {}, None
    for record in records:
        if record.get('schema') != SCHEMA or record.get('diagnostic_only') is not True:
            raise ValueError('Unexpected profile schema or diagnostic identity')
        event, call = record['event'], record['call']
        if event == 'profile_open':
            if record is not opening or call != 0:
                raise ValueError('Misplaced profile opening')
            continue
        validate_snapshot(record)
        if event == 'pricing_begin':
            if active is not None or call != len(calls) + 1 or record['pricing_returned']:
                raise ValueError('Invalid pricing begin sequence')
            context = record['context']
            if context.get('correcting') is not True or context.get('lazy_extension') is not True:
                raise ValueError('Observed profile source mode must retain correction and lazy extension')
            calls[call] = {'context': context, 'latest': record, 'returned': False}
            active = call
        elif event in {'pricing_progress', 'pricing_end'}:
            if call != active or record['pricing_returned'] != (event == 'pricing_end'):
                raise ValueError('Unmatched pricing progress/end')
            calls[call]['latest'] = record
            if event == 'pricing_end':
                if record['native_status'] != record['native_log']['status']:
                    raise ValueError('Explicit native status disagrees with native log')
                calls[call]['returned'] = True
                active = None
        else:
            raise ValueError('Unknown profile event')
    aggregates, statuses, signatures = {}, Counter(), []
    for call, data in calls.items():
        record, context = data['latest'], data['context']
        scope = 'returned_tasks' if data['returned'] else 'unfinished_tasks_last_snapshot'
        if data['returned']:
            statuses[record['native_status']] += 1
            signatures.append({'call': call, 'level': context['level'], 'native_status': record['native_status'],
                               'context_sha256': digest(context), 'native_log': record['native_log'],
                               'returned_routes': record['returned_routes'], 'pool_size': record['pool_size']})
        for direction in ('forward', 'backward'):
            for kind in KINDS:
                op = record[direction][kind]
                for outcome in OUTCOMES:
                    value = op[outcome]
                    key = f'{scope}/{context["level"]}/{direction}/{kind}/{outcome}'
                    total = aggregates.setdefault(key, {'tasks': 0, 'finished': 0, 'sampled_finished': 0,
                        'sampled_ns': 0, 'sampled_stage_ns': {}, 'counters': {}})
                    total['tasks'] += 1
                    for field in ('finished', 'sampled_finished', 'sampled_ns'):
                        total[field] += value[field]
                    for field in ('sampled_stage_ns', 'counters'):
                        for name, count in value[field].items():
                            total[field][name] = total[field].get(name, 0) + count
    for total in aggregates.values():
        total['sampled_stage_fraction'] = {name: value / total['sampled_ns'] if total['sampled_ns'] else None
                                           for name, value in total['sampled_stage_ns'].items()}
    return {'available': True, 'status': 'parsed', 'path': str(path), 'sha256': sha256(path),
            'records': len(records), 'trailing_fragment_bytes': fragment,
            'sample_stride': opening['sample_stride'], 'sample_offset': opening['sample_offset'],
            'begun_tasks': len(calls), 'returned_tasks': sum(value['returned'] for value in calls.values()),
            'unfinished_calls': [call for call, value in calls.items() if not value['returned']],
            'returned_native_statuses': dict(statuses), 'task_work_signatures': signatures,
            'aggregates': aggregates, 'throughput_claim': False,
            'limitation': 'Unfinished snapshots are censored. Surviving calls may mutate pieces. Sampled times include instrumentation.'}


def compare_rows(rows):
    comparisons = []
    for instance in ('C104', 'RC201'):
        arms = {row['variant']: row for row in rows if row['instance_name'] == instance}
        left = arms.get('correction-on', {})
        for name in ('dominance-profile', 'lera-minimal'):
            right = arms.get(name, {})
            signatures = {}
            for field in ('root_work_signature', 'whole_work_signature'):
                a, b = left.get('native_work', {}).get(field), right.get('native_work', {}).get(field)
                signatures[field] = {'control': a, 'other': b, 'equal': a == b if a and b else None}
            comparisons.append({'instance': instance, 'control': 'correction-on', 'other': name,
                'both_observations_usable': bool(left.get('proof_observation_usable') and right.get('proof_observation_usable')),
                'native_work': signatures, 'throughput_claim': False,
                'limitation': 'Missing native sidecars leave work equivalence unknown. Different censoring points can change recorded work.'})
    return comparisons
