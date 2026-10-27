"""Measure numeric covariate shift using fixed reference quantile bins and explicit PSI policy."""
from bisect import bisect_right
from hashlib import sha256
import json
import math
from services.predictor.contracts import FeatureVector


def canonical_bytes(value):
    """Serialize semantic state without clocks or whitespace-dependent identities."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def histogram(values, cuts):
    """Use half-open bins with unbounded tails so out-of-range drift is never discarded."""
    counts = [0] * (len(cuts) + 1)
    for value in values:
        counts[bisect_right(cuts, value)] += 1
    return counts


def population_stability(reference, current):
    """Return symmetric PSI with Jeffreys0.5 pseudocounts in every fixed bin."""
    size = len(reference)
    p = [(count + 0.5) / (sum(reference) + 0.5 * size) for count in reference]
    q = [(count + 0.5) / (sum(current) + 0.5 * size) for count in current]
    return sum((right - left) * math.log(right / left) for left, right in zip(p, q))


class DriftDetector:
    """Freeze bins from a trusted training reference; report drift without inferring model accuracy."""
    def __init__(self, bundle):
        """Validate policy and feature contracts before accepting any live comparisons."""
        if set(bundle) != {'format', 'lineage', 'policy', 'samples'} or bundle['format'] != 'iot-drift-reference-v1':
            raise ValueError('Unknown reference format')
        policy = bundle['policy']
        if policy != {'bins': 10, 'psi_threshold': 0.2, 'drifted_feature_fraction': 0.25, 'pseudocount': 0.5}:
            raise ValueError('Unsupported reference policy')
        if not 32 <= len(bundle['samples']) <= 2048:
            raise ValueError('Require32..2048 reference rows')
        samples = [FeatureVector.model_validate(row).model_dump() for row in bundle['samples']]
        self.reference_id = sha256(canonical_bytes(bundle)).hexdigest()
        self.lineage = bundle['lineage']
        self.policy = policy
        self.rows = len(samples)
        self.profiles = {}
        for name in FeatureVector.model_fields:
            values = sorted(row[name] for row in samples)
            cuts = sorted(set(values[min(len(values)-1, len(values)*i//10)] for i in range(1, 10)))
            self.profiles[name] = (cuts, histogram(values, cuts))

    def compare(self, window):
        """Compute eight fixed-bin distances and apply the declared inclusive alert thresholds."""
        scores = {}
        for name, (cuts, expected) in self.profiles.items():
            actual = histogram([row['features'][name] for row in window['observations']], cuts)
            psi = population_stability(expected, actual)
            scores[name] = {'psi': psi, 'drifted': psi >= self.policy['psi_threshold'],
                            'cuts': cuts, 'reference_counts': expected, 'current_counts': actual}
        fraction = sum(value['drifted'] for value in scores.values()) / len(scores)
        return {'reference_id': self.reference_id, 'window_id': window['window_id'],
                'start': window['start'], 'end': window['end'], 'unique_rows': len(window['observations']),
                'reference_rows': self.rows, 'lineage': self.lineage, 'policy': self.policy,
                'features': scores, 'drifted_feature_fraction': fraction,
                'drift_detected': fraction >= self.policy['drifted_feature_fraction'],
                'scope': 'Label-free marginal covariate drift; no ground truth, accuracy or concept-drift claim.'}
