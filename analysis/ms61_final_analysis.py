"""Validate and analyse an ms61 final-campaign run (five arms, all scheduled cells in every denominator).

Usage:
  numeric summary (needs the pinned checker; runs locally in the Nix dev shell or on a compute node with
  ~/ms56-ablation/experiment/.venv/bin/python):
    python analysis/ms61_final_analysis.py OUT_DIR [--workers 16] [--inputs-root DIR] [--no-figures]
  figures only, from the summary JSON (needs matplotlib only, no checker and no cell files):
    python analysis/ms61_final_analysis.py OUT_DIR --figures-only [--summary FILE]

Outputs go to OUT_DIR/analysis/ (or --analysis-dir): final-summary.json, results.md, per-instance.md and figures/.

Validation reuses analysis/ms61_summary.py (load_cell, consistency): every candidate and final route set is re-checked with
the pinned checker (mamut-routing-lib 0.11.0, identity from scripts/checker_contract.py), native costs are compared with the
checked costs, Kayros closures need cg_unresolved_final_pricing == 0 and checker_infeasible_columns == 0, Lera closures need
zero unbranched-fractional and RMP-infeasible counts, and per instance every claimed optimum and every lower bound (final and
streamed) must not exceed any checked feasible cost of that instance (all arms and trials, plus the frozen reference), and all
closures must agree. Violations print SOUNDNESS ALERT and are counted. Design-conformance checks (seed, backend, prefix,
environment, warm start, Lera binary identity) print DESIGN ALERT and are counted separately. A cell with any alert of either
kind gets no closure credit (its checked incumbents still count for the primal metrics).

Metrics (docs/protocol.md, configs/main-analysis.json), over the common horizon H measured from process launch, with equal
instance weights and equal trial weights within an instance, and every scheduled cell in the denominator (a cell without
cell.json counts as no incumbent, no bound and no closure, and the report is marked provisional):
  - reported computational-closure attainment F(t) and the restricted mean closure time (closure time capped at H),
  - squeezed gap (z - r) / (z + r), 1 before the first checked incumbent, its mean curve and Lambda (step integral / H),
  - raw gap (z - r) / r and the attainment of the frozen goals 0 %, 1 % and 5 % over time,
  - primal-dual residual (U - L) / U with U the best checked incumbent and L the best streamed global lower bound so far,
    residual 1 while either is missing (the missing-pair convention), clipped at 0, and the bound availability fraction,
  - a per-instance status matrix (closed, feasible unclosed, no incumbent, failed, memory, rejected claim, pending).
A closure counts only when the cell completed normally, its first closure event lies within H, and no alert touches it.
The untimed native final route set is checked but never placed on the time axis.
The mean squeezed-gap, 1 % goal, coverage and closure curves are cross-checked against ms58's implementation
(analysis/ms58_dabia_plots.py: curves) and Lambda against analysis/pilot_summary.py: score_trace whenever those import.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import textwrap
import time
import types

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for path in (HERE, REPO / 'scripts'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
import ms61_summary as base  # noqa: E402  (standard library only at import time)

REFERENCE_MANIFEST_SHA256 = 'ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'
F3_BINARY_SHA256 = '04073bef66d50273d018df9381e2a86b7a6cd744a68951b83fc60d04952e04f2'
GOALS = (0.0, 0.01, 0.05)
# Arm ids of the ms55 design (docs: phd-thesis-workspace/reports/plans/2026-11-09-ms55-reverse-ablation-design.md).
ARM_ORDER = ['lera-exact', 'kayros-bpca-cplex', 'kayros-bpca-highs', 'kayros-heuristics', 'kayros-full-cplex']
LABELS = {'lera-exact': 'Lera-Romero (f3), CPLEX', 'kayros-bpca-cplex': 'Kayros BPC cold, CPLEX',
          'kayros-bpca-highs': 'Kayros BPC cold, HiGHS', 'kayros-heuristics': 'Kayros ILS only',
          'kayros-full-cplex': 'Kayros full (ILS prefix + BPC), CPLEX'}
SHORT = {'lera-exact': 'Lera f3', 'kayros-bpca-cplex': 'BPC CPLEX', 'kayros-bpca-highs': 'BPC HiGHS',
         'kayros-heuristics': 'ILS', 'kayros-full-cplex': 'Full'}
THESIS_STYLE = Path(os.environ.get('MS61_THESIS_STYLE', '/home/onyr/code/phd/phd-thesis-workspace/src/thesis_style.py'))
CHECK_TOL = 1e-9


# ----------------------------------------------------------------------------------------------------------------------
# Plan and roles
# ----------------------------------------------------------------------------------------------------------------------
def role(cell):
    if cell['kind'] == 'lera':
        return 'lera'
    return {'cold': 'cold', 'full': 'full', 'ils': 'ils'}[cell.get('mode', 'cold')]


def load_plan(out):
    raw = json.loads((out / 'plan.json').read_bytes())
    design = raw.get('design', {}) if isinstance(raw, dict) else {}
    defaults, cells = ({}, raw) if isinstance(raw, list) else (raw.get('defaults', {}), raw['cells'])
    merged = []
    for entry in cells:
        cell = {'n': 100, 'memory_mb': 16384, 'rep': 1, **defaults, **entry}
        cell['env'] = {**defaults.get('env', {}), **entry.get('env', {})} if cell['kind'] == 'kayros' else {}
        if cell['kind'] == 'kayros':
            cell.setdefault('backend', 'cplex')
            cell.setdefault('mode', 'cold')
        cell.setdefault('arm', cell['id'])
        merged.append(cell)
    return raw, design, merged


def arm_sort_key(arm):
    return (ARM_ORDER.index(arm), arm) if arm in ARM_ORDER else (len(ARM_ORDER), arm)


# ----------------------------------------------------------------------------------------------------------------------
# Per-cell validation (worker processes, one instance per task so that each process loads one TD instance at a time)
# ----------------------------------------------------------------------------------------------------------------------
_CHECKER = None


def _checker():
    global _CHECKER
    if _CHECKER is None:
        _CHECKER = base.Checker()
    return _CHECKER


def read_events(directory):
    events = []
    path = directory / 'events.jsonl'
    if path.exists():
        for line in path.read_text(errors='replace').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return events


def conformance(spec, record, events, native, expected_lera_sha256):
    """Design-conformance issues of one finished cell, and the evidence fields worth reporting."""
    issues, evidence = [], {}
    r = role(spec)
    phases = [e for e in events if e.get('event') == 'phase']
    if spec['kind'] == 'lera':
        runtime = record.get('runtime') or {}
        evidence['lera_binary_sha256'] = runtime.get('binary_sha256')
        if expected_lera_sha256 and runtime.get('binary_sha256') != expected_lera_sha256:
            issues.append(f"Lera binary sha256 {runtime.get('binary_sha256')!r} is not the expected {expected_lera_sha256}")
        launches = [e for e in phases if e.get('phase') == 'native_launch']
        if launches and not all(v is True for e in launches for v in (e.get('metadata_equivalence') or {}).values()):
            issues.append('Lera native/materialized metadata equivalence failed')
        backends = [e.get('backend') for e in phases if e.get('phase') == 'backend_settings' and 'backend' in e]
        evidence['lp_backend'] = backends[0] if backends else None
        threads = [(e.get('effective') or {}).get('threads') for e in phases if e.get('phase') == 'backend_settings']
        if any(t not in (None, 1) for t in threads):
            issues.append(f'Lera backend threads {threads}')
        return issues, evidence
    expected_worker = {'ils': 'kayros-heuristics', 'full': 'kayros-full-cplex',
                       'cold': 'kayros-bpca-highs' if spec.get('backend') == 'highs' else 'kayros-bpca-cplex'}[r]
    if record.get('worker_arm') not in (None, expected_worker):
        issues.append(f"worker arm {record.get('worker_arm')} is not {expected_worker}")
    command = record.get('command') or []
    if command and '--arm' in command and command[command.index('--arm') + 1] != expected_worker:
        issues.append(f"command arm {command[command.index('--arm') + 1]} is not {expected_worker}")
    loading = next((e for e in phases if e.get('phase') == 'loading'), None)
    if loading is not None:
        recorded = loading.get('kayros_environment') or {}
        evidence['kayros_version'] = loading.get('kayros_version')
        if recorded != spec.get('env', {}):
            issues.append(f'recorded KAYROS_* environment {recorded} differs from the plan {spec.get("env", {})}')
    built = (((record.get('runtime') or {}).get('build_info') or {}).get('cmake_defines') or {}).get('LERA_LP_BACKEND')
    if built is not None and built.lower() != spec.get('backend', 'cplex'):
        issues.append(f'venv built with LERA_LP_BACKEND={built}, plan backend {spec.get("backend")}')
    ils = [e for e in phases if e.get('phase') == 'ils']
    exact = [e for e in phases if e.get('phase') == 'exact']
    evidence['lp_backend'] = exact[0].get('lp_backend') if exact else None
    evidence['warm_start_event'] = exact[0].get('warm_start') if exact else None
    exact_native = native.get('exact') if isinstance(native, dict) and isinstance(native.get('exact'), dict) else None
    evidence['warm_start_native'] = exact_native.get('warm_start') if exact_native else None
    if r in ('ils', 'full'):
        seeds = [e.get('seed') for e in ils]
        evidence['ils_seed'] = seeds[0] if seeds else None
        if record.get('status') != 'failed' and not ils:
            issues.append('no ILS phase event')
        if any(s != spec.get('seed') for s in seeds):
            issues.append(f"ILS seed {seeds} differs from the plan seed {spec.get('seed')}")
    elif ils:
        issues.append('cold cell ran an ILS phase')
    if r == 'ils':
        if exact:
            issues.append('ILS cell entered an exact phase')
        evidence['ils_candidates'] = sum(e.get('event') == 'candidate' and e.get('phase') == 'ils' for e in events)
    else:
        expected_backend = 'HiGHS' if spec.get('backend') == 'highs' else 'CPLEX'
        if any(e.get('lp_backend') != expected_backend for e in exact):
            issues.append(f"exact LP backend {[e.get('lp_backend') for e in exact]} is not {expected_backend}")
    if r == 'cold' and any(e.get('warm_start') for e in exact):
        issues.append('cold cell was warm-started')
    if r == 'full':
        prefix = [e for e in phases if e.get('phase') == 'fixed_prefix_complete']
        evidence['prefix_complete_s'] = prefix[0].get('elapsed_s') if prefix else None
        evidence['warm_witness'] = prefix[0].get('warm_witness') if prefix else None
        if prefix and prefix[0].get('prefix_seconds') != spec.get('prefix_s'):
            issues.append(f"prefix {prefix[0].get('prefix_seconds')} differs from the plan {spec.get('prefix_s')}")
        if prefix and prefix[0]['elapsed_s'] < spec['prefix_s'] - 1e-6:
            issues.append(f"prefix ended at {prefix[0]['elapsed_s']} s, before {spec['prefix_s']} s")
        if exact:
            evidence['exact_start_s'] = exact[0].get('elapsed_s')
            if exact[0]['elapsed_s'] < spec['prefix_s'] - 1e-6:
                issues.append(f"exact phase started at {exact[0]['elapsed_s']} s, inside the {spec['prefix_s']} s prefix")
            if prefix and bool(exact[0].get('warm_start')) != bool(prefix[0].get('warm_witness')):
                issues.append('exact warm_start flag differs from the prefix witness flag')
            if exact_native is not None and evidence['warm_start_native'] is not None \
                    and bool(evidence['warm_start_native']) != bool(exact[0].get('warm_start')):
                issues.append('native warm_start differs from the exact phase event')
        handoff = native.get('handoff_witness') if isinstance(native, dict) else None
        evidence['handoff_native_cost'] = handoff.get('native_cost') if isinstance(handoff, dict) else None
        late = [e for e in events if e.get('event') == 'candidate' and e.get('phase') == 'ils'
                and e.get('elapsed_s', 0) > spec['prefix_s'] + 1e-6]
        if late:
            issues.append(f'{len(late)} ILS candidates emitted as candidates after the prefix')
    return issues, evidence


def analyze_instance(task):
    """Validate the finished cells of one instance (runs in a worker process)."""
    out, inputs_root, specs, expected_lera = task
    out = Path(out)
    checker = _checker()
    results = []
    for spec in specs:
        directory = out / 'cells' / spec['id']
        if not (directory / 'cell.json').exists():
            results.append({'id': spec['id'], 'finished': False})
            continue
        started = time.monotonic()
        cell = base.load_cell(directory, out, checker, inputs_root)
        record = json.loads((directory / 'cell.json').read_bytes())
        native = json.loads((directory / 'native.json').read_bytes()) if (directory / 'native.json').exists() else None
        events = read_events(directory)
        issues, evidence = conformance(spec, record, events, native, expected_lera)
        # The runner records the plan cell it ran: it must be the scheduled one.
        ran = record.get('cell') or {}
        for key in ('instance', 'kind', 'mode', 'seed', 'prefix_s', 'time_limit_s', 'backend', 'venv', 'arm'):
            if key in spec and ran.get(key, spec.get(key) if key == 'backend' else None) != spec.get(key):
                issues.append(f'cell.json {key}={ran.get(key)!r} differs from the plan {spec.get(key)!r}')
        cell.pop('root_table', None)
        cell.update(finished=True, design_alerts=issues, evidence=evidence, reason=record.get('reason'),
                    killed_after_grace=record.get('killed_after_grace'), worker_max_rss_kib=record.get('worker_max_rss_kib'),
                    validation_seconds=time.monotonic() - started)
        results.append(cell)
    checker.cache.clear()
    return results


# ----------------------------------------------------------------------------------------------------------------------
# Traces, scores and curves (pure functions on JSON data, also used by --figures-only)
# ----------------------------------------------------------------------------------------------------------------------
def envelope_of(checks, horizon):
    """Best-so-far envelope of the timed, checker-valid candidates within the horizon: [[t, cost], ...]."""
    points = sorted((c['elapsed_s'], c['checked_cost']) for c in checks
                    if c.get('valid') and c.get('elapsed_s') is not None and base.finite(c.get('checked_cost'))
                    and 0 <= c['elapsed_s'] <= horizon)
    envelope, best = [], None
    for t, cost in points:
        if best is None or cost < best:
            best = cost
            envelope.append([t, cost])
    return envelope


def bound_envelope(trace, horizon):
    points = sorted((t, lb) for t, lb in trace if t is not None and 0 <= t <= horizon and base.finite(lb))
    envelope, best = [], None
    for t, lb in points:
        if best is None or lb > best:
            best = lb
            envelope.append([t, lb])
    return envelope


def score(envelope, reference, horizon):
    """Lambda, final squeezed gap, final raw gap and goal times (same convention as pilot_summary.score_trace)."""
    area, previous_t, previous_gap = 0.0, 0.0, 1.0
    goal_times = {str(goal): None for goal in GOALS}
    best = None
    for t, cost in envelope:
        area += (t - previous_t) * previous_gap
        previous_t, previous_gap, best = t, (cost - reference) / (cost + reference), cost
        for goal in GOALS:
            if goal_times[str(goal)] is None and (cost - reference) / reference <= goal:
                goal_times[str(goal)] = t
    area += (horizon - previous_t) * previous_gap
    return {'lambda': area / horizon, 'final_squeezed_gap': previous_gap, 'final_checked_cost': best,
            'final_raw_gap': None if best is None else (best - reference) / reference,
            'first_incumbent_s': envelope[0][0] if envelope else None, 'goal_attainment_s': goal_times}


def residual(cost, lb):
    if cost is None or lb is None or cost <= 0:
        return 1.0
    return min(1.0, max(0.0, (cost - lb) / cost))


def curves(cells, horizon):
    """Step curves (value holds from each time to the next) for one arm, all scheduled cells, equal instance weights."""
    trials = Counter(c['instance'] for c in cells)
    weights = [1.0 / (len(trials) * trials[c['instance']]) for c in cells]
    changes = defaultdict(list)
    for index, cell in enumerate(cells):
        for t, cost in cell['envelope']:
            changes[t].append((index, 'cost', cost))
        for t, lb in cell['bound_envelope']:
            changes[t].append((index, 'lb', lb))
        if cell['closure_s'] is not None:
            changes[cell['closure_s']].append((index, 'closure', True))
    times = sorted({0.0, float(horizon)} | set(changes))
    n = len(cells)
    cost, lb, closed = [None] * n, [None] * n, [False] * n
    refs = [c['reference_cost'] for c in cells]

    def contributions(i):
        z, r = cost[i], refs[i]
        present = z is not None
        raw = (z - r) / r if present else None
        pair = present and lb[i] is not None
        return (weights[i] * ((z - r) / (z + r) if present else 1.0),
                [weights[i] * (present and raw <= goal) for goal in GOALS],
                weights[i] * present, weights[i] * closed[i], weights[i] * residual(z, lb[i]), weights[i] * pair,
                weights[i] * (raw if present else 0.0), weights[i] * (lb[i] is not None))

    state = [contributions(i) for i in range(n)]
    sums = [math.fsum(s[0] for s in state), [math.fsum(s[1][g] for s in state) for g in range(len(GOALS))],
            math.fsum(s[2] for s in state), math.fsum(s[3] for s in state), math.fsum(s[4] for s in state),
            math.fsum(s[5] for s in state), math.fsum(s[6] for s in state), math.fsum(s[7] for s in state)]
    covered = sum(c is not None for c in cost)
    data = {'elapsed_s': [], 'mean_squeezed_gap': [], 'checked_incumbent_fraction': [], 'closure_fraction': [],
            'mean_residual': [], 'bound_pair_fraction': [], 'bound_available_fraction': [], 'mean_raw_gap_percent': [],
            **{f'goal_{goal:g}_fraction': [] for goal in GOALS}}
    for step, t in enumerate(times):
        touched = set()
        for index, kind, value in changes.get(t, []):
            if kind == 'cost':
                if cost[index] is None:
                    covered += 1
                cost[index] = value
            elif kind == 'lb':
                lb[index] = value
            else:
                closed[index] = True
            touched.add(index)
        for index in touched:
            old, new = state[index], contributions(index)
            sums[0] += new[0] - old[0]
            sums[1] = [a + b - c for a, b, c in zip(sums[1], new[1], old[1])]
            for k in range(2, 8):
                sums[k] += new[k] - old[k]
            state[index] = new
        if step % 2000 == 0 or step == len(times) - 1:  # Refresh the running sums exactly now and then.
            sums = [math.fsum(s[0] for s in state), [math.fsum(s[1][g] for s in state) for g in range(len(GOALS))],
                    math.fsum(s[2] for s in state), math.fsum(s[3] for s in state), math.fsum(s[4] for s in state),
                    math.fsum(s[5] for s in state), math.fsum(s[6] for s in state), math.fsum(s[7] for s in state)]
        data['elapsed_s'].append(t)
        data['mean_squeezed_gap'].append(sums[0])
        for g, goal in enumerate(GOALS):
            data[f'goal_{goal:g}_fraction'].append(sums[1][g])
        data['checked_incumbent_fraction'].append(sums[2])
        data['closure_fraction'].append(sums[3])
        data['mean_residual'].append(sums[4])
        data['bound_pair_fraction'].append(sums[5])
        data['bound_available_fraction'].append(sums[7])
        data['mean_raw_gap_percent'].append(100 * sums[6] if covered == n else None)
    return data


def value_at(curve, field, t):
    index = bisect_right(curve['elapsed_s'], t) - 1
    return curve[field][max(index, 0)]


def crosscheck_ms58(cells, horizon, mine):
    """Compare with ms58_dabia_plots.curves on the same cohort. Returns 'passed', 'unavailable: ...' or 'FAILED: ...'."""
    try:
        if 'ms58_dabia_summary' not in sys.modules:
            # ms58_dabia_plots only needs ARM_IDS from its summary module, whose own imports are heavy.
            sys.modules['ms58_dabia_summary'] = types.SimpleNamespace(ARM_IDS=[])
        from ms58_dabia_plots import curves as ms58_curves
    except Exception as error:  # noqa: BLE001  (an optional cross-check)
        return f'unavailable: {type(error).__name__}: {error}'
    adapted = [{'horizon_s': horizon, 'instance_key': c['instance'], 'reference_cost': c['reference_cost'],
                'reported_closure_s': c['closure_s'],
                'envelope': [{'elapsed_s': t, 'checked_cost': z} for t, z in c['envelope']]} for c in cells]
    theirs = ms58_curves(adapted)
    pairs = {'mean_squeezed_gap': 'mean_squeezed_gap', 'goal_1pct_fraction': 'goal_0.01_fraction',
             'checked_incumbent_fraction': 'checked_incumbent_fraction', 'reported_closure_fraction': 'closure_fraction'}
    worst = 0.0
    for t_index, t in enumerate(theirs['elapsed_s']):
        for field, own in pairs.items():
            worst = max(worst, abs(theirs[field][t_index] - value_at(mine, own, t)))
    return 'passed' if worst <= CHECK_TOL else f'FAILED: max abs difference {worst:.3e}'


def crosscheck_score(cells, horizon):
    try:
        from pilot_summary import score_trace
    except Exception as error:  # noqa: BLE001
        return f'unavailable: {type(error).__name__}: {error}'
    worst = 0.0
    for c in cells:
        theirs = score_trace([{'elapsed_s': t, 'checked_cost': z} for t, z in c['envelope']], c['reference_cost'], horizon)
        worst = max(worst, abs(theirs['lambda'] - c['lambda']), abs(theirs['final_squeezed_gap'] - c['final_squeezed_gap']))
    return 'passed' if worst <= CHECK_TOL else f'FAILED: max abs difference {worst:.3e}'


# ----------------------------------------------------------------------------------------------------------------------
# Numeric summary
# ----------------------------------------------------------------------------------------------------------------------
def frozen_references():
    raw = base.REFERENCE_MANIFEST.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != REFERENCE_MANIFEST_SHA256:
        raise SystemExit(f'Frozen reference manifest digest {digest} is not the pinned {REFERENCE_MANIFEST_SHA256}')
    manifest = json.loads(raw)
    return {row['instance_name']: row for row in manifest['instances']}, [row['instance_name'] for row in manifest['instances']]


def mean_by_instance(rows, field):
    groups = defaultdict(list)
    for row in rows:
        groups[row['instance']].append(row[field])
    return statistics.fmean(statistics.fmean(v) for v in groups.values()) if groups else None


def summarize(out, args):
    raw_plan, design, specs = load_plan(out)
    horizons = {float(c['time_limit_s']) for c in specs}
    if args.horizon is None and len(horizons) != 1:
        raise SystemExit(f'Cells have several time limits {sorted(horizons)}: pass --horizon')
    horizon = float(args.horizon or horizons.pop())
    refs, manifest_order = frozen_references()
    expected_lera = args.expected_lera_sha256 or design.get('expected_lera_binary_sha256') or F3_BINARY_SHA256
    by_instance = defaultdict(list)
    for spec in specs:
        by_instance[spec['instance']].append(spec)
    tasks = [(str(out), str(args.inputs_root) if args.inputs_root else None, group, expected_lera)
             for _, group in sorted(by_instance.items())]
    started = time.monotonic()
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            loaded = [cell for result in pool.map(analyze_instance, tasks) for cell in result]
    else:
        loaded = [cell for task in tasks for cell in analyze_instance(task)]
    validation_seconds = time.monotonic() - started
    checker_identity = _checker().identity if args.workers <= 1 else base.Checker().identity
    loaded_by_id = {c['id']: c for c in loaded}
    finished = [loaded_by_id[s['id']] for s in specs if loaded_by_id[s['id']]['finished']]
    report = base.consistency(finished, refs)
    soundness, design_alerts = [], []
    contradicted = set()
    for key, block in report.items():
        _, name = key.split('/')
        ceiling = block['feasible_ceiling']
        for alert in block['alerts']:
            soundness.append(f'[{key}] {alert}')
        group = [c for c in finished if c['instance'] == name and c['n'] == int(key.split('/')[0][2:])]
        closed_values = [c['final_value'] for c in group if c['closed'] and base.finite(c['final_value'])]
        disagree = len(closed_values) > 1 and any(base.differ(closed_values[0], v) for v in closed_values[1:])
        for c in group:
            if c['closed'] and (disagree or (ceiling is not None and base.finite(c['final_value']) and base.above(c['final_value'], ceiling))):
                contradicted.add(c['id'])
            if base.finite(c['final_lb']) and ceiling is not None and base.above(c['final_lb'], ceiling):
                contradicted.add(c['id'])
            for t, lb in c['bound_trace']:
                if ceiling is not None and base.above(lb, ceiling):
                    soundness.append(f"[{key}] {c['id']}: streamed lower bound {lb!r} at {t} s exceeds feasible {ceiling!r} ({block['ceiling_source']})")
                    contradicted.add(c['id'])
                    break
    for c in finished:
        soundness.extend(f"[{c['id']}] {a}" for a in c['alerts'])
        design_alerts.extend(f"[{c['id']}] {a}" for a in c['design_alerts'])
    cells = []
    for spec in specs:
        loaded_cell = loaded_by_id[spec['id']]
        ref = refs.get(spec['instance']) if spec['n'] == 100 else None
        if ref is None:
            raise SystemExit(f"No frozen reference for {spec['instance']} (n={spec['n']})")
        reference = ref['reference_cost']
        row = {'id': spec['id'], 'arm': spec['arm'], 'role': role(spec), 'backend': spec.get('backend'),
               'instance': spec['instance'], 'trial': spec.get('trial', spec.get('rep')), 'seed': spec.get('seed'),
               'reference_cost': reference, 'finished': loaded_cell['finished']}
        if not loaded_cell['finished']:
            row.update(status='pending', envelope=[], bound_envelope=[], closure_s=None, claimed_closure_s=None,
                       closure_rejected=False, alerts=[], design_alerts=[])
        else:
            c = loaded_cell
            claimed = c['closure_s'] if c['closed'] else None
            rejected_reasons = []
            if claimed is not None:
                if claimed > horizon:
                    rejected_reasons.append(f'closure at {claimed:.3f} s after the {horizon:g} s horizon')
                if c['alerts']:
                    rejected_reasons.append('soundness alert on the cell')
                if c['id'] in contradicted:
                    rejected_reasons.append('contradicted by the instance consistency checks')
                if c['design_alerts']:
                    rejected_reasons.append('design alert on the cell')
            accepted = claimed if claimed is not None and not rejected_reasons else None
            bounds = bound_envelope(c['bound_trace'], horizon) if not (c['alerts'] or c['id'] in contradicted) else []
            row.update(status=c['status'], reason=c.get('reason'), host=c.get('host'), elapsed_s=c.get('elapsed_s'),
                       envelope=envelope_of(c['checks'], horizon), bound_envelope=bounds, closure_s=accepted,
                       claimed_closure_s=claimed, closure_rejected=bool(rejected_reasons), rejected_reasons=rejected_reasons,
                       final_value=c.get('final_value'), final_lb=c.get('final_lb'), best_checked_cost=c.get('best_checked_cost'),
                       checked_route_sets=c.get('checked_route_sets'), invalid_route_sets=sum(not k['valid'] for k in c['checks']),
                       alerts=c['alerts'], design_alerts=c['design_alerts'], evidence=c['evidence'],
                       nodes_closed=(c.get('work') or {}).get('nodes_closed'), worker_max_rss_kib=c.get('worker_max_rss_kib'),
                       memory_failure=bool(c.get('allocation_failure')) or 'memory' in str(c.get('reason') or '').lower(), killed_after_grace=c.get('killed_after_grace'),
                       validation_seconds=c.get('validation_seconds'))
        row.update(score(row['envelope'], reference, horizon))
        row['capped_closure_s'] = row['closure_s'] if row['closure_s'] is not None else horizon
        row['final_residual'] = residual(row['final_checked_cost'], row['bound_envelope'][-1][1] if row['bound_envelope'] else None)
        row['state'], row['markers'] = instance_state(row)
        cells.append(row)
    arms = sorted({c['arm'] for c in cells}, key=arm_sort_key)
    groups, crosschecks = {}, {}
    for arm in arms:
        rows = [c for c in cells if c['arm'] == arm]
        curve = curves(rows, horizon)
        crosschecks[arm] = {'ms58_curves': crosscheck_ms58(rows, horizon, curve), 'pilot_score_trace': crosscheck_score(rows, horizon)}
        exact_capable = rows[0]['role'] != 'ils'
        feasible = [r for r in rows if r['final_checked_cost'] is not None]
        restricted = mean_by_instance(rows, 'capped_closure_s')
        area = math.fsum((b - a) * (1 - f) for a, b, f in zip(curve['elapsed_s'], curve['elapsed_s'][1:], curve['closure_fraction']))
        if abs(area - restricted) > 1e-6 * horizon:
            crosschecks[arm]['restricted_mean_vs_curve_area'] = f'FAILED: {restricted} vs {area}'
        else:
            crosschecks[arm]['restricted_mean_vs_curve_area'] = 'passed'
        groups[arm] = {
            'arm': arm, 'label': LABELS.get(arm, arm), 'role': rows[0]['role'], 'exact_capable': exact_capable,
            'scheduled_cells': len(rows), 'instances': len({r['instance'] for r in rows}),
            'finished_cells': sum(r['finished'] for r in rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
            'state_counts': dict(Counter(r['state'] for r in rows)),
            'marker_counts': dict(Counter(m for r in rows for m in r['markers'])),
            'accepted_closures': sum(r['closure_s'] is not None for r in rows),
            'claimed_closures': sum(r['claimed_closure_s'] is not None for r in rows),
            'rejected_closures': sum(r['closure_rejected'] for r in rows),
            # Claims whose only defect is the checker-infeasible priced column guard (ms58 qualified these instead of rejecting).
            'guard_only_rejected_closures': sum(r['closure_rejected'] and r['rejected_reasons'] == ['soundness alert on the cell'] and r['alerts']
                                                and all(a.startswith('Kayros closure with checker_infeasible_columns=') for a in r['alerts']) for r in rows),
            'restricted_mean_closure_s': restricted if exact_capable else None,
            'mean_lambda': mean_by_instance(rows, 'lambda'), 'mean_final_squeezed_gap': mean_by_instance(rows, 'final_squeezed_gap'),
            'feasible_runs': len(feasible),
            'final_goal_fraction': {f'{goal:g}': curve[f'goal_{goal:g}_fraction'][-1] for goal in GOALS},
            'final_goal_counts': {f'{goal:g}': sum(r['goal_attainment_s'][str(goal)] is not None for r in rows) for goal in GOALS},
            'mean_final_raw_gap_feasible_only': statistics.fmean(r['final_raw_gap'] for r in feasible) if feasible else None,
            'mean_final_raw_gap_population': f'{len(feasible)} feasible runs of {len(rows)} (plain mean over runs, conditional population)',
            'reference_attainment_count': sum(r['goal_attainment_s']['0.0'] is not None for r in rows),
            'mean_final_residual': curve['mean_residual'][-1], 'final_bound_pair_fraction': curve['bound_pair_fraction'][-1],
            'final_bound_available_fraction': curve['bound_available_fraction'][-1],
            'max_timeout_overshoot_s': max([r['elapsed_s'] - horizon for r in rows if r.get('elapsed_s') is not None] or [None], key=lambda v: -math.inf if v is None else v),
            'integrity_cells': sum(bool(r['alerts'] or r['design_alerts'] or r['closure_rejected']) for r in rows),
            'memory_failures': sum(r.get('memory_failure', False) for r in rows),
            'failed_cells': sum(r['status'] == 'failed' for r in rows),
            'curves': curve}
    instances = [name for name in manifest_order if name in {c['instance'] for c in cells}]
    pending = sum(not c['finished'] for c in cells)
    crosscheck_failures = sum(v.startswith('FAILED') for arm in crosschecks.values() for v in arm.values())
    return {
        'schema_version': 1, 'run': str(out), 'design': design, 'horizon_s': horizon,
        'plan_sha256': hashlib.sha256((out / 'plan.json').read_bytes()).hexdigest(),
        'analysis_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'summary_helper_sha256': hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest(),
        'checker': checker_identity, 'reference_manifest_sha256': REFERENCE_MANIFEST_SHA256,
        'expected_lera_binary_sha256': expected_lera, 'goals': list(GOALS), 'rel_tol': base.REL_TOL,
        'scheduled_cells': len(cells), 'finished_cells': len(cells) - pending, 'pending_cells': pending, 'provisional': pending > 0,
        'soundness_alert_count': len(soundness), 'soundness_alerts': soundness,
        'design_alert_count': len(design_alerts), 'design_alerts': design_alerts,
        'crosschecks': crosschecks, 'crosscheck_failure_count': crosscheck_failures,
        'validation_seconds': validation_seconds, 'workers': args.workers,
        'instances': instances, 'arms': arms, 'instance_consistency': report, 'groups': groups, 'cells': cells,
        'conventions': {
            'weights': 'equal instance weights, equal trial weights within an instance, all scheduled cells in every denominator',
            'closure': 'first solver closure event within the horizon, status completed, no soundness or design alert, not contradicted',
            'squeezed_gap': '(z - r) / (z + r), 1 before the first checked incumbent',
            'raw_gap_goal': '(z - r) / r <= goal, missing incumbents attain nothing',
            'residual': '(U - L) / U with U the best checked incumbent and L the best streamed global lower bound so far; 1 when either is missing; clipped to [0, 1]; bounds of cells with soundness alerts are ignored',
            'restricted_mean_closure': 'mean of closure time capped at the horizon (area under 1 - F)',
            'untimed_native_final': 'checked but never placed on the time axis'}}


def instance_state(row):
    """ms55 status-matrix state at the horizon and the separate failure markers.

    States: proved (accepted closure), feasible (checked incumbent, no accepted closure), no_incumbent, pending (no cell.json).
    Markers: memory (memory limit), crash (failed for another reason), integrity (soundness or design alert, or a rejected claim),
    killed (killed by the runner after deadline + grace).
    """
    if not row['finished']:
        return 'pending', []
    markers = []
    if row.get('memory_failure'):
        markers.append('memory')
    elif row['status'] == 'failed':
        markers.append('crash')
    if row['alerts'] or row['design_alerts'] or row['closure_rejected']:
        markers.append('integrity')
    if row.get('killed_after_grace'):
        markers.append('killed')
    if row['closure_s'] is not None:
        return 'proved', markers
    return ('feasible' if row['final_checked_cost'] is not None else 'no_incumbent'), markers


# ----------------------------------------------------------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------------------------------------------------------
def fmt(value, digits=1, percent=False):
    if value is None:
        return '-'
    if percent:
        return f'{100 * value:.{digits}f} %'
    return f'{value:,.{digits}f}'


def write_markdown(summary, directory):
    H = summary['horizon_s']
    lines = [f"# ms61 final analysis: {Path(summary['run']).name}", '',
             f"Common horizon T = {H:,.0f} s from process launch. {summary['scheduled_cells']} scheduled runs, {summary['finished_cells']} finished, "
             f"{summary['pending_cells']} pending{' (PROVISIONAL)' if summary['provisional'] else ''}. "
             f"Soundness alerts: {summary['soundness_alert_count']}. Design alerts: {summary['design_alert_count']}. "
             f"Cross-check failures: {summary['crosscheck_failure_count']}.", '',
             'Arithmetic aggregation with equal instance weights and equal trial weights within an instance. Every scheduled run is in every denominator. '
             'A proof is a solver-reported search closure within T that passes the acceptance checks (checked witness, closure census, cross-run and reference consistency). '
             'It is computational closure under the declared conventions, not an independent certificate. '
             'The restricted mean time to proof averages the closure time capped at T, an unproved run contributing T. Lower is better.', '',
             '## Main table', '',
             '| Arm | Mean Λ | Mean final squeezed gap | Feasible runs | Reference attained | Accepted closures | Restricted mean time to proof (s) |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm in summary['arms']:
        g = summary['groups'][arm]
        n = g['scheduled_cells']
        if g['exact_capable']:
            closed = f"{g['accepted_closures']} / {n}" + (f" ({g['rejected_closures']} rejected, {g['guard_only_rejected_closures']} of them only for the checker-infeasible column guard)" if g['rejected_closures'] else '')
            rmtp = fmt(g['restricted_mean_closure_s'])
        else:
            closed, rmtp = 'no proof capability', 'no proof capability'
        lines.append(f"| `{arm}` | {fmt(g['mean_lambda'], 5)} | {fmt(g['mean_final_squeezed_gap'], 5)} | {g['feasible_runs']} / {n} | "
                     f"{g['reference_attainment_count']} / {n} | {closed} | {rmtp} |")
    lines += ['', '## Goals, residual and failures', '',
              '| Arm | Goal 0 % | Goal 1 % | Goal 5 % | Mean final raw gap, feasible runs only | Mean final (U - L) / U | Bound available | Memory | Crash | Integrity | Max timeout overshoot (s) |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm in summary['arms']:
        g = summary['groups'][arm]
        goals = [fmt(g['final_goal_fraction'][f'{x:g}'], 1, True) for x in GOALS]
        raw = f"{fmt(g['mean_final_raw_gap_feasible_only'], 3, True)} (n = {g['feasible_runs']})"
        m = g['marker_counts']
        resid = fmt(g['mean_final_residual'], 5) if g['exact_capable'] else 'n/a'
        avail = fmt(g['final_bound_available_fraction'], 1, True) if g['exact_capable'] else 'n/a'
        lines.append(f"| `{arm}` | {' | '.join(goals)} | {raw} | {resid} | {avail} | {m.get('memory', 0)} | {m.get('crash', 0)} | "
                     f"{m.get('integrity', 0)} | {fmt(g['max_timeout_overshoot_s'], 2)} |")
    lines += ['', 'Goal columns: weighted fraction of scheduled runs whose best checked incumbent at T has raw gap (z - r) / r at most the goal (missing incumbents never attain). '
              'Raw-gap means over feasible runs are conditional on feasibility and name their population. '
              'The residual uses U = best checked incumbent, L = best streamed global lower bound, and value 1 until both exist. Bound availability is the weighted fraction of runs with a streamed bound at T. '
              'The overshoot is the largest launch-to-exit time minus T (negative when every run ended before T).', '']
    if summary['soundness_alerts']:
        lines += ['## Soundness alerts', ''] + [f'- {a}' for a in summary['soundness_alerts']] + ['']
    if summary['design_alerts']:
        lines += ['## Design alerts', ''] + [f'- {a}' for a in summary['design_alerts']] + ['']
    lines += ['## Cross-checks', ''] + [f"- `{arm}`: " + ', '.join(f'{k} {v}' for k, v in checks.items())
                                       for arm, checks in summary['crosschecks'].items()] + ['']
    (directory / 'results.md').write_text('\n'.join(lines) + '\n')
    header = ['Instance', 'Reference'] + [f'`{a}`' for a in summary['arms']]
    rows = ['| ' + ' | '.join(header) + ' |', '|' + ' --- |' + ' ---: |' * (len(header) - 1)]
    for name in summary['instances']:
        cols = [name, fmt(next(c['reference_cost'] for c in summary['cells'] if c['instance'] == name), 2)]
        for arm in summary['arms']:
            group = [c for c in summary['cells'] if c['instance'] == name and c['arm'] == arm]
            closed = [c['closure_s'] for c in group if c['closure_s'] is not None]
            gaps = [c['final_raw_gap'] for c in group if c['final_raw_gap'] is not None]
            if summary['groups'][arm]['exact_capable']:
                text = f"{len(closed)}/{len(group)} proved" + (f" · {statistics.median(closed):,.0f} s" if closed else '')
            else:
                text = f"{sum(g <= 0 for g in gaps)}/{len(group)} at ref"
            text += f" · {fmt(statistics.fmean(gaps), 2, True)}" if gaps else ' · no incumbent'
            flags = Counter(m for c in group for m in c['markers'])
            flags.update(c['state'] for c in group if c['state'] in ('pending', 'no_incumbent'))
            if flags:
                text += ' · ' + ', '.join(f'{v} {k}' for k, v in sorted(flags.items()))
            cols.append(text)
        rows.append('| ' + ' | '.join(cols) + ' |')
    (directory / 'per-instance.md').write_text(
        f"# Per-instance results: {Path(summary['run']).name}\n\n"
        'Exact-capable arms: proved trials / scheduled trials and median closure time over proved trials. ILS arm: trials whose final incumbent reaches the frozen reference. '
        'Then the mean final raw gap over trials with an incumbent, and the counts of memory, crash, integrity, killed, pending or no-incumbent trials.\n\n'
        + '\n'.join(rows) + '\n')


# ----------------------------------------------------------------------------------------------------------------------
# Figures (matplotlib optional)
# ----------------------------------------------------------------------------------------------------------------------
def house_style():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    palette = {'blue': '#3498DB', 'amber': '#E67E22', 'green': '#27AE60', 'violet': '#9B59B6', 'red': '#E74C3C',
               'grey': '#52514E', 'grid': '#CCCCCC', 'body': '#1A1A1A', 'muted': '#555555',
               'feasiblefill': '#5CB85C', 'infeasiblefill': '#D9534F', 'rampmid': '#FFF0B4'}
    source = 'inline fallback'
    try:
        if THESIS_STYLE.exists():
            sys.path.insert(0, str(THESIS_STYLE.parent))
            import thesis_style
            thesis_style.apply(scale=1.0)  # The 11-14 pt house band on an 8 in report canvas.
            palette.update({k: thesis_style.PALETTE[k] for k in palette if k in thesis_style.PALETTE})
            source = f'thesis_style ({THESIS_STYLE})'
    except Exception as error:  # noqa: BLE001
        source = f'inline fallback ({type(error).__name__}: {error})'
    if source.startswith('inline'):
        plt.rcParams.update({'font.family': 'serif', 'mathtext.fontset': 'cm', 'text.usetex': False,
                             'font.size': 12, 'axes.titlesize': 14, 'axes.labelsize': 13, 'xtick.labelsize': 12,
                             'ytick.labelsize': 12, 'legend.fontsize': 11, 'legend.frameon': False, 'lines.linewidth': 1.9,
                             'savefig.bbox': 'tight', 'savefig.pad_inches': 0.02})
    plt.rcParams.update({'pdf.fonttype': 42, 'ps.fonttype': 42, 'axes.unicode_minus': False})
    styles = {'lera-exact': {'color': palette['grey'], 'linestyle': (0, (1.2, 1.2))},
              'kayros-bpca-cplex': {'color': palette['blue'], 'linestyle': '-'},
              'kayros-bpca-highs': {'color': palette['violet'], 'linestyle': '--'},
              'kayros-heuristics': {'color': palette['amber'], 'linestyle': '-.'},
              'kayros-full-cplex': {'color': palette['green'], 'linestyle': '-'}}
    return plt, palette, styles, source


def thin(xs, ys, limit=4000):
    """Keep a step curve's exact change points but drop repeated values, then cap the point count for plotting only."""
    keep_x, keep_y = [], []
    for x, y in zip(xs, ys):
        if keep_y and y == keep_y[-1] and x != xs[-1]:
            continue
        keep_x.append(x)
        keep_y.append(y)
    if len(keep_x) > limit:
        step = math.ceil(len(keep_x) / limit)
        idx = sorted(set(range(0, len(keep_x), step)) | {len(keep_x) - 1})
        keep_x, keep_y = [keep_x[i] for i in idx], [keep_y[i] for i in idx]
    return keep_x, keep_y


