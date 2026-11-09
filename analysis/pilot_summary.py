"""Summarize scheduled pilot cells against frozen references, including failures."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from checker_contract import checker_identity
from probe_runner import digest


def assert_scheduled_row(task, row):
    """Refuse a row associated with another scheduled task, including failures."""
    if row and row.get('task_id') != task['task_id']:
        raise ValueError('Scheduled row task identity mismatch')
    for key, value in task.items():
        if key in row and row[key] != value:
            raise ValueError(f'Scheduled row mismatch: {key}')


def assert_task_result_identity(*, task, row, identity, result, wave, reference,
                                expected_reference_manifest_sha256,
                                scheduled_manifest=None):
    """Bind a completed observation to its scheduled cell after relocation.

    Event and native-sidecar file integrity are separate checks. Legacy trial 0
    is a real identifier and is compared directly rather than renumbered.
    """
    assert_scheduled_row(task, row)
    run_id = row.get('run_id')
    core_identity = {key: value for key, value in identity.items()
                     if key not in ('run_id', 'manifest')}
    manifest = identity.get('manifest', {})
    manifest_hash = digest(manifest)
    if (not run_id or identity.get('run_id') != run_id
            or result.get('run_id') != run_id or digest(core_identity) != run_id
            or result.get('identity_hash') != run_id
            or identity.get('manifest_hash') != manifest_hash
            or result.get('manifest_hash') != manifest_hash):
        raise ValueError('Run or manifest identity mismatch')
    if scheduled_manifest is not None and digest(scheduled_manifest) != manifest_hash:
        raise ValueError('Scheduled manifest identity mismatch')
    if wave.get('reference_manifest_sha256') != expected_reference_manifest_sha256:
        raise ValueError('Wave reference manifest identity mismatch')
    # Native legacy manifests have no reference/selection fields. Their wave
    # identity and exact input hashes still bind them to the frozen schedule.
    for key in ('reference_manifest_sha256', 'selection_sha256'):
        if key in manifest and manifest[key] != wave.get(key):
            raise ValueError(f'Manifest/wave mismatch: {key}')
    for key in ('wave_name', 'wave_id'):
        if key in wave and manifest.get(key) != wave[key]:
            raise ValueError(f'Manifest/wave mismatch: {key}')
    for key in ('created_utc', 'oar_job_id'):
        if key in manifest and key in wave and manifest[key] != wave[key]:
            raise ValueError(f'Manifest/wave mismatch: {key}')
    params = identity.get('parameters', {})
    expected = {'instance_name': task['instance_name'], 'arm': task['arm'],
                'prefix_seconds': task.get('prefix_s'),
                'repetition': task['replicate'], 'search_seed': task.get('seed'),
                'instance_sha256': reference['instance_sha256'],
                'atf_sha256': reference['atf_file_sha256']}
    missing = object()
    for key, value in expected.items():
        if params.get(key, missing) != value:
            raise ValueError(f'Scheduled parameter mismatch: {key}')
    if (task['instance_name'] != reference['instance_name']
            or task.get('instance_sha256', reference['instance_sha256']) != reference['instance_sha256']
            or identity.get('horizon_s') != task['horizon_s']
            or wave.get('horizon_s', task['horizon_s']) != task['horizon_s']):
        raise ValueError('Scheduled input or horizon mismatch')
    reference_cost = (float.fromhex(reference['reference_cost_hex'])
                      if 'reference_cost_hex' in reference else reference.get('reference_cost'))
    if 'reference_cost' in task and task['reference_cost'] != reference_cost:
        raise ValueError('Scheduled reference cost mismatch')
    if 'cpu_core' in task and params.get('cpu_affinity') != [task['cpu_core']]:
        raise ValueError('Scheduled CPU affinity mismatch')
    if 'memory_mb' in task and (params.get('memory_mb') != task['memory_mb']
            or params.get('memory_cap_bytes') != task['memory_mb'] * 1024 ** 2):
        raise ValueError('Scheduled memory cap mismatch')
    if 'result_path' in row:
        expected_path = Path(task['output_directory']) / run_id / 'result.json'
        if Path(row['result_path']) != expected_path:
            raise ValueError('Scheduled row result path mismatch')


def score_trace(envelope, reference, horizon):
    assert reference > 0 and horizon > 0
    area, previous_time, previous_gap = 0., 0., 1.
    best, first, attainment = None, None, None
    for point in envelope:
        t, cost = point['elapsed_s'], point['checked_cost']
        if not math.isfinite(t) or not math.isfinite(cost) or not 0 <= t <= horizon or cost < 0:
            raise ValueError('Invalid checked trace point')
        if t < previous_time:
            raise ValueError('Nonmonotone checked trace')
        area += (t - previous_time) * previous_gap
        if best is not None and cost > best:
            raise ValueError('Checked envelope is not best-so-far')
        best = cost
        previous_gap = (cost-reference)/(cost+reference)
        previous_time = t
        if first is None:
            first = t
        if cost <= reference and attainment is None:
            attainment = t
    area += (horizon-previous_time)*previous_gap
    return {'lambda': area/horizon, 'final_squeezed_gap': previous_gap,
            'final_checked_cost': best, 'final_raw_gap': None if best is None else (best-reference)/reference,
            'first_valid_s': first, 'reference_attainment_s': attainment}


def summarize(wave_path, snapshot):
    checker = checker_identity()
    project = Path(__file__).resolve().parents[1]
    pins = json.loads((project/'configs/source_pins.json').read_text())
    raw = (snapshot/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != pins['dataset']['reference_manifest_sha256']:
        raise ValueError('Reference manifest identity mismatch')
    refs = {e['instance_name']: e for e in json.loads(raw)['instances']}
    wave = json.loads((wave_path/'wave.json').read_text())
    if wave.get('reference_manifest_sha256') != pins['dataset']['reference_manifest_sha256']:
        raise ValueError('Wave reference manifest identity mismatch')
    cells = []
    seen = set()
    for task in wave['scheduled_cells']:
        tid = task['task_id']
        if tid in seen:
            raise ValueError('Repeated scheduled task')
        seen.add(tid)
        record_path = wave_path/'rows'/f'{tid}.json'
        row = json.loads(record_path.read_text()) if record_path.exists() else {}
        assert_scheduled_row(task, row)
        result = None
        events = []
        run_id = row.get('run_id')
        if run_id:
            cell = wave_path/'cells'/run_id
            result = json.loads((cell/'result.json').read_text())
            identity = json.loads((cell/'identity.json').read_text())
            ref = refs[task['instance_name']]
            scheduled_manifest = json.loads((wave_path/'manifests'/Path(task['manifest_path']).name).read_bytes())
            assert_task_result_identity(task=task, row=row, identity=identity,
                result=result, wave=wave, reference=ref,
                expected_reference_manifest_sha256=pins['dataset']['reference_manifest_sha256'],
                scheduled_manifest=scheduled_manifest)
            if hashlib.sha256((cell/'events.jsonl').read_bytes()).hexdigest() != result['events_sha256']:
                raise ValueError('Event digest mismatch')
            events = [json.loads(line) for line in (cell/'events.jsonl').read_text().splitlines()]
        validation = result.get('validation', {}) if result else {}
        if validation.get('checker') not in (None, checker):
            raise ValueError('Checker identity mismatch')
        envelope = validation.get('checked_envelope', [])
        if envelope and validation.get('checker') != checker:
            raise ValueError('Unchecked incumbent trace')
        score = score_trace(envelope, refs[task['instance_name']]['reference_cost'], task['horizon_s'])
        proof = validation.get('proof', {})
        accepted = proof.get('status') == 'accepted'
        tproof = proof.get('elapsed_s') if accepted else task['horizon_s']
        if not 0 <= tproof <= task['horizon_s']:
            raise ValueError('Invalid accepted proof timestamp')
        reported = [e for e in events if e['event'] == 'closure']
        integrity = [e for e in events if e['event'] == 'integrity']
        checks = {c['sequence']: c for c in validation.get('candidates', [])}
        closure = None
        for event in reported:
            previous = [e for e in events if e['event'] == 'candidate' and e['sequence'] < event['sequence']]
            if previous and checks.get(previous[-1]['sequence'], {}).get('valid') is True:
                closure = event
                break
        usable_status = result is not None and result['status'] in ('completed', 'deadline') and not result.get('issues')
        reported_time = closure['elapsed_s'] if closure and not integrity and usable_status else None
        native_guard_values = [event['checker_infeasible_columns'] for event in events
                               if event.get('checker_infeasible_columns') is not None]
        if any(type(value) is not int or value < 0 for value in native_guard_values):
            raise ValueError('Invalid native priced-column guard counter')
        native_guard_count = max(native_guard_values) if native_guard_values else None
        guard_stderr = (wave_path/'cells'/run_id/'stderr.log').read_bytes() if run_id else b''
        guard_stderr_count = guard_stderr.count(b'M5.9: skipped checker-infeasible priced route')
        guard_warning = bool(native_guard_count) or guard_stderr_count > 0
        cells.append({**task, 'run_id': run_id, 'status': result['status'] if result else row.get('status','pending'),
                      'completed_record': result is not None, 'finished_task': bool(row), **score,
                      'invalid_candidate_count': sum(not c.get('valid', False) for c in validation.get('candidates', [])),
                      'max_abs_native_checked_discrepancy': max((abs(c['native_minus_checked']) for c in validation.get('candidates', []) if c.get('native_minus_checked') is not None), default=0.),
                      'native_closure_unverified': proof.get('status') == 'proof_unverified',
                      'accepted_proof': accepted, 'capped_proof_s': tproof,
                      'reported_closure_with_checked_witness': reported_time is not None,
                      'reported_closure_s': reported_time,
                      'native_rejected_priced_columns': native_guard_count,
                      'native_guard_stderr_warning_count': guard_stderr_count,
                      'stderr_sha256': hashlib.sha256(guard_stderr).hexdigest() if run_id else None,
                      'native_guard_warning': guard_warning,
                      'reported_closure_with_native_guard_warning': reported_time is not None and guard_warning,
                      'capped_reported_closure_s': task['horizon_s'] if reported_time is None else reported_time,
                      'closure_diagnostics': (closure.get('closure_census') or {k: closure[k] for k in ('cg_optimum_count', 'certified_cg_optimum_count', 'restricted_master_infeasible', 'unbranched_fractional') if k in closure}) if closure else {},
                      'peak_rss_kib': result.get('sampled_solver_peak_rss_kib') if result else None,
                      'timeout_overshoot_s': result.get('timeout_overshoot_s') if result else None})
    grouped = {}
    for c in cells:
        key = c['arm'] + (f"/prefix={c['prefix_s']}" if c.get('prefix_s') is not None else '')
        grouped.setdefault(key, []).append(c)
    groups = []
    for key, rows in sorted(grouped.items()):
        by_instance = {}
        for row in rows:
            by_instance.setdefault(row['instance_name'], []).append(row)
        def avg(field):
            return statistics.mean(statistics.mean(c[field] for c in r) for r in by_instance.values())
        groups.append({'configuration': key, 'scheduled': len(rows), 'completed_records':sum(c['completed_record'] for c in rows),
                       'feasible':sum(c['final_checked_cost'] is not None for c in rows),
                       'reference_attained':sum(c['reference_attainment_s'] is not None for c in rows),
                       'invalid_candidate_count':sum(c['invalid_candidate_count'] for c in rows),
                       'max_abs_native_checked_discrepancy':max(c['max_abs_native_checked_discrepancy'] for c in rows),
                       'native_closures_unverified':sum(c['native_closure_unverified'] for c in rows),
                       'accepted_proofs':sum(c['accepted_proof'] for c in rows),
                       'reported_closures_with_checked_witness':sum(c['reported_closure_with_checked_witness'] for c in rows),
                       'native_guard_counter_available':sum(c['native_rejected_priced_columns'] is not None for c in rows),
                       'native_guard_warning_cells':sum(c['native_guard_warning'] for c in rows),
                       'reported_closures_with_native_guard_warning':sum(c['reported_closure_with_native_guard_warning'] for c in rows),
                       'restricted_mean_reported_closure_s':avg('capped_reported_closure_s') if 'heuristics' not in key else None,
                       'conditional_mean_final_raw_gap': statistics.mean(c['final_raw_gap'] for c in rows if c['final_raw_gap'] is not None) if any(c['final_raw_gap'] is not None for c in rows) else None,
                       'max_sampled_rss_kib': max((c['peak_rss_kib'] or 0 for c in rows), default=0),
                       'max_timeout_overshoot_s': max((c['timeout_overshoot_s'] or 0 for c in rows), default=0),
                       'mean_lambda':avg('lambda'), 'mean_final_squeezed_gap':avg('final_squeezed_gap'),
                       'restricted_mean_proof_s':avg('capped_proof_s') if 'heuristics' not in key else None})
    return {'wave_name':wave['wave_name'],'purpose':wave.get('purpose'), 'checker':checker,
            'reference_manifest_sha256':pins['dataset']['reference_manifest_sha256'],
            'provisional':any(not c['finished_task'] for c in cells),
            'warning':'Pending or failed cells remain in the denominator. Reported closure with checked witness is an implementation-level observation under declared conventions, not a formal certificate. Native rejected-column warnings remain flagged and preclude calling affected outcomes unqualified clean proofs. A missing guard counter is unknown, not zero; upstream does not expose the Kayros-specific guard. No cross-wave ranking without matched settings.',
            'groups':groups,'cells':cells}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wave',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path, default=Path(__file__).resolve().parents[1]/'references/dabia2013-n100-v011-2026-11-09')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=summarize(args.wave,args.snapshot)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    with (args.output/'groups.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(report['groups'][0])); writer.writeheader(); writer.writerows(report['groups'])
    print(json.dumps({'provisional':report['provisional'],'groups':report['groups']},indent=2))

if __name__=='__main__':
    main()
