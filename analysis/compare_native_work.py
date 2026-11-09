"""Read existing matched-wave native logs without executing either solver."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    observations = []
    for path in sorted((args.wave/'rows').glob('*.json')):
        row = json.loads(path.read_bytes())
        if row['arm'] not in ['lera-exact', 'kayros-bpca-cplex']:
            continue
        cell = args.wave/'cells'/row['run_id']
        result = json.loads((cell/'result.json').read_bytes())
        if sha(cell/'events.jsonl') != result['events_sha256']:
            raise RuntimeError('Event digest mismatch')
        events = [json.loads(line) for line in (cell/'events.jsonl').read_text().splitlines()]
        closures = [event for event in events if event['event'] == 'closure']
        bound = next((event for event in events if event['event'] == 'bound' and event.get('source') == 'root_complete'), None)
        record = {key: row[key] for key in ['instance_name', 'arm', 'run_id', 'host', 'cpu_core']}
        record.update(status=result['status'], checked_cost=result['validation'].get('best_checked_cost'),
                      reported_closure_s=closures[0]['elapsed_s'] if closures else None,
                      root_bound_outer_s=bound['elapsed_s'] if bound else None,
                      root_bound_native_s=bound.get('native_elapsed_s') if bound else None,
                      root_bound=bound.get('global_lb') if bound else None,
                      events_sha256=result['events_sha256'])
        artifact = result.get('native_result') or result['validation'].get('native_result')
        native = (cell/artifact['relative_path']).resolve()
        record['native_record_available'] = native.is_file()
        if native.is_file():
            if sha(native) != artifact['sha256']:
                raise RuntimeError('Native digest mismatch')
            raw = json.loads(native.read_bytes())
            output = raw.get('exact', raw.get('native_result'))
            log = output.get('exact_log', output.get('Exact'))
            root = log.get('root_log', {})
            record['native_sha256'] = artifact['sha256']
            record['whole_run'] = {key: log.get(key) for key in ['time', 'root_time', 'lp_time', 'pricing_time',
                'branching_time', 'cut_time', 'nodes_closed', 'nodes_open', 'cut_count',
                'root_variable_count', 'final_variable_count', 'root_constraint_count', 'final_constraint_count', 'root_lp_value']}
            record['root_cg'] = {key: root.get(key) for key in ['iteration_count', 'columns_added', 'lp_time', 'pricing_time', 'time']}
            levels = defaultdict(lambda: {'calls': 0, 'time_s': 0., 'processed_count': 0,
                                         'enumerated_count': 0, 'extended_count': 0, 'dominated_count': 0})
            for iteration in root.get('iterations', []):
                summary = levels[iteration['iteration_name']]
                summary['calls'] += 1
                summary['time_s'] += iteration.get('time', 0.)
                for direction in ['forward', 'backward']:
                    for key in ['processed_count', 'enumerated_count', 'extended_count', 'dominated_count']:
                        summary[key] += iteration.get(direction, {}).get(key, 0)
            record['root_labeling'] = dict(levels)
            labeling_time = sum(level['time_s'] for level in levels.values())
            record['root_labeling_time_s'] = labeling_time
            record['root_nonlabel_callback_minus_cut_time_s'] = root.get('pricing_time', 0)-labeling_time-log.get('cut_time', 0)
        else:
            text = (cell/'stderr.log').read_text()
            root_text = text.split('Solving BC with existing columns')[0]
            table = re.findall(r'^\s*(\d+(?:\.\d+)?)\s+(\d+)\s+([0-9.eE+-]+)\s+(\d+)\s*$', root_text, re.M)
            record['incomplete_root_console'] = {
                'last_cg_row': table[-1] if table else None,
                'cut_count': sum(map(int, re.findall(r'Cuts: (\d+)', root_text))),
                'exact_pricing': bound.get('exact_pricing') if bound else None,
                'branching_entered': 'Branching.' in text,
                'limits': 'Console state and root callback only. No final native record or completed root labeling breakdown.'}
        observations.append(record)
    by_instance = defaultdict(dict)
    for row in observations:
        by_instance[row['instance_name']][row['arm']] = row
    contributions = []
    for instance, pair in sorted(by_instance.items()):
        lera = pair['lera-exact']['reported_closure_s']
        kayros = pair['kayros-bpca-cplex']['reported_closure_s']
        contributions.append({'instance': instance, 'lera_closure_s': lera, 'kayros_closure_s': kayros,
                              'kayros_minus_lera_capped_s': (kayros if kayros is not None else 300.)-(lera if lera is not None else 300.)})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema_version': 1, 'analysis_sha256': sha(Path(__file__)),
        'source_wave_sha256': sha(args.wave/'wave.json'), 'scope': 'One matched 300-second trial per instance and arm. Root labeling counts do not cover descendants.',
        'observations': observations, 'capped_closure_contributions': contributions}, indent=2)+'\n')
    print(json.dumps(contributions, indent=2))


if __name__ == '__main__':
    main()
