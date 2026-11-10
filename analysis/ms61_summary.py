"""Summarize and validate an ms61 run directory after rsync back to Kenzae.

Usage (pinned checker in the workspace dev shell):
  nix develop ../nix-dev --command python analysis/ms61_summary.py outputs/ms61/<run> [--output FILE] [--slow-s 5]

For every cell it reports status, closure time, final value, final lower bound, B&B nodes and root work (CG iterations, columns, cuts, pricing calls and time per pricing level) plus the slow root iterations from the stderr table.
Validation re-checks every produced route set with mamut-routing-lib 0.11.0 (identity checked by scripts/checker_contract.py).
It then checks closures (Kayros census and checker guard) and cross-cell consistency per instance against all checked feasible costs and the frozen reference.
Any violation is printed as SOUNDNESS ALERT and recorded in summary.json (default OUT/summary.json).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
REFERENCE_MANIFEST = REPO / 'references/dabia2013-n100-v011-2026-11-09/manifest.json'
REL_TOL = 1e-6
LEVELS = ('Heuristic Cost', 'Heuristic Elementarity', 'Exact')
ROW = re.compile(r'^\s*([0-9.eE+-]+)\s+(\d+)\s+([0-9.eE+-]+)\s+(\d+)\s*$')


def sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def finite(value):
    # Lera reports a missing incumbent as a huge sentinel (about 1e51), which is treated as absent.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and abs(value) < 1e30


def above(a, b):
    """True when a exceeds b beyond the relative tolerance."""
    return a > b + REL_TOL * max(1.0, abs(b))


def differ(a, b):
    return abs(a - b) > REL_TOL * max(1.0, abs(a), abs(b))


def root_table(stderr):
    """Rows of the root column-generation table: (time, iteration, value, cols, after_cuts)."""
    rows, active, after_cuts = [], False, False
    for line in stderr.splitlines():
        if line.startswith('Processing root node.'):
            active, rows = True, []
            continue
        if not active:
            continue
        if re.match(r'^\s*time\s+#\s+value\s+#cols', line):
            continue
        if line.strip().startswith('Cuts:'):
            after_cuts = True
            continue
        match = ROW.match(line)
        if match:
            rows.append({'time': float(match[1]), 'iter': int(match[2]), 'value': float(match[3]),
                         'cols': int(match[4]), 'after_cuts': after_cuts})
            after_cuts = False
            continue
        break
    for previous, row in zip([None] + rows[:-1], rows):
        row['dt'] = row['time'] - previous['time'] if previous else row['time']
    return rows


def exact_record(native):
    """Return (exact_log, extras) for either native layout."""
    if not isinstance(native, dict):
        return None, {}
    if isinstance(native.get('exact'), dict):
        exact = native['exact']
        routes = [r.get('path', []) for r in exact.get('routes', []) or []]
        return exact.get('exact_log'), {'final_paths': routes, 'final_value': exact.get('value'),
            'closure_census': exact.get('closure_census'), 'checker_infeasible_columns': exact.get('checker_infeasible_columns'),
            'exact_pricing': exact.get('exact_pricing')}
    result = native.get('native_result')
    if isinstance(result, dict):
        best = result.get('Best solution') or {}
        routes = [r.get('path', []) for r in best.get('routes', []) or []]
        return result.get('Exact'), {'final_paths': routes, 'final_value': best.get('value'),
                                     'exact_pricing': result.get('exact_pricing')}
    return None, {}


def work(exact):
    if not isinstance(exact, dict):
        return None
    root = exact.get('root_log') or {}
    levels = {name: {'calls': 0, 'time_s': 0.0} for name in LEVELS}
    for iteration in root.get('iterations', []) or []:
        level = levels.setdefault(iteration.get('iteration_name', '?'), {'calls': 0, 'time_s': 0.0})
        level['calls'] += 1
        level['time_s'] += float(iteration.get('time') or 0.0)
    initial = exact.get('constraint_count')
    root_cuts = exact['root_constraint_count'] - initial if finite(exact.get('root_constraint_count')) and finite(initial) else None
    return {'status': exact.get('status'), 'time_s': exact.get('time'), 'best_bound': exact.get('best_bound'),
            'best_int_value': exact.get('best_int_value'), 'nodes_closed': exact.get('nodes_closed'),
            'nodes_open': exact.get('nodes_open'), 'root_time_s': exact.get('root_time'),
            'root_lp_value': exact.get('root_lp_value'), 'root_cg_iterations': root.get('iteration_count'),
            'root_columns_added': root.get('columns_added'), 'root_cuts': root_cuts, 'total_cuts': exact.get('cut_count'),
            'pricing_time_s': exact.get('pricing_time'), 'lp_time_s': exact.get('lp_time'),
            'branching_time_s': exact.get('branching_time'), 'root_pricing_levels': levels}


class Checker:
    def __init__(self):
        from checker_contract import checker_identity
        self.identity = checker_identity()
        from mamut_routing_lib.enums import ObjectiveFunction
        from mamut_routing_lib.models import BenchmarkSolution
        from mamut_routing_lib.td import load_td_instance, check_td_solution
        self.objective, self.solution, self.load, self.check = ObjectiveFunction.DURATION, BenchmarkSolution, load_td_instance, check_td_solution
        self.cache = {}

    def evaluate(self, instance_path, name, routes):
        key = str(instance_path)
        if key not in self.cache:
            self.cache[key] = self.load(Path(instance_path), verify_sha256=True, validate_complete=True)
        try:
            checked = self.check(self.cache[key], self.solution(instance_name=name, routes=routes), self.objective)
            valid = checked.is_valid()
            return {'valid': valid, 'checked_cost': checked.routing_cost if valid else None,
                    'violations': None if valid else str(checked.model_dump(mode='json'))[:400]}
        except Exception as error:
            return {'valid': False, 'checked_cost': None, 'violations': f'{type(error).__name__}: {error}'}


def load_cell(directory, out, checker):
    record = json.loads((directory / 'cell.json').read_bytes())
    spec = record['cell']
    native = json.loads((directory / 'native.json').read_bytes()) if (directory / 'native.json').exists() else None
    events = []
    if (directory / 'events.jsonl').exists():
        for line in (directory / 'events.jsonl').read_text(errors='replace').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    stderr = (directory / 'stderr.log').read_text(errors='replace') if (directory / 'stderr.log').exists() else ''
    exact, extras = exact_record(native)
    n = spec['n']
    instance = out / 'inputs/Dabia2013' / f'n={n}' / f"{spec['instance']}.vrp.json"
    alerts, notes = [], []
    if not instance.exists() or sha256(instance) != record.get('inputs', {}).get('instance_sha256'):
        alerts.append(f'local input copy {instance} missing or differs from the remote instance hash')
    checks = []
    if instance.exists():
        for event in events:
            if event.get('event') == 'candidate' and isinstance(event.get('routes'), list):
                result = checker.evaluate(instance, spec['instance'], event['routes'])
                result.update(source=f"event:{event.get('phase')}", native_cost=event.get('native_cost'), elapsed_s=event.get('elapsed_s'))
                checks.append(result)
        paths = extras.get('final_paths') or []
        if paths:
            routes = [p[1:-1] for p in paths]
            if any(p[:1] != [0] or p[-1:] != [n + 1] for p in paths):
                alerts.append('native final route numbering is not 0 ... n+1')
            result = checker.evaluate(instance, spec['instance'], routes)
            result.update(source='native_final', native_cost=extras.get('final_value'), elapsed_s=None)
            checks.append(result)
    for c in checks:
        if not c['valid']:
            alerts.append(f"invalid routes from {c['source']}: {c['violations']}")
        elif finite(c.get('native_cost')) and differ(c['native_cost'], c['checked_cost']):
            alerts.append(f"native cost {c['native_cost']!r} differs from checked {c['checked_cost']!r} ({c['source']})")
    valid_costs = [c['checked_cost'] for c in checks if c['valid']]
    closures = [e for e in events if e.get('event') == 'closure']
    bounds = [e['global_lb'] for e in events if e.get('event') in ('bound', 'closure') and finite(e.get('global_lb'))]
    measured = work(exact)
    final_lb = measured['best_bound'] if measured and finite(measured.get('best_bound')) else (bounds[-1] if bounds else None)
    final_value = extras.get('final_value') if finite(extras.get('final_value')) else (
        measured['best_int_value'] if measured and finite(measured.get('best_int_value')) else None)
    closed = bool(closures) and record.get('status') == 'completed'
    if closures and record.get('status') != 'completed':
        notes.append(f"closure event present but status {record.get('status')}")
    if closed:
        if spec['kind'] == 'kayros':
            census = extras.get('closure_census') or {}
            if census.get('cg_unresolved_final_pricing') != 0:
                alerts.append(f"Kayros closure with cg_unresolved_final_pricing={census.get('cg_unresolved_final_pricing')!r}")
            if extras.get('checker_infeasible_columns') != 0:
                alerts.append(f"Kayros closure with checker_infeasible_columns={extras.get('checker_infeasible_columns')!r}")
        else:
            pricing = extras.get('exact_pricing') or {}
            if pricing.get('unbranched_fractional') or pricing.get('restricted_master_infeasible'):
                alerts.append(f'Lera closure with exact_pricing {pricing}')
        if not finite(final_value):
            alerts.append('closure without a finite incumbent value')
        elif not valid_costs or all(differ(final_value, v) for v in valid_costs):
            alerts.append(f'closure value {final_value!r} has no matching checked witness')
    table = root_table(stderr)
    runtime = record.get('runtime') or {}
    if spec['kind'] == 'lera' and runtime.get('binary_matches_ms58_f2') is not True:
        notes.append('Lera binary hash differs from the ms58 f2 executable')
    return {'id': spec['id'], 'arm': spec.get('arm'), 'kind': spec['kind'], 'instance': spec['instance'], 'n': n,
            'rep': spec.get('rep'), 'status': record.get('status'), 'reason': record.get('reason'),
            'host': record.get('host'), 'core': record.get('core'), 'time_limit_s': spec.get('time_limit_s'),
            'elapsed_s': record.get('elapsed_s'), 'closure_s': closures[0]['elapsed_s'] if closures else None,
            'closed': closed, 'final_value': final_value, 'final_lb': final_lb, 'best_checked_cost': min(valid_costs) if valid_costs else None,
            'checked_route_sets': len(checks), 'native_present': native is not None, 'work': measured,
            'stderr_root_rows': len(table), 'stderr_root_last': table[-1] if table else None,
            'slow_root_iterations': [], 'root_table': table, 'env': spec.get('env'), 'runtime': runtime,
            'instance_sha256': record.get('inputs', {}).get('instance_sha256'), 'alerts': alerts, 'notes': notes,
            'extras': {k: v for k, v in extras.items() if k != 'final_paths'}}


def references():
    if not REFERENCE_MANIFEST.exists():
        return {}
    manifest = json.loads(REFERENCE_MANIFEST.read_bytes())
    return {row['instance_name']: row for row in manifest['instances']}


def consistency(cells, refs):
    by_instance = defaultdict(list)
    for cell in cells:
        by_instance[(cell['n'], cell['instance'])].append(cell)
    report = {}
    for (n, name), group in sorted(by_instance.items()):
        alerts = []
        feasible = [(c['best_checked_cost'], c['id']) for c in group if finite(c['best_checked_cost'])]
        reference = None
        ref = refs.get(name) if n == 100 else None
        if ref:
            reference = ref['reference_cost']
            if any(c['instance_sha256'] != ref['instance_sha256'] for c in group):
                alerts.append('instance hash differs from the frozen reference manifest')
            else:
                feasible.append((reference, 'frozen-reference'))
        ceiling = min(feasible) if feasible else None
        closed = [(c['final_value'], c['id']) for c in group if c['closed'] and finite(c['final_value'])]
        for value, cid in closed:
            if ceiling and above(value, ceiling[0]):
                alerts.append(f'{cid}: claimed optimum {value!r} exceeds feasible {ceiling[0]!r} from {ceiling[1]}')
        if closed and any(differ(closed[0][0], v) for v, _ in closed[1:]):
            alerts.append(f'closures disagree: {closed}')
        for c in group:
            if finite(c['final_lb']) and ceiling and above(c['final_lb'], ceiling[0]):
                alerts.append(f"{c['id']}: final lower bound {c['final_lb']!r} exceeds feasible {ceiling[0]!r} from {ceiling[1]}")
        note = None
        if closed and reference is not None and above(reference, closed[0][0]):
            note = f'closures improve the frozen reference {reference!r} to {closed[0][0]!r}'
        report[f'n={n}/{name}'] = {'reference_cost': reference, 'feasible_ceiling': ceiling[0] if ceiling else None,
            'ceiling_source': ceiling[1] if ceiling else None, 'closed_values': closed, 'alerts': alerts, 'note': note}
    return report


def fmt(value, digits=1):
    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.{digits}f}'
    return str(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--slow-s', type=float, default=5.0)
    args = parser.parse_args()
    out = args.out.resolve()
    checker = Checker()
    cells, unfinished = [], []
    for directory in sorted((out / 'cells').iterdir()):
        if not directory.is_dir() or '.stale-' in directory.name or '.failed-' in directory.name:
            continue
        if not (directory / 'cell.json').exists():
            unfinished.append(directory.name)
            continue
        cells.append(load_cell(directory, out, checker))
    for cell in cells:
        cell['slow_root_iterations'] = [(r['iter'], round(r['dt'], 3), r['value'], r['cols'], 'after-cuts' if r['after_cuts'] else '')
                                        for r in cell['root_table'] if r['dt'] > args.slow_s]
    report = consistency(cells, references())
    wid = max([28] + [len(c['id']) for c in cells])
    warm = max([20] + [len(c['arm'] or '') for c in cells])
    header = (f"{'id':<{wid}} {'arm':<{warm}} {'status':<9} {'closure':>8} {'elapsed':>8} {'value':>11} {'LB':>11} {'nodes':>5} "
              f"{'it':>4} {'cols':>6} {'cuts':>4} {'Hc n/t':>12} {'He n/t':>12} {'Ex n/t':>12} {'checked':>11} valid")
    total_alerts = 0
    print(f'ms61 summary of {out} ({len(cells)} finished cells, {len(unfinished)} unfinished)')
    print(f"checker {checker.identity['package']} {checker.identity['version']} {checker.identity['commit'][:10]}")
    for key, block in report.items():
        print(f"\n== {key}  reference={fmt(block['reference_cost'], 6)}  ceiling={fmt(block['feasible_ceiling'], 6)} ({block['ceiling_source']})")
        print(header)
        n, name = key.split('/')
        group = sorted((c for c in cells if f"n={c['n']}" == n and c['instance'] == name), key=lambda c: (c['arm'] or '', c['id']))
        for c in group:
            w = c['work'] or {}
            levels = w.get('root_pricing_levels', {})
            def lv(level):
                entry = levels.get(level)
                return f"{entry['calls']}/{entry['time_s']:.1f}" if entry else '-'
            closure = fmt(c['closure_s'], 2) if c['closed'] else c['status']
            print(f"{c['id']:<{wid}} {(c['arm'] or ''):<{warm}} {c['status']:<9} {closure:>8} {fmt(c['elapsed_s'], 2):>8} "
                  f"{fmt(c['final_value'], 2):>11} {fmt(c['final_lb'], 2):>11} {fmt(w.get('nodes_closed')):>5} "
                  f"{fmt(w.get('root_cg_iterations')):>4} {fmt(w.get('root_columns_added')):>6} {fmt(w.get('root_cuts')):>4} "
                  f"{lv('Heuristic Cost'):>12} {lv('Heuristic Elementarity'):>12} {lv('Exact'):>12} "
                  f"{fmt(c['best_checked_cost'], 2):>11} {'ok' if not c['alerts'] else 'ALERT'}")
            if not c['native_present']:
                print(f"    native.json absent: values from events and the stderr table (root rows {c['stderr_root_rows']}, last {c['stderr_root_last']})")
            if c['slow_root_iterations']:
                print(f"    root iterations > {args.slow_s:g} s (iter, dt, LP value, #cols): {c['slow_root_iterations']}")
            for note in c['notes']:
                print(f'    note: {note}')
            for alert in c['alerts']:
                total_alerts += 1
                print(f"    SOUNDNESS ALERT [{c['id']}]: {alert}")
        for alert in block['alerts']:
            total_alerts += 1
            print(f'  SOUNDNESS ALERT [{key}]: {alert}')
        if block['note']:
            print(f"  note: {block['note']}")
    if unfinished:
        print(f'\nUnfinished cells (no cell.json): {unfinished}')
    print(f"\n{'SOUNDNESS ALERTS: ' + str(total_alerts) if total_alerts else 'Validation passed: 0 soundness alerts'}")
    for cell in cells:
        cell.pop('root_table')
    target = args.output or out / 'summary.json'
    target.write_text(json.dumps({'run': str(out), 'checker': checker.identity, 'rel_tol': REL_TOL,
        'soundness_alert_count': total_alerts, 'unfinished_cells': unfinished, 'instances': report, 'cells': cells},
        indent=2, sort_keys=True, default=str) + '\n')
    print(f'Wrote {target}')
    return 1 if total_alerts else 0


if __name__ == '__main__':
    raise SystemExit(main())