def render(summary, directory):
    plt, palette, styles, source = house_style()
    directory.mkdir(parents=True, exist_ok=True)
    H = summary['horizon_s']
    arms = summary['arms']
    fallback = [palette[k] for k in ('blue', 'amber', 'green', 'violet', 'red', 'grey')]
    style = {arm: {'linewidth': 1.9, **styles.get(arm, {'color': fallback[i % len(fallback)], 'linestyle': '-'})}
             for i, arm in enumerate(arms)}
    tag = f"{Path(summary['run']).name}, Dabia2013 n=100, T = {H:,.0f} s"
    if summary['provisional']:
        tag += f", PROVISIONAL ({summary['pending_cells']} pending)"
    written = []
    provenance = {'run': summary['run'], 'plan_sha256': summary['plan_sha256'], 'analysis_sha256': summary['analysis_sha256'],
                  'horizon_s': H, 'provisional': summary['provisional'], 'conventions': summary['conventions'],
                  'weights': 'equal instance weights, equal trial weights within an instance'}

    def save(fig, name, data):
        for extension in ('pdf', 'png'):
            path = directory / f'{name}.{extension}'
            fig.savefig(path, dpi=200, bbox_inches='tight', metadata={'CreationDate': None} if extension == 'pdf' else None)
            written.append(path.name)
        (directory / f'{name}.json').write_text(json.dumps({**provenance, 'figure': name, **data}, indent=1, allow_nan=False) + '\n')
        written.append(f'{name}.json')
        plt.close(fig)

    def line_plot(name, field, ylabel, arm_filter, note, ylim=(-0.02, 1.02)):
        series = {}
        for arm in arms:
            group = summary['groups'][arm]
            if arm_filter(group):
                series[arm] = {'label': group['label'], 'scheduled_runs': group['scheduled_cells'],
                               'elapsed_s': group['curves']['elapsed_s'], field: group['curves'][field]}
                if field == 'closure_fraction':
                    series[arm].update(accepted_closures=group['accepted_closures'], rejected_closures=group['rejected_closures'],
                                       restricted_mean_time_to_proof_s=group['restricted_mean_closure_s'])
        for logarithmic in (False, True):
            fig, ax = plt.subplots(figsize=(8.0, 5.0))
            for arm, data in series.items():
                xs, ys = thin(data['elapsed_s'], data[field])
                if logarithmic:
                    xs = [max(0.01, x) for x in xs]
                label = data['label']
                if field == 'closure_fraction':
                    label += f" ({data['accepted_closures']}/{data['scheduled_runs']})"
                ax.step(xs, [float('nan') if y is None else y for y in ys], where='post', label=label, **style[arm])
            ax.set_xlabel('Elapsed time from process launch (s)' + (', log scale' if logarithmic else ''))
            ax.set_ylabel(ylabel)
            if logarithmic:
                ax.set_xscale('log')
                ax.set_xlim(0.1, H)
            else:
                ax.set_xlim(0, H)
            if ylim:
                ax.set_ylim(*ylim)
            ax.grid(True, which='major', color=palette['grid'], linewidth=0.5, alpha=0.6)
            # The note sits above the panel, under the title, never on the curves or the legend.
            ax.set_title(tag + ('\n' + textwrap.fill(note, 95) if note else ''), fontsize=10.5, color=palette['body'])
            handles, labels = ax.get_legend_handles_labels()
            fig.tight_layout()
            fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.0), ncol=2, frameon=False)
            save(fig, name + ('-log-time' if logarithmic else ''),
                 {'field': field, 'ylabel': ylabel, 'note': note, 'step': 'post', 'log_time_display': logarithmic,
                  'integration_measure': 'uniform elapsed time (a log axis is display only)', 'series': series})

    exact = lambda g: g['exact_capable']  # noqa: E731
    no_proof = [summary['groups'][a]['label'] for a in arms if not summary['groups'][a]['exact_capable']]
    rejected = sum(summary['groups'][a]['rejected_closures'] for a in arms)
    line_plot('proof-attainment', 'closure_fraction', 'Fraction of runs with an accepted proof', exact,
              'Accepted solver search closure (checked witness, census and consistency checks), all runs in the denominator.'
              + (f' {rejected} rejected claims count as unproved.' if rejected else '')
              + (f" {', '.join(no_proof)}: no proof capability, not plotted." if no_proof else ''))
    line_plot('mean-squeezed-gap', 'mean_squeezed_gap', 'Mean squeezed gap (z - r) / (z + r)', lambda g: True,
              'Best checked incumbent z against the frozen reference r, value 1 before the first checked incumbent.', ylim=None)
    for goal in GOALS:
        line_plot(f'goal-gap-{goal * 100:g}pct', f'goal_{goal:g}_fraction',
                  f'Fraction with raw gap (z - r) / r <= {goal * 100:g} %', lambda g: True,
                  'Missing incumbents never attain a goal. Reference attainment is not proof.')
    line_plot('mean-residual', 'mean_residual', 'Mean primal-dual residual (U - L) / U', exact,
              'U best checked incumbent, L best streamed global lower bound, value 1 until both exist. Near zero is not proof.')
    line_plot('bound-availability', 'bound_available_fraction', 'Fraction of runs with a global lower bound', exact,
              'A run counts once the solver has streamed a valid global lower bound (root completed).')
    status_matrix(summary, plt, palette, save)
    return written, source


