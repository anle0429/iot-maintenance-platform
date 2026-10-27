"""Identify one prediction snapshot without collapsing a machine's whole time series."""
from datetime import datetime, timezone
from hashlib import sha256
import re


def snapshot_id(machine_id: str, event_timestamp: datetime) -> str:
    """Hash a validated machine and canonical UTC microsecond timestamp, independent of local TZ."""
    if not isinstance(machine_id, str) or not re.fullmatch(r'P-\d{3,4}', machine_id):
        raise ValueError('Expected a pump machine ID')
    if not isinstance(event_timestamp, datetime) or event_timestamp.tzinfo is None:
        raise ValueError('Snapshot time must be timezone-aware')
    instant = event_timestamp.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    return sha256((machine_id + '|' + instant).encode('ascii')).hexdigest()
