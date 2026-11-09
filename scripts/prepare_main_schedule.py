"""Prepare the frozen 56-instance roster and 1,400-cell nonlaunchable template.

No solver runs, reservations, runtime environments or output cells are created.
The full-arm prefix and common horizon remain unset until pilot review.
"""
import argparse
import json
from pathlib import Path

from checker_contract import CHECKER_COMMIT, sha256
from development_wave import planned_cells
from probe_runner import atomic_json


def build_templates(reference: dict, reference_digest: str) -> tuple[dict, dict]:
    instances = reference['instances']
    names = [entry['instance_name'] for entry in instances]
    if len(instances) != 56 or len(set(names)) != 56:
        raise ValueError('Main preparation requires exactly 56 frozen distinct instances')
    if reference['checker']['commit'] != CHECKER_COMMIT:
        raise ValueError('Main references do not identify the pinned classic checker')
    selection = {'schema_version': 1, 'reference_manifest_sha256': reference_digest,
        'status': 'frozen_roster_only_no_main_launch',
        'rule': 'All 56 original n100 identities in the immutable pre-pilot reference snapshot.',
        'selected_instances': [{'instance': entry['instance_name'], 'instance_sha256': entry['instance_sha256'],
            'source_reference_sha256': entry['source_reference_sha256']} for entry in sorted(instances, key=lambda item: item['instance_name'])]}
    tasks = planned_cells(names, [None], 5, include_upstream=True, allow_unselected_prefix=True)
    for task in tasks:
        pending = task['arm'] == 'kayros-full-cplex'
        task.update(horizon_s=None, prefix_selection_pending=pending,
            task_id=f"{task['instance_name']}--{task['arm']}--prefix{'PENDING' if pending else 'None'}--rep{task['replicate']}")
    inventory = {'schema_version': 1, 'launchable': False, 'status': 'awaits_pilot_prefix_and_horizon_decisions',
        'reference_manifest_sha256': reference_digest, 'instance_count': 56, 'arm_count': 5,
        'trial_count': 5, 'scheduled_count': len(tasks), 'full_arm_configuration_count': 1,
        'common_horizon_s': None, 'selected_full_prefix_s': None,
        'paired_heuristic_seeds': [42, 123, 456, 789, 2026],
        'cold_exact_search_seed': None, 'placement_and_runtime_manifests': 'not assigned',
        'main_analysis': json.loads((Path(__file__).resolve().parents[1] / 'configs/main-analysis.json').read_bytes()),
        'scheduled_cells': tasks}
    if len(tasks) != 1400 or len({task['task_id'] for task in tasks}) != 1400:
        raise ValueError('Main template population or identity collision')
    return selection, inventory


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=project / 'references/dabia2013-n100-v011-2026-11-09')
    parser.add_argument('--pins', type=Path, default=project / 'configs/source_pins.json')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(args.pins.read_bytes())['dataset']['reference_manifest_sha256']
    path = args.snapshot / 'manifest.json'
    if sha256(path) != expected:
        raise ValueError('Reference manifest differs from independently pinned digest')
    selection, inventory = build_templates(json.loads(path.read_bytes()), expected)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ['main-selection.json', 'main-schedule-template.json']:
        if (args.output_dir / name).exists():
            raise FileExistsError('Refuse to replace an existing frozen main-preparation artifact')
    atomic_json(args.output_dir / 'main-selection.json', selection)
    atomic_json(args.output_dir / 'main-schedule-template.json', inventory)
    print(json.dumps({'instances': 56, 'arms': 5, 'trials': 5, 'cells': 1400, 'launchable': False}))


if __name__ == '__main__':
    main()