def status_matrix(summary, plt, palette, save):
    """ms55 aligned status matrix: arm rows, fixed instance columns, state proportions over the trials of each pair."""
    import matplotlib.patches as patches
    instances, arms = summary['instances'], summary['arms']
    panels = 2 if len(instances) > 30 else 1
    per_panel = math.ceil(len(instances) / panels)
    fig, axes = plt.subplots(panels, 1, figsize=(max(8.0, 0.36 * per_panel + 2.2), (0.62 * len(arms) + 1.3) * panels + 0.9), squeeze=False)
    colors = {'proved': palette['feasiblefill'], 'feasible': palette['rampmid'], 'no_incumbent': palette['infeasiblefill'], 'pending': '#FFFFFF'}
    hatches = {'proved': '', 'feasible': '..', 'no_incumbent': '//', 'pending': ''}
    marks = {'memory': 'M', 'crash': 'C', 'integrity': 'I', 'killed': 'K'}
    data = {}
    for panel in range(panels):
        ax = axes[panel][0]
        chunk = instances[panel * per_panel:(panel + 1) * per_panel]
        for row, arm in enumerate(arms):
            for col, name in enumerate(chunk):
                group = [c for c in summary['cells'] if c['instance'] == name and c['arm'] == arm]
                counts = Counter(c['state'] for c in group)
                markers = Counter(m for c in group for m in c['markers'])
                data.setdefault(arm, {})[name] = {'trials': len(group), 'states': dict(counts), 'markers': dict(markers),
                                                  'reference_attained': sum(c['goal_attainment_s']['0.0'] is not None for c in group),
                                                  'median_proof_s': statistics.median([c['closure_s'] for c in group if c['closure_s'] is not None])
                                                  if counts['proved'] else None}
                bottom = 0.0
                for state in ('proved', 'feasible', 'no_incumbent', 'pending'):
                    share = counts[state] / len(group) if group else 0
                    if share:
                        ax.add_patch(patches.Rectangle((col + 0.04, row + 0.06 + bottom * 0.88), 0.92, share * 0.88,
                                                       facecolor=colors[state], hatch=hatches[state], edgecolor='white', linewidth=0.4))
                        bottom += share
                flag = ''.join(f'{marks[k]}' for k in marks if markers[k])
                if flag:
                    ax.text(col + 0.5, row + 0.5, flag, ha='center', va='center', fontsize=8, color=palette['body'], fontweight='bold')
        ax.set_xlim(0, len(chunk))
        ax.set_ylim(len(arms), 0)
        ax.set_yticks([i + 0.5 for i in range(len(arms))])
        ax.set_yticklabels([SHORT.get(a, a) for a in arms], fontsize=11)
        ax.set_xticks([i + 0.5 for i in range(len(chunk))])
        ax.set_xticklabels(chunk, rotation=90, fontsize=9)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
    handles = [patches.Patch(facecolor=colors[k], hatch=hatches[k], edgecolor=palette['muted'], label=label)
               for k, label in (('proved', 'proved optimal (accepted closure)'), ('feasible', 'feasible, unproved'),
                                ('no_incumbent', 'no valid incumbent'))]
    fig.suptitle(f"Instance status at T = {summary['horizon_s']:,.0f} s, {Path(summary['run']).name}: "
                 'bar heights are proportions over the trials of each instance', fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(0.5, 0.03), ncol=3, frameon=False)
    fig.text(0.5, -0.03, 'Markers: M memory limit, C crash, I integrity (alert or rejected claim), K killed after the grace period.',
             ha='center', va='top', fontsize=10, color=palette['muted'])
    save(fig, 'status-matrix', {'states': ['proved', 'feasible', 'no_incumbent', 'pending'], 'markers': marks, 'cells': data,
                                'instances': instances, 'arms': arms})


