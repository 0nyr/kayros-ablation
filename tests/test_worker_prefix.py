"""Check fixed-prefix handoff with early and late-returning native ILS stubs."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


class PrefixHandoffTests(unittest.TestCase):
    def run_worker(self, late):
        spec = importlib.util.spec_from_file_location('probe_test_worker', Path(__file__).parents[1]/'scripts/kayros_worker.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        clock = [0]
        handoffs = []
        core = types.ModuleType('kayros._core')

        def ils(instance, params, seed, budget, callback):
            clock[0] = 5_000_000_000
            callback(types.SimpleNamespace(value=10., seconds=5.), [[1, 2]])
            if late:
                clock[0] = 12_000_000_000
                callback(types.SimpleNamespace(value=1., seconds=12.), [[2, 1]])
            return types.SimpleNamespace(value=1. if late else 10., routes=[[2, 1]] if late else [[1, 2]])

        core.solve_ils = ils
        kayros = types.ModuleType('kayros')
        kayros.__path__ = []
        kayros.__version__ = 'stub'
        kayros._core = core
        kayros._lera = types.SimpleNamespace(LP_BACKEND='CPLEX')
        kayros.Params = lambda **kwargs: types.SimpleNamespace(_to_ils_core=lambda: None)
        lera = types.ModuleType('kayros.lera')
        lera.routes_to_mamut = lambda *args: None

        def exact(loaded, **kwargs):
            handoffs.append((clock[0], kwargs['initial_routes']))
            return {'exact_log': {'status': 'TimeLimitReached'}}

        lera.solve_duration = exact
        routing = types.ModuleType('mamut_routing_lib.td.artifacts')
        routing.load_td_instance = lambda *args, **kwargs: types.SimpleNamespace(instance=types.SimpleNamespace(num_customers=2))
        core_io = types.ModuleType('kayros.io')
        core_io.to_core = lambda *args, **kwargs: None
        checker = types.ModuleType('checker_contract')
        checker.checker_identity = lambda: {'test': True}
        modules = {'kayros': kayros, 'kayros._core': core, 'kayros.lera': lera, 'kayros.io': core_io,
                   'mamut_routing_lib': types.ModuleType('mamut_routing_lib'),
                   'mamut_routing_lib.td': types.ModuleType('mamut_routing_lib.td'),
                   'mamut_routing_lib.td.artifacts': routing, 'checker_contract': checker}
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules), patch.dict(os.environ, {
            'PROBE_START_MONOTONIC_NS': '0', 'PROBE_DEADLINE_MONOTONIC_NS': '30000000000', 'PROBE_CPU_AFFINITY': '0'}), \
                patch.object(sys, 'argv', ['worker', '--instance', 'unused', '--arm', 'kayros-full-cplex', '--prefix-seconds', '10', '--native-result', str(Path(tmp)/'native.json')]), \
                patch.object(worker.os, 'sched_setaffinity'), patch.object(worker.os, 'sched_getaffinity', return_value={0}), \
                patch.object(worker.resource, 'setrlimit'), patch.object(worker.time, 'monotonic_ns', side_effect=lambda: clock[0]), \
                patch.object(worker.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0]+int(seconds*1e9))), \
                contextlib.redirect_stdout(output):
            worker.main()
            native = json.loads((Path(tmp)/'native.json').read_text())
        return handoffs, [json.loads(line) for line in output.getvalue().splitlines()], native

    def test_early_return_waits_for_fixed_boundary(self):
        handoffs, events, native = self.run_worker(False)
        self.assertEqual(handoffs, [(10_000_000_000, [[1, 2]])])

    def test_late_improvement_cannot_replace_prefix_witness(self):
        handoffs, events, native = self.run_worker(True)
        self.assertEqual(handoffs, [(12_000_000_000, [[1, 2]])])
        self.assertEqual(native['handoff_witness']['routes'], [[1, 2]])
        self.assertTrue(any(e.get('phase') == 'ils_late_candidate' for e in events))
        self.assertFalse(any(e['event'] == 'candidate' and e['native_cost'] == 1. for e in events))


if __name__ == '__main__':
    unittest.main()
