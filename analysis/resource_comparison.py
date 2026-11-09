"""Compare complete matched panels at separate resource settings without pooling."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def compare(before, after):
    if before['provisional'] or after['provisional']:
        raise ValueError('Both full panels must be complete')
    for field in ('checker', 'reference_manifest_sha256'):
        if before[field] != after[field]:
            raise ValueError('Different ' + field)
    def key(cell):
        return cell['instance_name'], cell['arm'], cell.get('prefix_s'), cell['replicate']
    panels = [{key(cell): cell for cell in report['cells']} for report in (before, after)]
    if any(len(panel) != len(report['cells']) for panel, report in zip(panels, (before, after))):
        raise ValueError('Duplicate logical cell')
    if panels[0].keys() != panels[1].keys():
        raise ValueError('Panels have different populations')
    for identity, first in panels[0].items():
        second = panels[1][identity]
        if any(first[field] != second[field] for field in
               ('seed', 'instance_sha256', 'reference_cost', 'horizon_s')):
            raise ValueError('Input, trial seed, reference or horizon mismatch')
    horizons = {cell['horizon_s'] for report in (before, after) for cell in report['cells']}
    caps = [{cell['memory_mb'] for cell in report['cells']} for report in (before, after)]
    if len(horizons) != 1 or any(len(values) != 1 for values in caps):
        raise ValueError('Each complete panel requires a common horizon and memory cap')
    metrics = ('scheduled', 'feasible', 'reported_closures_with_checked_witness',
               'mean_lambda', 'mean_final_squeezed_gap', 'restricted_mean_reported_closure_s',
               'memory_allocation_failures', 'native_memory_limit_outcomes',
               'reported_closures_with_native_guard_warning')
    groups = [{row['configuration']: row for row in report['groups']} for report in (before, after)]
    if groups[0].keys() != groups[1].keys():
        raise ValueError('Summary configurations differ')
    rows = []
    for name in sorted(groups[0]):
        first, second = groups[0][name], groups[1][name]
        if first['scheduled'] != second['scheduled']:
            raise ValueError('Summary denominators differ')
        row = {'configuration': name}
        for metric in metrics:
            row[metric + '_before'] = first[metric]
            row[metric + '_after'] = second[metric]
        rows.append(row)
    return {'before_wave': before['wave_name'], 'after_wave': after['wave_name'],
            'horizon_s': horizons.pop(), 'before_memory_mib': caps[0].pop(),
            'after_memory_mib': caps[1].pop(), 'scheduled_per_panel': len(panels[0]),
            'groups': rows,
            'interpretation': [
                'Each column retains the complete population of its own experiment, including resource failures.',
                'These are separate executions. Placement and concurrent load differ as well as the memory cap.',
                'Differences are descriptive operating-point comparisons, not an isolated causal memory effect.',
                'Neither execution replaces its counterpart, and no pooled score is produced.',
                'Known native guard warnings retain their original qualification.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.before.read_bytes()), json.loads(args.after.read_bytes()))
    result['sources'] = {name: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                         for name, path in [('before', args.before), ('after', args.after), ('script', Path(__file__))]}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'resource-comparison.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    with (args.output / 'resource-comparison.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result['groups'][0]))
        writer.writeheader()
        writer.writerows(result['groups'])


if __name__ == '__main__':
    main()
