"""Freeze a concrete main-campaign proposal after completed pilot review.

This prepares data for author inspection. It never reserves nodes or launches a solver.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

from checker_contract import sha256
from development_wave import planned_cells
from prepare_main_schedule import build_templates
from probe_runner import atomic_json


def build_proposal(reference, reference_digest, full, decision, focused, *, horizon, hosts, workers):
    if full['provisional'] or not decision['recommendation_eligible']:
        raise ValueError('Complete and review the full pilot before proposing the main campaign')
    if focused.get('recommendation_eligible') is not True:
        raise ValueError('Review recorded focused-cohort output issues before a main recommendation')
    if (full['wave_name'] != decision['wave_name'] or
            focused['full_wave'] != full['wave_name']):
        raise ValueError('Pilot review artifacts refer to different full-panel waves')
    if full['reference_manifest_sha256'] != reference_digest or full['checker'] != reference['checker']:
        raise ValueError('Pilot references or checker differ from the frozen main roster')
    prefix = decision['selected_prefix_s']
    caps = {cell['memory_mb'] for cell in full['cells']}
    horizons = {cell['horizon_s'] for cell in full['cells']}
    if len(caps) != 1 or horizons != {decision['horizon_s']}:
        raise ValueError('Full panel must have one matched cap and horizon')
    memory = caps.pop()
    if focused['memory_limit_mib'] != memory or focused['full_horizon_s'] != decision['horizon_s']:
        raise ValueError('Focused review does not match the full-panel resource setting')
    if (not isinstance(prefix, (int, float)) or not math.isfinite(prefix)
            or not math.isfinite(horizon) or not 0 < prefix < horizon
            or not decision['horizon_s'] <= horizon <= focused['focused_horizon_s']):
        raise ValueError('Proposal requires a selected prefix and an observed horizon range')
    if type(hosts) is not int or type(workers) is not int or min(hosts, workers) < 1:
        raise ValueError('Positive integer resource shape required')
    selection, template = build_templates(reference, reference_digest)
    names = [entry['instance'] for entry in selection['selected_instances']]
    tasks = planned_cells(names, [prefix], 5, include_upstream=True)
    for task in tasks:
        task.update(horizon_s=horizon, memory_mb=memory,
                    task_id=f"{task['instance_name']}--{task['arm']}--prefix{task['prefix_s']}--rep{task['replicate']}")
    if (len(tasks) != 1400 or len({task['task_id'] for task in tasks}) != 1400
            or set(Counter(task['instance_name'] for task in tasks).values()) != {25}):
        raise ValueError('The complete 56-instance paired five-arm main population is required')
    rounds = math.ceil(len(tasks) / (hosts * workers))
    walltime = math.ceil(rounds * horizon) + 1800
    return {
        **template, 'status': 'concrete_proposal_awaiting_author_inspection',
        'launchable': False, 'author_review_required': True,
        'selected_full_prefix_s': prefix, 'common_horizon_s': horizon,
        'memory_limit_mib': memory, 'clock_origin': 'process_launch',
        'prefix_evidence_wave': full['wave_name'], 'horizon_evidence_wave': focused['focused_wave'],
        'selection_rule': decision['selection_rule'],
        'scheduled_cells': tasks,
        'resource_shape': {'hosts': hosts, 'workers_per_host': workers,
                           'simultaneous_slots': hosts * workers, 'maximum_rounds': rounds,
                           'solver_core_hours_upper_bound': len(tasks) * horizon / 3600,
                           'walltime_seconds_including_margin': walltime,
                           'margin_seconds': 1800, 'node_ram_guard_mib': workers * memory + 16384,
                           'availability': 'Must be rechecked before any reservation'},
        'interpretation': [
            'The prefix follows the approved full-panel ordering under this resource budget.',
            'The focused cases support a separate horizon review and are not pooled into full-panel scores.',
            'Resource failures remain measured outcomes with checked incumbents and all denominator contributions.',
            'Host names, CPU assignments and fresh runtime inventories require the actual future OAR allocation.',
            'This artifact records a recommendation for inspection and does not authorize or execute the main campaign.',
        ],
    }


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['full-summary', 'decision', 'focused-review', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--horizon', type=float, required=True)
    parser.add_argument('--hosts', type=int, required=True)
    parser.add_argument('--workers-per-host', type=int, required=True)
    args = parser.parse_args()
    paths = {'full_summary': args.full_summary, 'prefix_decision': args.decision,
             'focused_review': args.focused_review,
             'reference_manifest': project / 'references/dabia2013-n100-v011-2026-11-09/manifest.json',
             'source_pins': project / 'configs/source_pins.json',
             'matched_artifacts': project / 'configs/ms56-matched-artifacts.json'}
    docs = {name: json.loads(path.read_bytes()) for name, path in paths.items()}
    reference_digest = sha256(paths['reference_manifest'])
    if reference_digest != docs['source_pins']['dataset']['reference_manifest_sha256']:
        raise ValueError('The immutable reference digest differs from its independent pin')
    result = build_proposal(docs['reference_manifest'], reference_digest, docs['full_summary'],
                            docs['prefix_decision'], docs['focused_review'], horizon=args.horizon,
                            hosts=args.hosts, workers=args.workers_per_host)
    paths.update(main_analysis=project / 'configs/main-analysis.json',
                 schedule_generator=project / 'scripts/prepare_main_schedule.py')
    result['evidence'] = {name: {'path': str(path.resolve()), 'sha256': sha256(path)}
                          for name, path in paths.items()}
    result['generator_sha256'] = sha256(Path(__file__))
    if args.output.exists():
        raise FileExistsError('Refuse to replace an existing main proposal')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, result)
    print(json.dumps({key: result[key] for key in ['launchable', 'scheduled_count',
                     'selected_full_prefix_s', 'common_horizon_s', 'resource_shape']}, indent=2))


if __name__ == '__main__':
    main()
