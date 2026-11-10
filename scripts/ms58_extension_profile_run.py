"""Extension diagnostics, six cold CPLEX cells after native gates.

Run with the pinned checker Python on a reserved compute node. Supply a
screen-format three-variant --plan (names best, extension-profile,
lera-minimal), --profile-source, a new --output, --job-id and two --cores.
All plan diagnostic_environment dictionaries must be empty. This controller
adds a fresh profile path only to each extension-profile cell. C104 and
RC201 n100 each run all three variants for 600 s with a 16 GiB cap, serially
within each physical core. --mode monitor is read-only and needs only output.
No SSH, reservation or remote launch is performed by this helper.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_dabia_screen import (BEST, BEST_PATCH, LERA, HORIZON, MEMORY_MB,
    execute, freeze_variant, immutable_json, monitor as screen_monitor, now)
from ms58_dabia_wave import checked_file, input_size, no_overrides, source_matches, topology_snapshot
from ms58_dabia_validate import run_command
from ms58_extension_profile_audit import compare_rows, summarize_profile

PROFILE = '4643f2159e6769db8246af67a9d559f70ae2105b'
PROFILE_PATCH = '2531f7ef2e17dbd56fe6cae0fac934f1d0bdb686497fecae19f8f5b8ef32b1ea'
PROFILE_FULL_PATCH = 'b02ce28244c0cd0be159ea54060ad35eea84be34c9dad87028392e98d60ba2ca'
PROFILE_BINARY = 'f261f170a5695ae6c2dff8c6b6e5fe608e06a83a9d6bd2ab588080f353e766ef'
BASELINE_PATCHES = {
    'a16e9fcd5ad0a5f595f8fa7741a3972af3786def24945fd6b09d51c8a3b35ba2',
    'de51c7e6c4380998397a2e292bf45ac9e23f52ee5a5828accdc637723c47f126',
    '989a68a75bbdfd687bdf0a940b22b07a02adfb985b45fb36e31705c2be425ad3',
    '1fe462773c572a331a32d4257d53da794a5e38bd84bca678bfaaf6c52cfa077a',
}
INSTANCES = ('C104', 'RC201')
EXPECTED_SOURCES = {'best': BEST, 'extension-profile': PROFILE, 'lera-minimal': LERA}
PROFILE_FILES = {'cpp/lera/CMakeLists.txt': '0b8644fe94622aa1f49f1003534615014f5faa4d9a75a1cc9edf3855a77561f4',
 'cpp/lera/main/include/labeling/bidirectional_labeling.h': 'f8d9b4618f2e6b10c8501bbc93b4165e07a18b41e9ef02aa301d68bc2e660489',
 'cpp/lera/main/include/labeling/extension_profile.h': 'd990ec9a5c4bf99870b3904a527b134d262c406976128979ca534916cc313575',
 'cpp/lera/main/include/labeling/monodirectional_labeling.h': '6a6a8e270a1f2cedc10565f951c3b96e38d0ee43f00649d10a84f12e287e1925',
 'cpp/lera/main/src/labeling/bidirectional_labeling.cpp': 'a2c9bef51fe34416a771ef22d9cb66ec98efce81be7af3186de6e929d5f41eaa',
 'cpp/lera/main/src/labeling/monodirectional_labeling.cpp': 'bdb3076b23d4da812910718393efda8da8990b5a1e5839e2312d620bf8b48652',
 'tests/cpp/test_extension_profile.cpp': '18666cefa93fb5890670566a3ab55c9354878dd78c438c071b82e90e3790e397',
 'tests/cpp/test_extension_profile_pwl.cpp': '762c5ea59f528b206d51e282eba8f61974c8d52a4a1b744409583787e599fe52'}


def validate_profile_patch_stack(manifest):
    hashes = set(manifest['source_patch_files'].values())
    if not BASELINE_PATCHES <= hashes or not (PROFILE_FULL_PATCH in hashes or {PROFILE_PATCH, BEST_PATCH} <= hashes):
        raise ValueError('Profile needs all four baseline patches plus either the full078-to464 patch or best and incremental profile patches')


def validate_plan(plan):
    if not isinstance(plan, list) or len(plan) != 3 or {v.get('name') for v in plan} != set(EXPECTED_SOURCES):
        raise ValueError('Exactly best, extension-profile and lera-minimal are required')
    for variant in plan:
        required = {'name', 'source_id', 'manifest_path', 'worker_python', 'diagnostic_environment'}
        if required - variant.keys() or variant.keys() - required - {'worker_path'}:
            raise ValueError('Use the documented screen-format variant fields')
        required_source = EXPECTED_SOURCES[variant['name']]
        if not source_matches(variant['source_id'], required_source) or (len(required_source) == 40 and variant['source_id'] != required_source):
            raise ValueError('Variant source differs from its pinned commit')
        if variant['diagnostic_environment'] != {}:
            raise ValueError('Profile switches are assigned dynamically, no plan overrides are allowed')


def allocation(job_id, cores):
    no_overrides(os.environ)
    host = socket.gethostname().split('.')[0]
    nodefile = Path(os.environ.get('OAR_NODEFILE', '/nonexistent-nodefile'))
    if not job_id or os.environ.get('OAR_JOB_ID') != job_id or host.startswith(('fnancy', 'flyon')):
        raise ValueError('A real declared OAR compute allocation is required')
    if not nodefile.is_file() or host not in {h.split('.')[0] for h in nodefile.read_text().split()}:
        raise ValueError('Current host is absent from the real OAR nodefile')
    if len(cores) != 2:
        raise ValueError('Exactly two physical lanes are required')
    topology = topology_snapshot(cores)
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if available < 48 * 1024 ** 2:
        raise ValueError('Two 16 GiB cells require 48 GiB available including reserve')
    return {'oar_job_id': job_id, 'host': socket.gethostname(), 'physical_slots': topology,
        'nodefile': nodefile.read_text(), 'allowed_cores': sorted(os.sched_getaffinity(0)),
        'memory_available_kib': available, 'created_utc': now()}


def frozen_inputs(inputs, output):
    root = output / 'frozen-inputs/benchmarks/TDVRPTW/Dabia2013/n=100'
    root.mkdir(parents=True)
    rows = {}
    for name in INSTANCES:
        source = (inputs / f'{name}.vrp.json').resolve()
        raw = json.loads(source.read_bytes())
        if input_size(source) != 100 or raw['num_customers'] != 100 or raw['instance_name'] != name:
            raise ValueError('Only Dabia2013 n100 C104 and RC201 are allowed')
        relative = Path(raw['td']['atf_path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('ATF must be relative and contained within the input directory')
        atf = source.parent / relative
        target, target_atf = root / source.name, root / relative
        target_atf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        shutil.copyfile(atf, target_atf)
        rows[name] = {'instance_name': name, 'num_customers': 100, 'dataset': 'Dabia2013',
            'instance_key': f'Dabia2013/n=100/{name}', 'instance_path': str(target),
            'instance_sha256': sha256(target), 'atf_path': str(target_atf), 'atf_sha256': sha256(target_atf),
            'source_instance_path': str(source), 'source_atf_path': str(atf)}
    return rows


def prepare(args, allocated, checker):
    plan = json.loads(args.plan.read_bytes())
    validate_plan(plan)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ('manifests', 'rows', 'started', 'launch-logs', 'profiles', 'profile-audits', 'native-gates'):
        (output / name).mkdir()
    shutil.copyfile(args.plan, output / 'plan.json')
    shutil.copytree(Path(__file__).resolve().parent, output / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    variants = {v['name']: freeze_variant(v, output, checker) for v in plan}
    profile_manifest = variants['extension-profile'][0]
    validate_profile_patch_stack(profile_manifest)
    for name in ('best', 'extension-profile'):
        manifest, python, _ = variants[name]
        installed = Path(subprocess.check_output([python, '-c',
            'from kayros import _lera; print(_lera.__file__)'], text=True, timeout=30).strip()).resolve()
        runtime = {str(Path(p).resolve()): h for p, h in manifest['runtime_files'].items()}
        checked_file(installed, runtime[str(installed)])
        if name == 'extension-profile':
            checked_file(installed, PROFILE_BINARY)
        compiled = b'kayros-ms58-extension-profile-v1' in installed.read_bytes()
        if compiled != (name == 'extension-profile'):
            raise ValueError('Installed binary diagnostic marker differs from the declared variant')
    inputs = frozen_inputs(args.inputs, output)
    orders = [('best', 'extension-profile', 'lera-minimal'), ('lera-minimal', 'extension-profile', 'best')]
    tasks = []
    for lane, (name, order, slot) in enumerate(zip(INSTANCES, orders, allocated['physical_slots'])):
        for position, variant in enumerate(order, 1):
            manifest, python, worker = variants[variant]
            manifest = json.loads(json.dumps(manifest))
            native = variant == 'lera-minimal'
            task_id = f'{name}--rep1--{variant}'
            environment = {} if variant != 'extension-profile' else {
                'KAYROS_EXTENSION_PROFILE': str(output / 'profiles' / f'{task_id}.jsonl'),
                'KAYROS_EXTENSION_PROFILE_STRIDE': str(args.sample_stride),
                'KAYROS_EXTENSION_PROFILE_OFFSET': str(args.sample_offset)}
            manifest['diagnostic_environment'] = environment
            task = dict(inputs[name], task_id=task_id, lane=lane, variant=variant,
                source_id=manifest['declared_source_commit'], position=position, replicate=1,
                cpu_core=slot['cpu_core'], host=allocated['host'],
                arm='lera-exact' if native else 'kayros-bpca-cplex', runtime_key='upstream' if native else 'cplex',
                seed=None, prefix_s=None, horizon_s=HORIZON, memory_mb=MEMORY_MB,
                diagnostic_environment=environment)
            if native:
                worker_environment = manifest['worker_environment']
                native_input = Path(worker_environment['UPSTREAM_SOURCE_ROOT']) / f'instances/dabia_et_al_2013/{name}_100.json'
                task['native_instance_sha256'] = sha256(native_input)
                worker_environment['UPSTREAM_NATIVE_INPUT_SHA256'] = task['native_instance_sha256']
                previous = manifest['runtime_files'].get(str(native_input.resolve()))
                if previous is not None and previous != task['native_instance_sha256']:
                    raise ValueError('Minimal Lera native input changed from the archived runtime')
                manifest['runtime_files'][str(native_input.resolve())] = task['native_instance_sha256']
            manifest['screen_cell'] = task.copy()
            manifest_path = output / 'manifests' / f'{task_id}.json'
            immutable_json(manifest_path, manifest)
            task.update(manifest_path=str(manifest_path), manifest_sha256=sha256(manifest_path),
                worker_python=python, worker_python_sha256=sha256(Path(python)), worker_path=worker,
                worker_sha256=sha256(Path(worker)), output_directory=str(output / 'cells'),
                launcher_stdout=str(output / 'launch-logs' / f'{task_id}.json'),
                launcher_stderr=str(output / 'launch-logs' / f'{task_id}.stderr.log'))
            tasks.append(task)
    spec = {'schema_version': 1, 'purpose': 'Extension mechanism diagnostics, no throughput claim',
        'created_utc': now(), 'allocation': allocated, 'checker': checker, 'scheduled_count': len(tasks),
        'horizon_s': HORIZON, 'memory_mb': MEMORY_MB, 'clock_origin': 'solver_process_launch',
        'scheduled_cells': tasks, 'checker_python': sys.executable, 'checker_python_sha256': sha256(Path(sys.executable)),
        'profile_source': str(args.profile_source.resolve()), 'profile_source_id': PROFILE,
        'frozen_files': {str(p.relative_to(output)): sha256(p) for directory in
            ('frozen-scripts', 'frozen-patches', 'frozen-inputs', 'manifests')
            for p in sorted((output / directory).rglob('*')) if p.is_file()},
        'plan_sha256': sha256(output / 'plan.json')}
    immutable_json(output / 'schedule.json', spec)
    immutable_json(output / 'schedule-hash.json', {'sha256': sha256(output / 'schedule.json')})
    return spec


def native_gates(source, output, core):
    directory = output / 'native-gates'
    for relative, expected in PROFILE_FILES.items():
        checked_file(source / relative, expected)
        target = directory / 'frozen-source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    cache = source / 'build/cplex/CMakeCache.txt'
    entries = {line.split('=', 1)[0].split(':', 1)[0]: line.split('=', 1)[1]
        for line in cache.read_text().splitlines() if '=' in line and ':' in line.split('=', 1)[0]
        and not line.startswith(('#', '//'))}
    if entries.get('LERA_LP_BACKEND') != 'cplex' or entries.get('KAYROS_EXTENSION_PROFILING') != 'ON':
        raise ValueError('Native library needs a CPLEX build with extension profiling ON')
    if Path(entries.get('CMAKE_HOME_DIRECTORY', '')).resolve() != source:
        raise ValueError('Native library build belongs to a different source directory')
    compiler = Path(entries['CMAKE_CXX_COMPILER'])
    library = source / 'build/cplex/cpp/lera/liblera.a'
    pinned = {str(p): sha256(p) for p in (cache, compiler, library)}
    shutil.copyfile(cache, directory / 'CMakeCache.txt')
    immutable_json(directory / 'identity.json', {'source_id': PROFILE, 'source_files': PROFILE_FILES,
        'build_files': pinned, 'core': core, 'created_utc': now()})
    rows = []
    for name in ('test_extension_profile', 'test_extension_profile_pwl'):
        for filename, expected in pinned.items():
            checked_file(filename, expected)
        executable = directory / name
        command = [str(compiler), '-std=c++23', '-O2', '-DNDEBUG', '-ffp-contract=off',
            '-DKAYROS_EXTENSION_PROFILING=1',
            *['-I' + str(source / relative) for relative in ('cpp/lera/main/include',
                'cpp/lera/goc/include', 'cpp/lera/nyr/include', 'cpp/lera/magic_enum/include')],
            *(['-I' + entries['Boost_INCLUDE_DIR']] if entries.get('Boost_INCLUDE_DIR') else []),
            str(source / 'tests/cpp' / f'{name}.cpp'),
            *([str(library), '-ldl', '-lm'] if name.endswith('_pwl') else []), '-o', str(executable)]
        compiled = run_command(directory, 'compile-' + name, command, core, 180, source)
        rows.append(compiled)
        if compiled['passed']:
            rows.append(run_command(directory, name, [str(executable)], core, 60, source,
                                    expected_text='PASS'))
    result = {'source_id': PROFILE, 'all_checks_passed': len(rows) == 4 and all(r['passed'] for r in rows),
        'results': rows, 'executables': {p.name: sha256(p) for p in directory.glob('test_extension_profile*') if p.is_file() and p.suffix == ''},
        'finished_utc': now()}
    immutable_json(directory / 'complete.json', result)
    return result['all_checks_passed']


def monitor(output):
    report = screen_monitor(output)
    gate_complete = output / 'native-gates/complete.json'
    report['native_gates'] = json.loads(gate_complete.read_bytes()) if gate_complete.is_file() else {
        'all_checks_passed': False, 'failure_record_present': (output / 'native-gates/failure.json').exists()}
    complete = output / 'complete.json'
    if complete.is_file():
        frozen = json.loads(complete.read_bytes())
        for field, directory in (('profile_audit_sha256', 'profile-audits'), ('profile_sha256', 'profiles')):
            for name, expected in frozen.get(field, {}).items():
                checked_file(output / directory / name, expected)
    rows = [json.loads(p.read_bytes()) for p in sorted((output / 'rows').glob('*.json'))]
    report['work_comparisons'] = compare_rows(rows)
    report['profiles'] = {}
    for name in INSTANCES:
        task_id = f'{name}--rep1--extension-profile'
        path = output / 'profiles' / f'{task_id}.jsonl'
        summary_path = output / 'profile-audits' / f'{task_id}.json'
        if summary_path.is_file():
            report['profiles'][task_id] = json.loads(summary_path.read_bytes())
        else:
            report['profiles'][task_id] = summarize_profile(path)
    report['throughput_claim'] = False
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=('run', 'monitor'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--profile-source', type=Path)
    parser.add_argument('--job-id')
    parser.add_argument('--cores', type=int, nargs=2)
    parser.add_argument('--sample-stride', type=int, default=1009)
    parser.add_argument('--sample-offset', type=int, default=0)
    parser.add_argument('--inputs', type=Path, default=Path.home() / 'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100')
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == 'monitor':
        print(json.dumps(monitor(output), indent=2))
        return 0
    if not all((args.plan, args.profile_source, args.job_id, args.cores)):
        parser.error('run requires plan, profile-source, job-id and two cores')
    if args.sample_stride <= 0 or not 0 <= args.sample_offset < args.sample_stride:
        parser.error('Sample stride must be positive and offset must lie below stride')
    spec = prepare(args, allocation(args.job_id, args.cores), checker_identity())
    try:
        passed = native_gates(args.profile_source.resolve(), output, args.cores[0])
    except Exception as error:
        immutable_json(output / 'native-gates/failure.json', {'created_utc': now(),
            'reason': f'{type(error).__name__}: {error}', 'solver_cells_started': 0})
        passed = False
    if not passed:
        immutable_json(output / 'gates-failed.json', {'created_utc': now(), 'solver_cells_started': 0})
        return 1
    for path, expected in spec['frozen_files'].items():
        checked_file(output / path, expected)
    checked_file(spec['checker_python'], spec['checker_python_sha256'])
    immutable_json(output / 'launch.json', {'created_utc': now(), 'schedule_sha256': sha256(output / 'schedule.json'),
        'native_gates_sha256': sha256(output / 'native-gates/complete.json'), 'oar_job_id': args.job_id})
    def lane(index):
        for task in spec['scheduled_cells']:
            if task['lane'] != index:
                continue
            row = execute(task, output, spec)
            if task['variant'] == 'extension-profile':
                try:
                    summary = summarize_profile(task['diagnostic_environment']['KAYROS_EXTENSION_PROFILE'])
                    if summary.get('available') and (summary['sample_stride'] != args.sample_stride or summary['sample_offset'] != args.sample_offset):
                        raise ValueError('Observed sampling settings differ from the frozen cell')
                    summary['source_id'] = task['source_id']
                    summary['task_id'] = task['task_id']
                    summary['cell_audit_ok'] = row.get('audit_ok', False)
                except Exception as error:
                    summary = {'available': False, 'status': 'profile_audit_failure', 'reason': str(error)}
                immutable_json(output / 'profile-audits' / f"{task['task_id']}.json", summary)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lane, range(2)))
    report = monitor(output)
    immutable_json(output / 'complete.json', dict(report, finished_utc=now(),
        row_sha256={p.name: sha256(p) for p in sorted((output / 'rows').glob('*.json'))},
        profile_audit_sha256={p.name: sha256(p) for p in sorted((output / 'profile-audits').glob('*.json'))},
        profile_sha256={p.name: sha256(p) for p in sorted((output / 'profiles').glob('*.jsonl'))}))
    print(json.dumps(report, indent=2))
    return int(bool(report['failed_or_unusable_cells'] or report['pending_cells'] or
        any(p.get('status') != 'parsed' for p in report['profiles'].values())))


if __name__ == '__main__':
    raise SystemExit(main())
