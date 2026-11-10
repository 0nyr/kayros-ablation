#!/usr/bin/env bash
set -euo pipefail
: "${OAR_JOB_ID:?Run validation inside the owned OAR allocation}"
if [[ "$OAR_JOB_ID" != 7013762 ]]; then
    echo "Unexpected allocation $OAR_JOB_ID, expected 7013762" >&2
    exit 1
fi
root="$HOME/ms58-bpca"
export MS58_VALIDATION_ROOT="$root"
export MAMUT_ROUTING_BENCHMARKS_ROOT="$HOME/kayros-sweeps/MAMUT-routing/benchmarks"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
"$HOME/ms56-ablation/experiment/.venv/bin/python" - <<'PY'
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import sys
import time

root = Path(os.environ['MS58_VALIDATION_ROOT'])
destination = root / 'outputs/resume-20261110/validation'
destination.mkdir(parents=True, exist_ok=False)
cores = []
physical = set()
for cpu in sorted(os.sched_getaffinity(0)):
    topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
    key = tuple((topology / part).read_text().strip()
                for part in ['physical_package_id', 'core_id'])
    if key not in physical:
        physical.add(key)
        cores.append(cpu)
    if len(cores) == 4:
        break
if len(cores) < 4:
    raise RuntimeError('Four distinct physical cores are required')

def write_json(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.replace(path)

def run(name, variant, core, arguments, timeout):
    python = root / f'venv-{variant}/bin/python'
    command = [str(python), *arguments]
    # A separate launcher applies limits without using preexec_fn in threads.
    launcher = ('import os,resource,sys; '
                'os.sched_setaffinity(0,{int(sys.argv[1])}); '
                'resource.setrlimit(resource.RLIMIT_AS,(16*1024**3,16*1024**3)); '
                'os.execv(sys.argv[2],sys.argv[2:])')
    wrapped = [sys.executable, '-c', launcher, str(core), *command]
    row = {'name': name, 'variant': variant, 'cpu': core, 'command': command,
           'cwd': str(root / f'sources/{variant}'), 'outer_timeout_s': timeout,
           'address_space_cap_bytes': 16 * 1024 ** 3,
           'started_utc': datetime.now(timezone.utc).isoformat()}
    write_json(destination / f'{name}.started.json', row)
    with (destination / f'{name}.log').open('x') as out:
        process = subprocess.Popen(wrapped, cwd=row['cwd'], stdout=out,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            row['returncode'] = process.wait(timeout=timeout)
            row['outer_timeout'] = False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            row.update(returncode=process.returncode, outer_timeout=True)
    row['finished_utc'] = datetime.now(timezone.utc).isoformat()
    row['log_sha256'] = hashlib.sha256((destination / f'{name}.log').read_bytes()).hexdigest()
    write_json(destination / f'{name}.result.json', row)
    print(json.dumps(row), flush=True)
    return row

plan = {'oar_job_id': os.environ['OAR_JOB_ID'], 'host': socket.gethostname(),
        'cores': cores, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'purpose': 'Retain test failures and distinguish timeout from soundness failures'}
write_json(destination / 'plan.json', plan)
target = ('tests/test_lera_stepwise_soundness.py::'
          'test_jumpfree_reverse_side_certificate_is_not_above_the_truth[Vu-A2-pB-d98-w100]')
rows = []
with ThreadPoolExecutor(max_workers=2) as pool:
    pending = [pool.submit(run, f'target-{variant}', variant, cores[index],
                           ['-m', 'pytest', '-vv', '--tb=long', target], 750)
               for index, variant in enumerate(['reference', 'normalize'])]
    rows += [future.result() for future in pending]

# Keep the already observed target in the denominator through its result above.
# Run the remaining gates independently so one failure cannot suppress evidence.
with ThreadPoolExecutor(max_workers=4) as pool:
    jobs = [
        ('remaining-soundness', 'normalize', cores[0],
         ['-m', 'pytest', '-vv', '--tb=long', 'tests/test_lera_stepwise_soundness.py',
          '--deselect', target], 3600),
        ('arithmetic-memory-fuzz-gates', 'normalize', cores[1],
         ['-m', 'pytest', '-vv', '--tb=long', 'tests/test_goc_pwl_stepwise.py',
          'tests/test_nyr_ndcpwlf.py', 'tests/test_lera_memory_guard.py',
          'tests/test_prover_fuzz_soundness.py'], 1500),
    ]
    for index, variant in enumerate(['reference', 'normalize']):
        jobs.append((f'coupling-{variant}', variant, cores[index + 2],
                     [str(root / 'experiment/scripts/ms58_coupling_check.py'),
                      '--output', str(destination / f'coupling-{variant}.json')], 420))
    pending = [pool.submit(run, *job) for job in jobs]
    rows += [future.result() for future in pending]

coupling = {}
for variant in ['reference', 'normalize']:
    path = destination / f'coupling-{variant}.json'
    if path.is_file():
        coupling[variant] = json.loads(path.read_bytes())
comparison = None
if len(coupling) == 2:
    def signature(report):
        return [(row['instance'], row['ils_before_cost'], row['ils_before_routes'])
                for row in report['cases']]
    comparison = {'same_core_binary': coupling['reference']['core_sha256'] == coupling['normalize']['core_sha256'],
                  'same_ils_outputs': signature(coupling['reference']) == signature(coupling['normalize'])}
summary = {**plan, 'results': rows, 'coupling_comparison': comparison,
           'all_processes_passed': all(row['returncode'] == 0 and not row['outer_timeout'] for row in rows),
           'random_fuzz_sweep_enabled': os.environ.get('KAYROS_FUZZ') == '1'}
write_json(destination / 'complete.json', summary)
print(json.dumps(summary), flush=True)
PY
