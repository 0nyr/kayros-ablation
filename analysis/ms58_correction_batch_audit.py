"""Re-audit the integrated batching screen without changing frozen or controller artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from checker_contract import sha256
from ms58_analyze_pairs import EXPECTED_CHECKER, closure_and_resource, native_work
from ms58_correction_batch_run import SOURCES, ORDERS, REFERENCE_SHA
from ms58_correction_batch_native import FIXTURE_SHA, ILS_BINARY, validate_fixture
from ms58_dabia_screen import LERA, LERA_BINARY, LERA_PATCH
from ms58_dabia_screen_summary import screen_manifest
from ms58_dabia_summary import checked_reference_cost, observation
from ms58_proof_consistency import apply_feasible_consistency
from probe_runner import digest, load_completed


def fixed_reference_pins(task, manifest):
    binary = manifest.get('worker_environment', {}).get('UPSTREAM_PROBE_BINARY')
    return (task.get('source_id') == LERA and manifest.get('source_id') == LERA
        and manifest.get('declared_source_commit') == LERA
        and manifest.get('backend', '').lower() == 'cplex'
        and manifest.get('runtime_files', {}).get(binary) == LERA_BINARY
        and LERA_PATCH in manifest.get('source_patch_files', {}).values()
        and manifest.get('diagnostic_environment', {}) == {})


def algorithm_setting_audit(task, native, manifest):
    """Separate missing fixed-reference metadata from an observed settings mismatch."""
    exact = (native.get('exact', native.get('native_result', {})) or {}) if native is not None else {}
    settings = exact.get('effective_algorithm_settings', {}) or {}
    expected = task['expected_algorithm_settings']
    pinned_reference = fixed_reference_pins(task, manifest)
    fields = {}
    for name, aliases in (('label_correction', ('label_correction',)),
                          ('lazy_extension', ('lazy_extension', 'label_lazy_extension'))):
        observed = {key: settings[key] for key in aliases if key in settings}
        state = ('unknown' if not observed else 'passed' if all(value is expected[name] for value in observed.values()) else 'failed')
        fields[name] = dict(expected=expected[name], observed_fields=observed, status=state,
            expectation_basis='Pinned source default; absence is not an observed match',
            blocking=state == 'failed' or state == 'unknown' and not pinned_reference)
    failed = any(field['blocking'] for field in fields.values())
    status = 'failed' if failed else 'unknown' if any(f['status'] == 'unknown' for f in fields.values()) else 'passed'
    return dict(status=status, fields=fields, observed=settings, expected=expected,
        native_sidecar_present=native is not None, fixed_reference_source_pins_verified=pinned_reference,
        missing_metadata_policy='Only absent settings in the exact pinned f2 reference are nonblocking unknowns. Present mismatches always fail.')


def classify_observation(cell, task, manifest, payload, result, events, stderr):
    algorithm = algorithm_setting_audit(task, payload, manifest)
    closure, resource = closure_and_resource(result, events, stderr)
    mismatches = sum(c.get('native_minus_checked') is not None and abs(c['native_minus_checked']) > 1e-6
                     for c in result['validation'].get('candidates', []))
    usable = (cell.get('audit_ok') is True and cell.get('independent_backend_audit', {}).get('passed') is True
        and result['status'] in {'completed', 'deadline'} and not result.get('issues')
        and not cell.get('invalid_candidate_count') and not mismatches and not resource
        and not cell.get('native_guard_warning') and algorithm['status'] != 'failed')
    cell.update(algorithm_settings_audit=algorithm, observation_usable=bool(usable),
        native_cost_mismatch_count=mismatches, resource_failure_kind=resource,
        raw_closure_event_times_s=[e['elapsed_s'] for e in events if e.get('event') == 'closure'],
        closure_from_checked_events_before_settings_s=closure,
        reported_closure_s=closure if usable else None,
        capped_reported_closure_s=closure if usable and closure is not None else task['horizon_s'],
        native_work=native_work(payload) if payload is not None else {'available': False})
    return cell


def reaudit(screen, *, allow_incomplete=False):
    screen = screen.resolve()
    schedule = screen / 'schedule.json'
    if sha256(schedule) != json.loads((screen / 'schedule-hash.json').read_bytes())['sha256']:
        raise ValueError('Schedule seal mismatch')
    spec = json.loads(schedule.read_bytes())
    tasks = spec['scheduled_cells']
    population = {(name, rep, variant) for name in ('C201', 'RC201') for rep in (1, 2, 3) for variant in SOURCES}
    if (spec['checker'] != EXPECTED_CHECKER or spec['scheduled_count'] != 18 or len(tasks) != 18
            or len({t['task_id'] for t in tasks}) != 18
            or {(t['instance_name'], t['replicate'], t['variant']) for t in tasks} != population
            or any(t['source_id'] != SOURCES[t['variant']] for t in tasks)):
        raise ValueError('Require the entire matched 18-cell integrated screen')
    for filename, expected in {**spec['frozen_files'], 'plan.json': spec['plan_sha256']}.items():
        if sha256(screen / filename) != expected:
            raise ValueError('Frozen input/helper/manifest mismatch: ' + filename)
    completion_path = screen / 'complete.json'
    if not completion_path.exists() and not allow_incomplete:
        raise ValueError('Final re-audit requires complete.json; use --allow-incomplete only for a labelled interim report')
    completion = json.loads(completion_path.read_bytes()) if completion_path.exists() else None
    if completion is not None and set(completion['row_sha256']) != {t['task_id'] + '.json' for t in tasks}:
        raise ValueError('Completion seal must cover every scheduled row')
    for index, (name, rep) in enumerate((n, r) for n in ('C201', 'RC201') for r in (1, 2, 3)):
        triplet = sorted((t for t in tasks if (t['instance_name'], t['replicate']) == (name, rep)), key=lambda t: t['position'])
        if len({(t['host'], t['cpu_core']) for t in triplet}) != 1 or tuple(t['variant'] for t in triplet) != ORDERS[index]:
            raise ValueError('Comparison triplet core/order mismatch')
    if len({t['host'] for t in tasks}) != 1:
        raise ValueError('Require one node for this cohort')
    gate_path = screen / 'native-gate/complete.json'
    gate = json.loads(gate_path.read_bytes())
    if (sha256(gate_path) != spec['native_gate_sha256']
            or gate.get('source_id') != SOURCES['correction-on-domain-batch']
            or gate.get('fixture_source_id') != SOURCES['correction-on-domain-batch']
            or gate.get('fixture_sha256') != FIXTURE_SHA
            or gate.get('all_checks_passed') is not True or gate.get('case_count') != 17
            or [r.get('name') for r in gate.get('results', [])] != ['compile', 'link', 'fixture']
            or not all(r.get('passed') is True for r in gate['results'])
            or sha256(screen / 'native-gate/fixture.stdout') != gate['fixture_stdout_sha256']):
        raise ValueError('The exact integrated 17-case native gate must remain passed and sealed')
    validate_fixture(json.loads((screen / 'native-gate/fixture.stdout').read_bytes()))
    reference_path = screen / 'frozen-references/manifest.json'
    if sha256(reference_path) != REFERENCE_SHA:
        raise ValueError('Reference snapshot mismatch')
    references = {r['instance_name']: r for r in json.loads(reference_path.read_bytes())['instances']}
    for name in ('C201', 'RC201'):
        checked_reference_cost(references[name], reference_path.parent)
    cells = []
    input_sha256 = {'schedule.json': sha256(schedule)}
    if completion is not None:
        input_sha256['complete.json'] = sha256(completion_path)
    for task in tasks:
        raw = None
        row_path = screen / 'rows' / (task['task_id'] + '.json')
        reference = references[task['instance_name']]
        try:
            if not row_path.exists():
                if completion is not None:
                    raise ValueError('Completed scheduled row is missing')
                cell = dict(task, status='pending', audit_ok=False, observation_usable=False, finished_task=False,
                            reported_closure_s=None, capped_reported_closure_s=task['horizon_s'])
            else:
                row_hash = sha256(row_path)
                input_sha256[str(row_path.relative_to(screen))] = row_hash
                if completion is not None and row_hash != completion['row_sha256'][row_path.name]:
                    raise ValueError('Controller row seal mismatch')
                raw = json.loads(row_path.read_bytes())
                manifest = screen_manifest(screen, task)
                if task['variant'] != 'minimal-lera':
                    for name, pinned in (('_core.', ILS_BINARY),
                        ('_lera.', gate['identity']['runtime'][task['variant']]['lera']['sha256'])):
                        values = [value for filename, value in manifest['runtime_files'].items()
                                  if Path(filename).name.startswith(name)]
                        if values != [pinned]:
                            raise ValueError('Kayros runtime differs from its sealed integrated/control build')
                if (task['horizon_s'] != 600 or task['memory_mb'] != 16384 or task['seed'] is not None
                        or task['prefix_s'] is not None or task['diagnostic_environment'] != {}
                        or task['expected_algorithm_settings'] != {'label_correction': task['variant'] != 'minimal-lera', 'lazy_extension': True}):
                    raise ValueError('Cold-cell resource or source expectation mismatch')
                cell = observation(screen, task, reference['reference_cost'], manifest)
                if raw.get('run_id'):
                    directory = screen / 'cells' / raw['run_id']
                    expected = json.loads((screen / 'started' / (task['task_id'] + '-expected-identity.json')).read_bytes())
                    identity = json.loads((directory / 'identity.json').read_bytes())
                    if digest(expected) != raw['run_id'] or any(identity.get(k) != v for k, v in expected.items()):
                        raise ValueError('Started/completed run identity mismatch')
                    result = load_completed(directory, expected_run_id=raw['run_id'])
                    if sha256(directory / 'result.json') != raw['result_sha256']:
                        raise ValueError('Controller result hash mismatch')
                    descriptor = result['validation'].get('native_result', {})
                    payload = json.loads((directory / descriptor['relative_path']).read_bytes()) if descriptor.get('sha256') else None
                    events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
                    cell = classify_observation(cell, task, manifest, payload, result, events,
                                                (directory / 'stderr.log').read_text(errors='replace'))
                    cell['native_payload_sha256'] = descriptor.get('sha256')
                else:
                    cell['observation_usable'] = False
                cell.update(raw_controller_row=raw, raw_controller_row_sha256=row_hash,
                            raw_controller_reported_closure_s=raw.get('reported_closure_s'),
                            raw_controller_algorithm_settings_audit=raw.get('algorithm_settings_audit'))
        except (OSError, ValueError, KeyError, TypeError) as error:
            cell = dict(task, status='independent_audit_failure', audit_ok=False, observation_usable=False,
                        finished_task=row_path.exists(), reason=f'{type(error).__name__}: {error}',
                        reported_closure_s=None, capped_reported_closure_s=task['horizon_s'], raw_controller_row=raw)
        cell.update(reference_cost=reference['reference_cost'], reference_manifest_sha256=REFERENCE_SHA)
        cells.append(cell)
    ceilings = apply_feasible_consistency(cells)
    for cell in cells:
        cell['closure_restored_from_raw_events'] = (cell.get('raw_controller_reported_closure_s') is None
            and cell.get('reported_closure_s') is not None)
    comparisons = []
    indexed = {(c['instance_name'], c['replicate'], c['variant']): c for c in cells}
    for name in ('C201', 'RC201'):
        for rep in (1, 2, 3):
            batch = indexed[name, rep, 'correction-on-domain-batch']
            for control in ('correction-on', 'minimal-lera'):
                reference = indexed[name, rep, control]
                a, b = batch.get('reported_closure_s'), reference.get('reported_closure_s')
                usable = batch.get('proof_observation_usable') and reference.get('proof_observation_usable')
                comparisons.append(dict(instance=name, replicate=rep, control=control, both_usable=bool(usable),
                    batch_closure_s=a, control_closure_s=b, control_over_batch_closure_ratio=b / a if usable and a and b else None,
                    matching_whole_work=bool(batch.get('native_work', {}).get('whole_work_signature')) and
                        batch.get('native_work', {}).get('whole_work_signature') == reference.get('native_work', {}).get('whole_work_signature'),
                    matching_root_work=bool(batch.get('native_work', {}).get('root_work_signature')) and
                        batch.get('native_work', {}).get('root_work_signature') == reference.get('native_work', {}).get('root_work_signature')))
    return dict(analysis_kind='offline metadata classification repair with independent sealed-evidence re-audit',
        interim=completion is None, screen=str(screen), scheduled_count=18, retained_cell_count=len(cells),
        input_sha256=input_sha256, analysis_script_sha256=sha256(Path(__file__)),
        statuses=dict(Counter(c['status'] for c in cells)),
        unknown_metadata_cells=[c['task_id'] for c in cells if c.get('algorithm_settings_audit', {}).get('status') == 'unknown'],
        restored_closure_cells=[c['task_id'] for c in cells if c['closure_restored_from_raw_events']],
        unusable_cells=[c['task_id'] for c in cells if not c.get('proof_observation_usable')],
        feasible_ceilings=ceilings, pairs=comparisons, cells=cells,
        qualification='All 18 scheduled outcomes retained. Original frozen/controller artifacts remain unchanged. Missing fixed-f2 metadata stays unknown and source expectations are not reported as observations. Present mismatches still fail. Two instances on one node do not establish overall superiority. Necessary proof consistency is not independent proof certification.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.screen.resolve()):
        parser.error('Offline report must be outside the preserved screen directory')
    report = reaudit(args.screen, allow_incomplete=args.allow_incomplete)
    with args.output.open('x') as output:
        json.dump(report, output, indent=2, allow_nan=False)
        output.write('\n')
    print(json.dumps({k: report[k] for k in ('interim', 'scheduled_count', 'retained_cell_count',
        'unknown_metadata_cells', 'restored_closure_cells', 'unusable_cells')}, indent=2))


if __name__ == '__main__':
    main()
