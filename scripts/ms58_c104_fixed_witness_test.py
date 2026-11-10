"""Offline C104 selection, distributed placement and long-clock contracts."""
from collections import Counter
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import ms58_c104_fixed_witness as c104
import ms58_fixed_witness as fixed
from checker_contract import sha256
from ms58_fixed_witness_worker import arm_stage_deadline
from probe_runner import digest


class C104FixedWitnessTests(unittest.TestCase):
    def hosts(self):
        return [f'gros-{i}.nancy.grid5000.fr' for i in range(1, 5)]

    def snapshots(self):
        identity = {'hardware': {'cpu': 'same-gros', 'kernel': 'same-kernel'}, 'runtimes': {'all': 'same-builds'}}
        return [{'status': 'passed', 'host': host.split('.')[0], 'host_role': role, 'job_id': '123',
            'plan_sha256': 'plan', 'cohort_identity': copy.deepcopy(identity), 'cohort_sha256': digest(identity),
            'hardware': {'allowed_cpu_ids': list(range(18)), 'memory_kib': {'MemAvailable': 80 * 1024**2}},
            'selected_slots': [{'cpu_core': i, 'physical_package_id': 0, 'physical_core_id': i}
                               for i in range(c104.COUNTS[role])]} for role, host in enumerate(self.hosts())]

    def test_placement_mixes_every_arm_and_rep_across_hosts_with_15_isolated_slots(self):
        tasks = c104.placement()
        self.assertEqual(len(tasks), 15)
        self.assertEqual(len({t['task_id'] for t in tasks}), 15)
        self.assertEqual(Counter(t['host_role'] for t in tasks), {0: 4, 1: 4, 2: 4, 3: 3})
        self.assertEqual(len({(t['host_role'], t['slot_ordinal']) for t in tasks}), 15)
        for arm in fixed.ARMS:
            self.assertEqual(len({t['host_role'] for t in tasks if t['arm_id'] == arm}), 3)
        for rep in (1, 2, 3):
            self.assertEqual(len({t['host_role'] for t in tasks if t['replicate'] == rep}), 4)
        slots = c104.validate_snapshots(self.snapshots(), self.hosts(), '123', 'plan')
        assigned = c104.assigned_tasks({'scheduled_cells': tasks}, {'slots': slots})
        self.assertEqual(len({(t['host'], t['cpu_core']) for t in assigned}), 15)

    def test_actual_gros_nodefile_repetitions_are_allowed_but_mixed_clusters_or_aliases_are_not(self):
        self.assertEqual(c104.allocation_hosts('\n'.join(self.hosts() * 18)), self.hosts())
        for hosts in [self.hosts()[:-1], self.hosts()[:-1] + ['grvingt-1.nancy.grid5000.fr'],
                      self.hosts()[:3] + ['gros-1']]:
            with self.assertRaises(ValueError):
                c104.allocation_hosts('\n'.join(hosts))

    def test_snapshot_identity_physical_cores_affinity_and_ram_are_enforced(self):
        for mutation in ('job', 'host', 'plan', 'kernel', 'sibling', 'affinity', 'ram'):
            rows = self.snapshots()
            if mutation == 'job': rows[0]['job_id'] = 'other'
            if mutation == 'host': rows[0]['host'] = 'gros-999'
            if mutation == 'plan': rows[0]['plan_sha256'] = 'other'
            if mutation == 'kernel':
                rows[0]['cohort_identity']['hardware']['kernel'] = 'different'
                rows[0]['cohort_sha256'] = digest(rows[0]['cohort_identity'])
            if mutation == 'sibling': rows[0]['selected_slots'][1]['physical_core_id'] = 0
            if mutation == 'affinity': rows[0]['selected_slots'][0]['cpu_core'] = 999
            if mutation == 'ram': rows[0]['hardware']['memory_kib']['MemAvailable'] = 79 * 1024**2
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                c104.validate_snapshots(rows, self.hosts(), '123', 'plan')

    def test_3600_kernel_native_deadline_and_3660_process_identity(self):
        with patch.dict(os.environ, PROBE_START_MONOTONIC_NS='42'), \
                patch('ms58_fixed_witness_worker.signal.signal'), \
                patch('ms58_fixed_witness_worker.signal.setitimer') as alarm, \
                patch('ms58_fixed_witness_worker.time.monotonic_ns', return_value=100):
            deadline = arm_stage_deadline(100, 3600)
            self.assertEqual(deadline, 3600000000100)
            self.assertEqual(os.environ['PROBE_DEADLINE_MONOTONIC_NS'], str(deadline))
            self.assertEqual(os.environ['PROBE_START_MONOTONIC_NS'], '42')
            self.assertEqual(alarm.call_args.args[1], 3600.)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'frozen-scripts'
            scripts.mkdir()
            (scripts / 'ms58_fixed_witness_worker.py').write_text('fixture')
            task = {'cpu_core': 1, 'instance_path': '/C104', 'witness_path': '/fixed-witness',
                'witness_sha256': 'witness', 'arm_id': 'ub-only', 'horizon_s': 3600., 'process_horizon_s': 3660.}
            spec = {'worker_python': sys.executable, 'worker_python_sha256': sha256(Path(sys.executable)),
                    'path': '/manifest', 'sha256': 'manifest'}
            expected, _, _ = fixed.cell_contract(task, spec, {'worker_environment': {}}, scripts, root)
            self.assertEqual(expected['horizon_s'], 3660.)
            self.assertEqual(expected['command'][expected['command'].index('--exact-seconds')+1], '3600')

    def test_long_stage_does_not_censor_at_600_and_retains_real_after_3600_claims(self):
        task = {'instance_name': 'C104', 'instance_sha256': 'input', 'atf_sha256': 'atf',
                'horizon_s': 3600., 'process_horizon_s': 3660.}
        events = [{'event': 'candidate', 'sequence': 0, 'elapsed_s': 1., 'routes': [[1]]},
            {'event': 'phase', 'sequence': 1, 'elapsed_s': 2., 'phase': 'exact_stage_start'},
            {'event': 'closure', 'sequence': 2, 'elapsed_s': 3150., 'global_lb': 100.},
            {'event': 'bound', 'sequence': 3, 'elapsed_s': 3603., 'global_lb': 101.}]
        validation = {k: task[k] for k in ['instance_name', 'instance_sha256', 'atf_sha256']}
        validation.update(checker=fixed.EXPECTED_CHECKER,
                          candidates=[{**events[0], 'valid': True, 'checked_cost': 100.}])
        start, accepted, evidence = fixed.stage_evidence({'validation': validation}, events, [], task)
        self.assertIn(events[2], accepted)
        self.assertEqual(evidence['raw_closure_claims'][0]['elapsed_s'], 3148.)
        self.assertEqual(evidence['global_bound_claims'][-1]['elapsed_s'], 3601.)
        self.assertEqual(evidence['global_bound_claims'][-1]['timing_scope'], 'after_horizon')

    def test_actual_outcome_selected_full15_seed456_rep3_witness(self):
        project = Path(__file__).resolve().parents[1]
        source = project / 'outputs/ms58/dabia-20261110/gros-five-arm-1h-v1'
        inputs = Path('/home/onyr/code/phd/mamut-routing-deploy/Mamut-routing/benchmarks/TDVRPTW/Dabia2013/n=100')
        if not (source / 'wave.json').exists() or not inputs.exists():
            self.skipTest('Local completed source wave or materialized C104 is unavailable')
        design = {'source_wave': {'path': str(source), 'sha256': sha256(source / 'wave.json')}, 'inputs': str(inputs)}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'witnesses').mkdir()
            rows = fixed.freeze_inputs_and_witnesses(design, output, fixed.EXPECTED_CHECKER, c104_confirmation=True)
            self.assertEqual(set(rows), {'C104'})
            witness = json.loads(Path(rows['C104']['witness_path']).read_bytes())
            self.assertEqual((witness['source_seed'], witness['source_repetition'], witness['source_prefix_s']), (456, 3, 15))
            self.assertEqual(witness['source_run_id'], '140ee8911177c4d3234b1b81dd5498bbdba22d2255a5b381b564f4372da359f1')
            self.assertAlmostEqual(witness['source_reported_closure_s'], 3158.282500852)
            self.assertEqual(witness['routes_sha256'], 'f4a851bf2682977d4fda6383bdf0e77a6f962d1dd07d4473f4d923d2b5c84281')
            self.assertTrue(witness['outcome_selected_mechanism_case'])
            self.assertEqual(sum(map(len, witness['routes'])), 100)
            reference = json.loads(Path(rows['C104']['proof_reference_path']).read_bytes())
            self.assertEqual(rows['C104']['reference_cost'], reference['checked_cost'])
            self.assertLess(reference['checked_cost'], witness['checked_cost'])
            self.assertEqual(sha256(Path(rows['C104']['proof_reference_path'])), rows['C104']['reference_manifest_sha256'])
            self.assertEqual({r['kind'] for r in rows['C104']['external_feasible_references']},
                             {'source_run_checked_incumbent', 'original_frozen_n100_reference'})

    def test_all_four_stored_gates_bind_the_declared_sources_and_runtime_manifests(self):
        variants = Path(__file__).resolve().parents[1] / 'outputs/ms58/dabia-20261110/warm-gates-v1/variants.json'
        if not variants.exists():
            self.skipTest('Local stored warm-handoff gate artifacts are unavailable')
        records = c104.local_variant_artifacts(variants)
        self.assertEqual(set(records), set(fixed.SOURCES))
        self.assertTrue(all(records[k]['source_id'] == fixed.SOURCES[k] for k in fixed.SOURCES))


if __name__ == '__main__':
    unittest.main()
