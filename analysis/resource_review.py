"""Report observed per-process memory peaks without summing address spaces."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from pilot_summary import assert_task_result_identity


GIB = 1024 ** 3
KIB_PER_GIB = 1024 ** 2
ARM_ORDER = {'lera-exact': 0, 'kayros-bpca-cplex': 1, 'kayros-bpca-highs': 2,
             'kayros-heuristics': 3, 'kayros-full-cplex': 4}


def native_result_argument(command):
    if command.count('--native-result') != 1:
        raise ValueError('Expected one exact native-result command argument')
    index = command.index('--native-result') + 1
    if index >= len(command) or not isinstance(command[index], str) or not command[index]:
        raise ValueError('Missing native-result command value')
    return command[index]


def configuration(cell):
    return cell['arm'] + (f"/prefix={cell['prefix_s']:g}" if cell.get('prefix_s') is not None else '')


def short_host(host):
    return host.split('.')[0]


def limit_bytes(value):
    if value == 'unlimited' or value is None:
        return None
    return int(value)


def associate_processes(specifications, snapshots):
    """Join only by the full native-result argument, then verify host/argv."""
    cells = [{**spec, 'observation': None} for spec in specifications]
    paths = {}
    for cell in cells:
        path = cell.get('native_result_path')
        if path is not None:
            if path in paths:
                raise ValueError('Native-result path belongs to multiple scheduled cells')
            paths[path] = cell
    unmatched = []
    for snapshot in snapshots:
        if snapshot.get('measurement_revision') != 4:
            raise ValueError('Expected revision 4 per-process virtual-memory records')
        host = snapshot['host']
        for process in snapshot['processes']:
            path = process.get('native_result_path')
            if path != native_result_argument(process['worker_argv']):
                raise ValueError('Sampler argv/native-result association mismatch')
            cell = paths.get(path)
            if cell is None:
                unmatched.append({'host': host, 'worker_pid': process['worker_pid'],
                                  'native_result_path': path, 'reason': 'no scheduled identity with this exact path'})
                continue
            if short_host(host) != short_host(cell['host']):
                raise ValueError('Observed process host differs from scheduled placement')
            if process['worker_argv'][1:] != cell['command'][1:]:
                raise ValueError('Observed worker arguments differ from recorded command')
            if cell['observation'] is not None:
                raise ValueError('Multiple worker records for one exact execution identity')
            members = []
            for key, item in process.get('member_maxima', {}).items():
                if int(key) != item['pid'] or item['role'] not in ('worker', 'descendant'):
                    raise ValueError('Malformed process-member identity')
                if (item['role'] == 'worker') != (item['pid'] == process['worker_pid']):
                    raise ValueError('Worker/descendant role mismatch')
                for name in ('VmPeak_kib', 'VmSize_kib', 'VmRSS_kib'):
                    if type(item[name]) is not int or item[name] < 0:
                        raise ValueError('Invalid sampled process memory value')
                soft = limit_bytes(item.get('rlimit_as_soft_bytes'))
                hard = limit_bytes(item.get('rlimit_as_hard_bytes'))
                members.append({'pid': item['pid'], 'role': item['role'], 'host': host,
                    'maximum_observed_lifetime_vm_peak_kib': item['VmPeak_kib'],
                    'maximum_sampled_current_vm_size_kib': item['VmSize_kib'],
                    'maximum_sampled_current_vm_rss_kib': item['VmRSS_kib'],
                    'rlimit_as_soft_bytes': soft, 'rlimit_as_hard_bytes': hard,
                    'limit_matches_declared': soft == hard == cell['cap_bytes']})
            usable = [member for member in members if member['maximum_observed_lifetime_vm_peak_kib'] > 0]
            owner = max(usable, key=lambda member: (member['maximum_observed_lifetime_vm_peak_kib'], member['pid']), default=None)
            cell['observation'] = {'worker_pid': process['worker_pid'], 'host': host,
                'members': members, 'peak_owner_pid': owner['pid'] if owner else None,
                'peak_owner_role': owner['role'] if owner else None,
                'maximum_observed_individual_vm_peak_kib': owner['maximum_observed_lifetime_vm_peak_kib'] if owner else None,
                'snapshot_checkpoint_utc': snapshot.get('checkpoint_utc'),
                'sampler_reports_wave_complete': snapshot.get('wave_complete'),
                'positive_vm_peak_available': owner is not None}
    return cells, unmatched


def build_report(summary, wave, snapshot_path):
    if summary['provisional']:
        raise ValueError('Resource comparison requires the completed scheduled cohort')
    wave_raw = (wave / 'wave.json').read_bytes()
    specification = json.loads(wave_raw)
    reference_raw = (snapshot_path / 'manifest.json').read_bytes()
    reference = json.loads(reference_raw)
    pin = hashlib.sha256(reference_raw).hexdigest()
    if (pin != summary['reference_manifest_sha256'] or pin != specification['reference_manifest_sha256']
            or summary['checker'] != reference['checker'] or summary['wave_name'] != specification['wave_name']):
        raise ValueError('Summary/wave/reference provenance differs')
    refs = {item['instance_name']: item for item in reference['instances']}
    tasks = {item['task_id']: item for item in specification['scheduled_cells']}
    if (len(tasks) != len(specification['scheduled_cells'])
            or len(summary['cells']) != len(tasks)
            or {cell['task_id'] for cell in summary['cells']} != set(tasks)):
        raise ValueError('Summary does not contain every unique scheduled cell')
    cells, identity_sources = [], {}
    for cell in summary['cells']:
        task = tasks[cell['task_id']]
        item = {key: task[key] for key in ('task_id', 'instance_name', 'arm', 'prefix_s', 'replicate', 'host', 'cpu_core')}
        item.update(run_id=cell['run_id'], status=cell['status'], cap_bytes=task['memory_mb'] * 1024 ** 2,
                    command=None, native_result_path=None)
        if cell['run_id']:
            directory = wave / 'cells' / cell['run_id']
            identity_raw = (directory / 'identity.json').read_bytes()
            result_raw = (directory / 'result.json').read_bytes()
            identity, result = json.loads(identity_raw), json.loads(result_raw)
            manifest = json.loads((wave / 'manifests' / Path(task['manifest_path']).name).read_bytes())
            assert_task_result_identity(task=task, row=cell, identity=identity, result=result,
                wave=specification, reference=refs[task['instance_name']],
                expected_reference_manifest_sha256=pin, scheduled_manifest=manifest)
            item['command'] = identity['command']
            item['native_result_path'] = native_result_argument(identity['command'])
            identity_sources[cell['run_id']] = {'identity_sha256': hashlib.sha256(identity_raw).hexdigest(),
                                                'result_sha256': hashlib.sha256(result_raw).hexdigest()}
        cells.append(item)
    snapshots, sources = [], []
    for path in sorted((wave / 'virtual-memory').glob('*.json')):
        raw = path.read_bytes()
        data = json.loads(raw)
        snapshots.append(data)
        sources.append({'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                        'host': data.get('host'), 'checkpoint_utc': data.get('checkpoint_utc'),
                        'nominal_interval_s': data.get('nominal_interval_s'),
                        'monitor_source_sha256': data.get('monitor_source_sha256'),
                        'wave_complete': data.get('wave_complete')})
    cells, unmatched = associate_processes(cells, snapshots)
    groups = defaultdict(list)
    for cell in cells:
        groups[configuration(cell)].append(cell)
    coverage = []
    for name, values in sorted(groups.items()):
        observed = sum(cell['observation'] is not None for cell in values)
        usable = sum(cell['observation'] is not None and cell['observation']['positive_vm_peak_available'] for cell in values)
        coverage.append({'configuration': name, 'scheduled_cells': len(values),
                         'observed_worker_records': observed, 'cells_with_usable_vm_peak': usable,
                         'cells_without_usable_observation': len(values) - usable})
    warnings = [{'task_id': cell['task_id'], 'pid': member['pid'], 'host': member['host'],
                 'reason': 'sampled address-space limits differ from declared cap'}
                for cell in cells if cell['observation'] for member in cell['observation']['members']
                if not member['limit_matches_declared']]
    return {'wave_name': summary['wave_name'], 'wave_sha256': hashlib.sha256(wave_raw).hexdigest(),
            'reference_manifest_sha256': pin, 'checker': summary['checker'], 'cells': cells,
            'coverage': coverage, 'unmatched_observations': unmatched, 'limit_review_warnings': warnings,
            'identity_sources': identity_sources, 'sampler_sources': sources,
            'measurement': 'Maximum observed individual-process lifetime VmPeak per scheduled cell, never a process-tree sum.',
            'limits': [
                'Every scheduled cell remains listed. No observation is missing data, not a zero-memory outcome.',
                'Revision 4 snapshots retain separate member maxima. VmSize and VmRSS are maxima of sampled current values, not necessarily values at the VmPeak observation.',
                'Kernel VmPeak includes earlier process lifetime when that process survives until observation. It does not recover already-exited short processes.',
                'The bounded sampler can start late or stop before a later allocation peak. V4 aggregate snapshots do not record per-process first-observation timestamps.',
                'Role worker means the Python adapter, including embedded Kayros native code. Descendant means a child process. Pinned upstream uses a native child. Executable names are not recorded per member.',
                'All limits are per process. VmSize or VmPeak across workers and children is never summed.',
                'The 8 GiB line is a reference budget, not an overlaid cohort or proof of the cause of earlier failures.']}


def render(report, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    caps = {cell['cap_bytes'] for cell in report['cells']}
    if len(caps) != 1:
        raise ValueError('Resource plot requires a common declared per-process cap')
    cap = caps.pop() / GIB
    grouped = defaultdict(list)
    for cell in report['cells']:
        grouped[cell['arm'], cell.get('prefix_s')].append(cell)
    keys = sorted(grouped, key=lambda key: (ARM_ORDER.get(key[0], 99), -1 if key[1] is None else key[1]))
    fig, ax = plt.subplots(figsize=(13, max(5.5, 0.56 * len(keys) + 1.8)))
    maximum = cap
    labels = []
    for index, key in enumerate(keys):
        arm, prefix = key
        label = arm.replace('kayros-bpca-', 'Cold ').replace('kayros-heuristics', 'ILS').replace('lera-exact', 'Lera CPLEX').replace('kayros-full-cplex', 'Full CPLEX')
        if prefix is not None:
            label += f' / {prefix:g} s'
        labels.append(label)
        rows = sorted(grouped[key], key=lambda cell: (cell['instance_name'], cell['replicate']))
        observed = 0
        for position, cell in enumerate(rows):
            observation = cell['observation']
            if not observation or observation['maximum_observed_individual_vm_peak_kib'] is None:
                continue
            observed += 1
            value = observation['maximum_observed_individual_vm_peak_kib'] / KIB_PER_GIB
            maximum = max(maximum, value)
            jitter = 0 if len(rows) == 1 else (position / (len(rows) - 1) - 0.5) * 0.46
            worker = observation['peak_owner_role'] == 'worker'
            ax.scatter(value, index + jitter, marker='o' if worker else '^',
                       color='#0072B2' if worker else '#D55E00', s=25, alpha=0.75)
        ax.text(1.01, index, f'{observed}/{len(rows)} observed', transform=ax.get_yaxis_transform(),
                va='center', fontsize=9)
    ax.axvline(8, color='0.5', linestyle=':', linewidth=1.3)
    ax.axvline(cap, color='#B2182B', linestyle='--', linewidth=1.3)
    ax.set_yticks(range(len(keys)), labels, fontsize=9)
    ax.set_ylim(len(keys) - 0.5, -0.5)
    ax.set_xlim(0, maximum * 1.07)
    ax.set_xlabel('Maximum observed individual-process VmPeak (GiB)')
    ax.set_title(report['wave_name'] + ' | sampled process lifetime peaks')
    ax.grid(axis='x', alpha=0.2)
    handles = [Line2D([], [], marker='o', color='#0072B2', linestyle='none', label='Worker / embedded Kayros'),
               Line2D([], [], marker='^', color='#D55E00', linestyle='none', label='Native child / descendant'),
               Line2D([], [], color='0.5', linestyle=':', label='8 GiB reference budget'),
               Line2D([], [], color='#B2182B', linestyle='--', label=f'{cap:g} GiB declared per-process cap')]
    fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(0.5, 0.065), ncol=2, frameon=False)
    fig.text(0.5, 0.014, 'Missing observations stay missing. Individual address spaces are never summed. Late sampling and short-lived processes limit coverage.', ha='center', fontsize=9)
    fig.tight_layout(rect=(0, 0.16, 0.87, 1))
    for extension in ('png', 'pdf'):
        fig.savefig(output / f'resource-vmpeak.{extension}', dpi=180, bbox_inches='tight')
    plt.close(fig)
    report['plot_settings'] = {'units': 'GiB', 'cap_gib': cap, 'reference_budget_gib': 8,
                               'matplotlib_version': matplotlib.__version__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--wave', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, default=Path(__file__).resolve().parents[1] / 'references/dabia2013-n100-v011-2026-11-09')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.summary.read_bytes()
    report = build_report(json.loads(raw), args.wave, args.snapshot)
    report['summary_sha256'] = hashlib.sha256(raw).hexdigest()
    report['analysis_source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    render(report, args.output)
    (args.output / 'resource-plot-data.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
