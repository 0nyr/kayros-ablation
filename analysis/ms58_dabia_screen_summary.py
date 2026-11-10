"""Audit the 36-cell Dabia screen against checked feasible witnesses."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics

from ms58_dabia_summary import (EXPECTED_CHECKER, UPSTREAM_BINARY_SHA256, UPSTREAM_PATCH_SHA256,
    apply_feasible_consistency, checked_reference_cost, digest, observation, sha256)
from ms58_analyze_pairs import native_work
from ms58_dabia_screen import algorithm_setting_audit
from pilot_summary import score_trace

REFERENCE_SHA256 = 'ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'
SOURCES = {'f2c9e20', '327e481713e1b9a3187fda2fc9f10f8d9d75b0c5',
           'e27f288d7b504c2e8e9f2b8d9086ba0b7da69f02', 'a63cf65d3f7e90e4c310ffcedd7a6dabfae0ce3d'}
BEST_SOURCE = '327e481713e1b9a3187fda2fc9f10f8d9d75b0c5'
SAFE_SOURCE = 'a63cf65d3f7e90e4c310ffcedd7a6dabfae0ce3d'
POST_MANIFEST_FIELDS = {'manifest_path', 'manifest_sha256', 'worker_python', 'worker_python_sha256',
    'worker_path', 'worker_sha256', 'output_directory', 'launcher_stdout', 'launcher_stderr'}


def frozen_schedule(screen):
    schedule_path = screen / 'schedule.json'
    seal = json.loads((screen / 'schedule-hash.json').read_bytes())
    if sha256(schedule_path) != seal['sha256']:
        raise ValueError('Screen schedule differs from its immutable seal')
    spec = json.loads(schedule_path.read_bytes())
    if spec['checker'] != EXPECTED_CHECKER or spec['horizon_s'] != 600 or spec['memory_mb'] != 16384:
        raise ValueError('Screen checker or budget contract mismatch')
    for filename, expected in {**spec['frozen_files'], 'plan.json': spec['plan_sha256']}.items():
        if sha256(screen / filename) != expected:
            raise ValueError(f'Frozen screen artifact mismatch: {filename}')
    tasks = spec['scheduled_cells']
    if len(tasks) != 36 or spec['scheduled_count'] != 36 or len({t['task_id'] for t in tasks}) != 36:
        raise ValueError('This analysis requires the entire 36-cell screen-v1 schedule')
    if {t['source_id'] for t in tasks} != SOURCES:
        raise ValueError('Screen source population differs from its four declared candidates')
    populations = defaultdict(set)
    for task in tasks:
        expected_environment = {'KAYROS_DIAGNOSTIC_CORRECTING': '1'} if task['source_id'] == SAFE_SOURCE else {}
        if (task['num_customers'] != 100 or task['horizon_s'] != 600 or task['memory_mb'] != 16384
                or task['seed'] is not None or task['prefix_s'] is not None
                or task['diagnostic_environment'] != expected_environment
                or task['arm'] != ('lera-exact' if task['source_id'] == 'f2c9e20' else 'kayros-bpca-cplex')):
            raise ValueError('Screen task deviates from cold CPLEX n100 identity or settings')
        populations[task['source_id']].add((task['instance_name'], task['replicate']))
    required = {(name, rep) for name in ['C201', 'RC201', 'C104'] for rep in [1, 2, 3]}
    if any(population != required for population in populations.values()):
        raise ValueError('Screen variants have unequal or incomplete instance-trial populations')
    complete = screen / 'complete.json'
    if complete.exists():
        completion = json.loads(complete.read_bytes())
        if set(completion['row_sha256']) != {f"{t['task_id']}.json" for t in tasks}:
            raise ValueError('Screen completion seal omits a scheduled row')
        for filename, expected in completion['row_sha256'].items():
            if sha256(screen / 'rows' / filename) != expected:
                raise ValueError('Completed screen row digest mismatch')
    return spec


def screen_manifest(screen, task):
    path = screen / 'manifests' / Path(task['manifest_path']).name
    if sha256(path) != task['manifest_sha256']:
        raise ValueError('Screen manifest digest mismatch')
    manifest = json.loads(path.read_bytes())
    if manifest['screen_cell'] != {k: v for k, v in task.items() if k not in POST_MANIFEST_FIELDS}:
        raise ValueError('Screen manifest cell binding mismatch')
    if (manifest['declared_source_commit'] != task['source_id']
            or not str(manifest['source_id']).startswith(task['source_id'])
            or manifest['diagnostic_environment'] != task['diagnostic_environment']
            or manifest['backend'].lower() != 'cplex' or manifest['checker'] != EXPECTED_CHECKER):
        raise ValueError('Screen manifest source, checker, backend or settings mismatch')
    if task['source_id'] == 'f2c9e20':
        environment = manifest['worker_environment']
        if (manifest['runtime_files'].get(environment['UPSTREAM_PROBE_BINARY']) != UPSTREAM_BINARY_SHA256
                or UPSTREAM_PATCH_SHA256 not in manifest['source_patch_files'].values()
                or environment['UPSTREAM_NATIVE_INPUT_SHA256'] != task['native_instance_sha256']):
            raise ValueError('Screen minimally repaired Lera artifact identity mismatch')
    return manifest


def screen_observation(screen, task, reference):
    manifest = screen_manifest(screen, task)
    cell = observation(screen, task, reference['reference_cost'], manifest)
    cell.update(reference_cost=reference['reference_cost'], reference_manifest_sha256=REFERENCE_SHA256,
                variant=task['variant'], manifest_source_id=cell.get('source_id'), source_id=task['source_id'],
                native_work={'available': False})
    row_path = screen / 'rows' / f"{task['task_id']}.json"
    if not row_path.exists():
        cell['observation_usable'] = False
        return cell
    raw = json.loads(row_path.read_bytes())
    cell['raw_controller_row'] = raw
    if not raw.get('run_id'):
        cell['observation_usable'] = False
        return cell
    directory = screen / 'cells' / raw['run_id']
    expected = json.loads((screen / 'started' / f"{task['task_id']}-expected-identity.json").read_bytes())
    identity = json.loads((directory / 'identity.json').read_bytes())
    if (digest(expected) != raw['run_id'] or raw.get('expected_run_id') != raw['run_id']
            or any(identity.get(k) != value for k, value in expected.items())
            or identity['parameters']['worker_sha256'] != task['worker_sha256']
            or identity['parameters']['worker_python_sha256'] != task['worker_python_sha256']):
        raise ValueError('Screen started identity, worker pin or completed identity mismatch')
    result = json.loads((directory / 'result.json').read_bytes())
    if raw.get('result_sha256') != sha256(directory / 'result.json'):
        raise ValueError('Controller result digest mismatch')
    native = result['validation'].get('native_result', {})
    payload = json.loads((directory / native['relative_path']).read_bytes()) if native.get('sha256') else None
    algorithm = algorithm_setting_audit(task, payload)
    mismatches = sum(c.get('native_minus_checked') is not None and abs(c['native_minus_checked']) > 1e-6
                     for c in result['validation'].get('candidates', []))
    cell.update(native_work=native_work(payload) if payload is not None else {'available': False},
        algorithm_settings_audit=algorithm, native_cost_mismatch_count=mismatches,
        sampled_solver_peak_rss_kib=result.get('sampled_solver_peak_rss_kib'),
        rss_scope=raw.get('rss_scope'), issues=result.get('issues', []))
    cell['observation_usable'] = (cell['audit_ok'] and cell['independent_backend_audit']['passed']
        and result['status'] in {'completed', 'deadline'} and not cell['issues']
        and not cell['invalid_candidate_count'] and not mismatches and not cell['resource_failure_kind']
        and not cell['native_guard_warning'] and algorithm['status'] != 'failed')
    if not cell['observation_usable']:
        cell['reported_closure_s'] = None
        cell['capped_reported_closure_s'] = task['horizon_s']
    return cell


def summarize(screen, reference_path):
    spec = frozen_schedule(screen)
    if sha256(reference_path) != REFERENCE_SHA256:
        raise ValueError('Screen requires the immutable pre-campaign n100 reference snapshot')
    reference = json.loads(reference_path.read_bytes())
    if reference['checker'] != EXPECTED_CHECKER:
        raise ValueError('Reference checker differs from the screen checker')
    references = {r['instance_name']: r for r in reference['instances']}
    for name in ['C201', 'RC201', 'C104']:
        checked_reference_cost(references[name], reference_path.parent)
    cells = []
    for task in spec['scheduled_cells']:
        frozen = references[task['instance_name']]
        if (task['instance_sha256'] != frozen['instance_sha256'] or task['atf_sha256'] != frozen['atf_file_sha256']
                or task['instance_key'] != f"Dabia2013/n=100/{task['instance_name']}"):
            raise ValueError('Screen input differs from independently checked frozen reference')
        try:
            cell = screen_observation(screen, task, frozen)
        except (OSError, ValueError, KeyError, TypeError) as error:
            row_path = screen / 'rows' / f"{task['task_id']}.json"
            raw = json.loads(row_path.read_bytes()) if row_path.exists() else None
            cell = {**task, 'status': raw.get('status') if raw else 'pending', 'raw_controller_row': raw,
                'analysis_status': 'audit_failure', 'audit_ok': False, 'observation_usable': False,
                'finished_task': raw is not None, 'reason': f'{type(error).__name__}: {error}',
                'reference_cost': frozen['reference_cost'], 'reference_manifest_sha256': REFERENCE_SHA256,
                'envelope': [], 'reported_closure_s': None, 'capped_reported_closure_s': task['horizon_s'],
                'native_work': {'available': False}}
        cell.update(score_trace(cell['envelope'], frozen['reference_cost'], task['horizon_s']))
        cells.append(cell)
    ceilings = apply_feasible_consistency(cells)
    groups = defaultdict(list)
    for cell in cells:
        groups[(cell['variant'], cell['instance_name'])].append(cell)
    summaries = []
    for (variant, instance), rows in sorted(groups.items()):
        summaries.append({'variant': variant, 'source_id': rows[0]['source_id'], 'instance_name': instance,
            'scheduled_cells': len(rows), 'statuses': dict(Counter(c['status'] for c in rows)),
            'usable_cells': sum(c['observation_usable'] for c in rows),
            'audit_failures': sum(not c['audit_ok'] for c in rows),
            'consistency_failures': sum(c['consistency_audit']['status'] == 'contradicted' for c in rows),
            'unsupported_closures': sum(c['consistency_audit']['status'] == 'unsupported' for c in rows),
            'reported_closures': sum(c['reported_closure_s'] is not None for c in rows),
            'mean_capped_reported_closure_s': statistics.mean(c['capped_reported_closure_s'] for c in rows),
            'mean_lambda': statistics.mean(c['lambda'] for c in rows),
            'final_checked_costs': [c['final_checked_cost'] for c in rows],
            'resource_failures': sum(c.get('resource_failure_kind') is not None for c in rows),
            'native_work_signatures': [c['native_work'].get('whole_work_signature') for c in rows]})
    paired = []
    by_pair = defaultdict(dict)
    for cell in cells:
        by_pair[(cell['instance_key'], cell['replicate'])][cell['source_id']] = cell
    for key, pair in sorted(by_pair.items()):
        best = pair[BEST_SOURCE]
        for source, other in pair.items():
            if source == BEST_SOURCE:
                continue
            if (best['lane'], best['host'], best['cpu_core']) != (other['lane'], other['host'], other['cpu_core']):
                raise ValueError('Screen paired comparison differs in lane, host or physical CPU')
            both = best['reported_closure_s'] is not None and other['reported_closure_s'] is not None
            paired.append({'instance_key': key[0], 'replicate': key[1], 'baseline_variant': best['variant'],
                'comparator_variant': other['variant'], 'both_closed': both,
                'baseline_closure_s': best['reported_closure_s'], 'comparator_closure_s': other['reported_closure_s'],
                'baseline_over_comparator_closure_ratio': best['reported_closure_s'] / other['reported_closure_s']
                    if both and other['reported_closure_s'] > 0 else None,
                'comparator_minus_baseline_capped_s': other['capped_reported_closure_s'] - best['capped_reported_closure_s'],
                'same_recorded_whole_work': bool(best['native_work'].get('whole_work_signature'))
                    and best['native_work'].get('whole_work_signature') == other['native_work'].get('whole_work_signature'),
                'both_observations_usable': best['observation_usable'] and other['observation_usable']})
    return {'schema_version': 1, 'screen': str(screen), 'schedule_sha256': sha256(screen / 'schedule.json'),
        'reference_manifest': {'path': str(reference_path), 'sha256': REFERENCE_SHA256}, 'checker': EXPECTED_CHECKER,
        'analysis_sha256': sha256(Path(__file__)), 'consistency_code_sha256': sha256(Path(__file__).with_name('ms58_dabia_summary.py')),
        'proof_consistency_helper_sha256': sha256(Path(__file__).resolve().parents[1] / 'scripts/ms58_proof_consistency.py'),
        'scheduled_cells': 36, 'completed_rows': sum(c['finished_task'] for c in cells),
        'provisional': any(not c['finished_task'] for c in cells), 'consistency_audit_completed': True,
        'audit_failure_count': sum(not c['audit_ok'] for c in cells),
        'consistency_failure_count': sum(c['consistency_audit']['status'] == 'contradicted' for c in cells),
        'unsupported_closure_count': sum(c['consistency_audit']['status'] == 'unsupported' for c in cells),
        'feasible_ceilings': ceilings, 'groups': summaries, 'pairs_against_best': paired, 'cells': cells,
        'qualification': 'Development screen, not independent confirmation. Censored pairs do not establish equal speed. Native child RSS is absent from Lera adapter measurements. Feasible-witness consistency is necessary but is not proof certification. No automatic candidate promotion.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen', type=Path, required=True)
    parser.add_argument('--references', type=Path, default=Path(__file__).resolve().parents[1] /
                        'references/dabia2013-n100-v011-2026-11-09/manifest.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.screen.resolve(), args.references.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({key: report[key] for key in ['scheduled_cells', 'completed_rows', 'provisional',
        'audit_failure_count', 'consistency_failure_count', 'groups']}, indent=2))


if __name__ == '__main__':
    main()
