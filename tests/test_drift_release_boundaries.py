"""Verify production cohort and reference binding and durable Drift API retirement."""
import unittest
from pathlib import Path
import json
from runpy import run_path
from tempfile import TemporaryDirectory

from services.drift_api import contracts, reference_matches_model
from services.drift_api.runtime import DriftRuntime, StoreFull, WindowConflict
from services.predictor.binding import LEGACY


FEATURES = {'temperature_mean_10m': 30.0, 'vibration_max_10m': 1.0, 'current_mean_10m': 12.0,
            'current_std_10m': 1.0, 'rpm_mean_10m': 3000.0, 'reading_count_10m': 10,
            'machine_age_days': 180.0, 'prior_failures_24h': 0}
MODEL = {'name': 'iot-pump-failure-v1', 'version': '7', 'run_id': '1' * 32,
         'data_version': '2' * 64, 'protocol_sha256': '3' * 64}
LINEAGE = {'model_name': MODEL['name'], 'model_version': MODEL['version'], 'model_run_id': MODEL['run_id'],
           'data_version': MODEL['data_version'], 'protocol_sha256': MODEL['protocol_sha256'], 'partition': 'train'}


def bundle():
    """Use a small standalone reference with an exact model-release identity."""
    return {'format': 'iot-drift-reference-v1', 'lineage': LINEAGE,
            'policy': {'bins': 10, 'psi_threshold': .2, 'drifted_feature_fraction': .25, 'pseudocount': .5},
            'samples': [FEATURES] * 32}


def window(reference, identity, changed=False):
    """Build three independent replay outcomes without needing an API process."""
    return contracts.CompareRequest.model_validate({'reference_id': reference, 'window_id': identity,
        'start': '2026-10-01T00:00:00Z', 'end': '2026-10-01T01:00:00Z',
        'observations': [{'id': str(i), 'event_timestamp': '2026-10-01T00:05:00Z',
                          'available_at': '2026-10-01T00:06:00Z',
                          'features': dict(FEATURES, reading_count_10m=11 if changed else 10)} for i in range(32)]})


class DriftReleaseBoundaries(unittest.TestCase):
    """Pin both the change of reference and the capacity boundary."""

    def test_release_reference_binds_train_partition_and_all_identity_fields(self):
        """Refuse another run, data version, protocol or non-training partition."""
        self.assertTrue(reference_matches_model(LINEAGE, MODEL))
        self.assertFalse(reference_matches_model({'partition': 'train'}, MODEL))
        for field in LINEAGE:
            self.assertFalse(reference_matches_model(dict(LINEAGE, **{field:'other'}), MODEL))
        for field in MODEL:
            self.assertFalse(reference_matches_model(LINEAGE, dict(MODEL, **{field:'other'})))

    def test_capacity_and_archived_replay_survive_restart(self):
        """Retire a window without breaking conflict detection or changing the stored response."""
        with TemporaryDirectory() as temp:
            database = Path(temp) / 'windows.db'
            runtime = DriftRuntime(bundle(), database, max_windows=2)
            reference = runtime.detector.reference_id
            initial = runtime.compare(window(reference, 'old'))
            runtime.compare(window(reference, 'next'))
            self.assertEqual(runtime.capacity()['accepts_new_windows'], False)
            with self.assertRaises(StoreFull):
                runtime.compare(window(reference, 'new'))
            self.assertEqual(runtime.archive_windows(keep_active=1), 1)
            runtime = DriftRuntime(bundle(), database, max_windows=2)
            self.assertTrue(runtime.capacity()['accepts_new_windows'])
            self.assertEqual(runtime.capacity()['archived_windows'], 1)
            self.assertEqual(runtime.compare(window(reference, 'old')), initial)
            with self.assertRaises(WindowConflict):
                runtime.compare(window(reference, 'old', changed=True))
            self.assertEqual(runtime.compare(window(reference, 'new'))['window_id'], 'new')

    def test_serving_configmap_is_immutable_and_uses_the_exact_reference(self):
        """Keep the same loaded bytes and refuse a reference for another registry version."""
        builder = run_path(str(Path(__file__).resolve().parents[1] / 'scripts/build-drift-reference.py'))
        pin = dict(LEGACY, version='7', production=True)
        current = bundle()
        current['lineage'] = dict(LINEAGE, model_run_id=pin['run_id'], data_version=pin['data_version'],
                                  protocol_sha256=pin['protocol_sha256'])
        configmap = builder['serving_configmap'](current, pin)
        self.assertTrue(configmap['immutable'])
        self.assertEqual(json.loads(configmap['data']['reference.json']), current)
        self.assertEqual(configmap['metadata']['namespace'], 'iot-features')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            builder['serving_configmap'](current, dict(pin, version='6'))
