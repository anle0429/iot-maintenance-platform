"""Preserve scoped discovery identities and filesystem permissions across an approved lab VM restart."""
from pathlib import Path
import base64
import json


def discovery_identity(runtime, filename, raw):
    """Refuse unknown token paths or a mismatched Kubernetes service-account subject before renewing a credential."""
    runtime = Path(runtime).resolve()
    path = Path(filename).resolve()
    expected = {
        runtime / 'api-observability-28b/prometheus-auth/token': 'iot-api-metrics',
        runtime / 'log-discovery-28/auth/token': 'iot-api-metrics',
        runtime / 'llm-monitoring-29a/auth/token': 'iot-llm-metrics-reader',
    }
    if path not in expected:
        raise ValueError('Inspect the unknown discovery credential path before renewal')
    try:
        payload = raw.strip().split(b'.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + b'=' * (-len(payload) % 4)))
    except (IndexError, ValueError) as error:
        raise ValueError('Invalid retained discovery credential') from error
    identity = expected[path]
    if claims.get('sub') != 'system:serviceaccount:iot-observability:' + identity:
        raise ValueError('Preserve the retained discovery service-account identity')
    return identity
