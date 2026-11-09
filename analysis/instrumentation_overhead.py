"""Replay paired overhead summaries with canonical route-set ordering."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from checker_contract import sha256
from measure_instrumentation import work_signature
from probe_runner import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.directory
    output.mkdir(parents=True, exist_ok=True)
    rows = json.loads((args.directory/'rows.json').read_bytes())
    grouped = defaultdict(dict)
    normalized = []
    for raw in rows:
        row = dict(raw)
        path = row.get('result_path')
        if path:
            result_path = args.directory/'cells'/row['run_id']/'result.json'
            result = json.loads(result_path.read_bytes())
            artifact = result.get('native_result') or result['validation'].get('native_result')
            native_path = (result_path.parent/artifact['relative_path']).resolve() if artifact and artifact.get('relative_path') else Path(artifact['path']) if artifact else None
            if native_path and native_path.is_file():
                if sha256(native_path) != artifact['sha256']:
                    raise RuntimeError('Native artifact hash mismatch')
                native = json.loads(native_path.read_bytes())['exact']
                row['canonical_fixed_work_sha256'] = work_signature(native)
        normalized.append(row)
        grouped[(row['backend'], row['case'])][(row['repetition'], row['enabled'])] = row
    summaries = []
    for (backend, case), table in sorted(grouped.items()):
        baseline = table[(-1, False)]
        summary = {'backend': backend, 'case': case}
        if baseline.get('native_status') != 'Optimum' or baseline.get('native_elapsed_s', 10) > 5:
            summary.update(status='excluded_not_closed_within_5_native_seconds', baseline_status=baseline.get('status'))
        else:
            pairs = []
            repetitions = sorted({rep for rep, enabled in table if rep >= 0})
            for repetition in repetitions:
                if (repetition, False) not in table or (repetition, True) not in table:
                    continue
                off, on = table[(repetition, False)], table[(repetition, True)]
                valid = (off.get('status') == on.get('status') == 'completed'
                         and off.get('native_status') == on.get('native_status') == 'Optimum'
                         and off.get('instrumentation_enabled') is False and on.get('instrumentation_enabled') is True
                         and off.get('checked_cost') == on.get('checked_cost') is not None
                         and off.get('canonical_fixed_work_sha256') == on.get('canonical_fixed_work_sha256'))
                pairs.append({'repetition': repetition, 'matched_fixed_work': valid,
                              'native_delta_s': on.get('native_elapsed_s', 0)-off.get('native_elapsed_s', 0),
                              'outer_delta_s': on.get('solver_elapsed_s', 0)-off.get('solver_elapsed_s', 0),
                              'off_native_s': off.get('native_elapsed_s'), 'on_native_s': on.get('native_elapsed_s'),
                              'off_outer_s': off.get('solver_elapsed_s'), 'on_outer_s': on.get('solver_elapsed_s')})
            valid = [pair for pair in pairs if pair['matched_fixed_work']]
            summary.update(status='measured' if valid else 'no_matched_work_pairs', pairs=pairs, matched_pairs=len(valid))
            if valid:
                for metric in ['native_delta_s', 'outer_delta_s', 'off_native_s', 'on_native_s', 'off_outer_s', 'on_outer_s']:
                    summary['median_'+metric] = statistics.median(pair[metric] for pair in valid)
                summary['median_native_relative_delta'] = statistics.median(pair['native_delta_s']/pair['off_native_s'] for pair in valid if pair['off_native_s'] > 0)
                summary['median_outer_relative_delta'] = statistics.median(pair['outer_delta_s']/pair['off_outer_s'] for pair in valid if pair['off_outer_s'] > 0)
        summaries.append(summary)
    atomic_json(output/'canonical-summary.json', {
        'schema_version': 1, 'raw_rows_sha256': sha256(args.directory/'rows.json'),
        'analysis_sha256': sha256(Path(__file__)),
        'signature_source_sha256': sha256(Path(__file__).resolve().parents[1]/'scripts/measure_instrumentation.py'),
        'normalization': 'Canonicalize route-list ordering only, preserving each path and all route attributes. Remove native clocks, console text, supplemental marker. Preserve CG iteration_count and complete timeless native search logs.',
        'cases': summaries})
    atomic_json(output/'canonical-rows.json', normalized)
    print(json.dumps({'cases': [{k: v for k, v in entry.items() if k != 'pairs'} for entry in summaries]}, indent=2))


if __name__ == '__main__':
    main()
