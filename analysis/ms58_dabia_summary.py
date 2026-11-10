"""Audit the frozen five-arm Dabia schedule, keeping every scheduled outcome."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from checker_contract import sha256
from ms58_analyze_pairs import EXPECTED_CHECKER, closure_and_resource
from probe_runner import digest, load_completed
from ms58_proof_consistency import apply_feasible_consistency, proof_evidence, validate_late_event_identity
from pilot_summary import score_trace
from ms58_dabia_wave import verify_allocation_preflight

ARM_IDS = ['lera-minimal', 'kayros-full-cplex-prefix5s',
           'kayros-full-cplex-prefix15s', 'kayros-cold-cplex', 'kayros-cold-highs']
ARM_CONTRACT = {'lera-minimal': ('lera-exact', None),
                'kayros-full-cplex-prefix5s': ('kayros-full-cplex', 5),
                'kayros-full-cplex-prefix15s': ('kayros-full-cplex', 15),
                'kayros-cold-cplex': ('kayros-bpca-cplex', None),
                'kayros-cold-highs': ('kayros-bpca-highs', None)}
UPSTREAM_BINARY_SHA256 = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
UPSTREAM_PATCH_SHA256 = 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf'


def checked_reference_cost(reference, snapshot_root, copied_assets=None):
    """Verify stored independent checks and solutions, without running a solver."""
    cost = float.fromhex(reference['reference_cost_hex'])
    if cost != reference['reference_cost'] or not math.isfinite(cost) or cost <= 0:
        raise ValueError('Frozen reference cost is not positive, finite and exact')
    artifacts = {}
    for kind in ['solution', 'check']:
        path = (copied_assets / f"{reference['instance_name']}-{kind}.json" if copied_assets else
                snapshot_root / reference[f'{kind}_path'])
        if sha256(path) != reference[f'{kind}_sha256']:
            raise ValueError('Frozen reference witness or checker artifact digest mismatch')
        artifacts[kind] = json.loads(path.read_bytes())
    if (artifacts['check'].get('status') != 'valid'
            or artifacts['check'].get('routing_cost') != cost
            or artifacts['solution'].get('instance_name') != reference['instance_name']
            or artifacts['solution'].get('objective_function') != 'Duration'
            or artifacts['solution'].get('cost') != cost):
        raise ValueError('Frozen feasible reference lacks matching checked-solution evidence')
    return cost


def reference_index(wave_path, wave):
    references, sources = {}, {}
    for size, description in wave['reference_manifests'].items():
        path = wave_path / 'frozen-references' / Path(description['path']).name
        if sha256(path) != description['sha256']:
            raise ValueError(f'Frozen reference digest mismatch for n={size}')
        manifest = json.loads(path.read_bytes())
        if manifest['checker'] != EXPECTED_CHECKER:
            raise ValueError('Frozen reference checker mismatch')
        sources[size] = {'path': str(path), 'sha256': description['sha256']}
        for row in manifest['instances']:
            if Path(row['instance_path']).parent.name != f'n={int(size)}':
                raise ValueError('Reference manifest assigned to the wrong customer count')
            key = f"Dabia2013/n={int(size)}/{row['instance_name']}"
            if key in references:
                raise ValueError('Duplicate frozen reference')
            references[key] = {**row, 'reference_manifest_sha256': description['sha256']}
    return references, sources


def task_reference(task, references):
    expected_key = f"Dabia2013/n={task['num_customers']}/{task['instance_name']}"
    if task['instance_key'] != expected_key or task['num_customers'] not in [25, 50, 100]:
        raise ValueError('Task size or instance key mismatch')
    reference = references[expected_key]
    cost = (float.fromhex(reference['reference_cost_hex']) if 'reference_cost_hex' in reference
            else reference['reference_cost'])
    if not math.isfinite(cost) or cost <= 0:
        raise ValueError('Positive finite reference required')
    for key, expected in [('instance_sha256', reference['instance_sha256']),
                          ('atf_sha256', reference['atf_file_sha256']),
                          ('reference_manifest_sha256', reference['reference_manifest_sha256']),
                          ('reference_cost', cost)]:
        if task[key] != expected:
            raise ValueError(f'Scheduled reference mismatch: {key}')
    return cost


def observed_backend(task, events):
    expected = 'highs' if task['arm'] == 'kayros-bpca-highs' else 'cplex'
    values = []
    required_launch_valid = True
    if task['arm'] == 'lera-exact':
        launches = [e for e in events if e.get('phase') == 'native_launch']
        valid = bool(launches) and all(e.get('native_instance_sha256') == task['native_instance_sha256']
                    and bool(e.get('metadata_equivalence'))
                    and all(v is True for v in e['metadata_equivalence'].values()) for e in launches)
        required_launch_valid = valid
        if valid:
            values.append('cplex')
    else:
        values = [str(e['lp_backend']).lower() for e in events
                  if e.get('phase') == 'exact' and 'lp_backend' in e]
    for event in events:
        if event.get('phase') == 'backend_settings' and 'backend' in event:
            values.append(str(event['backend']).lower())
            if (event.get('effective') or {}).get('threads') not in (None, 1):
                return {'passed': False, 'expected': expected, 'observed': values,
                        'reason': 'Native backend thread drift'}
        for section in (event.get('effective_backend_settings') or {}).values():
            if isinstance(section, dict) and 'backend' in section:
                values.append(str(section['backend']).lower())
                if section.get('threads') not in (None, 1):
                    return {'passed': False, 'expected': expected, 'observed': values,
                            'reason': 'Backend thread drift'}
    return {'passed': required_launch_valid and bool(values) and all(value == expected for value in values),
            'expected': expected, 'observed': values}


def scheduled_manifest(wave_path, wave, task):
    path = wave_path / 'manifests' / Path(task['manifest_path']).name
    if sha256(path) != task['manifest_sha256']:
        raise ValueError('Scheduled manifest digest mismatch')
    manifest = json.loads(path.read_bytes())
    expected_cell = {key: task[key] for key in ['task_id', 'instance_key', 'num_customers', 'arm_id',
        'arm', 'prefix_s', 'replicate', 'seed', 'horizon_s', 'memory_mb', 'reference_manifest_sha256']}
    if manifest.get('dabia_cell') != expected_cell:
        raise ValueError('Scheduled manifest cell binding mismatch')
    expected_source = wave['upstream_source_id'] if task['arm'] == 'lera-exact' else wave['kayros_source_id']
    source = re.match(r'^[0-9a-f]{7,40}(?=$|[,\s])', str(manifest.get('source_id')))
    if (manifest.get('declared_source_commit') != expected_source or source is None
            or not (source.group().startswith(expected_source) or expected_source.startswith(source.group()))):
        raise ValueError('Manifest source differs from the frozen comparison')
    if manifest.get('checker') != EXPECTED_CHECKER or manifest.get('diagnostic_environment', {}) != {}:
        raise ValueError('Manifest checker or diagnostic environment mismatch')
    expected_backend = 'highs' if task['arm'] == 'kayros-bpca-highs' else 'cplex'
    if manifest.get('backend', '').lower() != expected_backend:
        raise ValueError('Manifest backend mismatch')
    if task['arm'] == 'lera-exact':
        environment = manifest['worker_environment']
        if (manifest['runtime_files'].get(environment['UPSTREAM_PROBE_BINARY']) != UPSTREAM_BINARY_SHA256
                or UPSTREAM_PATCH_SHA256 not in manifest.get('source_patch_files', {}).values()
                or environment['UPSTREAM_NATIVE_INPUT_SHA256'] != task['native_instance_sha256']):
            raise ValueError('Minimal Lera binary, repair patch or native input pin mismatch')
    for filename, expected in manifest.get('source_patch_files', {}).items():
        relocated = wave_path / 'frozen-patches' / task['runtime_key'] / Path(filename).name
        if sha256(relocated) != expected:
            raise ValueError('Frozen source patch digest mismatch')
    return manifest


def observation(wave_path, task, reference_cost, manifest):
    base = {**task, 'run_id': None, 'status': 'pending', 'finished_task': False,
            'audit_ok': True, 'envelope': [], 'reported_closure_s': None,
            'capped_reported_closure_s': task['horizon_s'], 'native_guard_warning': False}
    row_path = wave_path / 'rows' / f"{task['task_id']}.json"
    if not row_path.exists():
        return base
    row = json.loads(row_path.read_bytes())
    if any(row.get(key) != value for key, value in task.items()):
        raise ValueError('Stored row differs from its scheduled task')
    base.update(status=row['status'], finished_task=row['status'] not in ['pending', 'running'],
                backend_audit=row.get('backend_audit'), reason=row.get('reason'), raw_controller_row=row)
    if not row.get('run_id'):
        return base
    cell = wave_path / 'cells' / row['run_id']
    identity = json.loads((cell / 'identity.json').read_bytes())
    if digest(manifest) != identity['manifest_hash']:
        raise ValueError('Scheduled manifest or checker mismatch')
    params = identity['parameters']
    expected = {'arm': task['arm'], 'instance_name': task['instance_name'],
                'instance_sha256': task['instance_sha256'], 'atf_sha256': task['atf_sha256'],
                'prefix_seconds': task['prefix_s'], 'repetition': task['replicate'],
                'search_seed': task['seed'], 'memory_mb': task['memory_mb'],
                'memory_cap_bytes': task['memory_mb'] * 1024 ** 2,
                'cpu_affinity': [task['cpu_core']], 'worker_python_sha256': task['worker_python_sha256'],
                'worker_sha256': sha256(wave_path / 'frozen-scripts' / Path(task['worker_path']).name),
                'orchestration_files': {name: sha256(wave_path / 'frozen-scripts' / name) for name in
                    ['run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py']}}
    if any(params.get(key) != value for key, value in expected.items()):
        raise ValueError('Run parameters differ from frozen schedule')
    if (identity['command'][:2] != [task['worker_python'], task['worker_path']]
            or identity['cwd'] != str(Path(task['worker_path']).parent.parent)):
        raise ValueError('Run worker command or working directory differs from frozen schedule')
    if identity['horizon_s'] != task['horizon_s']:
        raise ValueError('Run horizon mismatch')
    result = load_completed(cell, expected_run_id=row['run_id'])
    events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
    late_events = [json.loads(line) for line in (cell / 'late-events.jsonl').read_text().splitlines()]
    validate_late_event_identity(late_events, identity)
    stderr = (cell / 'stderr.log').read_text(errors='replace')
    validation = result.get('validation', {})
    envelope = validation.get('checked_envelope', [])
    if validation.get('checker') != EXPECTED_CHECKER:
        raise ValueError('Unchecked incumbent envelope')
    if any(validation.get(key) != task[key] for key in ['instance_name', 'instance_sha256', 'atf_sha256']):
        raise ValueError('Checked validation payload is bound to another input')
    candidates = {e['sequence']: e for e in validation.get('candidates', []) if e.get('valid') is True}
    for point in envelope:
        checked = candidates.get(point['sequence'])
        if not checked or any(checked.get(key) != point.get(key)
                              for key in ['elapsed_s', 'checked_cost', 'routes']):
            raise ValueError('Envelope point lacks matching checked witness')
    score_trace(envelope, reference_cost, task['horizon_s'])
    backend = observed_backend(task, events)
    if row.get('backend_audit', {}).get('passed') is not backend['passed']:
        raise ValueError('Controller and independently observed backend disagree')
    closure, resource = closure_and_resource(result, events, stderr)
    expected_settings = manifest.get('expected_algorithm_settings', {})
    observed_settings = [event['effective_algorithm_settings'] for event in [*events, *late_events]
                         if isinstance(event.get('effective_algorithm_settings'), dict)]
    algorithm_failed = bool(expected_settings) and ((not observed_settings and result['status'] == 'completed')
        or any(any(settings.get(key) is not expected for key, expected in expected_settings.items())
               for settings in observed_settings))
    algorithm_audit = {'expected': expected_settings, 'observed': observed_settings,
        'status': 'failed' if algorithm_failed else 'passed' if expected_settings and observed_settings else
                  'unavailable' if expected_settings else 'not_requested'}
    controller_failure = row['status'] in ['backend_audit_failure', 'launch_or_audit_failure']
    if not backend['passed'] or controller_failure or algorithm_failed:
        closure = None
    guards = [e['checker_infeasible_columns'] for e in events if 'checker_infeasible_columns' in e]
    warned = any(guards) or 'skipped checker-infeasible priced route' in stderr
    return {**base, 'run_id': row['run_id'], 'status': row['status'], 'raw_result_status': result['status'],
            'finished_task': True, 'audit_ok': not controller_failure and not algorithm_failed,
            'independent_algorithm_settings_audit': algorithm_audit,
            'source_id': manifest.get('source_id'), 'manifest_sha256': digest(manifest),
            'runtime_identity': digest(sorted(manifest['runtime_files'].values())),
            'envelope': envelope, 'reported_closure_s': closure,
            'capped_reported_closure_s': closure if closure is not None else task['horizon_s'],
            'resource_failure_kind': resource, 'native_guard_warning': warned,
            'reported_closure_with_native_guard_warning': closure is not None and warned,
            'independent_backend_audit': backend,
            'invalid_candidate_count': sum(not c.get('valid', False) for c in validation.get('candidates', [])),
            'result_issues': result.get('issues', []),
            'raw_proof_status': validation.get('proof', {}).get('status'),
            **proof_evidence(validation, events, task['horizon_s'], late_events, expected_inputs=task),
            'sources': {name: sha256(cell / name) for name in ['identity.json', 'result.json', 'events.jsonl', 'late-events.jsonl']}}


def summarize(wave_path):
    wave = json.loads((wave_path / 'wave.json').read_bytes())
    allocation_preflight = verify_allocation_preflight(wave_path, wave)
    if wave['checker'] != EXPECTED_CHECKER or wave.get('diagnostic_environment', {}) != {}:
        raise ValueError('Wave checker or diagnostic environment mismatch')
    if wave['upstream_source_id'] != 'f2c9e20' or not re.fullmatch(r'[0-9a-f]{40}', wave['kayros_source_id']):
        raise ValueError('Comparison source identities are not explicitly pinned')
    if (wave['upstream_binary_sha256'] != UPSTREAM_BINARY_SHA256
            or wave['upstream_minimal_patch_sha256'] != UPSTREAM_PATCH_SHA256):
        raise ValueError('Wave minimal Lera artifact pins differ')
    for filename, expected in {**wave['frozen_scripts_sha256'], 'design.json': wave['design_sha256'],
                               'slots.json': wave['slots_sha256']}.items():
        if sha256(wave_path / filename) != expected:
            raise ValueError('Frozen schedule or launcher input digest mismatch')
    tasks = wave['scheduled_cells']
    if not tasks or len({task['task_id'] for task in tasks}) != len(tasks):
        raise ValueError('Empty or duplicate schedule')
    if {t['arm_id'] for t in tasks} != set(ARM_IDS):
        raise ValueError('The five declared configurations must all be scheduled')
    if {t['horizon_s'] for t in tasks} != {3600}:
        raise ValueError('This panel requires the declared 3600 s common horizon')
    populations = defaultdict(set)
    full_seeds = {}
    for task in tasks:
        if (task['arm'], task['prefix_s']) != ARM_CONTRACT[task['arm_id']]:
            raise ValueError('Arm label differs from the actual worker configuration')
        if (task['arm'] == 'kayros-full-cplex') != (task['seed'] is not None):
            raise ValueError('Full arms require seeds and cold arms require null seeds')
        key = (task['num_customers'], task['instance_key'], task['replicate'])
        if key in populations[task['arm_id']]:
            raise ValueError('Duplicate instance trial within an arm')
        populations[task['arm_id']].add(key)
        if task['arm'] == 'kayros-full-cplex':
            if key in full_seeds and full_seeds[key] != task['seed']:
                raise ValueError('Full-prefix arms must share the paired ILS seed')
            full_seeds[key] = task['seed']
    if any(population != populations[ARM_IDS[0]] for population in populations.values()):
        raise ValueError('Arms have different frozen instance-trial populations')
    references, snapshots = reference_index(wave_path, wave)
    for key in {task['instance_key'] for task in tasks}:
        reference = references[key]
        size = Path(reference['instance_path']).parent.name[2:]
        checked_reference_cost(reference, wave_path / 'frozen-references',
                               wave_path / 'frozen-references' / f'n{size}')
    cells = []
    for task in tasks:
        cost = task_reference(task, references)
        try:
            manifest = scheduled_manifest(wave_path, wave, task)
            cell = observation(wave_path, task, cost, manifest)
        except (OSError, ValueError, KeyError, TypeError) as error:
            row_path = wave_path / 'rows' / f"{task['task_id']}.json"
            try:
                raw = json.loads(row_path.read_bytes())
            except (OSError, ValueError):
                raw = None
            cell = {**task, 'run_id': raw.get('run_id') if raw else None,
                    'status': raw.get('status') if raw else 'audit_failure', 'analysis_status': 'audit_failure',
                    'raw_controller_row': raw, 'finished_task': raw is not None,
                    'audit_ok': False, 'reason': f'{type(error).__name__}: {error}', 'envelope': [],
                    'reported_closure_s': None, 'capped_reported_closure_s': task['horizon_s']}
        cell.update(score_trace(cell['envelope'], cost, task['horizon_s']))
        cells.append(cell)
    feasible_ceilings = apply_feasible_consistency(cells)
    groups = defaultdict(list)
    for cell in cells:
        groups[(cell['num_customers'], cell['arm_id'])].append(cell)
    summaries = []
    for (size, arm_id), rows in sorted(groups.items()):
        per_instance = defaultdict(list)
        for row in rows:
            per_instance[row['instance_key']].append(row)
        mean = lambda field: statistics.mean(statistics.mean(r[field] for r in trials)
                                             for trials in per_instance.values())
        summaries.append({'num_customers': size, 'arm_id': arm_id, 'scheduled_cells': len(rows),
                          'instances': len(per_instance), 'status_counts': dict(Counter(r['status'] for r in rows)),
                          'reported_closures': sum(r['reported_closure_s'] is not None for r in rows),
                          'contradictory_proof_observations': sum(r['consistency_audit']['status'] == 'contradicted' for r in rows),
                          'unsupported_closure_observations': sum(r['consistency_audit']['status'] == 'unsupported' for r in rows),
                          'mean_capped_reported_closure_s': mean('capped_reported_closure_s'),
                          'mean_lambda': mean('lambda'),
                          'final_missing_incumbents': sum(r['final_checked_cost'] is None for r in rows)})
    return {'schema_version': 1, 'wave_name': wave.get('wave_name', wave_path.name),
            'allocation_preflight': allocation_preflight,
            'checker': EXPECTED_CHECKER, 'wave_sha256': sha256(wave_path / 'wave.json'),
            'reference_manifests': snapshots, 'analysis_sha256': sha256(Path(__file__)),
            'proof_consistency_helper_sha256': sha256(Path(__file__).resolve().parents[1] / 'scripts/ms58_proof_consistency.py'),
            'scheduled_cells': len(cells), 'provisional': any(not c['finished_task'] for c in cells),
            'consistency_audit_completed': True, 'feasible_ceilings': feasible_ceilings,
            'consistency_failure_count': sum(c['consistency_audit']['status'] == 'contradicted' for c in cells),
            'unsupported_closure_count': sum(c['consistency_audit']['status'] == 'unsupported' for c in cells),
            'audit_failure_count': sum(not c['audit_ok'] for c in cells), 'groups': summaries, 'cells': cells,
            'qualification': 'Development comparison. Reported closure with a checked witness is not independently certified proof. All scheduled outcomes retained. Sizes are separate populations.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.wave.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({key: report[key] for key in ['scheduled_cells', 'provisional', 'audit_failure_count',
                                                 'consistency_failure_count', 'groups']}, indent=2))


if __name__ == '__main__':
    main()
