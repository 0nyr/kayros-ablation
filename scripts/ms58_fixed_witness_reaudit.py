"""Read-only final 20-cell pilot re-audit with original frozen feasible ceilings.

--panel is the completed fixed-witness-v1 local copy. --source-wave names
the original main wave, --external-ceilings the independently checked original
n100 reference artifact, and --output is a new analysis JSON. No live/frozen
row, solver treatment or timing population is changed. No solver is invoked.
"""
import argparse
from collections import Counter
import copy
import json
from pathlib import Path

import ms58_fixed_witness as fixed
from checker_contract import sha256
from ms58_proof_consistency import apply_feasible_consistency
from probe_runner import digest, load_completed


def pooled_external_audit(rows, external):
    audited = copy.deepcopy(rows)
    supplements = []
    for reference in external:
        matching = [r for r in audited if r['instance_key'] == reference['instance_key']]
        if not matching or any(r['instance_sha256'] != reference['instance_sha256']
                               or r['atf_sha256'] != reference['atf_sha256'] for r in matching):
            raise ValueError('External feasible witness belongs to another panel input')
        base = matching[0]
        supplements.append({'task_id': 'external-frozen-n100/' + reference['instance_name'],
            'run_id': None, 'instance_key': base['instance_key'], 'instance_sha256': base['instance_sha256'],
            'atf_sha256': base['atf_sha256'], 'reference_cost': base['reference_cost'],
            'reference_manifest_sha256': base['reference_manifest_sha256'], 'horizon_s': base['horizon_s'],
            'witness_evidence_audited': True, 'checked_candidates': [{'sequence': 0, 'elapsed_s': 0.,
                'checked_cost': reference['checked_external_witness']['checked_cost'],
                'routes_sha256': reference['checked_external_witness']['routes_sha256'],
                'external_source_solution_sha256': reference['source_solution_sha256']}],
            'reported_closure_s': None, 'capped_reported_closure_s': base['horizon_s']})
    ceilings = apply_feasible_consistency([*audited, *supplements])
    for ceiling in ceilings.values():
        if str(ceiling.get('task_id', '')).startswith('external-frozen-n100/'):
            ceiling['source'] = 'external_frozen_checked_reference'
    return audited, ceilings


def verify_expected(task, spec, manifest, expected, panel):
    wanted = {**task, 'worker_python_sha256': spec['worker_python_sha256'],
        'worker_sha256': sha256(panel / 'frozen-scripts/ms58_fixed_witness_worker.py'),
        'manifest_sha256': spec['sha256']}
    if (expected['parameters'] != wanted or expected['manifest_hash'] != digest(manifest)
            or expected['horizon_s'] != 660. or expected['schema_version'] != 1):
        raise ValueError('Expected identity differs from the frozen task, manifest or worker')
    command = expected['command']
    if command[0] != spec['worker_python'] or Path(command[1]).name != 'ms58_fixed_witness_worker.py':
        raise ValueError('Expected worker interpreter or entrypoint differs')
    for flag, value in [('--instance', task['instance_path']), ('--witness', task['witness_path']),
            ('--witness-sha256', task['witness_sha256']), ('--arm', task['arm_id']),
            ('--runtime-manifest', spec['path']), ('--runtime-manifest-sha256', spec['sha256'])]:
        if command.count(flag) != 1 or command[command.index(flag) + 1] != value:
            raise ValueError('Expected worker command differs from the frozen task')
    if '--exact-seconds' in command and command[command.index('--exact-seconds') + 1] != '600':
        raise ValueError('The 20-cell pilot must retain its 600 s exact-stage treatment')
    if command[-2] != '--native-result':
        raise ValueError('Expected native artifact argument is absent')
    common = {k: v for k, v in expected.items() if k != 'command'}
    native_hash = digest({**common, 'command': command[:-2]})
    if Path(command[-1]) != Path(expected['cwd']) / 'cells/native' / f'{native_hash}.json':
        raise ValueError('Expected native artifact identity differs')


