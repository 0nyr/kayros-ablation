"""Read-only recovery audit. Never starts, closes, replaces or retries a task.

Stored checker evidence is checked for internal consistency. This is not a
fresh checker replay or a new solver-proof acceptance decision. No audit timing
measurements are collected. An external frozen wave.json digest is required.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

from probe_runner import digest, load_completed

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from pilot_summary import assert_scheduled_row, assert_task_result_identity


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def signature(task):
    return (task['instance_name'], task['arm'], task.get('prefix_s'), task['replicate'],
            task.get('seed'), task['horizon_s'], task.get('cpu_core'))


def identity_signature(identity):
    params = identity['parameters']
    cores = params.get('cpu_affinity', [])
    return (params['instance_name'], params['arm'], params.get('prefix_seconds'), params['repetition'],
            params.get('search_seed'), identity['horizon_s'], cores[0] if len(cores) == 1 else None)


def assert_envelope(events, result, checker):
    """Reconstruct the checked envelope from every stored candidate assessment."""
    validation = result['validation']
    if result['status'] == 'validation_failure':
        if validation.get('checked_envelope'):
            raise ValueError('Validation failure unexpectedly contains a checked envelope')
        return 'unavailable_validation_failure'
    if validation.get('checker') != checker:
        raise ValueError('Stored checker identity mismatch')
    produced = [event for event in events if event['event'] == 'candidate']
    candidates = validation.get('candidates', [])
    if len(produced) != len(candidates):
        raise ValueError('Candidate assessment population mismatch')
    expected, best, routes = [], None, None
    for event, record in zip(produced, candidates):
        for key in ['sequence', 'elapsed_s', 'routes', 'native_cost']:
            if record.get(key) != event.get(key):
                raise ValueError(f'Candidate production/assessment mismatch: {key}')
        if type(record.get('valid')) is not bool:
            raise ValueError('Candidate validity must be explicit')
        if not record['valid']:
            if record.get('checked_cost') is not None:
                raise ValueError('Invalid candidate carries a checked cost')
            continue
        cost = record.get('checked_cost')
        evidence = record.get('checker_evidence', {})
        if (isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost)
                or cost < 0 or evidence.get('status') != 'valid' or evidence.get('routing_cost') != cost):
            raise ValueError('Valid candidate lacks consistent complete checker evidence')
        native = event.get('native_cost')
        discrepancy = native - cost if isinstance(native, (int, float)) and not isinstance(native, bool) and math.isfinite(native) else None
        if record.get('native_minus_checked') != discrepancy:
            raise ValueError('Native/checker discrepancy mismatch')
        if best is None or cost < best:
            best, routes = cost, event['routes']
            expected.append({'sequence': event['sequence'], 'elapsed_s': event['elapsed_s'],
                             'checked_cost': cost, 'routes': routes})
    if validation.get('checked_envelope') != expected:
        raise ValueError('Checked best-so-far envelope mismatch')
    if validation.get('best_checked_cost') != best or validation.get('best_routes') != routes:
        raise ValueError('Final checked incumbent mismatch')
    return 'stored_evidence_consistent'


def audit_wave(wave: Path, *, expected_wave_sha256: str, reference_manifest: Path,
               manifest_anchors: dict | None = None) -> dict:
    """Return an action-free report, preserving failed terminal observations.

    Artifact paths refer to the original execution environment. For archived
    copies, declared runtime/input paths must remain readable to audit their
    actual bytes. Native sidecars use load_completed's relocation-safe path.
    """
    wave = wave.resolve()
    if sha(wave / 'wave.json') != expected_wave_sha256:
        raise ValueError('External wave manifest digest mismatch')
    specification = json.loads((wave / 'wave.json').read_bytes())
    reference_digest = sha(reference_manifest)
    if reference_digest != specification['reference_manifest_sha256']:
        raise ValueError('Reference manifest digest mismatch')
    reference = json.loads(reference_manifest.read_bytes())
    refs = {entry['instance_name']: entry for entry in reference['instances']}
    tasks = specification['scheduled_cells']
    if len({task['task_id'] for task in tasks}) != len(tasks) or len({signature(task) for task in tasks}) != len(tasks):
        raise ValueError('Duplicate scheduled logical task')
    if specification.get('scheduled_count', len(tasks)) != len(tasks):
        raise ValueError('Scheduled population mismatch')
    if sha(wave / 'frozen-scripts/development_wave.py') != specification['driver_sha256']:
        raise ValueError('Frozen driver digest mismatch')
    allocated = set((wave / 'allocated-nodefile.txt').read_text().split())
    if any(task['host'] not in allocated for task in tasks):
        raise ValueError('Scheduled host is outside the recorded real nodefile')
    seed_slots = {slot['replicate']: slot['paired_heuristic_seed'] for slot in specification.get('repetition_slots', [])}
    manifests, manifest_errors, unanchored = {}, {}, []
    cache = {}
    def checked_file(filename, expected):
        path = Path(filename)
        if path not in cache:
            cache[path] = sha(path)
        if cache[path] != expected:
            raise ValueError(f'Artifact digest mismatch: {filename}')
    for task in tasks:
        if task.get('wave_id') != specification.get('wave_id'):
            raise ValueError('Scheduled task/wave identity mismatch')
        if task['arm'] in ['kayros-heuristics', 'kayros-full-cplex']:
            if seed_slots and task.get('seed') != seed_slots.get(task['replicate']):
                raise ValueError('Scheduled paired seed mismatch')
        elif task.get('seed') is not None:
            raise ValueError('Cold exact task carries a search seed')
        name = Path(task['manifest_path']).name
        if name in manifests:
            continue
        manifest = json.loads((wave / 'manifests' / name).read_bytes())
        manifests[name] = manifest
        try:
            anchor = task.get('manifest_hash') or (manifest_anchors or {}).get(name)
            if anchor is None:
                unanchored.append(name)
            elif anchor != digest(manifest):
                raise ValueError('External scheduled runtime manifest digest mismatch')
            if manifest.get('checker') != reference['checker']:
                raise ValueError('Runtime manifest checker mismatch')
            for key in ['wave_id', 'wave_name']:
                if specification.get(key) is not None and manifest.get(key) != specification[key]:
                    raise ValueError(f'Runtime manifest/wave mismatch: {key}')
            for filename, expected in {**manifest['runtime_files'], **manifest['source_patch_files']}.items():
                checked_file(filename, expected)
        except (OSError, ValueError, KeyError, TypeError) as error:
            manifest_errors[name] = f'{type(error).__name__}: {error}'
    rows, row_errors, explicit = {}, {}, {}
    for task in tasks:
        path = wave / 'rows' / (task['task_id'] + '.json')
        if not path.exists():
            continue
        try:
            row = json.loads(path.read_bytes())
            assert_scheduled_row(task, row)
            rows[task['task_id']] = row
            if row.get('run_id'):
                explicit[row['run_id']] = task['task_id']
        except (OSError, ValueError, KeyError, TypeError) as error:
            row_errors[task['task_id']] = f'{type(error).__name__}: {error}'
    by_signature = {signature(task): task['task_id'] for task in tasks}
    found, orphaned = defaultdict(list), []
    for cell in sorted((wave / 'cells').iterdir()) if (wave / 'cells').exists() else []:
        if not cell.is_dir() or cell.name == 'native':
            continue
        try:
            identity = json.loads((cell / 'identity.json').read_bytes())
            key = explicit.get(cell.name) or by_signature.get(identity_signature(identity))
            if key is None:
                raise ValueError('Cell is not associated with a scheduled task')
            found[key].append((cell, identity))
        except (OSError, ValueError, KeyError, TypeError) as error:
            orphaned.append({'cell': str(cell), 'reason': f'{type(error).__name__}: {error}'})
    reports = []
    for task in tasks:
        tid = task['task_id']
        item = {'task_id': tid, 'state': None, 'automatic_action': None}
        reports.append(item)
        row = rows.get(tid, {})
        candidates = found.get(tid, [])
        name = Path(task['manifest_path']).name
        if tid in row_errors or name in manifest_errors:
            item.update(state='identity_or_artifact_error', reason=row_errors.get(tid, manifest_errors.get(name)))
            continue
        if len(candidates) > 1:
            item.update(state='ambiguous_attempts', cells=[str(cell) for cell, _ in candidates])
            continue
        if not candidates:
            if row.get('status') == 'launch_failure' and not row.get('run_id'):
                item.update(state='terminal_launch_failure', reason=row.get('reason'))
            elif row.get('run_id'):
                item.update(state='identity_or_artifact_error', reason='Row points to missing cell identity')
            else:
                item.update(state='no_associated_artifact', process_state='not_checked; no claim ledger exists')
            continue
        cell, identity = candidates[0]
        try:
            core = {key: value for key, value in identity.items() if key not in ['run_id', 'manifest']}
            if identity.get('run_id') != cell.name or digest(core) != cell.name or identity.get('manifest_hash') != digest(identity['manifest']):
                raise ValueError('Cell identity digest mismatch')
            if digest(identity['manifest']) != digest(manifests[name]):
                raise ValueError('Cell/scheduled runtime manifest mismatch')
            params = identity['parameters']
            required_code = {'run_solver_probe.py', 'probe_runner.py', 'validate_probe.py', 'checker_contract.py'}
            if not required_code <= params['orchestration_files'].keys():
                raise ValueError('Missing orchestration source identities')
            for filename, expected in params['orchestration_files'].items():
                if Path(filename).name != filename:
                    raise ValueError('Invalid orchestration source path')
                checked_file(wave / 'frozen-scripts' / filename, expected)
            worker = Path(task.get('worker_path') or wave / 'frozen-scripts/kayros_worker.py')
            checked_file(worker, params['worker_sha256'])
            checked_file(task['worker_python'], params['worker_python_sha256'])
            command = [str(Path(task['worker_python']).absolute()), str(worker.resolve()),
                       '--instance', str(Path(task['instance_path']).resolve()), '--arm', task['arm'],
                       '--memory-mb', str(task['memory_mb'])]
            if task.get('seed') is not None:
                command += ['--seed', str(task['seed'])]
            if task.get('prefix_s') is not None:
                command += ['--prefix-seconds', str(float(task['prefix_s']))]
            expected_cwd = str(worker.resolve().parent.parent)
            native_name = digest({'manifest': identity['manifest'], 'parameters': params,
                'horizon_s': identity['horizon_s'], 'command': command,
                'environment_hash': identity['environment_hash'], 'cwd': expected_cwd}) + '.json'
            native = Path(task['output_directory']).resolve() / 'native' / native_name
            if identity['command'] != command + ['--native-result', str(native)] or identity['cwd'] != expected_cwd:
                raise ValueError('Scheduled worker command or working directory mismatch')
            ref = refs[task['instance_name']]
            checked_file(task['instance_path'], ref['instance_sha256'])
            raw_instance = json.loads(Path(task['instance_path']).read_bytes())
            atf = Path(task['instance_path']).parent / raw_instance['td']['atf_path']
            checked_file(atf, ref['atf_file_sha256'])
            # A stub binds even incomplete attempts to the full scheduled identity.
            stub = {'run_id': cell.name, 'identity_hash': cell.name, 'manifest_hash': identity['manifest_hash']}
            association = row or {'task_id': tid, 'run_id': cell.name,
                                 'result_path': str(Path(task['output_directory']) / cell.name / 'result.json')}
            assert_task_result_identity(task=task, row=association, identity=identity, result=stub,
                wave=specification, reference=ref, expected_reference_manifest_sha256=reference_digest,
                scheduled_manifest=manifests[name])
            if not (cell / 'result.json').exists():
                item.update(state='incomplete_claim_needs_recovery', run_id=cell.name,
                            process_state='not_checked; may still be running', prefix_events_present=(cell / 'events.jsonl').exists())
                continue
            result = load_completed(cell, expected_run_id=cell.name)
            assert_task_result_identity(task=task, row=association, identity=identity, result=result,
                wave=specification, reference=ref, expected_reference_manifest_sha256=reference_digest,
                scheduled_manifest=manifests[name])
            events = [json.loads(line) for line in (cell / 'events.jsonl').read_bytes().splitlines()]
            consistency = assert_envelope(events, result, reference['checker'])
            observed = {'status': result['status'], 'returncode': result['returncode'],
                        'event_count': result['event_count'],
                        'checked_cost': result['validation'].get('best_checked_cost'),
                        'valid_candidates': sum(candidate['valid'] for candidate in result['validation'].get('candidates', [])),
                        'proof_status': result['validation'].get('proof', {}).get('status')}
            if any(key in row and row[key] != value for key, value in observed.items()):
                raise ValueError('Summary row disagrees with its terminal result')
            item.update(state='verified_terminal' if row else 'missing_row_reconstructable', run_id=cell.name,
                        observed_status=result['status'], envelope_check=consistency,
                        result_path=str(cell / 'result.json'))
        except (OSError, ValueError, KeyError, TypeError) as error:
            item.update(state='identity_or_artifact_error', run_id=cell.name, reason=f'{type(error).__name__}: {error}')
    counts = {state: sum(item['state'] == state for item in reports) for state in sorted({item['state'] for item in reports})}
    return {'schema_version': 1, 'read_only': True, 'wave_id': specification.get('wave_id'),
            'expected_wave_sha256': expected_wave_sha256, 'scheduled': len(tasks), 'counts': counts,
            'coordinator_complete': (wave / 'coordinator-complete.json').exists(),
            'runtime_manifest_errors': manifest_errors, 'orphaned_cells': orphaned,
            'readiness_gaps': ['No per-task exclusive prelaunch attempt ledger exists in development schema v1.',
                               'Process liveness is not checked. Incomplete attempts cannot be retried automatically.'] +
                              ([f'Runtime manifests lack external per-task anchors: {sorted(set(unanchored))}'] if unanchored else []),
            'checker_assurance': 'Stored complete checker evidence and envelope consistency only; no fresh route replay or new proof acceptance.',
            'tasks': reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--expected-wave-sha256', required=True)
    parser.add_argument('--reference-manifest', type=Path, required=True)
    parser.add_argument('--manifest-anchors', type=Path)
    args = parser.parse_args()
    anchors = json.loads(args.manifest_anchors.read_bytes()) if args.manifest_anchors else None
    report = audit_wave(args.wave, expected_wave_sha256=args.expected_wave_sha256,
                        reference_manifest=args.reference_manifest, manifest_anchors=anchors)
    print(json.dumps(report, indent=2, allow_nan=False))
    if report['orphaned_cells'] or any(report['counts'].get(state, 0) for state in ['identity_or_artifact_error', 'ambiguous_attempts']):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
