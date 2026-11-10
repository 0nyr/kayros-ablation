"""Offline witness and clock/proof regressions. These tests launch no solvers."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from checker_contract import sha256
from ms58_fixed_witness import (BASE, EXACT_SECONDS, EXPECTED_CHECKER, INSTANCES,
    apply_feasible_consistency, audit, cell, cell_contract, checked_witness, digest,
    freeze_inputs_and_witnesses, stage_evidence, validate_original_routes)
from ms58_fixed_witness_worker import arm_stage_deadline


class FixedWitnessTests(unittest.TestCase):
    def raw(self):
        return {'num_customers': 3, 'demands': [0, 1, 2, 3], 'vehicle_capacity': 4,
                'num_vehicles': 3}

    def test_all_original_ids_are_checked_before_demand_indexing(self):
        good = [[1, 2], [3]]
        validate_original_routes(self.raw(), good)
        for invalid in (True, False, 1., '1', -1, 0, 4, 10**9, None):
            routes = [[1, 2], [invalid]]
            raw = self.raw()
            raw['demands'] = None
            with self.assertRaisesRegex(ValueError, 'Original customer IDs'):
                validate_original_routes(raw, routes)

    def test_partition_capacity_and_empty_routes_fail(self):
        for routes in ([], [[]], [[1, 2], [2]], [[1], [2]], [[1, 2, 3]]):
            with self.assertRaises(ValueError):
                validate_original_routes(self.raw(), routes)
        raw = self.raw()
        raw['demands'][3] = float('nan')
        with self.assertRaisesRegex(ValueError, 'finite'):
            validate_original_routes(raw, [[1, 2], [3]])

    def test_kernel_and_native_child_share_the_exact_stage_deadline(self):
        with patch.dict(os.environ, PROBE_START_MONOTONIC_NS='42', PROBE_DEADLINE_MONOTONIC_NS='999999999999'), \
                patch('ms58_fixed_witness_worker.signal.signal'), \
                patch('ms58_fixed_witness_worker.signal.setitimer') as alarm, \
                patch('ms58_fixed_witness_worker.time.monotonic_ns', return_value=120):
            deadline = arm_stage_deadline(100)
            self.assertEqual(deadline, 600000000100)
            self.assertEqual(os.environ['PROBE_DEADLINE_MONOTONIC_NS'], str(deadline))
            self.assertEqual(os.environ['PROBE_START_MONOTONIC_NS'], '42')
            self.assertAlmostEqual(alarm.call_args.args[1], 599.99999998)

    def proof_fixture(self):
        routes = [[1]]
        task = {'instance_name': 'C201', 'instance_key': 'Dabia2013/n=100/C201',
            'instance_sha256': 'input', 'atf_sha256': 'atf', 'reference_cost': 100.,
            'reference_manifest_sha256': 'reference', 'horizon_s': EXACT_SECONDS}
        candidate = {'event': 'candidate', 'sequence': 0, 'elapsed_s': 1., 'routes': routes}
        events = [candidate, {'event': 'phase', 'sequence': 1, 'elapsed_s': 2., 'phase': 'exact_stage_start'},
            {'event': 'closure', 'sequence': 2, 'elapsed_s': 12., 'global_lb': 100., 'status': 'Optimum'}]
        result = {'validation': {k: task[k] for k in ['instance_name', 'instance_sha256', 'atf_sha256']}}
        result['validation'].update(checker=EXPECTED_CHECKER, candidates=[{**candidate, 'valid': True, 'checked_cost': 100.}])
        return task, events, result

    def test_caller_witness_supports_empty_native_route_closure(self):
        task, events, result = self.proof_fixture()
        result['native_result'] = {'value': 100., 'routes': []}
        start, accepted, evidence = stage_evidence(result, events, [], task)
        self.assertEqual(start, 2.)
        self.assertEqual(evidence['checked_candidates'][0]['elapsed_s'], 0.)
        self.assertEqual(evidence['raw_closure_claims'][0]['elapsed_s'], 10.)
        row = {**task, **evidence, 'task_id': 'fixture', 'run_id': 'fixture',
            'audit_ok': True, 'finished_task': True, 'raw_result_status': 'completed',
            'reported_closure_s': 10., 'capped_reported_closure_s': 10.}
        apply_feasible_consistency([row])
        self.assertEqual(row['reported_closure_s'], 10.)
        self.assertTrue(row['proof_observation_usable'])

    def test_after_stage_candidate_is_excluded_but_false_late_bound_refutes(self):
        task, events, result = self.proof_fixture()
        events += [{'event': 'candidate', 'sequence': 3, 'elapsed_s': 603., 'routes': [[1]]},
                   {'event': 'bound', 'sequence': 4, 'elapsed_s': 604., 'global_lb': 101.}]
        result['validation']['candidates'].append({**events[3], 'valid': True, 'checked_cost': 1.})
        _, _, evidence = stage_evidence(result, events, [], task)
        self.assertEqual(len(evidence['checked_candidates']), 1)
        self.assertEqual(evidence['global_bound_claims'][1]['timing_scope'], 'after_horizon')
        row = {**task, **evidence, 'task_id': 'fixture', 'run_id': 'fixture',
            'reported_closure_s': 10., 'capped_reported_closure_s': 10.}
        apply_feasible_consistency([row])
        self.assertIsNone(row['reported_closure_s'])
        self.assertEqual(row['consistency_audit']['status'], 'contradicted')

    def test_cell_identity_binds_witness_core_variant_and_process_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'frozen-scripts'
            scripts.mkdir()
            (scripts / 'ms58_fixed_witness_worker.py').write_text('fixture')
            task = {'cpu_core': 3, 'instance_path': '/input', 'witness_path': '/witness',
                    'witness_sha256': 'frozen-witness', 'arm_id': 'ub-only'}
            spec = {'worker_python': sys.executable, 'worker_python_sha256': sha256(Path(sys.executable)),
                    'path': '/runtime-manifest', 'sha256': 'manifest-pin'}
            expected, _, native = cell_contract(task, spec, {'worker_environment': {}}, scripts, root)
            self.assertEqual(expected['horizon_s'], 660.)
            self.assertIn('frozen-witness', expected['command'])
            self.assertEqual(native.parent, root / 'cells/native')
            for key, value in [('cpu_core', 4), ('arm_id', 'dedup-only'), ('witness_sha256', 'other')]:
                changed, _, _ = cell_contract(dict(task, **{key: value}), spec, {'worker_environment': {}}, scripts, root)
                self.assertNotEqual(digest(expected), digest(changed))

    def test_actual_cell_entrypoint_uses_the_frozen_expected_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'plan.json').write_text('{}')
            scripts = root / 'frozen-scripts'
            scripts.mkdir()
            (scripts / 'ms58_fixed_witness_worker.py').write_text('fixture')
            manifest = {'worker_environment': {}}
            manifest_path = root / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest))
            task = {'cpu_core': min(os.sched_getaffinity(0)), 'instance_path': '/input',
                'witness_path': '/witness', 'witness_sha256': 'witness', 'arm_id': 'ub-only', 'runtime_key': 'ub-only'}
            task_path = root / 'task.json'
            task_path.write_text(json.dumps(task))
            spec = {'worker_python': sys.executable, 'worker_python_sha256': sha256(Path(sys.executable)),
                    'path': str(manifest_path), 'sha256': sha256(manifest_path)}
            plan = {'checker': EXPECTED_CHECKER, 'runtimes': {'ub-only': spec},
                    'scheduled_cells': [{k: v for k, v in task.items() if k != 'cpu_core'}]}
            (root / 'launch.json').write_text(json.dumps({'plan_sha256': sha256(root / 'plan.json'),
                'scheduled_cells': [task], 'job_id': '123'}))
            with patch.dict(os.environ, OAR_JOB_ID='123'), \
                    patch('ms58_fixed_witness.verify_plan', return_value=plan), \
                    patch('ms58_fixed_witness.checker_identity', return_value=EXPECTED_CHECKER), \
                    patch('ms58_fixed_witness.run_probe', return_value={}) as probe, \
                    contextlib.redirect_stdout(io.StringIO()):
                expected, _, _ = cell_contract(task, spec, manifest, scripts, root)
                cell(SimpleNamespace(output=root, task=task_path))
            observed = probe.call_args.kwargs
            actual = {'schema_version': 1, 'manifest_hash': digest(observed['manifest']),
                'parameters': observed['parameters'], 'command': observed['command'],
                'horizon_s': observed['horizon_s'], 'cwd': str(observed['cwd']),
                'environment_hash': digest(observed['environment'])}
            self.assertEqual(expected, actual)

    def test_empty_native_routes_keep_checked_caller_closure_and_failed_policy_removes_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            witness = {'routes': [[1]], 'routes_sha256': digest([[1]]), 'checked_cost': 100.}
            witness_path = root / 'witness.json'
            witness_path.write_text(json.dumps(witness))
            expected = {'fixture': 'frozen-identity'}
            run_id = digest(expected)
            directory = root / 'cells' / run_id
            directory.mkdir(parents=True)
            task, _, _ = self.proof_fixture()
            task.update(arm_id='ub-only', runtime_key='ub-only', warm=True, cpu_core=0,
                        witness_path=str(witness_path), witness_sha256=sha256(witness_path))
            identity = {**expected, 'run_id': run_id, 'manifest_hash': 'manifest', 'schema_version': 1, 'horizon_s': 660.}
            (directory / 'identity.json').write_text(json.dumps(identity))
            events = [{'event': 'phase', 'sequence': 0, 'elapsed_s': .1, 'phase': 'fixed_witness_ready',
                'routes_sha256': witness['routes_sha256'], 'checked_cost': 100., 'supplied_to_native': True,
                'cpu_affinity': [0], 'address_space_limit_bytes': 16384*1024**2},
                {'event': 'candidate', 'sequence': 1, 'elapsed_s': 1., 'routes': [[1]], 'native_cost': 100.},
                {'event': 'phase', 'sequence': 2, 'elapsed_s': 2., 'phase': 'exact_stage_start'},
                {'event': 'closure', 'sequence': 3, 'elapsed_s': 12., 'global_lb': 100., 'status': 'Optimum'}]
            (directory / 'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            (directory / 'late-events.jsonl').write_text('')
            (directory / 'stderr.log').write_text('')
            payload = {'run_id': run_id, 'manifest_hash': 'manifest',
                'caller_witness_sha256': task['witness_sha256'], 'caller_witness_routes_sha256': witness['routes_sha256'],
                'caller_witness_supplied_to_native': True, 'exact': {'value': 100., 'routes': [],
                    'effective_backend_settings': {'lp': {'backend': 'CPLEX', 'threads': 1}},
                    'effective_algorithm_settings': {'label_correction': True, 'lazy_extension': True,
                        'cut_limit': 500, 'algorithm_epsilon': 1e-6, 'warm_start_policy': 'incumbent_only'},
                    'warm_start': {'routes_given': 1, 'routes_feasible': 1, 'routes_added': 0, 'ub': 100.,
                        'ub_set': True, 'policy': 'incumbent_only', 'witness_storage': 'caller'}}}
            native = directory / 'native.json'
            native.write_text(json.dumps(payload))
            validation = {k: task[k] for k in ['instance_name', 'instance_sha256', 'atf_sha256']}
            validation.update(checker=EXPECTED_CHECKER,
                candidates=[{**events[1], 'valid': True, 'checked_cost': 100., 'native_minus_checked': 0.}],
                native_result={'relative_path': 'native.json', 'sha256': sha256(native)})
            result = {'run_id': run_id, 'returncode': 0, 'solver_elapsed_s': 13.,
                      'status': 'completed', 'issues': [], 'validation': validation}
            (directory / 'result.json').write_text(json.dumps(result))
            with patch('ms58_fixed_witness.load_completed', return_value=result):
                observed = audit(task, root, result, expected)
                self.assertTrue(observed['audit_ok'], observed['checks'])
                self.assertEqual(observed['reported_closure_s'], 10.)
                payload['exact']['warm_start']['routes_added'] = 1
                native.write_text(json.dumps(payload))
                rejected = audit(task, root, result, expected)
            self.assertFalse(rejected['audit_ok'])
            self.assertIsNone(rejected['reported_closure_s'])
            self.assertEqual(rejected['raw_reported_closure_s'], 10.)
            self.assertTrue(rejected['checked_candidates'])
            self.assertTrue(rejected['raw_closure_claims'])

    def test_actual_four_seed123_witnesses_pass_the_independent_full_checker(self):
        project = Path(__file__).resolve().parents[1]
        wave = project / 'outputs/ms58/dabia-20261110/gros-five-arm-1h-v1'
        inputs = Path('/home/onyr/code/phd/mamut-routing-deploy/Mamut-routing/benchmarks/TDVRPTW/Dabia2013/n=100')
        if not (wave / 'wave.json').exists() or not inputs.exists():
            self.skipTest('Local main-panel artifacts or materialized inputs are unavailable')
        design = {'source_wave': {'path': str(wave), 'sha256': sha256(wave / 'wave.json')}, 'inputs': str(inputs)}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / 'witnesses').mkdir()
            rows = freeze_inputs_and_witnesses(design, output, EXPECTED_CHECKER)
            self.assertEqual(set(rows), set(INSTANCES))
            for name, row in rows.items():
                witness = json.loads(Path(row['witness_path']).read_bytes())
                self.assertEqual((witness['source_seed'], witness['source_repetition'], witness['source_prefix_s']), (123, 2, 5))
                self.assertEqual(sum(map(len, witness['routes'])), 100)
                self.assertEqual(witness['checked_cost'], row['reference_cost'])
                checked_witness(Path(row['instance_path']), witness['routes'], witness['checked_cost'])


if __name__ == '__main__':
    unittest.main()