# ----------------------------------------------------------------------------------------------------------------------
def print_summary(summary):
    print(f"ms61 final analysis of {summary['run']}: {summary['finished_cells']}/{summary['scheduled_cells']} cells finished"
          f"{' (PROVISIONAL)' if summary['provisional'] else ''}, horizon {summary['horizon_s']:g} s, "
          f"validation {summary['validation_seconds']:.0f} s with {summary['workers']} workers")
    print(f"checker {summary['checker']['package']} {summary['checker']['version']} {summary['checker']['commit'][:10]}")
    print(f"{'arm':<24} {'cells':>5} {'closed':>7} {'rej':>4} {'RMCT s':>8} {'Lambda':>8} {'sq fin':>8} {'g0':>6} {'g1':>6} {'g5':>6} {'resid':>7} {'fail':>4}")
    for arm in summary['arms']:
        g = summary['groups'][arm]
        f = g['final_goal_fraction']
        print(f"{arm:<24} {g['scheduled_cells']:>5} {g['accepted_closures'] if g['exact_capable'] else '-':>7} {g['rejected_closures']:>4} "
              f"{fmt(g['restricted_mean_closure_s']):>8} {g['mean_lambda']:>8.5f} {g['mean_final_squeezed_gap']:>8.5f} "
              f"{f['0']:>6.3f} {f['0.01']:>6.3f} {f['0.05']:>6.3f} {g['mean_final_residual']:>7.4f} {g['failed_cells']:>4}")
    for alert in summary['soundness_alerts']:
        print(f'SOUNDNESS ALERT {alert}')
    for alert in summary['design_alerts']:
        print(f'DESIGN ALERT {alert}')
    for arm, checks in summary['crosschecks'].items():
        for name, value in checks.items():
            if not value.startswith('passed'):
                print(f'{"ANALYSIS CROSS-CHECK " + name}: {arm}: {value}')
    print('SOUNDNESS ALERTS: ' + str(summary['soundness_alert_count']) if summary['soundness_alert_count']
          else 'Validation passed: 0 soundness alerts')
    print(f"design alerts: {summary['design_alert_count']}, cross-check failures: {summary['crosscheck_failure_count']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out', type=Path)
    parser.add_argument('--analysis-dir', type=Path, help='default OUT/analysis')
    parser.add_argument('--summary', type=Path, help='summary JSON for --figures-only (default ANALYSIS_DIR/final-summary.json)')
    parser.add_argument('--inputs-root', type=Path, help='instance root replacing OUT/inputs/Dabia2013 (hashes are still checked)')
    parser.add_argument('--workers', type=int, default=min(8, os.cpu_count() or 1))
    parser.add_argument('--horizon', type=float)
    parser.add_argument('--expected-lera-sha256', help=f'default: the plan design value, else f3 {F3_BINARY_SHA256[:12]}...')
    parser.add_argument('--no-figures', action='store_true')
    parser.add_argument('--figures-only', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    directory = (args.analysis_dir or out / 'analysis').resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = args.summary or directory / 'final-summary.json'
    if args.figures_only:
        summary = json.loads(target.read_bytes())
    else:
        summary = summarize(out, args)
        target.write_text(json.dumps(summary, indent=1, allow_nan=False) + '\n')
        write_markdown(summary, directory)
        print_summary(summary)
        print(f'Wrote {target}, {directory / "results.md"}, {directory / "per-instance.md"}')
    if not args.no_figures:
        try:
            written, source = render(summary, directory / 'figures')
            print(f"Figures ({source}): {len(written)} files in {directory / 'figures'}")
        except ImportError as error:
            print(f'Figures skipped: {error} (run --figures-only locally on the fetched summary)')
    if summary['soundness_alert_count']:
        return 1
    return 3 if summary['design_alert_count'] or summary['crosscheck_failure_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
