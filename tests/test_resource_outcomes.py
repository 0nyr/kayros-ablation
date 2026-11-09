import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from pilot_summary import (resource_failure_kind, digest, UPSTREAM_BINARY_SHA256,
                           UPSTREAM_ADAPTER_SHA256, KAYROS_LERA_SHA256, KAYROS_ADAPTER_SHA256)


def evidence(arm='lera-exact', code=3, status='failed'):
    runtime = {'/frozen/worker.py': UPSTREAM_ADAPTER_SHA256,
               '/build/bp': UPSTREAM_BINARY_SHA256}
    if arm.startswith('kayros-'):
        del runtime['/frozen/worker.py']
        runtime['/venv/_lera.cpython-313.so'] = next(iter(KAYROS_LERA_SHA256))
    manifest = {'runtime_files': runtime, 'worker_environment': {'UPSTREAM_PROBE_BINARY': '/build/bp'}}
    identity = {'command': ['/python', '/frozen/worker.py'],
                'parameters': {'arm': arm, 'worker_sha256': KAYROS_ADAPTER_SHA256 if arm.startswith('kayros-') else UPSTREAM_ADAPTER_SHA256}, 'manifest_hash': digest(manifest)}
    run = digest(identity)
    identity.update(run_id=run, manifest=manifest)
    result = {'status': status, 'returncode': code, 'run_id': run,
              'manifest_hash': digest(manifest)}
    return identity, result


class ResourceOutcomes(unittest.TestCase):
    def test_pinned_upstream_native_exit(self):
        identity, result = evidence()
        self.assertEqual(resource_failure_kind(result, b'', identity=identity),
                         'upstream_native_allocation_failure')

    def test_generic_exit_and_unknown_identity_rejected(self):
        identity, result = evidence()
        self.assertIsNone(resource_failure_kind(result, b''))
        identity['manifest']['runtime_files']['/build/bp'] = 'unknown'
        self.assertIsNone(resource_failure_kind(result, b'', identity=identity))

    def test_rehashed_unknown_adapter_is_rejected(self):
        for arm, code, status in [('lera-exact', 3, 'failed'), ('kayros-bpca-cplex', 0, 'completed')]:
            identity, result = evidence(arm, code, status)
            if arm.startswith('kayros-'):
                identity['parameters']['worker_sha256'] = 'unknown'
            else:
                identity['manifest']['runtime_files']['/frozen/worker.py'] = 'unknown'
            identity['manifest_hash'] = digest(identity['manifest'])
            identity['run_id'] = digest({k: v for k, v in identity.items() if k not in ('manifest', 'run_id')})
            result.update(run_id=identity['run_id'], manifest_hash=identity['manifest_hash'])
            events = [{'phase': 'exact_return', 'status': 'MemoryLimitReached'}]
            self.assertIsNone(resource_failure_kind(result, b'', identity=identity, events=events))

    def test_rehashed_unknown_selected_binary_with_known_decoy_rejected(self):
        identity, result = evidence()
        identity['manifest']['worker_environment']['UPSTREAM_PROBE_BINARY'] = '/other/bp'
        identity['manifest']['runtime_files']['/other/bp'] = 'unknown'
        identity['manifest_hash'] = digest(identity['manifest'])
        identity['run_id'] = digest({k: v for k, v in identity.items() if k not in ('manifest', 'run_id')})
        result.update(run_id=identity['run_id'], manifest_hash=identity['manifest_hash'])
        self.assertIsNone(resource_failure_kind(result, b'', identity=identity))

    def test_wrong_adapter_and_signal_rejected(self):
        identity, result = evidence()
        identity['command'][1] = '/other.py'
        self.assertIsNone(resource_failure_kind(result, b'', identity=identity))
        identity, result = evidence(code=-9)
        self.assertIsNone(resource_failure_kind(result, b'', identity=identity))

    def test_graceful_memory_limit_keeps_completed_status(self):
        identity, result = evidence('kayros-bpca-cplex', 0, 'completed')
        events = [{'phase': 'exact_return', 'status': 'MemoryLimitReached'}]
        self.assertEqual(resource_failure_kind(result, b'', identity=identity, events=events),
                         'native_memory_limit_reached')
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(resource_failure_kind(result, b'', identity=identity))


if __name__ == '__main__':
    unittest.main()
