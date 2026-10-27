"""Check versioned alarm-policy ambiguity, threshold boundaries and the safe broadcast dimension contract."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from pipelines.factory_policy import load_policy, threshold_milli, validate_policy


class FactoryPolicyTests(unittest.TestCase):
    """Configuration errors must be rejected before a join can drop or multiply machine readings."""

    def setUp(self):
        """Represent two valid plants with distinct operational thresholds."""
        self.policy = {'version': 'test-v1', 'factories': [
            {'factory_id': 'F-02', 'temperature_limit_c': 80, 'vibration_limit_mm_s': 7.5},
            {'factory_id': 'F-01', 'temperature_limit_c': 75, 'vibration_limit_mm_s': 7}]}

    def test_exact_threshold_units_and_input_unchanged(self):
        """Decimal 7.501 must stay one milli-unit above 7.5 without changing the supplied configuration."""
        before = deepcopy(self.policy)
        result = validate_policy(self.policy)
        self.assertEqual([row['factory_id'] for row in result['factories']], ['F-01', 'F-02'])
        self.assertEqual(result['factories'][1]['vibration_limit_milli'], 7500)
        self.assertEqual(threshold_milli(Decimal('7.501')), 7501)
        self.assertEqual(threshold_milli(Decimal('0.001')), 1)
        self.assertEqual(threshold_milli(1_000_000), 1_000_000_000)
        self.assertEqual(self.policy, before)

    def test_invalid_numeric_partitions_and_precision_boundary(self):
        """Reject non-numbers, zero/negative/nonfinite values and values requiring silent precision loss."""
        for value in (True, '7.5', None, 0, -1, float('nan'), float('inf'), 1_000_000.001, Decimal('0.0009')):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                threshold_milli(value)

    def test_duplicate_or_missing_factory_configuration_is_rejected(self):
        """A duplicated dimension key would multiply joined readings even when its two threshold rows match."""
        duplicate = deepcopy(self.policy)
        duplicate['factories'].append(deepcopy(duplicate['factories'][0]))
        missing = deepcopy(self.policy)
        del missing['factories'][0]['temperature_limit_c']
        empty = {**self.policy, 'factories': []}
        for policy in (duplicate, missing, empty):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate_policy(policy)

    def test_unknown_fields_and_invalid_identity_fail_closed(self):
        """Do not silently ignore a misspelled threshold or an unversioned policy change."""
        unknown = deepcopy(self.policy)
        unknown['factories'][0]['temperature_limt_c'] = 81
        for policy in (unknown, {**self.policy, 'version': ''}, {**self.policy, 'version': None},
                       {**self.policy, 'unexpected': 1}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate_policy(policy)

    def test_json_decimal_loading_keeps_milli_precision(self):
        """The versioned file boundary preserves decimal values instead of introducing binary-float rounding."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'
            path.write_text('{"version":"v1","factories":[{"factory_id":"F-01","temperature_limit_c":75.001,"vibration_limit_mm_s":7.501}]}')
            value = load_policy(path)['factories'][0]
        self.assertEqual(value['temperature_limit_milli'], 75001)
        self.assertEqual(value['vibration_limit_milli'], 7501)


if __name__ == '__main__':
    unittest.main()
