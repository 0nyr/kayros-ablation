"""Plot frozen raw-gap targets from checked envelopes without solver evaluation."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

from pilot_summary import assert_scheduled_row, assert_task_result_identity, score_trace
from checker_contract import checker_identity
from probe_runner import load_completed
from plot_style import arm_order, arm_style


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def attainment_curves(cells, targets, horizon):
    """Average trials within instances, retaining missing and failed observations."""
    if not cells or horizon <= 0 or not math.isfinite(horizon):
        raise ValueError('Positive common horizon and scheduled cells required')
    times = {0., horizon}
    for cell in cells:
        score_trace(cell['envelope'], cell['reference_cost'], horizon)
        times.update(point['elapsed_s'] for point in cell['envelope'])
    times = sorted(times)
    curves = []
    for target in targets:
        if not math.isfinite(target) or target < 0:
            raise ValueError('Nonnegative finite raw-gap target required')
        values = []
        for time in times:
            by_instance = defaultdict(list)
            for cell in cells:
                available = [p['checked_cost'] for p in cell['envelope'] if p['elapsed_s'] <= time]
                attained = bool(available and (available[-1] - cell['reference_cost']) / cell['reference_cost'] <= target)
                by_instance[cell['instance_name']].append(float(attained))
            values.append(sum(sum(v) / len(v) for v in by_instance.values()) / len(by_instance))
        curves.append({'raw_gap_target': target, 'attainment_fraction': values})
    return {'elapsed_s': times, 'targets': curves, 'scheduled_cells': len(cells),
            'instance_trial_counts': dict(sorted((name, sum(c['instance_name'] == name for c in cells))
                                                for name in {c['instance_name'] for c in cells}))}


def build_data(summary_path, wave_path, config_path, snapshot):
    project = Path(__file__).resolve().parents[1]
    summary = json.loads(summary_path.read_bytes())
    if summary['provisional']:
        raise ValueError('Completed scheduled wave required')
    specification = json.loads((wave_path / 'wave.json').read_bytes())
    config = json.loads(config_path.read_bytes())
    if config['raw_gap_targets'] != [0., .01, .05]:
        raise ValueError('Frozen main targets must be 0%, 1% and 5%')
    pin = json.loads((project / 'configs/source_pins.json').read_bytes())['dataset']['reference_manifest_sha256']
    manifest_path = snapshot / 'manifest.json'
    if sha(manifest_path) != pin or summary['reference_manifest_sha256'] != pin:
        raise ValueError('Frozen reference identity mismatch')
    reference = json.loads(manifest_path.read_bytes())
    checker = checker_identity()
    if summary['checker'] != checker or reference['checker'] != checker:
        raise ValueError('Pinned checker identity mismatch')
    if summary['wave_name'] != specification['wave_name']:
        raise ValueError('Summary/wave association mismatch')
    tasks = {t['task_id']: t for t in specification['scheduled_cells']}
    if (len(tasks) != len(specification['scheduled_cells']) or len(summary['cells']) != len(tasks)
            or {c['task_id'] for c in summary['cells']} != set(tasks)):
        raise ValueError('Every unique scheduled cell must remain in the population')
    horizons = {t['horizon_s'] for t in tasks.values()}
    if len(horizons) != 1:
        raise ValueError('Common process-launch horizon required')
    horizon = horizons.pop()
    refs = {r['instance_name']: r for r in reference['instances']}
    groups, sources = defaultdict(list), {}
    for cell in summary['cells']:
        task = tasks[cell['task_id']]
        assert_scheduled_row(task, cell)
        envelope = []
        if cell['run_id']:
            directory = wave_path / 'cells' / cell['run_id']
            identity = json.loads((directory / 'identity.json').read_bytes())
            result = load_completed(directory, expected_run_id=cell['run_id'])
            manifest = json.loads((wave_path / 'manifests' / Path(task['manifest_path']).name).read_bytes())
            assert_task_result_identity(task=task, row=cell, identity=identity, result=result,
                wave=specification, reference=refs[task['instance_name']],
                expected_reference_manifest_sha256=pin, scheduled_manifest=manifest)
            validation = result['validation']
            envelope = validation.get('checked_envelope', [])
            if envelope and validation.get('checker') != checker:
                raise ValueError('Unchecked envelope')
            valid = {c['sequence']: c for c in validation.get('candidates', []) if c.get('valid')}
            for point in envelope:
                checked = valid.get(point['sequence'])
                if (not checked or checked['elapsed_s'] != point['elapsed_s']
                        or checked['checked_cost'] != point['checked_cost']):
                    raise ValueError('Envelope point lacks matching checked candidate')
            sources[cell['run_id']] = {name: sha(directory / name)
                                      for name in ['identity.json', 'result.json', 'events.jsonl']}
        if task['horizon_s'] != cell['horizon_s']:
            raise ValueError('Summary horizon mismatch')
        score = score_trace(envelope, refs[task['instance_name']]['reference_cost'], horizon)
        if score['final_checked_cost'] != cell['final_checked_cost']:
            raise ValueError('Summary final incumbent differs from checked envelope')
        groups[(task['arm'], task['prefix_s'])].append({
            'task_id': task['task_id'], 'run_id': cell['run_id'], 'status': cell['status'],
            'instance_name': task['instance_name'], 'replicate': task['replicate'],
            'reference_cost': refs[task['instance_name']]['reference_cost'], 'envelope': envelope})
    plots = []
    for (arm, prefix), cells in sorted(groups.items(), key=lambda item: arm_order(*item[0])):
        plots.append({'arm': arm, 'prefix_s': prefix, 'cells': cells,
                      **attainment_curves(cells, config['raw_gap_targets'], horizon)})
    return {'wave_name': summary['wave_name'], 'horizon_s': horizon, 'analysis_config': config,
            'sources': {'summary_sha256': sha(summary_path), 'wave_sha256': sha(wave_path / 'wave.json'),
                        'config_sha256': sha(config_path), 'reference_manifest_sha256': pin,
                        'script_sha256': sha(Path(__file__)),
                        'summary_identity_source_sha256': sha(project / 'analysis/pilot_summary.py'),
                        'completion_loader_source_sha256': sha(project / 'scripts/probe_runner.py'),
                        'plot_style_sha256': sha(project / 'analysis/plot_style.py'), 'cells': sources},
            'interpretation': 'All scheduled cells retained. Equal instance weights and equal trial weights within instances. Valid incumbents persist after resource failure. Reference attainment is not computational closure.',
            'configurations': plots}


def render(data, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.8), sharey=True)
    labels = {'lera-exact': 'Lera CPLEX', 'kayros-bpca-cplex': 'Cold CPLEX',
              'kayros-bpca-highs': 'Cold HiGHS', 'kayros-heuristics': 'ILS', 'kayros-full-cplex': 'Full CPLEX'}
    for group in data['configurations']:
        label = labels[group['arm']]
        if group['prefix_s'] is not None:
            label += f" / prefix {group['prefix_s']:g} s"
        for ax, target in zip(axes, group['targets']):
            ax.step(group['elapsed_s'], target['attainment_fraction'], where='post',
                    label=label, **arm_style(group['arm'], group['prefix_s']))
            ax.set_title(f"Raw reference gap ≤ {100 * target['raw_gap_target']:g}%")
    for ax in axes:
        ax.set(xlim=(0, data['horizon_s']), ylim=(-.02, 1.02), xlabel='Time from process launch (s)')
        ax.grid(alpha=.25)
    axes[0].set_ylabel('Equal-instance attainment fraction')
    fig.suptitle(data['wave_name'] + ' | frozen raw-gap targets')
    handles, names = axes[0].get_legend_handles_labels()
    fig.legend(handles, names, loc='lower center', bbox_to_anchor=(.5, .045), ncol=3, frameon=False)
    fig.text(.5, .012, 'All scheduled cells retained. Checked incumbents persist after failures. Reference attainment is not proof.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, .23, 1, .94))
    output.mkdir(parents=True, exist_ok=True)
    data['matplotlib_version'] = matplotlib.__version__
    for extension in ['png', 'pdf']:
        fig.savefig(output / f'raw-gap-attainment.{extension}', dpi=180)
    (output / 'raw-gap-plot-data.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    plt.close(fig)


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['summary', 'wave', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--config', type=Path, default=project / 'configs/main-analysis.json')
    parser.add_argument('--snapshot', type=Path, default=project / 'references/dabia2013-n100-v011-2026-11-09')
    args = parser.parse_args()
    render(build_data(args.summary, args.wave, args.config, args.snapshot), args.output)


if __name__ == '__main__':
    main()