def main(args):
    panel, source = args.panel.resolve(), args.source_wave.resolve()
    plan = json.loads((panel / 'plan.json').read_bytes())
    launch = json.loads((panel / 'launch.json').read_bytes())
    complete = json.loads((panel / 'complete.json').read_bytes())
    if (plan['scheduled_count'] != 20 or len(launch['scheduled_cells']) != 20
            or complete['completed_row_count'] != 20 or launch['plan_sha256'] != sha256(panel / 'plan.json')):
        raise ValueError('A completed frozen 20-cell population is required')
    if (complete.get('plan_sha256') != sha256(panel / 'plan.json')
            or complete.get('launch_sha256') != sha256(panel / 'launch.json')
            or {(t['instance_name'], t['arm_id']) for t in launch['scheduled_cells']}
                != {(name, arm) for name in fixed.INSTANCES for arm in fixed.ARMS}):
        raise ValueError('Completed source plan, launch or 20-cell population differs')
    for name, value in complete['row_sha256'].items():
        fixed.checked_file(panel / 'rows' / name, value)
    for relative, value in plan['frozen_files_sha256'].items():
        fixed.checked_file(panel / relative, value)
    design = json.loads((panel / 'design.json').read_bytes())
    external = json.loads(args.external_ceilings.read_bytes())
    if external['source_wave_sha256'] != sha256(source / 'wave.json') or design['source_wave']['sha256'] != external['source_wave_sha256']:
        raise ValueError('Original reference evidence is bound to another main wave')
    references = []
    for record in external['rows']:
        name = record['instance_name']
        assets = source / 'frozen-references/n100'
        solution = json.loads(fixed.checked_file(assets / f'{name}-solution.json', record['source_solution_sha256']).read_bytes())
        checked_file = json.loads(fixed.checked_file(assets / f'{name}-check.json', record['source_check_sha256']).read_bytes())
        fixed.checked_file(source / 'frozen-references/n100.json', record['source_reference_manifest_sha256'])
        if checked_file.get('status') != 'valid' or checked_file.get('routing_cost') != record['frozen_reference_cost']:
            raise ValueError('Original frozen reference lacks matching stored checker evidence')
        instance = panel / 'frozen-inputs/benchmarks/TDVRPTW/Dabia2013/n=100' / f'{name}.vrp.json'
        if sha256(instance) != record['instance_sha256'] or solution['routes'] != record['checked_external_witness']['routes']:
            raise ValueError('External original reference routes or input bytes differ')
        rechecked, _ = fixed.checked_witness(instance, solution['routes'], record['frozen_reference_cost'])
        references.append(dict(record, checked_external_witness=rechecked))
    if {r['instance_name'] for r in references} != set(fixed.INSTANCES):
        raise ValueError('All 4 original n100 external references are required')
    rows = []
    original_closures = {}
    for task in launch['scheduled_cells']:
        row_path = panel / 'rows' / f"{task['task_id']}.json"
        original = json.loads(row_path.read_bytes())
        if any(original.get(k) != v for k, v in task.items()):
            raise ValueError('Stored controller row differs from the launched task')
        original_closures[task['task_id']] = original.get('reported_closure_s')
        row = dict(task, audit_ok=False, finished_task=True, raw_result_status=original.get('raw_result_status'),
            reported_closure_s=None, capped_reported_closure_s=task['horizon_s'],
            original_controller_row=original, native_work={'available': False})
        try:
            spec = plan['runtimes'][task['runtime_key']]
            manifest = json.loads(fixed.checked_file(panel / 'manifests' / Path(spec['path']).name, spec['sha256']).read_bytes())
            expected = json.loads((panel / 'rows' / f"{task['task_id']}-expected-identity.json").read_bytes())
            verify_expected(task, spec, manifest, expected, panel)
            run_id = digest(expected)
            if original.get('run_id') not in (None, run_id):
                raise ValueError('Controller run identity differs from the frozen expected identity')
            result = load_completed(panel / 'cells' / run_id, expected_run_id=run_id)
            witness = panel / 'witnesses' / Path(task['witness_path']).name
            fixed.checked_file(witness, task['witness_sha256'])
            local = dict(task, witness_path=str(witness))
            row.update(fixed.audit(local, panel, result, expected))
            if original.get('audit_ok') is not True:
                row.update(audit_ok=False, reported_closure_s=None, capped_reported_closure_s=task['horizon_s'],
                           retained_controller_failure=True)
        except (OSError, ValueError, KeyError, TypeError) as error:
            row['reaudit_error'] = f'{type(error).__name__}: {error}'
        rows.append(row)
    audited, ceilings = pooled_external_audit(rows, references)
    report = {'schema_version': 1, 'qualification': 'Separate retrospective correctness audit. The original20-cell treatment, timing population and live/frozen rows remain unchanged. External references add necessary feasible ceilings only. No independent proof certification.',
        'scheduled_count': 20, 'audited_row_count': len(audited), 'external_reference_count': len(references),
        'audit_failure_count': sum(not r['audit_ok'] for r in audited),
        'consistency_status_counts': dict(Counter(r['consistency_audit']['status'] for r in audited)),
        'reported_closures': sum(r['reported_closure_s'] is not None for r in audited),
        'closure_changed_task_ids': [r['task_id'] for r in audited if r['reported_closure_s'] != original_closures[r['task_id']]],
        'feasible_ceilings': ceilings, 'external_references': references, 'rows': audited,
        'sources': {'plan_sha256': sha256(panel / 'plan.json'), 'launch_sha256': sha256(panel / 'launch.json'),
            'original_complete_sha256': sha256(panel / 'complete.json'), 'source_main_wave_sha256': sha256(source / 'wave.json'),
            'external_ceiling_artifact_sha256': sha256(args.external_ceilings),
            'reaudit_helper_sha256': sha256(Path(__file__)),
            'shared_proof_helper_sha256': sha256(Path(__file__).with_name('ms58_proof_consistency.py'))}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: report[k] for k in ['scheduled_count', 'audit_failure_count', 'consistency_status_counts', 'reported_closures', 'closure_changed_task_ids']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', type=Path, required=True)
    parser.add_argument('--source-wave', type=Path, required=True)
    parser.add_argument('--external-ceilings', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args())
