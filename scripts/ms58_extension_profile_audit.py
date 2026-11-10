"""Read-only extension-profile summaries, preserving incomplete pricing calls."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from checker_contract import sha256
from probe_runner import digest

SCHEMA = 'kayros-ms58-extension-profile-v1'
BASE = '327e481713e1b9a3187fda2fc9f10f8d9d75b0c5'
COUNTERS = ('processed_count', 'enumerated_count', 'extended_count', 'dominated_count', 'corrected_count')


def summarize_profile(path):
    path = Path(path)
    if not path.is_file():
        return {'available': False, 'status': 'missing', 'path': str(path)}
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    fragment = 0
    if lines and not lines[-1].endswith(b'\n'):
        fragment = len(lines.pop())
    records = [json.loads(line) for line in lines]
    if not records or records[0].get('event') != 'profile_open':
        raise ValueError('Profile has no complete opening record')
    opening = records[0]
    if opening.get('base_source') != BASE:
        raise ValueError('Profile base source differs from best327')
    calls, active = {}, None
    for record in records:
        if record.get('schema') != SCHEMA or record.get('diagnostic_only') is not True:
            raise ValueError('Profile schema or diagnostic identity differs')
        event, call = record['event'], record['call']
        if event == 'profile_open':
            if record is not opening or call != 0:
                raise ValueError('Duplicate or misplaced opening record')
        elif event == 'pricing_begin':
            if active is not None or call != len(calls) + 1 or record['pricing_returned']:
                raise ValueError('Pricing begin sequence differs')
            active = call
            calls[call] = {'context': record['context'], 'latest': record, 'returned': False}
        elif event in {'pricing_progress', 'pricing_end'}:
            if call != active or record['pricing_returned'] != (event == 'pricing_end'):
                raise ValueError('Pricing progress/end has no matching active begin')
            calls[call]['latest'] = record
            if event == 'pricing_end':
                calls[call]['returned'] = True
                active = None
        else:
            raise ValueError(f'Unknown profile event {event}')
    aggregates, signatures, statuses = {}, [], Counter()
    for call, observation in calls.items():
        latest, context = observation['latest'], observation['context']
        scope = 'returned_tasks' if observation['returned'] else 'unfinished_tasks_last_snapshot'
        level = context['level']
        if observation['returned']:
            log = latest['native_log']
            statuses[log.get('status', 'unknown')] += 1
            signatures.append({'call': call, 'level': level, 'status': log.get('status'),
                'pool_size': latest['pool_size'], 'returned_routes': latest['returned_routes'],
                'context_sha256': digest(context),
                **{d: {k: log.get(d, {}).get(k) for k in (*COUNTERS, 'status', 'count_by_length')}
                   for d in ('forward', 'backward')}})
        for direction in ('forward', 'backward'):
            observed = latest[direction]
            summed_ns = sum(s['sampled_ns'] for s in observed['stages'].values())
            if summed_ns != observed['sampled_total_ns'] or observed['sampled_finished'] != observed['sampled_attempts']:
                raise ValueError('Flushed extension snapshot has incomplete or overlapping sample timers')
            key = f'{scope}/{level}/{direction}'
            aggregate = aggregates.setdefault(key, {'tasks': 0, 'attempts': 0, 'sampled_attempts': 0,
                'sampled_total_ns': 0, 'stages': {}, 'events': {}, 'sampled_pieces': {}})
            aggregate['tasks'] += 1
            for field in ('attempts', 'sampled_attempts', 'sampled_total_ns'):
                aggregate[field] += observed[field]
            for stage, values in observed['stages'].items():
                total = aggregate['stages'].setdefault(stage, dict.fromkeys(values, 0))
                for field, value in values.items():
                    total[field] += value
            for event, value in observed['events'].items():
                aggregate['events'][event] = aggregate['events'].get(event, 0) + value
            for kind, values in observed['sampled_pieces'].items():
                total = aggregate['sampled_pieces'].setdefault(kind, {'observations': 0, 'sum': 0, 'maximum': 0})
                total['observations'] += values['observations']
                total['sum'] += values['sum']
                total['maximum'] = max(total['maximum'], values['maximum'])
    for aggregate in aggregates.values():
        denominator = aggregate['sampled_total_ns']
        for stage in aggregate['stages'].values():
            stage['sampled_fraction'] = stage['sampled_ns'] / denominator if denominator else None
    return {'available': True, 'status': 'parsed', 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
        'records': len(records), 'trailing_fragment_bytes': fragment,
        'sample_stride': opening['sample_stride'], 'sample_offset': opening['sample_offset'],
        'begun_tasks': len(calls), 'returned_tasks': len(signatures),
        'unfinished_calls': [c for c, v in calls.items() if not v['returned']],
        'returned_native_statuses': dict(statuses), 'task_work_signatures': signatures,
        'aggregates': aggregates, 'throughput_claim': False,
        'limitations': 'Sampled diagnostic wall time includes instrumentation. Composition includes assignment and temporary teardown. Unfinished snapshots remain censored.'}


def compare_rows(rows):
    comparisons = []
    for name in ('C104', 'RC201'):
        cells = {r['variant']: r for r in rows if r['instance_name'] == name}
        base = cells.get('best', {})
        for variant in ('extension-profile', 'lera-minimal'):
            other = cells.get(variant, {})
            left, right = base.get('native_work', {}), other.get('native_work', {})
            signatures = {}
            for key in ('root_work_signature', 'whole_work_signature'):
                a, b = left.get(key), right.get(key)
                signatures[key] = {'best': a, 'other': b, 'equal': a == b if a and b else None}
            comparisons.append({'instance': name, 'control': 'best', 'other': variant,
                'both_rows_present': bool(base and other),
                'both_observations_usable': bool(base.get('observation_usable') and other.get('observation_usable')),
                'native_work_comparison': signatures, 'throughput_claim': False,
                'interpretation': 'Different censoring points may change whole-run work. Missing native sidecars leave equivalence unknown. Lera work equality is descriptive and is not a correctness oracle.'})
    return comparisons
