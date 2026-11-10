"""Run matched bounded fuzz batches on the reserved point-repair node."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
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
    raise RuntimeError('Point fuzz requires reserved compute job 7013766')
root = Path.home() / 'ms58-bpca'
output = root / 'resume-20261110/point-fuzz'
output.mkdir(parents=True, exist_ok=False)
environment = dict(os.environ)
environment.update(KAYROS_FUZZ='1', KAYROS_FUZZ_CASES='2', PYTHONUNBUFFERED='1',
                   MAMUT_ROUTING_BENCHMARKS_ROOT=str(Path.home() / 'kayros-sweeps/MAMUT-routing/benchmarks'))
for name in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS']:
    environment[name] = '1'
cores, seen = [], set()
for cpu in sorted(os.sched_getaffinity(0)):
    topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
    key = tuple((topology / name).read_text().strip() for name in ['physical_package_id', 'core_id'])
    if key not in seen:
        seen.add(key)
        cores.append(cpu)
assert len(cores) >= 8
launcher = ('import os,resource,sys; os.sched_setaffinity(0,{int(sys.argv[1])}); '
            'resource.setrlimit(resource.RLIMIT_AS,(16*1024**3,16*1024**3)); '
            'os.execvp(sys.argv[2],sys.argv[2:])')
plan = {'job_id': os.environ['OAR_JOB_ID'], 'host': socket.gethostname(),
        'cores': cores[6:8], 'candidate_binary_source': '250105c', 'reference_source': '0787925',
        'seeds': [7, 11, 23], 'requested_cases_per_seed': 2, 'outer_timeout_per_variant_s': 900,
        'address_space_cap_bytes': 16 * 1024 ** 3, 'created_utc': stamp(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'environment': {key: value for key, value in environment.items()
                        if key.startswith('KAYROS_') or key == 'MAMUT_ROUTING_BENCHMARKS_ROOT'},
        'scope': 'Existing parameterized pytest batch, one process per variant. Each process runs 3 seeds sequentially.'}
write(output / 'plan.json', plan)


def run(variant, core):
    source = root / 'sources' / variant
    python = root / f'venv-{variant}/bin/python'
    command = [str(python), '-m', 'pytest', '-vv', '-s', '--tb=long',
               '--junitxml=' + str(output / f'{variant}.junit.xml'),
               'tests/test_prover_fuzz_soundness.py::test_fuzz_batch']
    row = {'variant': variant, 'command': command, 'cpu': core, 'started_utc': stamp()}
    write(output / f'{variant}.started.json', row)
    with (output / f'{variant}.log').open('x') as stream:
        process = subprocess.Popen([sys.executable, '-c', launcher, str(core), *command],
                                   cwd=source, env=environment, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            row.update(returncode=process.wait(timeout=900), outer_timeout=False)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            row.update(returncode=process.returncode, outer_timeout=True)
    log = (output / f'{variant}.log').read_text(errors='replace')
    cases = [line for line in log.splitlines() if re.search(r'\[(7|11|23):\d{2}\]', line)]
    row.update(finished_utc=stamp(), case_lines=cases, executed_cases=len(cases),
               requested_cases=6, all_requested_cases_reported=len(cases) == 6,
               unsound_lines=[line for line in cases if 'UNSOUND' in line],
               log_sha256=hashlib.sha256((output / f'{variant}.log').read_bytes()).hexdigest())
    write(output / f'{variant}.result.json', row)
    print(json.dumps(row), flush=True)
    return row


with ThreadPoolExecutor(max_workers=2) as pool:
    futures = [pool.submit(run, variant, core) for variant, core in
               zip(['reference', 'point-addition'], cores[6:8])]
    rows = [future.result() for future in futures]
write(output / 'complete.json', {**plan, 'results': rows, 'finished_utc': stamp(),
      'all_batches_passed': all(row['returncode'] == 0 and not row['outer_timeout'] for row in rows),
      'all_requested_cases_reported': all(row['all_requested_cases_reported'] for row in rows)})
