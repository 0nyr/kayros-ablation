"""Virtual address-space maxima belong to individual processes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sample_virtual_memory import largest_member


class VirtualMemoryMonitorTests(unittest.TestCase):
    def test_virtual_sizes_are_maximized_per_process_never_summed(self):
        members = {'1': {'pid': 1, 'VmSize_kib': 100, 'VmPeak_kib': 180},
                   '2': {'pid': 2, 'VmSize_kib': 150, 'VmPeak_kib': 160}}
        self.assertEqual(largest_member(members, 'VmSize_kib'), {'value_kib': 150, 'owner_pid': 2})
        self.assertEqual(largest_member(members, 'VmPeak_kib'), {'value_kib': 180, 'owner_pid': 1})
        self.assertIsNone(largest_member({}, 'VmSize_kib'))


if __name__ == '__main__':
    unittest.main()
