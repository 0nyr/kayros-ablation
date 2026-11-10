"""Inspect a real 45-host OAR allocation and freeze 180 homogeneous slots.

The parent runs this with the pinned checker Python on an allocated node:
  --job-id ID --design DESIGN.json --output NEW_DIRECTORY
The design uses the ms58_dabia_wave manifest schema. Only metadata, file
digests, backend imports and dynamic-library inspection are performed.
No solver is called. The coordinator reads the real inherited OAR_NODEFILE,
uses oarsh for every allocated host, and writes slots.json only if all 45
snapshots agree on their CPU, kernel, topology and runtime cohort.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_dabia_wave import checked_file, no_overrides, source_matches, topology_snapshot
from probe_runner import atomic_json, digest

SOURCE = '9d64b7c53ddaea7a578d247ebd28db43ae0d4e56'
SOURCE_PATCH = '18b453c73422771ce0a2c6c5cbd6b934b62bcded0566c9c495d253106df28e87'
LERA_SOURCE = 'f2c9e205bd1ecc9afb45d5efe7fcb0df881f2b33'
LERA_BINARY = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
LERA_PATCH = 'ba75d827faff78c1c12fe73774ca658fe28fe96cd4806ee69502db8e6f685bbf'
HOSTS = 45
LANES = 4
MIN_AVAILABLE_KIB = 80 * 1024 ** 2


def now():
    return datetime.now(timezone.utc).isoformat()


def nodefile_hosts(raw):
    hosts = sorted(set(raw.split()))
    if len(hosts) != HOSTS or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', h) for h in hosts):
        raise ValueError('The real OAR nodefile must contain exactly 45 distinct valid hostnames')
    if len({h.split('.')[0] for h in hosts}) != HOSTS:
        raise ValueError('The nodefile contains aliases for the same host')
    return hosts


def require_job(job_id):
    if not job_id or os.environ.get('OAR_JOB_ID') != job_id:
        raise ValueError('Preflight requires the same declared OAR_JOB_ID on every process')
    if socket.gethostname().split('.')[0].startswith(('fnancy', 'flyon')):
        raise ValueError('Allocation preflight must execute on a reserved compute node')
    no_overrides(os.environ)


def select_physical_slots(allowed, sys_cpu=Path('/sys/devices/system/cpu')):
    records, seen = [], set()
    for cpu in sorted(allowed):
        record = topology_snapshot([cpu], sys_cpu, allowed)[0]
        key = (record['physical_package_id'], record['physical_core_id'])
        if key in seen:
            continue
        seen.add(key)
        numa = sorted(int(path.name[4:]) for path in (sys_cpu / f'cpu{cpu}').glob('node[0-9]*'))
        record['numa_nodes'] = numa
        records.append(record)
        if len(records) == LANES:
            return records
    raise ValueError('Fewer than 4 distinct physical cores exist inside the actual OAR affinity')


def observed_hardware():
    allowed = sorted(os.sched_getaffinity(0))
    slots = select_physical_slots(allowed)
    topology = []
    for cpu in allowed:
        record = topology_snapshot([cpu], allowed=allowed)[0]
        record['numa_nodes'] = sorted(int(p.name[4:]) for p in Path(f'/sys/devices/system/cpu/cpu{cpu}').glob('node[0-9]*'))
        topology.append(record)
    processors = {}
    for block in Path('/proc/cpuinfo').read_text().strip().split('\n\n'):
        row = dict(line.split(':', 1) for line in block.splitlines() if ':' in line)
        row = {k.strip(): v.strip() for k, v in row.items()}
        if row.get('processor', '').isdigit():
            processors[int(row['processor'])] = row
    fields = ('vendor_id', 'model name', 'cpu family', 'model', 'stepping', 'microcode', 'flags')
    cpu_identities = [{k: processors[cpu].get(k) for k in fields} for cpu in allowed]
    if not cpu_identities or len({digest(row) for row in cpu_identities}) != 1:
        raise ValueError('Allowed processors do not have one common CPU model and feature identity')
    if not cpu_identities[0].get('model name'):
        raise ValueError('CPU model identity is unavailable')
    frequencies = []
    for slot in slots:
        base = Path(f"/sys/devices/system/cpu/cpu{slot['cpu_core']}/cpufreq")
        frequencies.append({name: (base / name).read_text().strip() if (base / name).exists() else None
            for name in ('scaling_driver', 'scaling_governor', 'cpuinfo_min_freq', 'cpuinfo_max_freq')})
    memory = {line.split(':', 1)[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
              if line.startswith(('MemAvailable:', 'MemTotal:'))}
    packages = {record['physical_package_id'] for record in topology}
    physical = {(record['physical_package_id'], record['physical_core_id']) for record in topology}
    signature = {'architecture': platform.machine(), 'cpu_identity': cpu_identities[0],
        'kernel': {'system': platform.system(), 'release': platform.release(), 'version': platform.version()},
        'allowed_logical_cpus': len(allowed), 'allowed_physical_cores': len(physical),
        'allowed_packages': len(packages),
        'selected_slot_structure': [{k: v for k, v in slot.items() if k != 'cpu_core'} for slot in slots],
        'selected_frequency_configuration': frequencies}
    return {'cohort_hardware': signature, 'allowed_cpu_ids': allowed, 'allowed_topology': topology,
        'selected_physical_slots': slots, 'memory_kib': memory,
        'process_cgroup': Path('/proc/self/cgroup').read_text(),
        'load_average': list(os.getloadavg())}


def validate_runtime_spec(spec, key, checker):
    manifest = json.loads(checked_file(spec['path'], spec['sha256']).read_bytes())
    native = key == 'upstream'
    source = LERA_SOURCE if native else SOURCE
    if manifest.get('schema_version') != 1 or manifest.get('checker') != checker:
        raise ValueError(f'{key} manifest schema or checker differs from the pinned contract')
    if not source_matches(manifest.get('source_id'), source):
        raise ValueError(f'{key} manifest has the wrong source commit')
    if manifest.get('runtime_kind', 'kayros') != ('native' if native else 'kayros'):
        raise ValueError(f'{key} manifest has the wrong runtime kind')
    if manifest.get('backend', '').lower() != ('cplex' if native else key):
        raise ValueError(f'{key} manifest has the wrong backend')
    if manifest.get('diagnostic_environment', {}):
        raise ValueError('Final cohort runtimes must not enable diagnostic environment variables')
    runtime, patches = manifest.get('runtime_files', {}), manifest.get('source_patch_files', {})
    if not runtime or not patches:
        raise ValueError('Both runtime files and source patches must be pinned')
    for filename, expected in {**runtime, **patches}.items():
        checked_file(filename, expected)
    if (LERA_PATCH if native else SOURCE_PATCH) not in patches.values():
        raise ValueError(f'{key} source patch inventory lacks the required exact patch')
    checked_file(spec['worker_python'], spec['worker_python_sha256'])
    environment = manifest.get('worker_environment', {})
    if native:
        if set(environment) - {'UPSTREAM_PROBE_BINARY', 'UPSTREAM_SOURCE_ROOT', 'UPSTREAM_NATIVE_INPUT_SHA256'}:
            raise ValueError('Minimal Lera algorithm overrides are forbidden')
        binary = checked_file(environment['UPSTREAM_PROBE_BINARY'], LERA_BINARY)
        if str(binary) not in {str(Path(p).resolve()) for p in runtime}:
            raise ValueError('Minimal Lera binary is not pinned in runtime_files')
    elif environment:
        raise ValueError('Kayros runtimes cannot override upstream worker settings')
    return manifest


def native_dependencies(binary):
    result = subprocess.run(['ldd', str(binary)], text=True, capture_output=True, timeout=20)
    text = result.stdout + result.stderr
    if 'not found' in text:
        raise ValueError('Minimal Lera native executable has an unresolved dynamic dependency')
    if result.returncode and not any(x in text for x in ('not a dynamic executable', 'statically linked')):
        raise ValueError(f'Native dynamic-dependency inspection failed: {text.strip()}')
    libraries = {}
    for line in text.splitlines():
        match = re.search(r'(?:=>\s+|^\s*)(/\S+)\s+\(', line)
        if match:
            path = Path(match.group(1)).resolve()
            libraries[str(path)] = sha256(path)
    return {'inspection': text, 'resolved_libraries': libraries}


def runtime_import(plan, key):
    import importlib.metadata as metadata
    checker = checker_identity()
    if checker != plan['checker']:
        raise ValueError('Imported checker differs from the frozen cohort checker')
    spec = plan['runtimes'][key]
    manifest = validate_runtime_spec(spec, key, checker)
    record = {'source_id': LERA_SOURCE if key == 'upstream' else SOURCE,
        'python_version': sys.version, 'python_executable_sha256': sha256(Path(sys.executable)),
        'checker': checker, 'manifest_sha256': spec['sha256'],
        'runtime_files_digest': digest(manifest['runtime_files']),
        'source_patches_digest': digest(manifest['source_patch_files']),
        'libc_version': list(platform.libc_ver()),
        'runtime_environment': {key: os.environ.get(key) for key in
            ('LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'LOADEDMODULES')}}
    if key == 'upstream':
        binary = Path(manifest['worker_environment']['UPSTREAM_PROBE_BINARY']).resolve()
        record.update(backend='CPLEX', backend_evidence='Pinned minimal native artifact',
                      native_binary_sha256=sha256(binary), native_dependencies=native_dependencies(binary))
    else:
        import kayros
        from kayros import _core, _lera
        expected = 'CPLEX' if key == 'cplex' else 'HiGHS'
        if _lera.LP_BACKEND != expected:
            raise ValueError(f'Imported {key} backend differs from {expected}')
        distribution = metadata.distribution('kayros')
        installed = {str(distribution.locate_file(p).resolve()) for p in distribution.files
                     if str(p).endswith(('.so', '.py'))}
        if not installed or not installed <= {str(Path(p).resolve()) for p in manifest['runtime_files']}:
            raise ValueError('Manifest does not pin every installed Kayros module and extension')
        record.update(backend=_lera.LP_BACKEND, backend_evidence='Imported extension backend tag',
            kayros_distribution_version=distribution.version, kayros_import_version=kayros.__version__,
            imported_extensions={name: {'path': str(Path(module.__file__).resolve()),
                'sha256': sha256(Path(module.__file__))} for name, module in [('_core', _core), ('_lera', _lera)]})
        for imported in record['imported_extensions'].values():
            if manifest['runtime_files'].get(imported['path']) != imported['sha256']:
                raise ValueError('Imported extension shadows its pinned manifest entry')
    common_libraries = {}
    for line in Path('/proc/self/maps').read_text().splitlines():
        path = line.split()[-1]
        if path.startswith('/') and re.search(r'/(?:libc[.-]|libstdc\+\+|libgcc_s|libm[.-]|ld-linux)', path):
            library = Path(path).resolve()
            common_libraries[str(library)] = sha256(library)
    record['loaded_common_libraries'] = common_libraries
    return record


def verify_plan(path, expected_hash):
    plan = json.loads(checked_file(path, expected_hash).read_bytes())
    for relative, expected in plan['frozen_scripts_sha256'].items():
        checked_file(Path(path).parent / relative, expected)
    checked_file(plan['checker_python'], plan['checker_python_sha256'])
    return plan


def cohort_runtime_identity(runtimes):
    """Loader addresses are diagnostic output, not a runtime identity."""
    identity = json.loads(json.dumps(runtimes))
    for record in identity.values():
        record.get('native_dependencies', {}).pop('inspection', None)
    return identity


def node_snapshot(args):
    require_job(args.job_id)
    plan = verify_plan(args.plan, args.plan_sha256)
    host = socket.gethostname().split('.')[0]
    if args.host not in plan['hosts'] or host != args.host.split('.')[0] or plan['oar_job_id'] != args.job_id:
        raise ValueError('Observed host or OAR job differs from the frozen allocation')
    observed = {'status': 'failed', 'requested_host': args.host, 'observed_hostname': socket.gethostname(),
        'observed_fqdn': socket.getfqdn(), 'oar_job_id': os.environ.get('OAR_JOB_ID'),
        'created_utc': now(), 'plan_sha256': args.plan_sha256}
    try:
        observed.update(observed_hardware())
        if observed['memory_kib']['MemAvailable'] < MIN_AVAILABLE_KIB:
            raise ValueError('Node has less than 80 GiB available RAM for four capped lanes plus reserve')
        observed['runtimes'] = {}
        environment = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1')
        for key, spec in plan['runtimes'].items():
            command = [spec['worker_python'], str(Path(__file__).resolve()), '--mode', 'runtime',
                '--job-id', args.job_id, '--plan', str(args.plan), '--plan-sha256', args.plan_sha256,
                '--runtime-key', key]
            record = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=45)
            if record.returncode:
                raise ValueError(f'{key} import inspection failed: {record.stderr.strip()}')
            observed['runtimes'][key] = json.loads(record.stdout)
        if observed['runtimes']['cplex']['checker'] != observed['runtimes']['highs']['checker']:
            raise ValueError('Candidate backend checker identities differ')
        observed['cohort_identity'] = {'hardware': observed['cohort_hardware'],
                                      'runtimes': cohort_runtime_identity(observed['runtimes'])}
        observed['cohort_sha256'] = digest(observed['cohort_identity'])
        observed['status'] = 'passed'
    except Exception as error:
        observed['reason'] = f'{type(error).__name__}: {error}'
    observed['finished_utc'] = now()
    print(json.dumps(observed, allow_nan=False))
    return int(observed['status'] != 'passed')


def cohort_slots(snapshots, hosts, job_id):
    if len(snapshots) != HOSTS or len(hosts) != HOSTS:
        raise ValueError('Every one of the 45 allocated hosts must have a snapshot')
    if {row.get('requested_host') for row in snapshots} != set(hosts):
        raise ValueError('Host snapshots do not cover the actual allocation exactly')
    failures = [row.get('requested_host') for row in snapshots if row.get('status') != 'passed']
    if failures:
        raise ValueError(f'Allocation preflight failed on: {failures}')
    signatures = {row['cohort_sha256'] for row in snapshots}
    if len(signatures) != 1 or any(digest(row['cohort_identity']) != row['cohort_sha256'] for row in snapshots):
        raise ValueError('Heterogeneous CPU, kernel, topology or runtime cohort')
    slots = []
    for row in sorted(snapshots, key=lambda x: x['requested_host']):
        if row.get('observed_hostname', '').split('.')[0] != row['requested_host'].split('.')[0]:
            raise ValueError('Observed hostname differs from the allocated snapshot target')
        if row['oar_job_id'] != job_id or row['memory_kib']['MemAvailable'] < MIN_AVAILABLE_KIB:
            raise ValueError('Snapshot job identity or memory reserve differs from this allocation')
        physical = row['selected_physical_slots']
        if len(physical) != LANES or len({(s['physical_package_id'], s['physical_core_id']) for s in physical}) != LANES:
            raise ValueError('Node slot selection does not contain four distinct physical cores')
        if any(s['cpu_core'] not in row['allowed_cpu_ids'] for s in physical):
            raise ValueError('Selected core lies outside actual OAR affinity')
        slots.extend({'host': row['requested_host'], 'cpu_core': s['cpu_core']} for s in physical)
    if len(slots) != HOSTS * LANES:
        raise ValueError('The allocation must yield exactly 180 physical slots')
    return slots


def coordinator(args):
    require_job(args.job_id)
    nodefile = os.environ.get('OAR_NODEFILE')
    if not nodefile or not Path(nodefile).is_file():
        raise ValueError('A real inherited OAR_NODEFILE is required, never a synthesized host list')
    raw = Path(nodefile).read_text()
    hosts = nodefile_hosts(raw)
    if socket.gethostname().split('.')[0] not in {h.split('.')[0] for h in hosts}:
        raise ValueError('Coordinator is not running on an allocated host')
    checker = checker_identity()
    design = json.loads(args.design.read_bytes())
    if design.get('kayros_source_id') != SOURCE or set(design['manifests']) != {'cplex', 'highs', 'upstream'}:
        raise ValueError('Preflight requires the declared correction-on five-arm runtime design')
    if Path(design['checker_python']).absolute() != Path(sys.executable).absolute():
        raise ValueError('Invoke preflight with the exact checker_python declared in the wave design')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'snapshots').mkdir()
    (output / 'logs').mkdir()
    shutil.copyfile(nodefile, output / 'allocated-nodefile.txt')
    shutil.copyfile(args.design, output / 'design.json')
    shutil.copytree(Path(__file__).resolve().parent, output / 'frozen-scripts', ignore=shutil.ignore_patterns('__pycache__'))
    runtimes = json.loads(json.dumps(design['manifests']))
    for key, spec in runtimes.items():
        spec['worker_python'] = str(Path(spec['worker_python']).absolute())
        spec['worker_python_sha256'] = sha256(Path(spec['worker_python']))
        validate_runtime_spec(spec, key, checker)
    plan = {'schema_version': 1, 'oar_job_id': args.job_id, 'created_utc': now(), 'hosts': hosts,
        'nodefile_original_path': str(Path(nodefile).resolve()), 'nodefile_sha256': sha256(Path(nodefile)),
        'checker': checker, 'checker_python': sys.executable, 'checker_python_sha256': sha256(Path(sys.executable)),
        'runtimes': runtimes, 'design_sha256': sha256(output / 'design.json'),
        'frozen_scripts_sha256': {str(p.relative_to(output)): sha256(p) for p in sorted((output / 'frozen-scripts').glob('*.py'))},
        'scope': 'New homogeneous allocation cohort. No solver runs and no comparison with curves from other environments.'}
    atomic_json(output / 'plan.json', plan)
    plan_sha = sha256(output / 'plan.json')
    def inspect(host):
        command = ['env', f'OAR_JOB_ID={args.job_id}', sys.executable,
            str(output / 'frozen-scripts/ms58_dabia_preflight.py'), '--mode', 'node', '--job-id', args.job_id,
            '--plan', str(output / 'plan.json'), '--plan-sha256', plan_sha, '--host', host]
        row = {'status': 'failed', 'requested_host': host, 'oar_job_id': args.job_id, 'created_utc': now()}
        try:
            with (output / 'logs' / f'{host}.stdout').open('x') as stdout, (output / 'logs' / f'{host}.stderr').open('x') as stderr:
                result = subprocess.run(['oarsh', host, shlex.join(command)], stdout=stdout, stderr=stderr,
                                        stdin=subprocess.DEVNULL, timeout=180)
            row = json.loads((output / 'logs' / f'{host}.stdout').read_bytes())
            if result.returncode and row.get('status') == 'passed':
                row.update(status='failed', reason=f'oarsh exited {result.returncode}')
        except Exception as error:
            row.update(status='failed', reason=f'{type(error).__name__}: {error}')
        row['stdout_path'] = str(output / 'logs' / f'{host}.stdout')
        row['stderr_path'] = str(output / 'logs' / f'{host}.stderr')
        atomic_json(output / 'snapshots' / f'{host}.json', row)
        print(json.dumps({'host': host, 'status': row['status'], 'reason': row.get('reason')}), flush=True)
        return row
    with ThreadPoolExecutor(max_workers=HOSTS) as pool:
        snapshots = list(pool.map(inspect, hosts))
    summary = {'created_utc': now(), 'oar_job_id': args.job_id, 'plan_sha256': plan_sha,
        'expected_hosts': HOSTS, 'expected_slots': HOSTS * LANES, 'snapshot_count': len(snapshots),
        'snapshot_sha256': {p.name: sha256(p) for p in sorted((output / 'snapshots').glob('*.json'))},
        'all_checks_passed': False}
    try:
        slots = cohort_slots(snapshots, hosts, args.job_id)
        if Path(nodefile).read_text() != raw:
            raise ValueError('Real allocation nodefile changed during preflight')
        atomic_json(output / 'slots.json', slots)
        summary.update(all_checks_passed=True, slots_sha256=sha256(output / 'slots.json'),
            cohort_sha256=snapshots[0]['cohort_sha256'])
    except Exception as error:
        summary['reason'] = f'{type(error).__name__}: {error}'
    atomic_json(output / 'complete.json', summary)
    print(json.dumps(summary, indent=2))
    return int(not summary['all_checks_passed'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=('coordinator', 'node', 'runtime'), default='coordinator')
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--design', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--plan-sha256')
    parser.add_argument('--host')
    parser.add_argument('--runtime-key', choices=('cplex', 'highs', 'upstream'))
    args = parser.parse_args()
    if args.mode == 'coordinator':
        if not args.design or not args.output:
            parser.error('coordinator requires --design and --output')
        return coordinator(args)
    if not args.plan or not args.plan_sha256:
        parser.error('node and runtime require a frozen --plan and --plan-sha256')
    if args.mode == 'node':
        if not args.host:
            parser.error('node requires --host')
        return node_snapshot(args)
    if not args.runtime_key:
        parser.error('runtime requires --runtime-key')
    require_job(args.job_id)
    plan = verify_plan(args.plan, args.plan_sha256)
    if plan['oar_job_id'] != args.job_id:
        raise ValueError('Runtime import job differs from frozen allocation')
    print(json.dumps(runtime_import(plan, args.runtime_key), allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
