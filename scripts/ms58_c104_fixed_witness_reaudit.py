"""Independent read-only audit of the outcome-selected 15-cell C104 case.

verify-plan checks the saved allocation, treatment, references and all expected
identities before completion. final requires complete.json, retains all15
outcomes, reruns candidate/proof checks and writes new JSON/Markdown only.
No solver, remote action or frozen/live row mutation is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics

import ms58_fixed_witness as fixed
from ms58_c104_fixed_witness import COUNTS, SCOPE, allocation_hosts, validate_snapshots
from checker_contract import sha256
from ms58_proof_consistency import apply_feasible_consistency, proof_evidence
from probe_runner import digest, load_completed

TREATMENT_ROUTES_SHA = 'f4a851bf2682977d4fda6383bdf0e77a6f962d1dd07d4473f4d923d2b5c84281'
SOURCE_RUN = '140ee8911177c4d3234b1b81dd5498bbdba22d2255a5b381b564f4372da359f1'


def verify_expected(task, spec, manifest, expected, panel):
    wanted = {**task, 'worker_python_sha256': spec['worker_python_sha256'],
        'worker_sha256': sha256(panel / 'frozen-scripts/ms58_fixed_witness_worker.py'),
        'manifest_sha256': spec['sha256']}
    if (expected['parameters'] != wanted or expected['schema_version'] != 1
            or expected['manifest_hash'] != digest(manifest) or expected['horizon_s'] != 3660.):
        raise ValueError('Expected C104 identity differs from its frozen task/worker/manifest/process clock')
    command = expected['command']
    original_root = Path(task['witness_path']).parent.parent
    if (command[0] != spec['worker_python'] or expected['cwd'] != str(original_root)
            or Path(command[1]) != original_root / 'frozen-scripts/ms58_fixed_witness_worker.py'):
        raise ValueError('Expected worker interpreter or entrypoint differs')
    for flag, value in [('--instance', task['instance_path']), ('--witness', task['witness_path']),
            ('--witness-sha256', task['witness_sha256']), ('--arm', task['arm_id']),
            ('--runtime-manifest', spec['path']), ('--runtime-manifest-sha256', spec['sha256']),
            ('--exact-seconds', '3600')]:
        if command.count(flag) != 1 or command[command.index(flag) + 1] != value:
            raise ValueError('Expected C104 command changes its treatment or 3600 s exact stage')
    if command[-2] != '--native-result':
        raise ValueError('Expected native artifact argument is absent')
    common = {k: v for k, v in expected.items() if k != 'command'}
    native_hash = digest({**common, 'command': command[:-2]})
    if Path(command[-1]) != Path(expected['cwd']) / 'cells/native' / f'{native_hash}.json':
        raise ValueError('Expected native artifact identity differs')


def verify_context(panel, source):
    plan = json.loads((panel / 'plan.json').read_bytes())
    launch = json.loads((panel / 'launch.json').read_bytes())
    allocation = json.loads((panel / 'allocation.json').read_bytes())
    if (plan['scheduled_count'] != 15 or len(plan['scheduled_cells']) != 15 or len(launch['scheduled_cells']) != 15
            or plan['physical_slot_counts'] != list(COUNTS) or plan['checker'] != fixed.EXPECTED_CHECKER
            or plan['exact_stage_s'] != 3600. or plan['process_cap_s'] != 3660. or plan['launcher_cap_s'] != 3680.
            or launch['plan_sha256'] != sha256(panel / 'plan.json')
            or launch['allocation_sha256'] != sha256(panel / 'allocation.json')
            or allocation['plan_sha256'] != launch['plan_sha256'] or allocation.get('all_checks_passed') is not True
            or allocation['job_id'] != launch['job_id'] or allocation['hosts'] != launch['hosts']
            or allocation['cohort_sha256'] != launch['cohort_sha256']
            or allocation_hosts(allocation['actual_nodefile']) != allocation['hosts']):
        raise ValueError('C104 population, clocks, plan or allocation binding differs')
    for relative, value in plan['frozen_files_sha256'].items():
        fixed.checked_file(panel / relative, value)
    snapshots = [json.loads(fixed.checked_file(panel / 'allocation-snapshots' / name, value).read_bytes())
                 for name, value in allocation['snapshots_sha256'].items()]
    slots = validate_snapshots(snapshots, allocation['hosts'], allocation['job_id'], allocation['plan_sha256'])
    if slots != allocation['slots'] or snapshots[0]['cohort_sha256'] != allocation['cohort_sha256']:
        raise ValueError('C104 physical slot or homogeneous cohort differs')
    slots_by_role = {(s['host_role'], s['slot_ordinal']): s for s in slots}
    planned = {t['task_id']: t for t in plan['scheduled_cells']}
    tasks = launch['scheduled_cells']
    if ({(t['arm_id'], t['replicate']) for t in tasks} != {(a, r) for a in fixed.ARMS for r in (1, 2, 3)}
            or len({t['task_id'] for t in tasks}) != 15 or len({(t['host'], t['cpu_core']) for t in tasks}) != 15):
        raise ValueError('C104 arm/repetition population or slot isolation differs')
    for task in tasks:
        role = (fixed.ARMS.index(task['arm_id']) + task['replicate'] - 1) % 4
        slot = slots_by_role[(task['host_role'], task['slot_ordinal'])]
        key = fixed.RUNTIMES[task['arm_id']]
        if (task['host_role'] != role or {k: v for k, v in task.items() if k not in ('host', 'cpu_core')} != planned[task['task_id']]
                or task['host'] != slot['host'] or task['cpu_core'] != slot['cpu_core']
                or task['instance_name'] != 'C104' or task['num_customers'] != 100
                or task['horizon_s'] != 3600. or task['process_horizon_s'] != 3660. or task['memory_mb'] != 16384
                or task['runtime_key'] != key or task['source_id'] != fixed.SOURCES[key]
                or task['warm'] is not (task['arm_id'] in fixed.ARMS[:3])):
            raise ValueError('C104 task source, treatment, budget or placement differs')
    witness_path = panel / 'witnesses/C104.json'
    witness = json.loads(witness_path.read_bytes())
    instance = panel / 'frozen-inputs/benchmarks/TDVRPTW/Dabia2013/n=100/C104.vrp.json'
    treatment, _ = fixed.checked_witness(instance, witness['routes'], witness['checked_cost'])
    raw_instance = json.loads(instance.read_bytes())
    atf_sha = sha256(instance.parent / raw_instance['td']['atf_path'])
    if any(t['instance_sha256'] != sha256(instance) or t['atf_sha256'] != atf_sha for t in tasks):
        raise ValueError('Scheduled C104 input bytes differ from the independently loaded model')
    if (treatment['routes_sha256'] != TREATMENT_ROUTES_SHA or len(witness['routes']) != 11
            or witness['checked_cost'] != 97829.81555195048
            or (witness['source_seed'], witness['source_repetition'], witness['source_prefix_s']) != (456, 3, 15)
            or witness['source_run_id'] != SOURCE_RUN or witness.get('outcome_selected_mechanism_case') is not True
            or any(t['witness_sha256'] != sha256(witness_path) for t in tasks)):
        raise ValueError('Outcome-selected original 11-route treatment differs')
    design = json.loads((panel / 'design.json').read_bytes())
    if design['source_wave']['sha256'] != sha256(source / 'wave.json'):
        raise ValueError('Original witness source wave differs')
    for name, value in witness['source_artifacts_sha256'].items():
        fixed.checked_file(source / name, value)
    source_cell = source / 'cells' / SOURCE_RUN
    source_result = load_completed(source_cell, expected_run_id=SOURCE_RUN)
    source_events = [json.loads(line) for line in (source_cell / 'events.jsonl').read_text().splitlines()]
    proof_evidence(source_result['validation'], source_events, 3600., expected_inputs=tasks[0])
    source_native = json.loads((source_cell / source_result['validation']['native_result']['relative_path']).read_bytes())
    if source_native['handoff_witness']['routes'] != witness['routes']:
        raise ValueError('Frozen C104 treatment is not the original source handoff')
    references = []
    for description in tasks[0]['external_feasible_references']:
        path = panel / 'witnesses' / Path(description['path']).name
        reference = json.loads(fixed.checked_file(path, description['sha256']).read_bytes())
        checked, _ = fixed.checked_witness(instance, reference['routes'], description['checked_cost'])
        if reference['instance_sha256'] != tasks[0]['instance_sha256'] or reference['atf_sha256'] != tasks[0]['atf_sha256']:
            raise ValueError('External C104 feasible witness belongs to another input')
        if description['kind'] == 'source_run_checked_incumbent':
            candidates = [c for c in source_result['validation']['candidates'] if c.get('valid') is True
                and c['sequence'] == reference['source_candidate_sequence'] and c['routes'] == reference['routes']
                and c['checked_cost'] == checked['checked_cost']]
            if not candidates or reference['source_run_id'] != SOURCE_RUN:
                raise ValueError('Source-run external feasible witness is not bound to its checked event')
        elif description['kind'] == 'original_frozen_n100_reference':
            assets = source / 'frozen-references/n100'
            solution = json.loads(fixed.checked_file(assets / 'C104-solution.json', reference['source_solution_sha256']).read_bytes())
            stored = json.loads(fixed.checked_file(assets / 'C104-check.json', reference['source_check_sha256']).read_bytes())
            fixed.checked_file(source / 'frozen-references/n100.json', reference['source_reference_manifest_sha256'])
            if solution['routes'] != reference['routes'] or stored.get('status') != 'valid' or stored.get('routing_cost') != checked['checked_cost']:
                raise ValueError('Original frozen n100 feasible witness differs')
        else:
            raise ValueError('Unexpected C104 external reference kind')
        references.append({'kind': description['kind'], 'path': str(path), 'sha256': sha256(path), **checked})
    if {r['kind'] for r in references} != {'source_run_checked_incumbent', 'original_frozen_n100_reference'}:
        raise ValueError('Both external C104 feasible ceilings are required')
    for task in tasks:
        if (task['external_feasible_references'] != tasks[0]['external_feasible_references']
                or task['reference_cost'] != min(r['checked_cost'] for r in references)
                or not any(r['sha256'] == task['reference_manifest_sha256']
                    and r['checked_cost'] == task['reference_cost']
                    and Path(r['path']).name == Path(task['proof_reference_path']).name for r in references)):
            raise ValueError('C104 external reference population differs across arms')
    expected_records = {}
    for task in tasks:
        spec = plan['runtimes'][task['runtime_key']]
        manifest_path = panel / 'manifests' / Path(spec['path']).name
        manifest = json.loads(fixed.checked_file(manifest_path, spec['sha256']).read_bytes())
        key = task['runtime_key']
        gate_path = panel / 'gates' / f'{key}.json'
        fixed.checked_file(gate_path, design['manifests'][key]['gate']['sha256'])
        gate = json.loads(gate_path.read_bytes())
        if (manifest.get('checker') != fixed.EXPECTED_CHECKER or manifest.get('backend', '').lower() != 'cplex'
                or not fixed.source_matches(manifest.get('source_id'), task['source_id'])
                or manifest.get('declared_source_commit') != task['source_id']
                or manifest.get('parent_manifest_sha256') != design['manifests'][key]['sha256']
                or gate.get('source_id') != task['source_id'] or gate.get('all_checks_passed') is not True):
            raise ValueError('C104 runtime/source/checker/native gate binding differs')
        if key == 'lera':
            binary = manifest['worker_environment']['UPSTREAM_PROBE_BINARY']
            if (manifest['runtime_files'].get(binary) != fixed.LERA_BINARY
                    or fixed.LERA_PATCH not in manifest['source_patch_files'].values()):
                raise ValueError('Fixed minimal f2 binary or correction patch differs')
        expected_path = panel / 'rows' / f"{task['task_id']}-expected-identity.json"
        try:
            expected = json.loads(expected_path.read_bytes())
            verify_expected(task, spec, manifest, expected, panel)
            task_file = json.loads((panel / 'rows' / f"{task['task_id']}-task.json").read_bytes())
            if task_file != task:
                raise ValueError('Saved C104 launcher task differs')
            expected_records[task['task_id']] = {'identity': expected, 'sha256': sha256(expected_path)}
        except (OSError, ValueError, KeyError, TypeError) as error:
            expected_records[task['task_id']] = {'error': f'{type(error).__name__}: {error}'}
    return {'plan': plan, 'launch': launch, 'allocation': allocation, 'treatment': treatment,
            'references': references, 'expected_records': expected_records}


def pool_references(rows, references):
    audited = copy.deepcopy(rows)
    base = audited[0]
    external = [{k: base[k] for k in ['instance_key', 'instance_sha256', 'atf_sha256', 'reference_cost',
                                    'reference_manifest_sha256', 'horizon_s']} |
        {'task_id': 'external-C104/' + ref['kind'], 'run_id': None, 'witness_evidence_audited': True,
         'checked_candidates': [{'sequence': 0, 'elapsed_s': 0., 'checked_cost': ref['checked_cost'],
             'routes_sha256': ref['routes_sha256'], 'external_evidence_sha256': ref['sha256']}],
         'reported_closure_s': None, 'capped_reported_closure_s': 3600.} for ref in references]
    ceilings = apply_feasible_consistency([*audited, *external])
    return audited, ceilings


def outcome(row):
    if not row.get('audit_ok'):
        return 'audit_failure'
    if row.get('resource_failure_kind'):
        return row['resource_failure_kind']
    status = row.get('native_work', {}).get('status')
    if status in ('MemoryLimitReached', 'TimeLimitReached'):
        return 'native_' + status
    return row['raw_result_status']


def printed_progress(path):
    table, cuts = [], []
    for line in path.read_text(errors='replace').splitlines():
        fields = line.split()
        if len(fields) == 4:
            try:
                table.append({'native_printed_time_s': float(fields[0]), 'printed_iteration': int(fields[1]),
                              'rounded_restricted_master_value': float(fields[2]), 'master_variables': int(fields[3])})
            except ValueError:
                pass
        match = re.fullmatch(r'\s*Cuts:\s*([0-9]+)\s*', line)
        if match:
            cuts.append({'after_printed_row': len(table) - 1, 'cuts': int(match.group(1))})
    return {'rows': table, 'cuts': cuts,
        'meaning': 'Coarse streamed restricted-master progress. Rounded values are not certified global lower bounds. These rows do not replace missing native totals.'}


def groups(rows):
    result = []
    for arm in fixed.ARMS:
        trials = sorted([r for r in rows if r['arm_id'] == arm], key=lambda r: r['replicate'])
        if len(trials) != 3:
            raise ValueError('Every C104 arm must retain its 3 scheduled repetitions')
        available = [r for r in trials if r.get('native_work', {}).get('available')]
        def all_mean(get):
            return statistics.mean(get(r) for r in trials) if len(available) == 3 and all(get(r) is not None for r in trials) else None
        result.append({'arm_id': arm, 'scheduled_trials': 3,
            'reported_closures': sum(r['reported_closure_s'] is not None for r in trials),
            'mean_capped_reported_closure_s': statistics.mean(r['capped_reported_closure_s'] for r in trials),
            'closure_times_s': [r['reported_closure_s'] for r in trials],
            'status_counts': dict(Counter(r['raw_result_status'] for r in trials)),
            'outcome_counts': dict(Counter(outcome(r) for r in trials)),
            'time_censored_trials': sum(outcome(r) in ('deadline', 'native_TimeLimitReached') for r in trials),
            'resource_failure_trials': sum(bool(r.get('resource_failure_kind'))
                or r.get('native_work', {}).get('status') == 'MemoryLimitReached' for r in trials),
            'missing_checked_incumbent_trials': sum(r.get('final_checked_cost') is None for r in trials),
            'audit_failures': sum(not r['audit_ok'] for r in trials),
            'native_work_available_trials': len(available), 'native_work_mean_time_s': all_mean(lambda r: r['native_work']['whole_run'].get('time')),
            'whole_lp_mean_time_s': all_mean(lambda r: r['native_work']['whole_run'].get('lp_time')),
            'whole_pricing_mean_time_s': all_mean(lambda r: r['native_work']['whole_run'].get('pricing_time')),
            'whole_branching_mean_time_s': all_mean(lambda r: r['native_work']['whole_run'].get('branching_time')),
            'root_labeling_mean_time_s': all_mean(lambda r: r['native_work'].get('root_labeling_time_s')),
            'root_cg_iterations': [r.get('native_work', {}).get('root_cg', {}).get('iteration_count') for r in trials],
            'root_columns_added': [r.get('native_work', {}).get('root_cg', {}).get('columns_added') for r in trials],
            'nodes_closed': [r.get('native_work', {}).get('whole_run', {}).get('nodes_closed') for r in trials],
            'root_work_signatures': [r.get('native_work', {}).get('root_work_signature') for r in trials],
            'whole_work_signatures': [r.get('native_work', {}).get('whole_work_signature') for r in trials]})
    return result


def final_audit(panel, source, context):
    plan, launch = context['plan'], context['launch']
    complete = json.loads((panel / 'complete.json').read_bytes())
    if (complete['scheduled_count'] != 15 or complete['completed_row_count'] != 15
            or complete['plan_sha256'] != sha256(panel / 'plan.json')
            or complete['launch_sha256'] != sha256(panel / 'launch.json')
            or complete['allocation_sha256'] != sha256(panel / 'allocation.json')
            or complete['cohort_sha256'] != launch['cohort_sha256']):
        raise ValueError('A complete and bound 15-cell C104 source population is required')
    for name, value in complete['row_sha256'].items():
        fixed.checked_file(panel / 'rows' / name, value)
    rows, changed = [], []
    for task in launch['scheduled_cells']:
        original = json.loads((panel / 'rows' / f"{task['task_id']}.json").read_bytes())
        if any(original.get(k) != v for k, v in task.items()):
            raise ValueError('Original C104 controller row differs from its launched task')
        row = dict(task, original_controller_row=original, audit_ok=False, finished_task=True,
            raw_result_status=original.get('raw_result_status'), reported_closure_s=None,
            capped_reported_closure_s=3600., native_work={'available': False})
        record = context['expected_records'][task['task_id']]
        try:
            if 'error' in record:
                raise ValueError(record['error'])
            expected = record['identity']
            run_id = digest(expected)
            if original.get('run_id') not in (None, run_id):
                raise ValueError('Original controller run identity differs')
            result = load_completed(panel / 'cells' / run_id, expected_run_id=run_id)
            cell = panel / 'cells' / run_id
            events = [json.loads(line) for line in (cell / 'events.jsonl').read_text().splitlines()]
            starts = [e for e in events if e.get('phase') == 'exact_stage_start']
            if len(starts) != 1 or starts[0].get('exact_budget_s') != 3600.:
                raise ValueError('C104 event clock does not declare the 3600 s exact stage')
            local = dict(task, witness_path=str(panel / 'witnesses/C104.json'))
            row.update(fixed.audit(local, panel, result, expected))
            row['sources']['stderr.log'] = sha256(cell / 'stderr.log')
            row['printed_cg_progress'] = printed_progress(cell / 'stderr.log')
            row['final_checked_cost'] = min((c['checked_cost'] for c in row['checked_candidates']), default=None)
            if row['native_work'].get('status') == 'MemoryLimitReached' and not row.get('resource_failure_kind'):
                row.update(resource_failure_kind='native_memory_limit_reported_in_sidecar',
                    reported_closure_s=None, capped_reported_closure_s=3600., observation_usable=False)
            if original.get('audit_ok') is not True:
                row.update(audit_ok=False, reported_closure_s=None, capped_reported_closure_s=3600.,
                           retained_controller_failure=True)
        except (OSError, ValueError, KeyError, TypeError) as error:
            row['reaudit_error'] = f'{type(error).__name__}: {error}'
        rows.append(row)
    audited, ceilings = pool_references(rows, context['references'])
    for row in audited:
        original = row['original_controller_row']
        if row['reported_closure_s'] != original.get('reported_closure_s'):
            changed.append(row['task_id'])
    return {'rows': audited, 'groups': groups(audited), 'feasible_ceilings': ceilings,
            'audit_failure_count': sum(not r['audit_ok'] for r in audited),
            'consistency_status_counts': dict(Counter(r['consistency_audit']['status'] for r in audited)),
            'reported_closures': sum(r['reported_closure_s'] is not None for r in audited),
            'closure_changed_task_ids': changed, 'original_complete_sha256': sha256(panel / 'complete.json')}


def render(report, json_sha):
    labels = {'current-warm': 'Current warm 9d', 'ub-only': 'UB-only 74ed', 'dedup-only': 'Deduplicated 3919',
              'current-cold': 'Current cold 9d', 'lera-minimal': 'Fixed Lera f2'}
    lines = ['# Outcome-selected C104 fixed-witness result', '', SCOPE, '',
        'The selected treatment remains the original 11-route Full 15 s, seed 456, repetition 3 handoff with checked cost 97829.81555195048. Both the source run’s better checked incumbent and the original frozen n100 reference remain external feasible ceilings. They are never passed as replacement handoffs.', '',
        f"The separate final audit retains all 15 scheduled observations. It reports {report['audit_failure_count']} artifact/settings audit failures and the consistency counts {report['consistency_status_counts']}. Reported closure after necessary consistency checks is not independent proof certification.", '',
        'The exact-stage clock starts after checking/loading/payload preparation and has a 3600 s cap. The independent process cap is 3660 s. Every unclosed cell, including failures, contributes 3600 s to its arm’s capped mean. Native-work means remain unavailable when any repetition lacks a completed native sidecar.', '',
        '| Arm | Reported closures | Capped mean (s) | Native work available | Outcome counts |',
        '| --- | ---: | ---: | ---: | --- |']
    for group in report['groups']:
        lines.append(f"| {labels[group['arm_id']]} | {group['reported_closures']}/3 | {group['mean_capped_reported_closure_s']:.3f} | {group['native_work_available_trials']}/3 | {group['outcome_counts']} |")
    lines += ['', '| Arm | Rep | Host/core | Outcome | Closure (s) | Native time (s) | Root CG iterations | Root columns | Closed nodes |',
              '| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |']
    def number(value):
        return 'unavailable' if value is None else f'{value:.3f}' if isinstance(value, float) else str(value)
    for arm in fixed.ARMS:
        for row in sorted([r for r in report['rows'] if r['arm_id'] == arm], key=lambda r: r['replicate']):
            work = row.get('native_work', {})
            native, root = work.get('whole_run', {}), work.get('root_cg', {})
            lines.append(f"| {labels[arm]} | {row['replicate']} | {row['host'].split('.')[0]}/{row['cpu_core']} | {outcome(row)} | "
                + ' | '.join(number(v) for v in [row['reported_closure_s'], native.get('time'), root.get('iteration_count'), root.get('columns_added'), native.get('nodes_closed')]) + ' |')
    lines += ['', '| Arm | Native mean (s) | LP mean (s) | Pricing mean (s) | Branching mean (s) | Root labeling mean (s) |',
              '| --- | ---: | ---: | ---: | ---: | ---: |']
    for group in report['groups']:
        lines.append('| ' + labels[group['arm_id']] + ' | ' + ' | '.join(number(group[k]) for k in
            ['native_work_mean_time_s', 'whole_lp_mean_time_s', 'whole_pricing_mean_time_s',
             'whole_branching_mean_time_s', 'root_labeling_mean_time_s']) + ' |')
    lines += ['', 'Native timing categories are retained independently and are not assumed to partition total time. A native-work mean requires all 3 sidecars. Missing counters remain unavailable, and deadline scores remain in the capped means. The JSON separately preserves streamed CG tables when final totals are unavailable. Their rounded restricted-master values are not certified global lower bounds.', '',
        'These measurements belong only to this new homogeneous Gros allocation cohort. The 15 isolated slots are distributed 4/4/4/3 across 4 hosts. Each arm spans 3 hosts, and each repetition spans all 4. The 3-lane host has a different concurrent load from the 4-lane hosts, which qualifies small timing differences. No source main-panel, short-pilot or EPS timing curve is merged into this report.', '',
        'This is one outcome-selected instance with one fixed witness and 3 repetitions per arm. Results can describe whether a handoff component retains the benefit on this case. They do not estimate unbiased general performance, establish pricing completeness or support a global policy decision by themselves.', '',
        f"Audit JSON SHA256 is `{json_sha}`. It retains the source/gate/allocation/expected-identity hashes, both independently rechecked external feasible witnesses, raw claims, candidate checks, late consistency evidence and every native-work availability limit."]
    return '\n'.join(lines) + '\n'


def main(args):
    panel, source = args.panel.resolve(), args.source_wave.resolve()
    context = verify_context(panel, source)
    report = {'schema_version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(), 'qualification': SCOPE,
        'mode': args.mode, 'scheduled_count': 15, 'expected_identity_count': len(context['expected_records']),
        'expected_identity_failure_count': sum('error' in r for r in context['expected_records'].values()),
        'treatment': context['treatment'], 'external_references': context['references'],
        'job_id': context['launch']['job_id'], 'hosts': context['launch']['hosts'],
        'cohort_sha256': context['allocation']['cohort_sha256'], 'physical_slot_counts': list(COUNTS),
        'sources': {'plan_sha256': sha256(panel / 'plan.json'), 'launch_sha256': sha256(panel / 'launch.json'),
            'allocation_sha256': sha256(panel / 'allocation.json'), 'source_main_wave_sha256': sha256(source / 'wave.json'),
            'treatment_sha256': sha256(panel / 'witnesses/C104.json'),
            'reaudit_helper_sha256': sha256(Path(__file__)),
            'shared_proof_helper_sha256': sha256(Path(__file__).with_name('ms58_proof_consistency.py'))},
        'expected_identity_records': context['expected_records']}
    report['runtime_source_identities'] = {key: {'source_id': fixed.SOURCES[key],
        'manifest_sha256': spec['sha256'], 'gate_sha256': sha256(panel / 'gates' / f'{key}.json'),
        'worker_python_sha256': spec['worker_python_sha256']}
        for key, spec in context['plan']['runtimes'].items()}
    if args.mode == 'final':
        report.update(final_audit(panel, source, context))
    destination = args.output_prefix.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.with_suffix('.json').open('x') as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    if args.mode == 'final':
        with destination.with_suffix('.md').open('x') as stream:
            stream.write(render(report, sha256(destination.with_suffix('.json'))))
    print(json.dumps({k: report[k] for k in ['mode', 'scheduled_count', 'expected_identity_failure_count']
                     + (['audit_failure_count', 'consistency_status_counts', 'reported_closures', 'closure_changed_task_ids'] if args.mode == 'final' else [])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['verify-plan', 'final'], default='final')
    parser.add_argument('--panel', type=Path, required=True)
    parser.add_argument('--source-wave', type=Path, required=True)
    parser.add_argument('--output-prefix', type=Path, required=True)
    main(parser.parse_args())
