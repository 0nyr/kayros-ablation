"""Independently audit archived C104 controls and the minimal reference smoke."""
import argparse
import json
from pathlib import Path

from checker_contract import sha256
from ms58_analyze_pairs import EXPECTED_CHECKER, closure_and_resource, native_work
from probe_runner import atomic_json, digest, load_completed


def audit_row(root, row, manifest_path, setting=None):
    cell = root / 'cells' / row['run_id']
    identity = json.loads((cell / 'identity.json').read_bytes())
    result = load_completed(cell, expected_run_id=row['run_id'])
    manifest = json.loads(manifest_path.read_bytes())
    assert identity['manifest'] == manifest
    assert identity['manifest_hash'] == digest(manifest)
    assert manifest['checker'] == result['validation']['checker'] == EXPECTED_CHECKER
    params = identity['parameters']
    for key, expected in {'instance_name': row['instance'], 'instance_sha256': row['input_sha256'],
                          'atf_sha256': row['atf_sha256'], 'cpu_affinity': [row['cpu']],
                          'memory_mb': row['memory_mb'], 'memory_cap_bytes': row['memory_mb'] * 1024 ** 2,
                          'arm': 'lera-exact', 'search_seed': None, 'prefix_seconds': None}.items():
        assert params[key] == expected, key
    assert identity['horizon_s'] == row['horizon_s']
    for path, expected in manifest['source_patch_files'].items():
        assert sha256(root / 'frozen-patches' / Path(path).name) == expected
    for name, expected in params['orchestration_files'].items():
        assert sha256(root / 'frozen-scripts' / name) == expected
    assert sha256(root / 'frozen-scripts/upstream_event_worker.py') == params['worker_sha256']
    native = result['validation']['native_result']
    native_path = (cell / native['relative_path']).resolve()
    assert sha256(native_path) == native['sha256']
    raw = json.loads(native_path.read_bytes())
    assert raw['run_id'] == row['run_id'] and raw['manifest_hash'] == digest(manifest)
    work = native_work(raw)
    assert work == row['native_work']
    events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
    stderr = (cell / 'stderr.log').read_text(errors='replace')
    closure, resource = closure_and_resource(result, events, stderr)
    assert closure == row['reported_closure_s'] and resource == row['resource_outcome']
    assert result['validation']['best_checked_cost'] == row['checked_cost']
    assert result['status'] == row['status'] and not result['issues']
    expected_environment = {} if setting is None else {'KAYROS_DIAGNOSTIC_CORRECTING': str(setting)}
    assert manifest['diagnostic_environment'] == expected_environment
    if setting is not None:
        assert row['diagnostic_environment'] == expected_environment
        assert raw['native_result']['ms58_diagnostic_configuration']['correcting'] is bool(setting)
        assert f'Explicit diagnostic correcting: {setting}\n' in stderr
    native_launch = next(event for event in events if event.get('phase') == 'native_launch')
    assert all(native_launch['metadata_equivalence'].values())
    assert native_launch['native_instance_sha256'] == raw['native_instance_sha256']
    native_input = next(path for path in manifest['runtime_files'] if path.endswith(f"/{row['instance']}_100.json"))
    assert manifest['runtime_files'][native_input] == raw['native_instance_sha256']
    iterations = raw['native_result']['Exact']['root_log']['iterations']
    correction_time = sum(iteration[direction]['correction_time']
                          for iteration in iterations for direction in ['forward', 'backward'])
    return {'run_id': row['run_id'], 'status': row['status'], 'instance': row['instance'],
        'cpu': row['cpu'], 'reported_closure_s': closure, 'checked_cost': row['checked_cost'],
        'manifest_hash': digest(manifest), 'native_sha256': native['sha256'],
        'environment_hash': identity['environment_hash'], 'declared_environment': expected_environment,
        'native_setting_confirmed': setting is not None, 'resource_outcome': resource,
        'native_work': work, 'root_correction_time_s': correction_time,
        'audit': 'Identity, event hashes, manifest, frozen patches/scripts, checker, inputs, parameters, native hash/setting and closure event verified. Remote runtime bytes were checked by the launch harness and are identified by the stored manifest.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--long-root', type=Path, required=True)
    parser.add_argument('--archived-cells', type=Path, required=True)
    parser.add_argument('--minimal-root', type=Path)
    parser.add_argument('--explicit-off-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    schedule = json.loads((args.long_root / 'schedule.json').read_bytes())
    assert schedule['scheduled_count'] == 2
    controls = {}
    for lane in schedule['lanes']:
        setting, = lane['order']
        row = json.loads((args.long_root / 'rows' / f'C104-correcting-{setting}.json').read_bytes())
        assert all(row[key] == value for key, value in lane.items())
        controls[str(setting)] = audit_row(args.long_root, row, args.long_root / f'correcting-{setting}-manifest.json', setting)
    assert set(controls) == {'0', '1'}
    archived = []
    for path in sorted(args.archived_cells.glob('*/identity.json')):
        identity = json.loads(path.read_bytes())
        params = identity['parameters']
        if params['instance_name'] != 'C104' or params['arm'] != 'lera-exact':
            continue
        result = load_completed(path.parent, expected_run_id=identity['run_id'])
        native = result['validation']['native_result']
        native_path = (path.parent / native['relative_path']).resolve()
        assert sha256(native_path) == native['sha256']
        work = native_work(json.loads(native_path.read_bytes()))
        archived.append({'run_id': identity['run_id'], 'native_sha256': native['sha256'],
            'root_work_signature': work['root_work_signature'], 'whole_work_signature': work['whole_work_signature'],
            'correction_on_root_match': work['root_work_signature'] == controls['1']['native_work']['root_work_signature'],
            'correction_on_whole_match': work['whole_work_signature'] == controls['1']['native_work']['whole_work_signature']})
    off, on = controls['0'], controls['1']
    metrics = {}
    triples = [('reported_closure_s', off['reported_closure_s'], on['reported_closure_s']),
               ('root_correction_time_s', off['root_correction_time_s'], on['root_correction_time_s'])]
    for field in ['root_labeling_time_s']:
        triples.append((field, off['native_work'][field], on['native_work'][field]))
    for field in ['root_time', 'root_variable_count', 'lp_time', 'pricing_time', 'cut_count']:
        triples.append((field, off['native_work']['whole_run'][field], on['native_work']['whole_run'][field]))
    for field in ['calls', 'processed_count', 'extended_count', 'enumerated_count', 'corrected_count']:
        triples.append((field, sum(level[field] for level in off['native_work']['root_labeling'].values()),
                        sum(level[field] for level in on['native_work']['root_labeling'].values())))
    for field, a, b in triples:
        metrics[field] = {'off': a, 'on': b, 'on_minus_off': b-a,
                          'on_relative_to_off_percent': 100*(b-a)/a if a else None}
    minimal = []
    if args.minimal_root and (args.minimal_root / 'complete.json').exists():
        for path in sorted((args.minimal_root / 'rows').glob('*.json')):
            row = json.loads(path.read_bytes())
            item = audit_row(args.minimal_root, row, args.minimal_root / 'manifest.json')
            item['corrected_count'] = sum(level['corrected_count'] for level in item['native_work']['root_labeling'].values())
            assert item['corrected_count'] == 0
            if args.explicit_off_root:
                previous = args.explicit_off_root / 'rows' / f"{row['instance']}-correcting-0.json"
                if previous.exists():
                    previous_row = json.loads(previous.read_bytes())
                    assert previous_row['input_sha256'] == row['input_sha256']
                    previous_work = previous_row['native_work']
                    item['explicit_off_root_match'] = previous_work['root_work_signature'] == item['native_work']['root_work_signature']
                    item['explicit_off_whole_match'] = previous_work['whole_work_signature'] == item['native_work']['whole_work_signature']
            minimal.append(item)
    atomic_json(args.output, {'controls': controls, 'metrics': metrics, 'archived_comparisons': archived,
        'minimal_reference': minimal, 'same_checked_cost': off['checked_cost'] == on['checked_cost'],
        'qualification': schedule['interpretation']})


if __name__ == '__main__':
    main()
