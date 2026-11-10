"""Run separately identified opt-in first-pricing diagnostics on an unused core."""
import json
import os
from pathlib import Path
import subprocess
import sys

from checker_contract import checker_identity, sha256
from ms58_paired_probe import candidate_manifest, frozen_existing_manifest
from probe_runner import atomic_json

root = Path.home() / 'ms58-bpca'
assert os.environ.get('OAR_JOB_ID') == '7013080'
cores = []
seen = set()
for allowed_cpu in sorted(os.sched_getaffinity(0)):
    topology = Path(f'/sys/devices/system/cpu/cpu{allowed_cpu}/topology')
    key = tuple((topology / name).read_text().strip() for name in ['physical_package_id', 'core_id'])
    if key not in seen:
        seen.add(key)
        cores.append(allowed_cpu)
cpu = cores[6]
os.sched_setaffinity(0, {cpu})
out = root / 'outputs/traces'
out.mkdir(exist_ok=False)
scripts = root / 'experiment/scripts'
checker = checker_identity()
for name in ['kayros-patches', 'upstream-patches']:
    (out / name).mkdir()
kayros = candidate_manifest(root / 'venv-diagnostics/bin/python', root / 'patches-diagnostics',
                           out / 'kayros-patches', checker, 'eb94418')
upstream = frozen_existing_manifest(root / 'archived-manifests/upstream.json', out / 'upstream-patches',
                                   scripts, root / 'experiment/patches', checker)
old = upstream['worker_environment']['UPSTREAM_PROBE_BINARY']
binary = root / 'upstream-build/bp'
del upstream['runtime_files'][old]
upstream['runtime_files'][str(binary)] = sha256(binary)
upstream['worker_environment']['UPSTREAM_PROBE_BINARY'] = str(binary)
patch = root / 'scripts/upstream-trace.patch'
upstream['source_patch_files'][str(patch)] = sha256(patch)
upstream['source_id'] = 'd32687b diagnostic derivative, not fixed control timing'
for variant, manifest in [('kayros', kayros), ('upstream', upstream)]:
    manifest['purpose'] = 'First pricing discrepancy diagnostics with instrumentation enabled'
    atomic_json(out / f'{variant}-manifest.json', manifest)
rows = []
for name in ['C104', 'R201', 'RC201']:
    for variant in ['upstream', 'kayros']:
        trace = out / f'{name}-{variant}.jsonl'
        worker_python = root / 'venv-diagnostics/bin/python' if variant == 'kayros' else Path(sys.executable)
        command = [sys.executable, str(scripts / 'run_solver_probe.py'),
                   '--manifest', str(out / f'{variant}-manifest.json'),
                   '--worker-python', str(worker_python),
                   '--instance', str(Path.home() / f'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013/n=100/{name}.vrp.json'),
                   '--output', str(out / f'{name}-{variant}-cell'),
                   '--arm', 'lera-exact' if variant == 'upstream' else 'kayros-bpca-cplex',
                   '--horizon', '60', '--memory-mb', '16384', '--cpu-core', str(cpu)]
        if variant == 'upstream':
            command += ['--worker', str(scripts / 'upstream_event_worker.py')]
        environment = dict(os.environ, KAYROS_PRICING_TRACE=str(trace))
        with (out / f'{name}-{variant}-result.json').open('x') as stdout, (out / f'{name}-{variant}.stderr').open('x') as stderr:
            p = subprocess.run(command, stdout=stdout, stderr=stderr, env=environment)
        rows.append({'instance': name, 'variant': variant, 'command': command, 'returncode': p.returncode,
                     'trace_sha256': sha256(trace) if trace.exists() else None})
        atomic_json(out / 'rows.json', rows)
    if all((out / f'{name}-{variant}.jsonl').exists() for variant in ['upstream', 'kayros']):
        subprocess.run([sys.executable, str(scripts / 'ms58_compare_pricing_traces.py'),
                        str(out / f'{name}-upstream.jsonl'), str(out / f'{name}-kayros.jsonl'),
                        '--output', str(out / f'{name}-comparison.json')], check=True)
atomic_json(out / 'complete.json', {'rows': len(rows)})
