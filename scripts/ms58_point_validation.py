"""Validate the scalar-point addition repair on an isolated OAR node."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys


def write(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.replace(path)


def stamp():
    return datetime.now(timezone.utc).isoformat()


if os.environ.get('OAR_JOB_ID') != '7013766' or socket.gethostname().startswith(('fnancy', 'flyon')):
    raise RuntimeError('Point validation requires reserved compute job7013766')
root = Path.home() / 'ms58-bpca'
output = root / 'resume-20261110/point-validation'
output.mkdir(parents=True, exist_ok=False)
source = root / 'sources/point-addition'
scripts = root / 'experiment/scripts'
python = root / 'venv-point-addition/bin/python'
os.environ['MAMUT_ROUTING_BENCHMARKS_ROOT'] = str(Path.home() / 'kayros-sweeps/MAMUT-routing/benchmarks')
for name in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
    os.environ[name] = '1'
cores, seen = [], set()
for cpu in sorted(os.sched_getaffinity(0)):
    topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
    key = tuple((topology / name).read_text().strip() for name in ['physical_package_id', 'core_id'])
    if key not in seen:
        seen.add(key)
        cores.append(cpu)
assert len(cores) >= 4
launcher = ('import os,resource,sys; os.sched_setaffinity(0,{int(sys.argv[1])}); '
            'resource.setrlimit(resource.RLIMIT_AS,(16*1024**3,16*1024**3)); '
            'os.execvp(sys.argv[2],sys.argv[2:])')


def run(name, command, core, timeout):
    row = {'name': name, 'command': command, 'cpu': core, 'outer_timeout_s': timeout,
           'started_utc': stamp(), 'address_space_cap_bytes': 16 * 1024 ** 3}
    write(output / f'{name}.started.json', row)
    with (output / f'{name}.log').open('x') as stream:
        process = subprocess.Popen([sys.executable, '-c', launcher, str(core), *command],
                                   cwd=source, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            row.update(returncode=process.wait(timeout=timeout), outer_timeout=False)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            row.update(returncode=process.returncode, outer_timeout=True)
    row['finished_utc'] = stamp()
    row['log_sha256'] = hashlib.sha256((output / f'{name}.log').read_bytes()).hexdigest()
    write(output / f'{name}.result.json', row)
    print(json.dumps(row), flush=True)
    return row


plan = {'job_id': os.environ['OAR_JOB_ID'], 'host': socket.gethostname(),
        'cores': cores[:4], 'candidate_source': '250105c', 'reference_source': '0787925',
        'created_utc': stamp(), 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope': 'Focused native invariant, existing broad gates and full solver coupling. Fuzz remains opt-in.'}
write(output / 'plan.json', plan)
rows = []
native = {}
fixture = source / 'tests/cpp/test_point_addition.cpp'
for variant in ['reference', 'point-addition']:
    variant_source = root / 'sources' / variant
    executable = output / f'native-{variant}'
    command = ['c++', '-std=c++23', '-O3', '-DNDEBUG', '-ffp-contract=off',
               '-I' + str(variant_source / 'cpp/lera/goc/include'), str(fixture),
               str(variant_source / 'build/cplex/cpp/lera/liblera.a'), '-ldl', '-lm',
               '-o', str(executable)]
    compilation = run(f'compile-{variant}', command, cores[0], 120)
    rows.append(compilation)
    if compilation['returncode'] == 0:
        execution = run(f'native-{variant}', [str(executable)], cores[0], 30)
        rows.append(execution)
        native[variant] = execution
native_check = {'reference_expected_failure_observed': (
    native.get('reference', {}).get('returncode') == 1
    and 'Adding a scalar point lost an attainable departure choice' in (output / 'native-reference.log').read_text()),
    'candidate_passed': native.get('point-addition', {}).get('returncode') == 0}
write(output / 'native-comparison.json', native_check)

instance = Path(os.environ['MAMUT_ROUTING_BENCHMARKS_ROOT']) / 'TDVRPTW/Vu2020/n=59/Vu-A2-pB-d98-w100.vrp.json'
jobs = [
    ('broad-gates', [str(python), '-m', 'pytest', '-vv', '--tb=long',
                    'tests/test_goc_pwl_stepwise.py', 'tests/test_nyr_ndcpwlf.py',
                    'tests/test_lera_stepwise_soundness.py', 'tests/test_lera_memory_guard.py',
                    'tests/test_prover_fuzz_soundness.py'], cores[0], 2700),
    ('coupling', [str(python), str(scripts / 'ms58_coupling_check.py'),
                  '--output', str(output / 'coupling.json')], cores[1], 420),
]


def vu_lane(warm, core):
    lane = []
    for cut_limit in [100, 0]:
        name = f'vu-{"warm" if warm else "cold"}-cuts-{cut_limit}'
        command = [str(python), str(scripts / 'ms58_vu_witness_probe.py'),
                   '--instance', str(instance),
                   '--witness', str(instance).replace('.vrp.json', '.bks.Duration.json'),
                   '--output', str(output / name), '--source-id', '250105c',
                   '--horizon', '120', '--cut-limit', str(cut_limit)]
        if warm:
            command.append('--warm')
        lane.append(run(name, command, core, 180))
    return lane


with ThreadPoolExecutor(max_workers=4) as pool:
    general = [pool.submit(run, *job) for job in jobs]
    vu = [pool.submit(vu_lane, False, cores[2]), pool.submit(vu_lane, True, cores[3])]
    rows.extend(future.result() for future in general)
    for future in vu:
        rows.extend(future.result())
write(output / 'complete.json', {**plan, 'native_comparison': native_check, 'results': rows,
      'all_candidate_processes_passed': all(row['returncode'] == 0 and not row['outer_timeout']
          for row in rows if row['name'] != 'native-reference'),
      'random_fuzz_sweep_enabled': os.environ.get('KAYROS_FUZZ') == '1', 'finished_utc': stamp()})
