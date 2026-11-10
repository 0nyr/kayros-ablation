"""Offline fixed-reference metadata regression checks without changing controller evidence."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'analysis'))
from ms58_correction_batch_audit import algorithm_setting_audit, classify_observation
from ms58_dabia_screen import LERA, LERA_BINARY, LERA_PATCH


def reference_manifest():
    return dict(source_id=LERA, declared_source_commit=LERA, backend='cplex', diagnostic_environment={},
                worker_environment={'UPSTREAM_PROBE_BINARY': '/pinned/f2'},
                runtime_files={'/pinned/f2': LERA_BINARY}, source_patch_files={'minimal.patch': LERA_PATCH})


def task(variant='minimal-lera'):
    return dict(variant=variant, source_id=LERA if variant == 'minimal-lera' else 'correction-source', horizon_s=600,
                expected_algorithm_settings=dict(label_correction=variant != 'minimal-lera', lazy_extension=True))


class FixedReferenceMetadataTests(unittest.TestCase):
    def test_missing_fixed_reference_fields_are_explicit_unknowns(self):
        audit = algorithm_setting_audit(task(), {'exact': {}}, reference_manifest())
        self.assertEqual(audit['status'], 'unknown')
        self.assertTrue(audit['fixed_reference_source_pins_verified'])
        for field in audit['fields'].values():
            self.assertEqual(field['status'], 'unknown')
            self.assertFalse(field['blocking'])
            self.assertEqual(field['observed_fields'], {})

    def test_unknown_fields_require_exact_source_binary_and_patch_pins(self):
        for field in ('source_id', 'declared_source_commit', 'runtime_files', 'source_patch_files'):
            manifest = reference_manifest()
            manifest[field] = 'wrong' if isinstance(manifest[field], str) else {}
            self.assertEqual(algorithm_setting_audit(task(), {'exact': {}}, manifest)['status'], 'failed')

    def test_actual_fixed_reference_mismatch_fails_even_with_another_missing_field(self):
        for observed in ({'label_correction': True}, {'lazy_extension': False}, {'label_correction': None},
                         {'lazy_extension': True, 'label_lazy_extension': False}):
            audit = algorithm_setting_audit(task(), {'exact': {'effective_algorithm_settings': observed}}, reference_manifest())
            self.assertEqual(audit['status'], 'failed')

    def test_observed_matching_reference_settings_pass(self):
        native = {'exact': {'effective_algorithm_settings': {'label_correction': False, 'label_lazy_extension': True}}}
        self.assertEqual(algorithm_setting_audit(task(), native, reference_manifest())['status'], 'passed')

    def test_both_kayros_sources_still_require_observed_on_settings(self):
        for variant in ('correction-on', 'correction-on-domain-batch'):
            for settings, expected in (({}, 'failed'), ({'label_correction': True}, 'failed'),
                                       ({'label_correction': False, 'lazy_extension': True}, 'failed'),
                                       ({'label_correction': True, 'lazy_extension': True}, 'passed')):
                audit = algorithm_setting_audit(task(variant), {'exact': {'effective_algorithm_settings': settings}}, {})
                self.assertEqual(audit['status'], expected)

    def test_recompute_checked_closure_without_rewriting_or_losing_raw_controller_claims(self):
        raw = dict(reported_closure_s=None, observation_usable=False, algorithm_settings_audit={'status': 'failed'})
        saved = copy.deepcopy(raw)
        cell = dict(audit_ok=True, independent_backend_audit={'passed': True}, invalid_candidate_count=0,
                    native_guard_warning=False, raw_controller_row=raw,
                    raw_closure_claims=[{'elapsed_s': 5.25, 'global_lb': 100}])
        result = dict(status='completed', issues=[], validation={'candidates': [{'sequence': 0, 'valid': True}]})
        events = [dict(event='candidate', sequence=0, elapsed_s=5.0), dict(event='closure', sequence=1, elapsed_s=5.25)]
        updated = classify_observation(cell, task(), reference_manifest(), {'exact': {}}, result, events, '')
        self.assertEqual(updated['reported_closure_s'], 5.25)
        self.assertEqual(updated['raw_closure_event_times_s'], [5.25])
        self.assertTrue(updated['observation_usable'])
        self.assertEqual(updated['algorithm_settings_audit']['status'], 'unknown')
        self.assertEqual(updated['raw_controller_row'], saved)
        self.assertEqual(updated['raw_closure_claims'], [{'elapsed_s': 5.25, 'global_lb': 100}])
        mismatch = {'exact': {'effective_algorithm_settings': {'label_correction': True}}}
        failed = classify_observation(updated, task(), reference_manifest(), mismatch, result, events, '')
        self.assertIsNone(failed['reported_closure_s'])
        self.assertEqual(failed['closure_from_checked_events_before_settings_s'], 5.25)
        self.assertEqual(failed['raw_closure_event_times_s'], [5.25])
        self.assertEqual(failed['raw_controller_row'], saved)


if __name__ == '__main__':
    unittest.main()
