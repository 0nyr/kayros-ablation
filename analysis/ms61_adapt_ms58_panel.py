"""Test adapter: rewrite an ms58 five-arm wave (older format) as an ms61 run directory for ms61_final_analysis.py.

Usage:
  python analysis/ms61_adapt_ms58_panel.py outputs/ms58/dabia-20261110/gros-five-arm-1h-v1 TARGET_DIR
  python analysis/ms61_final_analysis.py TARGET_DIR --inputs-root <Dabia2013 root> --expected-lera-sha256 59ce565d...

It only copies: plan.json from wave.json, and per scheduled task cells/<id>/{cell.json, events.jsonl, native.json, stderr.log}
from the ms58 row, identity, result and native files. It exists so that the full-arm, HiGHS and warm-start code paths of the
final analysis see real archived data. It is not part of the campaign pipeline.
"""
import json
from pathlib import Path
import shutil
import sys

ARMS = {'lera-minimal': ('lera-exact', 'lera', None, None),
        'kayros-cold-cplex': ('kayros-bpca-cplex', 'kayros', 'cold', 'cplex'),
        'kayros-cold-highs': ('kayros-bpca-highs', 'kayros', 'cold', 'highs'),
        'kayros-full-cplex-prefix15s': ('kayros-full-cplex', 'kayros', 'full', 'cplex'),
        'kayros-full-cplex-prefix5s': ('kayros-full-cplex-5s', 'kayros', 'full', 'cplex')}
F2_SHA256 = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'


def main():
    wave_dir, target = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    if target.exists():
        sys.exit(f'{target} exists')
    wave = json.loads((wave_dir / 'wave.json').read_bytes())
    cells = []
    for task in wave['scheduled_cells']:
        arm, kind, mode, backend = ARMS[task['arm_id']]
        cid = f"{task['instance_name']}--{arm}--t{task['replicate']}"
        spec = {'id': cid, 'arm': arm, 'kind': kind, 'instance': task['instance_name'], 'n': task['num_customers'],
                'rep': task['replicate'], 'trial': task['replicate'], 'time_limit_s': task['horizon_s'],
                'memory_mb': task['memory_mb'], 'seed': task['seed']}
        if kind == 'kayros':
            spec.update(mode=mode, backend=backend, venv=task['worker_python'].rsplit('/bin/', 1)[0], env={})
            if mode == 'full':
                spec['prefix_s'] = task['prefix_s']
        cells.append(spec)
        row_path = wave_dir / 'rows' / f"{task['task_id']}.json"
        if not row_path.exists():
            continue
        row = json.loads(row_path.read_bytes())
        if not row.get('run_id'):
            continue
        source = wave_dir / 'cells' / row['run_id']
        result = json.loads((source / 'result.json').read_bytes())
        identity = json.loads((source / 'identity.json').read_bytes())
        directory = target / 'cells' / cid
        directory.mkdir(parents=True)
        shutil.copyfile(source / 'events.jsonl', directory / 'events.jsonl')
        shutil.copyfile(source / 'stderr.log', directory / 'stderr.log')
        native = (result.get('validation') or {}).get('native_result') or {}
        if native.get('relative_path') and (source / native['relative_path']).exists():
            shutil.copyfile(source / native['relative_path'], directory / 'native.json')
        status = row['status'] if row['status'] in ('completed', 'deadline') else 'failed'
        command = identity['command']
        record = {'cell': spec, 'host': task['host'].split('.')[0], 'status': status, 'reason': row.get('reason'),
                  'elapsed_s': result.get('solver_elapsed_s'), 'returncode': result.get('returncode'),
                  'command': command, 'inputs': {'instance_sha256': task['instance_sha256'], 'atf_sha256': task['atf_sha256']},
                  'runtime': {'binary_sha256': F2_SHA256} if kind == 'lera' else {},
                  'adapted_from_ms58': {'task_id': task['task_id'], 'run_id': row['run_id']}}
        if kind == 'kayros':
            record['worker_arm'] = command[command.index('--arm') + 1]
        (directory / 'cell.json').write_text(json.dumps(record, indent=1) + '\n')
    (target / 'plan.json').write_text(json.dumps({'design': {'name': 'ms58-five-arm-adapted', 'expected_lera_binary_sha256': F2_SHA256},
                                                  'defaults': {}, 'cells': cells}, indent=1) + '\n')
    print(f'Wrote {len(cells)} plan cells to {target}')


if __name__ == '__main__':
    main()
