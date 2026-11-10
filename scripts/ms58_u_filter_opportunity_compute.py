"""Reserved-node U-opportunity gates and isolated adapter to the existing probe controller.

Subcommands native/manifest run synthetic gates or freeze runtime manifests.
Subcommand pilot forwards --mode prepare/launch/monitor to a private instance
of the reviewed a2dc controller. Existing helper files and module instances
are not edited. No SSH or reservation operation is performed.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_dabia_screen import immutable_json, now
from ms58_dabia_validate import run_command
from ms58_dabia_wave import checked_file, source_matches
from ms58_dominance_profile_native import allocation, source_build
from ms58_dominance_profile_prepare import installed
from ms58_paired_probe import candidate_manifest

SOURCE = '7d68859e8d6d8b8ae2382c1f7a88088709f099b1'
PARENT = 'a2dcf8870c17e7edeccb472b27bee0e544eec997'
BASE = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
SCHEMA = 'kayros-ms58-u-filter-opportunity-v1'
PWL = 'PASS native PWL bitwise cases=5040 digest=dcbfcebb1acf42ef'
NATIVE = 'PASS native U-filter scenarios=20 state_digest=bdbf2a6cabb793c3'


def patch_stack(manifest, pins, *, profile):
    expected = set(pins['baseline_patch_sha256']) | {pins['selected_full_patch_sha256']}
    if profile:
        expected |= {pins['parent_incremental_patch_sha256'], pins['incremental_patch_sha256']}
    if set(manifest['source_patch_files'].values()) != expected or len(manifest['source_patch_files']) != len(expected):
        raise ValueError('Require baseline4 + selected9d full + both diagnostic increments for the opportunity arm')


def identity(profile, control, parent, pins_path):
    pins = json.loads(pins_path.read_bytes())
    if pins['source_id'] != SOURCE or pins['diagnostic_parent_source_id'] != PARENT or pins['base_source_id'] != BASE:
        raise ValueError('Source inventory differs from exact new/a2dc/9d pins')
    result = {'profile': source_build(profile, pins['files'], True),
              'control': source_build(control, pins['base_files'], False),
              'parent': source_build(parent, pins['parent_files'], True), 'pins_sha256': sha256(pins_path)}
    for name in ('profile', 'control', 'parent'):
        enabled = result[name]['entries'].get('KAYROS_U_FILTER_OPPORTUNITY_PROFILING') == 'ON'
        if enabled != (name == 'profile'):
            raise ValueError('U-opportunity build flag differs from the declared source role')
    return result


def validate_gate(path, profile, control, pins_path):
    value = json.loads(Path(path).read_bytes())
    if (value.get('source_id') != SOURCE or value.get('base_source_id') != BASE
            or value.get('parent_source_id') != PARENT or value.get('all_checks_passed') is not True
            or len(value.get('results', [])) != 10 or not all(row['passed'] for row in value['results'])):
        raise ValueError('All ten reserved-node compile/run gates must pass')
    parent = Path(value['identity']['parent']['source'])
    if value['identity'] != identity(Path(profile), Path(control), parent, Path(pins_path)):
        raise ValueError('Source/library identity changed after the native gate')
    for relative, expected in value['artifacts'].items():
        checked_file(Path(path).parent / relative, expected)
    return value


def native(args):
    allocated = allocation(args.job_id, args.cores)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    immutable_json(output / 'allocation.json', allocated)
    try:
        ids = identity(args.profile_source, args.control_source, args.parent_source, args.source_pins)
        immutable_json(output / 'identity.json', ids)
        rows = []
        cases = [('accounting', 'test_u_filter_opportunity', 'profile', False, 'PASS'),
                 ('pwl-on', 'test_dominance_profile_pwl', 'profile', True, PWL),
                 ('pwl-off', 'test_dominance_profile_pwl', 'control', True, PWL),
                 ('labeling-on', 'test_u_filter_opportunity_labeling', 'profile', True, NATIVE),
                 ('labeling-parent', 'test_u_filter_opportunity_labeling', 'parent', True, NATIVE)]
        source = args.profile_source.resolve()
        for name, fixture, role, linked, expected in cases:
            arm = ids[role]
            for path, expected_hash in arm['files'].items(): checked_file(path, expected_hash)
            flags = [] if role == 'control' else ['-DKAYROS_DOMINANCE_PROFILING=1']
            if role == 'profile': flags += ['-DKAYROS_U_FILTER_OPPORTUNITY_PROFILING=1']
            entries = arm['entries']
            command = [arm['compiler'], '-std=c++23', '-O2', '-DNDEBUG', '-ffp-contract=off', *flags,
                *['-I' + str(source / relative) for relative in ('cpp/lera/main/include', 'cpp/lera/goc/include',
                    'cpp/lera/nyr/include', 'cpp/lera/magic_enum/include')],
                *(['-I' + entries['Boost_INCLUDE_DIR']] if entries.get('Boost_INCLUDE_DIR') else []),
                str(source / 'tests/cpp' / (fixture + '.cpp'))]
            if linked:
                command += [arm['library']]
                if fixture.endswith('_labeling'):
                    graph = entries.get('Boost_GRAPH_LIBRARY_RELEASE')
                    command += [graph] if graph and Path(graph).is_file() else ['-lboost_graph']
                command += ['-ldl', '-lm']
            executable = output / name
            command += ['-o', str(executable)]
            compiled = run_command(output, 'compile-' + name, command, args.cores[0], 240, source)
            rows.append(compiled)
            if compiled['passed']:
                row = run_command(output, name, [str(executable)], args.cores[0], 90, source, expected_text=expected)
                if row['passed'] and expected != 'PASS':
                    row['passed'] = (output / (name + '.stdout')).read_text().splitlines()[-1] == expected
                rows.append(row)
        passed = len(rows) == 10 and all(row['passed'] for row in rows)
        immutable_json(output / 'complete.json', {'source_id': SOURCE, 'base_source_id': BASE,
            'parent_source_id': PARENT, 'identity': ids, 'all_checks_passed': passed, 'results': rows,
            'solver_cells_started': 0, 'finished_utc': now(),
            'artifacts': {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()}})
        print(json.dumps({'native_gate': str(output / 'complete.json'), 'passed': passed, 'solver_cells_started': 0}))
        return int(not passed)
    except Exception as error:
        immutable_json(output / 'failure.json', {'reason': f'{type(error).__name__}: {error}', 'solver_cells_started': 0})
        raise


def manifest(args):
    allocated = allocation(args.job_id, args.cores)
    gate = validate_gate(args.native_gate, args.profile_source, args.control_source, args.source_pins)
    checker = checker_identity()
    pins, runtime = json.loads(args.source_pins.read_bytes()), json.loads(args.runtime_pins.read_bytes())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name, path in [('source-pins.json', args.source_pins), ('runtime-pins.json', args.runtime_pins), ('native-gate.json', args.native_gate)]:
        shutil.copyfile(path, output / name)
    variants = []
    for name, source, python, original in [('correction-on', BASE, args.control_python, args.control_manifest),
            ('dominance-profile', SOURCE, args.profile_python, None), ('lera-minimal', 'f2c9e20', args.lera_python, args.lera_manifest)]:
        patches = output / (name + '-patches'); patches.mkdir()
        if original:
            value = json.loads(original.read_bytes())
            if value.get('checker') != checker or not source_matches(value.get('source_id'), source):
                raise ValueError('Control checker/source differs from pinned 9d/f2')
            for filename, expected in {**value['runtime_files'], **value['source_patch_files']}.items(): checked_file(filename, expected)
            rebound = {}
            for index, (filename, expected) in enumerate(value['source_patch_files'].items()):
                target = patches / f'{index:02d}-{Path(filename).name}'; shutil.copyfile(filename, target); rebound[str(target)] = expected
            value.update(source_patch_files=rebound, parent_manifest_sha256=sha256(original))
        else:
            value = candidate_manifest(python, args.profile_patches, patches, checker, SOURCE)
        if name != 'lera-minimal':
            patch_stack(value, pins, profile=name == 'dominance-profile')
            observed = installed(python)
            expected = runtime[name]
            if expected.get('source_id') != source or observed['backend'] != 'CPLEX': raise ValueError('Runtime source/backend mismatch')
            for kind in ('core', 'lera'):
                checked_file(observed[kind], expected[kind + '_sha256'])
                if value['runtime_files'].get(observed[kind]) != expected[kind + '_sha256']: raise ValueError('Runtime hash mismatch')
            if (SCHEMA.encode() in Path(observed['lera']).read_bytes()) != (name == 'dominance-profile'):
                raise ValueError('U-opportunity binary schema marker differs')
            value['expected_algorithm_settings'] = {'label_correction': True, 'lazy_extension': True}
        value.update(diagnostic_environment={}, native_gate_sha256=sha256(args.native_gate),
                     purpose='Sampled U-filter rejection coverage; no runtime benefit claim')
        target = output / (name + '.json'); immutable_json(target, value)
        variant = {'name': name, 'source_id': source, 'manifest_path': str(target),
                   'worker_python': str(python.absolute()), 'diagnostic_environment': {}}
        if name == 'lera-minimal': variant['worker_path'] = str(args.lera_worker.resolve())
        variants.append(variant)
    immutable_json(output / 'plan.json', variants)
    immutable_json(output / 'complete.json', {'source_id': SOURCE, 'base_source_id': BASE,
        'native_gate_sha256': sha256(args.native_gate), 'native_identity': gate['identity'],
        'allocation': allocated, 'checker': checker, 'solver_cells_started': 0,
        'files': {p.relative_to(output).as_posix(): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}})
    print(json.dumps({'plan': str(output / 'plan.json'), 'solver_cells_started': 0}))
    return 0


def private_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summarizer():
    audit = private_module('_u_filter_parent_audit', 'ms58_dominance_profile_audit.py')
    audit.SCHEMA = SCHEMA
    def summarize(path):
        result = audit.summarize_profile(path)
        if result.get('status') != 'parsed': return result
        lines = Path(path).read_bytes().splitlines(keepends=True)
        if lines and not lines[-1].endswith(b'\n'): lines.pop()
        calls = {}
        for record in map(json.loads, lines):
            if record['event'] == 'pricing_begin': calls[record['call']] = {'context': record['context']}
            if record['event'] != 'profile_open': calls[record['call']]['record'] = record
        groups = {}
        def add(total, values):
            for key, value in values.items():
                if isinstance(value, dict): add(total.setdefault(key, {}), value)
                else: total[key] = total.get(key, 0) + value
        for call in calls.values():
            record = call['record']; scope = 'returned_tasks' if record['event'] == 'pricing_end' else 'unfinished_tasks_last_snapshot'
            for direction in ('forward', 'backward'):
                for kind in ('dominance', 'correction'):
                    for outcome in ('survived', 'eliminated'):
                        value = record[direction][kind][outcome]
                        u = value['sampled_u_filter_opportunity']
                        if u['membership_violations'] or u['sampled_calls'] != value['sampled_finished']:
                            raise ValueError('U-filter sample/membership invariant failed')
                        denominators = set()
                        for scale in u['scales'].values():
                            if (scale['certificate_violations'] or scale['actual_u_tests'] != scale['subset_pass'] + scale['subset_reject']
                                    or scale['covered_actual_rejects'] > scale['subset_reject']):
                                raise ValueError('U-filter rejection-coverage invariant failed')
                            denominators.add(scale['actual_u_tests'])
                        if len(denominators) != 1: raise ValueError('Granularity denominators differ')
                        key = '/'.join((scope, call['context']['level'], direction, kind, outcome))
                        add(groups.setdefault(key, {}), u)
        for group in groups.values():
            for scale in group['scales'].values():
                scale['coverage_of_actual_u_tests'] = scale['covered_actual_rejects'] / scale['actual_u_tests'] if scale['actual_u_tests'] else None
                scale['coverage_of_actual_rejections'] = scale['covered_actual_rejects'] / scale['subset_reject'] if scale['subset_reject'] else None
        result.update(u_filter_opportunity_aggregates=groups, maintenance_costs_measured=False,
                      runtime_benefit_claim=False, granularities_additive=False, timing_comparable_to_parent=False)
        return result
    return summarize


def controller():
    # Private module objects retain the reviewed controller's function bodies.
    # Overrides are explicit diagnostic pins/callbacks, never file mutations.
    module = private_module('_u_filter_parent_controller', 'ms58_dominance_profile_run.py')
    module.PROFILE = SOURCE
    module.SOURCES = {'correction-on': BASE, 'dominance-profile': SOURCE, 'lera-minimal': 'f2c9e20'}
    module.validate_gate = validate_gate
    module.validate_patch_stack = patch_stack
    module.summarize_profile = summarizer()
    original_monitor = module.monitor
    def monitor(output):
        result = original_monitor(output)
        comparisons = result.get('work_comparisons', [])
        rc201 = next((row for row in comparisons if row['instance'] == 'RC201' and row['other'] == 'dominance-profile'), None)
        consistency = Path(output) / 'consistency-audit.json'
        rows = json.loads(consistency.read_bytes())['cells'] if consistency.is_file() else []
        rc201_rows = [row for row in rows if row['instance_name'] == 'RC201'
                      and row['variant'] in ('correction-on', 'dominance-profile')]
        completed = len(rc201_rows) == 2 and all(row.get('raw_result_status', row.get('status')) == 'completed'
            and row.get('native_work', {}).get('status') == 'Optimum' for row in rc201_rows)
        result['rc201_completed_work_equivalence'] = {'required_for_interpretation': True,
            'native_runs_completed': completed,
            'passed': bool(completed and rc201 and rc201['both_observations_usable'] and all(
                rc201['native_work'][name]['equal'] is True for name in ('root_work_signature', 'whole_work_signature'))),
            'meaning': 'Completed RC201 must match uninstrumented9d native root/whole work. C104 may remain censored.'}
        return result
    module.monitor = monitor
    return module


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'pilot':
        module = controller()
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        result = module.main()
        if '--mode' in sys.argv and sys.argv[sys.argv.index('--mode') + 1] == 'launch':
            output = Path(sys.argv[sys.argv.index('--output') + 1])
            result = result or int(not module.monitor(output)['rc201_completed_work_equivalence']['passed'])
        return result
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('native', 'manifest'))
    for name in ('profile-source', 'control-source', 'source-pins', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--job-id', required=True); parser.add_argument('--cores', type=int, nargs=2, required=True)
    parser.add_argument('--parent-source', type=Path)
    for name in ('native-gate', 'runtime-pins', 'control-python', 'control-manifest', 'profile-python', 'profile-patches',
                 'lera-python', 'lera-manifest', 'lera-worker'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    if args.command == 'native':
        if not args.parent_source: parser.error('native requires separately built a2dc --parent-source')
        return native(args)
    needed = ('native_gate', 'runtime_pins', 'control_python', 'control_manifest', 'profile_python', 'profile_patches',
              'lera_python', 'lera_manifest', 'lera_worker')
    if not all(getattr(args, name) for name in needed): parser.error('manifest requires all runtime/native-gate options')
    return manifest(args)


if __name__ == '__main__':
    raise SystemExit(main())
