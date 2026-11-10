"""Small orchestration invariants without a solver or OAR allocation."""
import json
from pathlib import Path
import tempfile
import unittest

from checker_contract import sha256
from ms58_paired_probe import frozen_existing_manifest, paired_schedule


class PairedScheduleTests(unittest.TestCase):
    def test_every_pair_is_sequential_and_alternates_order(self):
        schedule = paired_schedule(['C104', 'R201'], ['candidate', 'upstream'], 3, 42)
        self.assertEqual(len(schedule), 24)
        self.assertEqual(len({cell['cell_id'] for cell in schedule}), 24)
        starts = {}
        for index in range(0, len(schedule), 2):
            a, b = schedule[index:index + 2]
            self.assertEqual(a['pair_id'], b['pair_id'])
            self.assertEqual([a['position'], b['position']], [1, 2])
            self.assertEqual({a['variant'], b['variant']}, {'reference', a['comparison']})
            starts[(a['instance'], a['comparison'], a['repetition'])] = a['variant']
        for name in ['C104', 'R201']:
            for comparison in ['candidate', 'upstream']:
                self.assertNotEqual(starts[(name, comparison, 1)], starts[(name, comparison, 2)])
                self.assertEqual(starts[(name, comparison, 1)], starts[(name, comparison, 3)])
        self.assertEqual(schedule, paired_schedule(['C104', 'R201'], ['candidate', 'upstream'], 3, 42))

    def test_archived_paths_rebind_only_when_bytes_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['patches', 'scripts', 'fallback']:
                (root / name).mkdir()
            patch = root / 'fallback/test.patch'
            patch.write_text('corrected source bytes\n')
            worker = root / 'scripts/upstream_event_worker.py'
            worker.write_text('preserved control worker\n')
            manifest = {'schema_version': 1, 'checker': {'identity': 'test'},
                        'source_patch_files': {'/nonexistent/ms58-test/test.patch': sha256(patch)},
                        'runtime_files': {'/nonexistent/ms58-test/upstream_event_worker.py': sha256(worker)}}
            source = root / 'manifest.json'
            source.write_text(json.dumps(manifest))
            rebound = frozen_existing_manifest(source, root / 'patches', root / 'scripts', root / 'fallback', manifest['checker'])
            self.assertEqual(list(rebound['runtime_files']), [str(worker)])
            self.assertEqual(list(rebound['source_patch_files']), [str(root / 'patches/test.patch')])
            worker.write_text('changed control worker\n')
            with self.assertRaisesRegex(ValueError, 'runtime mismatch'):
                frozen_existing_manifest(source, root / 'patches', root / 'scripts', root / 'fallback', manifest['checker'])


if __name__ == '__main__':
    unittest.main()
