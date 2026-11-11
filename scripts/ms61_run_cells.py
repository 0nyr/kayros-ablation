#!/usr/bin/env python3
"""ms61 lean development-cell runner. Run it ON A G5K COMPUTE NODE, never on a frontend.

Usage (normally started by ms61_launch.sh):
  python3 ms61_run_cells.py --plan PLAN.json --out OUT_DIR --jobs N [--shard I/K | --shard dynamic] [--grace-s 10] [--stop-launching-after UTC_ISO]

The plan is a JSON object {"defaults": {...}, "cells": [...]} or a plain list of cells.
Each cell has: id, kind ("kayros" or "lera"), instance (e.g. "C104"), n (default 100), time_limit_s, memory_mb (default 16384), rep (default 1) and an optional arm label.
Kayros cells also take venv, mode ("cold", "full" or "ils"), backend ("cplex" by default, or "highs" for cold cells), prefix_s (full only), seed (full and ils, required; cold cells may record "seed": null only) and env (extra string environment variables, e.g. KAYROS_BPC_DROPBACK_LEVEL).
Worker arms: cold+cplex -> kayros-bpca-cplex, cold+highs -> kayros-bpca-highs, full -> kayros-full-cplex (CPLEX only), ils -> kayros-heuristics (standalone ILS for the whole time limit).
The backend is checked against the venv build-info (LERA_LP_BACKEND) before launch, and the worker checks _lera.LP_BACKEND again at the exact phase.
Lera cells run the pinned f2 binary exactly as ms58's lera-minimal arm (BP-CUTS, cut limit 500).

With --shard I/K, host I of K runs the cells with plan index % K == I (the historical behaviour).
With --shard dynamic (used by ms61_remote_launch.sh), every runner walks the whole plan in order and claims the next cell nobody holds, so hosts stay balanced whatever the cell durations and host count.

Each cell runs on its own physical core (one hardware thread per physical core, the physical core of CPU 0 is left to the runner).
The clock starts directly before process creation and the solver receives deadline = start + time_limit_s.
The runner sends SIGKILL to the whole process group at deadline + grace when the solver did not return by itself.
Outputs go to OUT/cells/<id>/: stderr.log, events.jsonl, native.json (when produced) and cell.json (written last, atomically).
A cell with a cell.json is skipped on resume. An unfinished cell directory from a dead runner on this host is moved aside to <id>.stale-<timestamp>.
An unfinished directory claimed by another host is moved aside only when the OAR job recorded in its claim.json is no longer running (checked with oarstat), so a resume in a new job with other hosts picks it up.
With --stop-launching-after T (UTC ISO), a lane starts no new cell after T: the remaining cells stay pending for a later resume.
Choose T = job end - longest time limit - grace - margin (ms61_remote_launch.sh derives it from the OAR job), so that no started cell is cut by the walltime.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

HOME = Path.home()
DEFAULT_INPUTS = HOME / 'ms56-ablation/inputs/benchmarks/TDVRPTW/Dabia2013'
DEFAULT_LERA_BINARY = HOME / 'ms58-bpca/upstream-minimal-build/bp'
DEFAULT_LERA_SOURCE = HOME / 'ms56-phase2/upstream/source'
# Pinned f2 minimal-reference executable used by ms58 (LERA_BINARY in ms58_dabia_screen.py).
MS58_F2_BINARY_SHA256 = '59ce565d28ada373f5ae59a13691e64e4f9976e6ed79e4a48808d84fb1110783'
SCRIPT_DIR = Path(__file__).resolve().parent
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._=+-]*')
ENV_PATTERN = re.compile(r'[A-Z][A-Z0-9_]*')
FINAL_STATUSES = ('completed', 'deadline', 'failed')
PRINT_LOCK = threading.Lock()
ORPHAN_AGE_S = 900


def now():
    return datetime.now(timezone.utc).isoformat()


def log(message):
    with PRINT_LOCK:
        print(f'[{now()}] {message}', flush=True)


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(f'{path.name}.tmp-{os.getpid()}-{threading.get_ident()}')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def atomic_copy(source, target):
    target = Path(target)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # Many runners on several hosts copy the same files at once over NFS: the temporary name carries host and pid.
    temporary = target.with_name(f'{target.name}.tmp-{socket.gethostname().split(".")[0]}-{os.getpid()}')
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def refuse_frontend():
    host = socket.gethostname().split('.')[0]
    if host.startswith(('fnancy', 'flyon', 'frennes', 'frontend')):
        sys.exit(f'Refusing to run on frontend {host}')
    if os.environ.get('MS61_LOCAL_TEST') == '1':
        return host
    if not re.match(r'(gros|grvingt|grappe|grdix|paradoxe)-\d+$', host):
        sys.exit(f'Refusing to run on {host}: ms61_run_cells.py must run on a reserved compute node')
    return host


def physical_lanes():
    """One CPU per physical core within the process affinity, excluding the physical core of CPU 0."""
    allowed = sorted(os.sched_getaffinity(0))
    groups = {}
    for cpu in allowed:
        base = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = (int((base / 'physical_package_id').read_text()), int((base / 'core_id').read_text()))
        groups.setdefault(key, []).append(cpu)
    ordered = sorted(groups.items(), key=lambda item: min(item[1]))
    runner_group = next((g for g in ordered if 0 in g[1]), ordered[0])
    lanes = [{'cpu': min(cpus), 'physical_package_id': key[0], 'core_id': key[1], 'siblings': cpus}
             for key, cpus in ordered if (key, cpus) != runner_group]
    return lanes, {'cpus': runner_group[1], 'physical_package_id': runner_group[0][0], 'core_id': runner_group[0][1]}


def load_plan(path):
    raw = json.loads(Path(path).read_bytes())
    defaults, cells = ({}, raw) if isinstance(raw, list) else (raw.get('defaults', {}), raw['cells'])
    result, seen = [], set()
    for index, entry in enumerate(cells):
        cell = {'n': 100, 'memory_mb': 16384, 'rep': 1, 'env': {}, **defaults, **entry}
        cell['env'] = {**defaults.get('env', {}), **entry.get('env', {})}
        cid = cell.get('id')
        if not isinstance(cid, str) or not ID_PATTERN.fullmatch(cid) or cid in seen:
            raise ValueError(f'Cell {index}: id must be a unique simple string, got {cid!r}')
        seen.add(cid)
        if cell.get('kind') not in ('kayros', 'lera'):
            raise ValueError(f'{cid}: kind must be kayros or lera')
        if not isinstance(cell.get('instance'), str) or not re.fullmatch(r'[A-Z]+\d+', cell['instance']):
            raise ValueError(f'{cid}: instance must look like C104')
        if cell['n'] not in (25, 50, 100):
            raise ValueError(f'{cid}: n must be 25, 50 or 100')
        if not isinstance(cell.get('time_limit_s'), (int, float)) or cell['time_limit_s'] <= 0:
            raise ValueError(f'{cid}: positive time_limit_s required')
        if not isinstance(cell['env'], dict) or any(not ENV_PATTERN.fullmatch(k) or not isinstance(v, str) for k, v in cell['env'].items()):
            raise ValueError(f'{cid}: env must map UPPER_CASE names to strings')
        if cell['kind'] == 'kayros':
            if cell.get('mode') not in ('cold', 'full', 'ils'):
                raise ValueError(f'{cid}: kayros mode must be cold, full or ils')
            if not cell.get('venv'):
                raise ValueError(f'{cid}: kayros cells need venv')
            cell.setdefault('backend', 'cplex')
            if cell['backend'] not in ('cplex', 'highs'):
                raise ValueError(f'{cid}: backend must be cplex or highs')
            if cell['backend'] == 'highs' and cell['mode'] != 'cold':
                raise ValueError(f'{cid}: only cold cells can use the highs backend (the full arm is CPLEX by design)')
            seed = cell.get('seed')
            if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
                raise ValueError(f'{cid}: seed must be an integer or null')
            if cell['mode'] == 'full' and (cell.get('prefix_s') is None or seed is None):
                raise ValueError(f'{cid}: full mode needs prefix_s and seed')
            if cell['mode'] == 'full' and not (isinstance(cell['prefix_s'], (int, float)) and 0 < cell['prefix_s'] < cell['time_limit_s']):
                raise ValueError(f'{cid}: prefix_s must lie strictly inside the time limit')
            if cell['mode'] == 'ils' and seed is None:
                raise ValueError(f'{cid}: ils mode needs a seed')
            if cell['mode'] in ('cold', 'ils') and cell.get('prefix_s') is not None:
                raise ValueError(f'{cid}: {cell["mode"]} mode takes no prefix_s')
            if cell['mode'] == 'cold' and seed is not None:
                raise ValueError(f'{cid}: cold exact runs have no random seed (record seed null)')
        elif cell['env']:
            raise ValueError(f'{cid}: lera cells take no env (the f2 control runs unmodified)')
        cell.setdefault('arm', default_arm(cell))
        result.append(cell)
    return result


def default_arm(cell):
    if cell['kind'] == 'lera':
        return 'lera-f2'
    name = Path(str(cell['venv']).rstrip('/')).name
    label = f"kayros-{cell['mode']}" + (f"{cell['prefix_s']:g}s" if cell['mode'] == 'full' else '') + f'-{name}'
    if cell.get('backend', 'cplex') != 'cplex':
        label += f"-{cell['backend']}"
    if cell['env']:
        label += '-' + '-'.join(f'{k.removeprefix("KAYROS_").lower()}={v}' for k, v in sorted(cell['env'].items()))
    return label


class Runtime:
    """Caches per-venv and per-binary identities once per runner."""

    def __init__(self):
        self.cache, self.lock = {}, threading.Lock()

    def kayros(self, venv):
        venv = Path(venv)
        with self.lock:
            if str(venv) not in self.cache:
                packages = sorted(venv.glob('lib/python3*/site-packages/kayros'))
                if len(packages) != 1:
                    raise FileNotFoundError(f'No unique kayros package under {venv}')
                record = {'venv': str(venv), 'python': str(venv / 'bin/python'),
                          'lera_so_sha256': {p.name: sha256(p) for p in sorted(packages[0].glob('_lera*.so'))},
                          'core_so_sha256': {p.name: sha256(p) for p in sorted(packages[0].glob('_core*.so'))}}
                info = Path(str(venv).rstrip('/') + '.build-info.json')
                record['build_info'] = json.loads(info.read_bytes()) if info.exists() else None
                self.cache[str(venv)] = record
            return self.cache[str(venv)]

    def lera(self, binary, source):
        key = f'lera:{binary}:{source}'
        with self.lock:
            if key not in self.cache:
                digest = sha256(binary)
                self.cache[key] = {'binary': str(binary), 'binary_sha256': digest,
                                   'binary_matches_ms58_f2': digest == MS58_F2_BINARY_SHA256,
                                   'source_root': str(source),
                                   'experiment_sha256': sha256(Path(source) / 'experiments/bp.json')}
            return self.cache[key]


def instance_paths(inputs, cell):
    path = Path(inputs) / f"n={cell['n']}" / f"{cell['instance']}.vrp.json"
    data = json.loads(path.read_bytes())
    atf = path.parent / data['td']['atf_path']
    return path, atf


def solver_status(native):
    if not isinstance(native, dict):
        return None
    if isinstance(native.get('exact'), dict):
        return native['exact'].get('exact_log', {}).get('status')
    if isinstance(native.get('native_result'), dict):
        return native['native_result'].get('Exact', {}).get('status')
    return None


def worker_arm(cell):
    if cell['mode'] == 'ils':
        return 'kayros-heuristics'
    if cell['mode'] == 'full':
        return 'kayros-full-cplex'
    return 'kayros-bpca-highs' if cell.get('backend') == 'highs' else 'kayros-bpca-cplex'


def oar_job_running(job):
    """True, False, or None when OAR cannot be queried (then nothing is reclaimed)."""
    if not job or not str(job).isdigit():
        return None
    try:
        result = subprocess.run(['oarstat', '-s', '-j', str(job)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    state = result.stdout.strip().rsplit(':', 1)[-1].strip()
    return state in ('Running', 'Launching', 'toLaunch', 'Finishing', 'Suspended', 'Resuming')


def claim(cell_dir, host, runner_id, rerun_failed, job_cache=None, quiet=False):
    """Return True when this runner owns a fresh cell directory.

    The claim is the atomic mkdir of the cell directory (atomic on the NFS server as well), followed by claim.json.
    In dynamic sharding every runner walks the whole plan, so losing a race or meeting a live claim of another host is normal (quiet=True).
    """
    done = cell_dir / 'cell.json'
    if done.exists():
        status = json.loads(done.read_bytes()).get('status')
        if not (rerun_failed and status == 'failed'):
            return False
        try:
            cell_dir.rename(cell_dir.with_name(f'{cell_dir.name}.failed-{int(time.time())}-{host}'))
        except OSError:
            return False  # Another runner moved it first and owns the rerun.
    elif cell_dir.exists():
        owner_file = cell_dir / 'claim.json'
        owner = json.loads(owner_file.read_bytes()) if owner_file.exists() else {}
        if not owner_file.exists() and time.time() - cell_dir.stat().st_mtime > ORPHAN_AGE_S and not any(cell_dir.iterdir()):
            # An empty directory without claim.json for a long time is a lost claim (for example a retried NFSv3 mkdir).
            stale = cell_dir.with_name(f'{cell_dir.name}.orphan-{int(time.time())}-{host}')
            try:
                cell_dir.rename(stale)
            except OSError:
                return False
            log(f'Moved empty unclaimed {cell_dir.name} aside to {stale.name}')
        elif owner.get('host') != host:
            job = owner.get('oar_job_id')
            current = os.environ.get('OAR_JOB_ID')
            if job_cache is not None and job not in job_cache:
                job_cache[job] = oar_job_running(job)
            running = job_cache.get(job) if job_cache is not None else None
            if job and job != current and running is False:
                stale = cell_dir.with_name(f'{cell_dir.name}.stale-{int(time.time())}')
                try:
                    cell_dir.rename(stale)
                except OSError as error:
                    log(f'SKIP {cell_dir.name}: could not move aside the directory of finished job {job}: {error}')
                    return False
                log(f'Moved unfinished {cell_dir.name} of host {owner.get("host")!r} (job {job}, no longer running) aside to {stale.name}')
            else:
                if not (quiet and (job == current or not owner)):  # A claim being written right now is normal.
                    log(f'SKIP {cell_dir.name}: unfinished directory claimed by host {owner.get("host")!r} in job {job!r} '
                        f'(running={running}); move it aside by hand if that runner is dead')
                return False
        else:
            # The per-host flock guarantees no other live runner on this host, so the directory is stale.
            stale = cell_dir.with_name(f'{cell_dir.name}.stale-{int(time.time())}')
            cell_dir.rename(stale)
            log(f'Moved unfinished {cell_dir.name} aside to {stale.name}')
    try:
        cell_dir.mkdir(parents=True)
    except FileExistsError:
        return False
    atomic_json(cell_dir / 'claim.json', {'host': host, 'pid': os.getpid(), 'runner_id': runner_id, 'claimed_utc': now(),
                                          'oar_job_id': os.environ.get('OAR_JOB_ID')})
    return True


def run_cell(cell, lane, args, host, runtime, runner_id):
    out = Path(args.out)
    cell_dir = out / 'cells' / cell['id']
    if not claim(cell_dir, host, runner_id, args.rerun_failed, args.job_cache, quiet=args.dynamic):
        return None
    record = {'cell': cell, 'host': host, 'core': lane['cpu'], 'core_siblings': lane['siblings'],
              'runner_id': runner_id, 'grace_s': args.grace_s, 'status': None, 'reason': None}
    try:
        instance, atf = instance_paths(args.inputs, cell)
        record['inputs'] = {'instance_path': str(instance), 'instance_sha256': sha256(instance),
                            'atf_path': str(atf), 'atf_sha256': sha256(atf)}
        native_path = cell_dir / 'native.json'
        memory = str(cell['memory_mb'])
        if cell['kind'] == 'kayros':
            identity = runtime.kayros(cell['venv'])
            built = ((identity.get('build_info') or {}).get('cmake_defines') or {}).get('LERA_LP_BACKEND')
            if built is not None and built.lower() != cell['backend']:
                raise RuntimeError(f"cell backend {cell['backend']} but venv {cell['venv']} was built with LERA_LP_BACKEND={built}")
            arm = worker_arm(cell)
            record['worker_arm'] = arm
            command = [identity['python'], str(SCRIPT_DIR / 'ms61_kayros_worker.py'), '--instance', str(instance),
                       '--arm', arm, '--memory-mb', memory, '--native-result', str(native_path)]
            if cell['mode'] in ('full', 'ils'):
                # Always explicit: the worker's own default seed (42) must never stand in for a trial seed.
                command += ['--seed', str(cell['seed'])]
            if cell['mode'] == 'full':
                command += ['--prefix-seconds', str(cell['prefix_s'])]
        else:
            identity = runtime.lera(Path(os.path.expanduser(cell.get('lera_binary') or str(args.lera_binary))).resolve(), Path(args.lera_source).resolve())
            command = [args.lera_python, str(SCRIPT_DIR / 'ms61_lera_worker.py'), '--instance', str(instance),
                       '--memory-mb', memory, '--native-result', str(native_path),
                       '--binary', identity['binary'], '--source-root', identity['source_root']]
        record['runtime'] = identity
        command = ['taskset', '-c', str(lane['cpu'])] + command
        environment = {k: v for k, v in os.environ.items() if not k.startswith(('KAYROS_', 'PROBE_', 'UPSTREAM_'))}
        environment.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                           NUMEXPR_NUM_THREADS='1', PYTHONUNBUFFERED='1', PROBE_CPU_AFFINITY=str(lane['cpu']))
        environment.update(cell['env'])
        record['command'] = command
        record['solver_environment'] = {k: v for k, v in sorted(environment.items())
                                        if k.startswith(('KAYROS_', 'OMP_', 'OPENBLAS_', 'MKL_', 'NUMEXPR_', 'PROBE_CPU'))}
        limit_ns = int(cell['time_limit_s'] * 1e9)
        with (cell_dir / 'events.jsonl').open('xb') as events, (cell_dir / 'stderr.log').open('xb') as stderr:
            record['start_utc'] = now()
            start_ns = time.monotonic_ns()
            deadline_ns = start_ns + limit_ns
            environment.update(PROBE_START_MONOTONIC_NS=str(start_ns), PROBE_DEADLINE_MONOTONIC_NS=str(deadline_ns))
            process = subprocess.Popen(command, cwd=cell_dir, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=events, stderr=stderr, start_new_session=True)
            log(f'START {cell["id"]} core={lane["cpu"]} pid={process.pid} limit={cell["time_limit_s"]}s')
            kill_ns = deadline_ns + int(args.grace_s * 1e9)
            killed, wait_status, usage = False, None, None
            while True:
                pid, wait_status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    break
                if time.monotonic_ns() >= kill_ns and not killed:
                    killed = True
                    record['kill_elapsed_s'] = (time.monotonic_ns() - start_ns) / 1e9
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                time.sleep(0.01)
            end_ns = time.monotonic_ns()
            process.returncode = os.waitstatus_to_exitcode(wait_status)
            try:
                os.killpg(process.pid, signal.SIGKILL)  # Remove descendants that outlived the leader.
            except ProcessLookupError:
                pass
        record.update(end_utc=now(), elapsed_s=(end_ns - start_ns) / 1e9, returncode=process.returncode,
                      killed_after_grace=killed, worker_max_rss_kib=usage.ru_maxrss if usage else None)
        native = json.loads(native_path.read_bytes()) if native_path.exists() else None
        events_list = []
        for line in (cell_dir / 'events.jsonl').read_text(errors='replace').splitlines():
            try:
                events_list.append(json.loads(line))
            except ValueError:
                pass
        closures = [e for e in events_list if e.get('event') == 'closure']
        record['closure_s'] = closures[0]['elapsed_s'] if closures else None
        record['solver_status'] = solver_status(native)
        record['native_present'] = native is not None
        stderr_text = (cell_dir / 'stderr.log').read_text(errors='replace')
        if killed:
            status, reason = 'deadline', 'killed by runner at deadline + grace'
        elif cell['kind'] == 'kayros' and cell['mode'] == 'ils' and process.returncode == 0 and native is not None:
            # The standalone ILS always runs to its budget: its normal end is the horizon, never a closure.
            status, reason = 'deadline', 'ILS ran to its time limit'
        elif process.returncode != 0:
            status, reason = 'failed', f'returncode {process.returncode}'
        elif native is None:
            status, reason = 'failed', 'exit 0 without native.json'
        elif 'Memory' in str(record['solver_status']) or 'std::bad_alloc' in stderr_text:
            status, reason = 'failed', f'memory: solver status {record["solver_status"]}'
        elif 'TimeLimit' in str(record['solver_status']) or (not closures and record['elapsed_s'] >= cell['time_limit_s']):
            status, reason = 'deadline', f'solver status {record["solver_status"]}'
        else:
            status, reason = 'completed', None
        record.update(status=status, reason=reason)
    except Exception as error:
        record.update(status='failed', reason=f'runner exception {type(error).__name__}: {error}')
    atomic_json(cell_dir / 'cell.json', record)
    log(f'END {cell["id"]} status={record["status"]} elapsed={record.get("elapsed_s")} closure={record.get("closure_s")} reason={record.get("reason")}')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, required=True)
    parser.add_argument('--shard', default='0/1', help="I/K: run plan cells with index %% K == I, or 'dynamic': every runner walks the whole plan in order and claims the next free cell")
    parser.add_argument('--grace-s', type=float, default=10.0)
    parser.add_argument('--inputs', type=Path, default=DEFAULT_INPUTS)
    parser.add_argument('--lera-binary', type=Path, default=DEFAULT_LERA_BINARY)
    parser.add_argument('--lera-source', type=Path, default=DEFAULT_LERA_SOURCE)
    parser.add_argument('--lera-python', default=sys.executable)
    parser.add_argument('--rerun-failed', action='store_true')
    parser.add_argument('--stop-launching-after', help='UTC ISO time after which no new cell is started (cells left for a later resume)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    host = refuse_frontend()
    stop_epoch = None
    if args.stop_launching_after:
        stamp = datetime.fromisoformat(args.stop_launching_after.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            sys.exit('--stop-launching-after needs an explicit UTC offset, e.g. 2026-11-11T18:00:00Z')
        stop_epoch = stamp.timestamp()
    args.job_cache = {}
    args.dynamic = args.shard == 'dynamic'
    if args.dynamic:
        cells = load_plan(args.plan)
    else:
        index, count = (int(x) for x in args.shard.split('/'))
        cells = [c for i, c in enumerate(load_plan(args.plan)) if i % count == index]
    lanes, runner_core = physical_lanes()
    if args.jobs < 1 or args.jobs > len(lanes):
        sys.exit(f'--jobs {args.jobs} exceeds the {len(lanes)} free physical cores on {host}')
    lanes = lanes[:args.jobs]
    available = next(int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))
    needed = max((c['memory_mb'] for c in cells), default=0) * args.jobs * 1024
    if needed > available:
        log(f'WARNING: {args.jobs} lanes x max memory_mb need {needed} KiB, only {available} KiB available')
    out = Path(args.out).resolve()
    args.out = out
    (out / 'cells').mkdir(parents=True, exist_ok=True)
    (out / 'runners').mkdir(exist_ok=True)
    for cell in cells:  # Self-contained inputs for the local summary.
        instance, atf = instance_paths(args.inputs, cell)
        target = out / 'inputs/Dabia2013' / f"n={cell['n']}"
        atomic_copy(instance, target / instance.name)
        atomic_copy(atf, target / Path(json.loads(instance.read_bytes())['td']['atf_path']))
    if args.dry_run:
        for i, cell in enumerate(cells):
            print(cell['id'], cell['arm'], 'lane', lanes[i % len(lanes)]['cpu'])
        return 0
    lock_file = (out / 'runners' / f'{host}.lock').open('w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f'Another ms61 runner holds {out}/runners/{host}.lock')
    os.sched_setaffinity(0, set(runner_core['cpus']))
    runner_id = f'{host}-{os.getpid()}-{int(time.time())}'
    atomic_json(out / 'runners' / f'{host}.json', {
        'runner_id': runner_id, 'pid': os.getpid(), 'host': host, 'started_utc': now(), 'argv': sys.argv,
        'shard': args.shard, 'jobs': args.jobs, 'lanes': lanes, 'runner_core': runner_core,
        'cells': [c['id'] for c in cells], 'oar_job_id': os.environ.get('OAR_JOB_ID'),
        'script_sha256': {p.name: sha256(p) for p in sorted(SCRIPT_DIR.glob('ms61_*.py'))},
        'mem_available_kib': available, 'python': sys.executable})
    log(f'{host}: {len(cells)} cells on lanes {[l["cpu"] for l in lanes]} (runner on cpus {runner_core["cpus"]})')
    runtime = Runtime()
    pending = queue.Queue()
    for cell in cells:
        pending.put(cell)

    held = []

    def lane_loop(lane):
        results = []
        while True:
            try:
                cell = pending.get_nowait()
            except queue.Empty:
                return results
            if stop_epoch is not None and time.time() > stop_epoch and not (out / 'cells' / cell['id'] / 'cell.json').exists():
                held.append(cell['id'])
                log(f'HOLD {cell["id"]}: launch window closed at {args.stop_launching_after}, left for a later resume')
                continue
            results.append(run_cell(cell, lane, args, host, runtime, runner_id))

    with ThreadPoolExecutor(max_workers=len(lanes)) as pool:
        records = [r for rs in pool.map(lane_loop, lanes) for r in rs if r is not None]
    counts = {s: sum(r['status'] == s for r in records) for s in FINAL_STATUSES}
    atomic_json(out / 'runners' / f'{host}.done.json', {'runner_id': runner_id, 'finished_utc': now(), 'ran': len(records),
                                                        'status_counts': counts, 'held_for_resume': sorted(held),
                                                        'stop_launching_after': args.stop_launching_after})
    log(f'{host}: done, ran {len(records)} cells, {counts}, held {len(held)} for a later resume')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
