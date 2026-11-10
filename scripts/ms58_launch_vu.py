"""Run four independently archived modes of the confirmed Vu counterexample."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys


assert os.environ.get('OAR_JOB_ID') == '7013762'
root = Path.home() / 'ms58-bpca'
output = root / 'resume-20261110/vu-counterexample'
output.mkdir(parents=True, exist_ok=False)
cores, seen = [], set()
for cpu in sorted(os.sched_getaffinity(0)):
    topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
    key = tuple((topology / name).read_text().strip() for name in ['physical_package_id', 'core_id'])
    if key not in seen:
        seen.add(key)
        cores.append(cpu)
assert len(cores) >= 15
instance = Path.home() / 'kayros-sweeps/MAMUT-routing/benchmarks/TDVRPTW/Vu2020/n=59/Vu-A2-pB-d98-w100.vrp.json'
launcher = ('import os,resource,sys; os.sched_setaffinity(0,{int(sys.argv[1])}); '
            'resource.setrlimit(resource.RLIMIT_AS,(16*1024**3,16*1024**3)); '
            'os.execv(sys.argv[2],sys.argv[2:])')
tasks = []
for index, (name, switches) in enumerate([
        ('cold-cuts', []), ('warm-cuts', ['--warm']),
        ('cold-no-cuts', ['--cut-limit', '0']), ('warm-no-cuts', ['--warm', '--cut-limit', '0'])]):
    command = [sys.executable, '-c', launcher, str(cores[11 + index]),
               str(root / 'venv-reference/bin/python'),
               str(root / 'experiment/scripts/ms58_vu_witness_probe.py'),
               '--instance', str(instance), '--witness', str(instance).replace('.vrp.json', '.bks.Duration.json'),
               '--output', str(output / name), '--source-id', '0787925', '--horizon', '120', *switches]
    tasks.append({'name': name, 'cpu': cores[11 + index], 'command': command})
(output / 'schedule.json').write_text(json.dumps(tasks, indent=2) + '\n')


def run(task):
    with (output / f"{task['name']}.log").open('x') as stream:
        process = subprocess.run(task['command'], stdout=stream, stderr=subprocess.STDOUT,
                                 env=dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1'))
    row = {**task, 'returncode': process.returncode}
    (output / f"{task['name']}.result.json").write_text(json.dumps(row, indent=2) + '\n')
    return row


with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(run, tasks))
(output / 'complete.json').write_text(json.dumps(results, indent=2) + '\n')
