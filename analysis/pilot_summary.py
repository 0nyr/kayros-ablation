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
    cells = []
    seen = set()
    for task in wave['scheduled_cells']:
        tid = task['task_id']
        if tid in seen:
            raise ValueError('Repeated scheduled task')
        seen.add(tid)
        record_path = wave_path/'rows'/f'{tid}.json'
        row = json.loads(record_path.read_text()) if record_path.exists() else {}
        result = None
        events = []
        run_id = row.get('run_id')
        if run_id:
            cell = wave_path/'cells'/run_id
            result = json.loads((cell/'result.json').read_text())
            identity = json.loads((cell/'identity.json').read_text())
            if result['run_id'] != run_id or identity['run_id'] != run_id:
                raise ValueError('Run identity mismatch')
            params = identity['parameters']
            ref = refs[task['instance_name']]
            if params['instance_sha256'] != ref['instance_sha256'] or params['atf_sha256'] != ref['atf_file_sha256']:
                raise ValueError('Input identity mismatch')
            if identity['horizon_s'] != task['horizon_s'] or params['arm'] != task['arm']:
                raise ValueError('Scheduled task mismatch')
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
        closure = next((e for e in reported if any(p['elapsed_s'] <= e['elapsed_s'] for p in envelope)), None)
        reported_time = closure['elapsed_s'] if closure and not integrity else None
        cells.append({**task, 'run_id': run_id, 'status': result['status'] if result else row.get('status','pending'),
                      'completed_record': result is not None, 'finished_task': bool(row), **score,
                      'native_closure_unverified': proof.get('status') == 'proof_unverified',
                      'accepted_proof': accepted, 'capped_proof_s': tproof,
                      'reported_closure_with_checked_witness': reported_time is not None,
                      'reported_closure_s': reported_time,
                      'capped_reported_closure_s': task['horizon_s'] if reported_time is None else reported_time,
                      'closure_diagnostics': closure.get('closure_census', {}) if closure else {},
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
                       'native_closures_unverified':sum(c['native_closure_unverified'] for c in rows),
                       'accepted_proofs':sum(c['accepted_proof'] for c in rows),
                       'reported_closures_with_checked_witness':sum(c['reported_closure_with_checked_witness'] for c in rows),
                       'restricted_mean_reported_closure_s':avg('capped_reported_closure_s') if 'heuristics' not in key else None,
                       'conditional_mean_final_raw_gap': statistics.mean(c['final_raw_gap'] for c in rows if c['final_raw_gap'] is not None) if any(c['final_raw_gap'] is not None for c in rows) else None,
                       'max_sampled_rss_kib': max((c['peak_rss_kib'] or 0 for c in rows), default=0),
                       'max_timeout_overshoot_s': max((c['timeout_overshoot_s'] or 0 for c in rows), default=0),
                       'mean_lambda':avg('lambda'), 'mean_final_squeezed_gap':avg('final_squeezed_gap'),
                       'restricted_mean_proof_s':avg('capped_proof_s') if 'heuristics' not in key else None})
    return {'wave_name':wave['wave_name'],'purpose':wave.get('purpose'), 'checker':checker,
            'reference_manifest_sha256':pins['dataset']['reference_manifest_sha256'],
            'provisional':any(not c['finished_task'] for c in cells),
            'warning':'Pending or failed cells remain in the denominator. Reported closure with checked witness is an implementation-level observation under declared conventions, not a formal certificate. Closure diagnostics remain visible. No cross-wave ranking without matched settings.',
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
