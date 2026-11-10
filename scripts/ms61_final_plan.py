#!/usr/bin/env python3
"""Write the ms61 final five-arm plan (or a small pilot) for ms61_run_cells.py.

Final design (docs/protocol.md, configs/main-proposal.json): 56 Dabia2013 n=100 instances, 5 arms, 5 trials, 7,200 s from process launch, 16 GiB, 1 pinned core.
  lera-exact         Lera-Romero exact with CPLEX, repaired control f3 (kind lera, lera_binary)
  kayros-bpca-cplex  cold Kayros BPC with CPLEX
  kayros-bpca-highs  cold Kayros BPC with HiGHS (separate HiGHS venv)
  kayros-heuristics  standalone Kayros ILS for the whole horizon
  kayros-full-cplex  ILS prefix of 15 s, then warm-started BPC with CPLEX, one budget
Arm ids equal the worker arm names of ms58/ms61 (the ms55 design ids).
Trial k of the ILS and full arms uses seed k of the paired list (42, 123, 456, 789, 2026). Cold exact and Lera trials are timing replications of a deterministic run (seed null).
Cell ids are <instance>--<arm>--t<k>.

Order: instances are grouped into duration strata (estimated from configs/ms61/final-duration-hints.json, long first), and cells are shuffled within each stratum with a seed chosen so that the static round-robin shards of ms61_run_cells.py (plan index % K) stay balanced in arms and in estimated load for every host count K in 2..60.
The chosen seed and the worst-case balance figures are recorded in the plan's "design" block.

Examples:
  python3 scripts/ms61_final_plan.py --cplex-venv /home/frascous/ms61-bpc/venvs/K17 --highs-venv /home/frascous/ms61-bpc/venvs/K17-highs --output configs/ms61/final.json
  python3 scripts/ms61_final_plan.py ... --pilot --instances C101,R101,RC105,C201,R201 --trials 2 --horizon 600 --output configs/ms61/final-pilot.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import math
import re

REPO = Path(__file__).resolve().parents[1]
REFERENCE_MANIFEST = REPO / 'references/dabia2013-n100-v011-2026-11-09/manifest.json'
REFERENCE_MANIFEST_SHA256 = 'ea7310862b191655ff3109ee303afdd9dd420664da4ebb82ea30790ac415af66'
DEFAULT_HINTS = REPO / 'configs/ms61/final-duration-hints.json'
DEFAULT_LERA_BINARY = '/home/frascous/ms61-bpc/upstream-f3-build/bp'
F3_BINARY_SHA256 = '04073bef66d50273d018df9381e2a86b7a6cd744a68951b83fc60d04952e04f2'
SEEDS = [42, 123, 456, 789, 2026]
ARMS = ['lera-exact', 'kayros-bpca-cplex', 'kayros-bpca-highs', 'kayros-heuristics', 'kayros-full-cplex']
LEADING_ENV = {'KAYROS_BPC_PACKED': '1', 'KAYROS_BPC_HE_CUTBLIND': '1', 'KAYROS_BPC_MERGE_PREFILTER': '1',
               'KAYROS_BPC_LAM_ITERATE': '1', 'KAYROS_BPC_NEAR_REL': '0.10'}


def parse_env(items):
    env = {}
    for item in items or []:
        key, sep, value = item.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise SystemExit(f'--env expects NAME=value, got {item!r}')
        env[key] = value
    return env


def manifest_instances():
    raw = REFERENCE_MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REFERENCE_MANIFEST_SHA256:
        raise SystemExit('Frozen reference manifest digest mismatch')
    return [row['instance_name'] for row in json.loads(raw)['instances']]


def estimated_seconds(instance, arm, horizon, prefix, hints):
    if arm == 'kayros-heuristics':
        return horizon
    hint = hints.get(instance)
    base = hint['max_closure_s'] if hint and hint.get('all_closed') and hint.get('max_closure_s') is not None else horizon
    if arm == 'kayros-full-cplex':
        base += prefix
    return min(horizon, base)


STRATA_EDGES_S = [7200.0, 1800.0, 300.0, 60.0, 0.0]
GOLDEN = (math.sqrt(5) - 1) / 2


def balanced_order(cells, estimates, horizon):
    """Long cells first, arms interleaved so that every residue class index % K sees the arm mix of its stratum.

    Cells fall into strata by estimated duration (a cell at the full horizon is in the first stratum, whatever the edge list says).
    Position i of the plan draws its arm from u = frac(i * golden ratio) mapped onto the stratum's arm proportions.
    For any K the positions r, r+K, r+2K, ... give an equidistributed sequence u, so each host shard of ms61_run_cells.py gets about the stratum's arm mix and load.
    Within an arm, cells keep the instance order of their stratum, longest first, then trial.
    """
    edges = [min(edge, horizon) for edge in STRATA_EDGES_S]

    def stratum(cell):
        return next(index for index, edge in enumerate(edges) if estimates[cell['id']] >= edge)

    order = []
    for s in sorted({stratum(c) for c in cells}):
        group = [c for c in cells if stratum(c) == s]
        arms = sorted({c['arm'] for c in group}, key=ARMS.index)
        pools = {arm: sorted((c for c in group if c['arm'] == arm), key=lambda c: (-estimates[c['id']], c['trial'], c['instance']))
                 for arm in arms}
        total = len(group)
        shares = [len(pools[arm]) / total for arm in arms]
        taken = {arm: 0 for arm in arms}
        for _ in range(total):
            u = (len(order) * GOLDEN) % 1.0
            cumulative, choice = 0.0, arms[-1]
            for arm, share in zip(arms, shares):
                cumulative += share
                if u < cumulative:
                    choice = arm
                    break
            if taken[choice] >= len(pools[choice]):
                # Exhausted: take the arm furthest behind its share.
                choice = max((a for a in arms if taken[a] < len(pools[a])), key=lambda a: len(pools[a]) - taken[a])
            order.append(pools[choice][taken[choice]])
            taken[choice] += 1
    return order


def balance(order, estimates, ks):
    """Worst cases over K of the host load ratio (max / mean) and of the per-host deviation from the plan's arm mix."""
    present = sorted({c['arm'] for c in order}, key=ARMS.index)
    share = {arm: sum(c['arm'] == arm for c in order) / len(order) for arm in present}
    worst_load, worst_dev = (1.0, None), (0.0, None)
    for k in ks:
        if k > len(order):
            continue
        loads, arms, sizes = [0.0] * k, [Counter() for _ in range(k)], [0] * k
        for index, cell in enumerate(order):
            loads[index % k] += estimates[cell['id']]
            arms[index % k][cell['arm']] += 1
            sizes[index % k] += 1
        mean = sum(loads) / k
        ratio = max(loads) / mean if mean else 1.0
        if ratio > worst_load[0]:
            worst_load = (ratio, k)
        for counter, size in zip(arms, sizes):
            deviation = max(abs(counter.get(arm, 0) - share[arm] * size) for arm in present)
            if deviation > worst_dev[0]:
                worst_dev = (deviation, k)
    return {'max_over_k_host_load_ratio': round(worst_load[0], 4), 'load_ratio_at_k': worst_load[1],
            'max_over_k_host_arm_count_deviation': round(worst_dev[0], 2), 'deviation_at_k': worst_dev[1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cplex-venv', required=True, help='remote CPLEX venv (cold CPLEX, ILS and full arms)')
    parser.add_argument('--highs-venv', required=True, help='remote HiGHS venv (cold HiGHS arm)')
    parser.add_argument('--lera-binary', default=DEFAULT_LERA_BINARY)
    parser.add_argument('--horizon', type=float, default=7200.0)
    parser.add_argument('--prefix', type=float, default=15.0)
    parser.add_argument('--trials', type=int, default=5)
    parser.add_argument('--memory-mb', type=int, default=16384)
    parser.add_argument('--instances', help='comma-separated subset, in the frozen manifest order by default (all 56)')
    parser.add_argument('--arms', default=','.join(ARMS), help='comma-separated subset of the 5 arms (pilots only)')
    parser.add_argument('--env', action='append', help='NAME=value added to every Kayros cell (repeatable)')
    parser.add_argument('--leading-env', action='store_true', help=f'add the leading-arm environment {LEADING_ENV}')
    parser.add_argument('--pilot', action='store_true', help='mark the plan as a pilot (any horizon, subset or trial count)')
    parser.add_argument('--name', default=None)
    parser.add_argument('--hints', type=Path, default=DEFAULT_HINTS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    names = manifest_instances()
    instances = names if not args.instances else [x.strip() for x in args.instances.split(',') if x.strip()]
    unknown = [x for x in instances if x not in names]
    if unknown or len(set(instances)) != len(instances):
        raise SystemExit(f'Unknown or repeated instances: {unknown or instances}')
    instances = [x for x in names if x in instances]  # the frozen manifest order
    arms = [x.strip() for x in args.arms.split(',') if x.strip()]
    if any(a not in ARMS for a in arms) or len(set(arms)) != len(arms):
        raise SystemExit(f'Arms must be a subset of {ARMS}')
    arms = [a for a in ARMS if a in arms]
    if not 1 <= args.trials <= len(SEEDS):
        raise SystemExit(f'trials must be in 1..{len(SEEDS)}')
    if not 0 < args.prefix < args.horizon:
        raise SystemExit('prefix must lie strictly inside the horizon')
    final_shape = (len(instances) == 56 and arms == ARMS and args.trials == 5 and args.horizon == 7200.0
                   and args.prefix == 15.0 and args.memory_mb == 16384)
    if not args.pilot and not final_shape:
        raise SystemExit('Not the approved final design (56 x 5 arms x 5 trials, 7,200 s, 15 s, 16 GiB): pass --pilot for a reduced plan')
    common_env = {**(LEADING_ENV if args.leading_env else {}), **parse_env(args.env)}
    hints = json.loads(args.hints.read_bytes())['instances'] if args.hints.exists() else {}

    cells = []
    for instance in instances:
        for trial in range(1, args.trials + 1):
            seed = SEEDS[trial - 1]
            for arm in arms:
                cell = {'id': f'{instance}--{arm}--t{trial}', 'arm': arm, 'instance': instance, 'rep': trial, 'trial': trial}
                if arm == 'lera-exact':
                    cell.update(kind='lera', lera_binary=args.lera_binary, seed=None)
                else:
                    cell.update(kind='kayros', env=dict(common_env))
                    if arm == 'kayros-bpca-cplex':
                        cell.update(mode='cold', backend='cplex', venv=args.cplex_venv, seed=None)
                    elif arm == 'kayros-bpca-highs':
                        cell.update(mode='cold', backend='highs', venv=args.highs_venv, seed=None)
                    elif arm == 'kayros-heuristics':
                        cell.update(mode='ils', backend='cplex', venv=args.cplex_venv, seed=seed)
                    else:
                        cell.update(mode='full', backend='cplex', venv=args.cplex_venv, seed=seed, prefix_s=args.prefix)
                cells.append(cell)
    estimates = {c['id']: estimated_seconds(c['instance'], c['arm'], args.horizon, args.prefix, hints) for c in cells}
    order = balanced_order(cells, estimates, args.horizon)
    report = balance(order, estimates, list(range(2, 61)))
    design = {
        'name': args.name or ('ms61-final-pilot' if args.pilot else 'ms61-final'),
        'pilot': args.pilot, 'final_shape': final_shape, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'generator': 'scripts/ms61_final_plan.py', 'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'instances': instances, 'arms': arms, 'trials': args.trials, 'paired_seeds': SEEDS[:args.trials],
        'horizon_s': args.horizon, 'prefix_s': args.prefix, 'memory_mb': args.memory_mb,
        'clock_origin': 'process launch (runner, directly before Popen)', 'common_kayros_env': common_env,
        'cplex_venv': args.cplex_venv, 'highs_venv': args.highs_venv, 'lera_binary': args.lera_binary,
        'expected_lera_binary_sha256': F3_BINARY_SHA256, 'reference_manifest_sha256': REFERENCE_MANIFEST_SHA256,
        'cell_count': len(order), 'order': 'long first by estimated cell duration (strata edges below), arms interleaved by a weighted golden-ratio sequence',
        'order_strata_edges_s': STRATA_EDGES_S,
        'shard_balance_k_2_to_60': report, 'estimated_core_seconds': round(sum(estimates.values()), 1),
        'hints': str(args.hints.relative_to(REPO)) if args.hints.exists() and args.hints.is_relative_to(REPO) else str(args.hints),
    }
    plan = {'design': design, 'defaults': {'n': 100, 'memory_mb': args.memory_mb, 'time_limit_s': args.horizon}, 'cells': order}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=1) + '\n')
    print(f"Wrote {args.output}: {len(order)} cells, {len(instances)} instances x {len(arms)} arms x {args.trials} trials, "
          f"horizon {args.horizon:g} s, prefix {args.prefix:g} s")
    print(f"shard balance over K=2..60: {json.dumps(report)}")
    print(f"estimated {design['estimated_core_seconds'] / 3600:.0f} core-hours")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
