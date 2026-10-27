"""Validate small versioned factory alarm policies before broadcasting them in an IoT analytics query."""

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re


def threshold_milli(value) -> int:
    """Retain exact three-decimal thresholds and reject booleans, nonfinite values and silent rounding."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError('threshold must be a JSON number')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not 0 < number <= 1_000_000:
            raise ValueError('threshold must be positive, finite and at most 1000000')
        scaled = number * 1000
        if scaled != scaled.to_integral_value():
            raise ValueError('threshold supports at most three decimal places')
        return int(scaled)
    except InvalidOperation as error:
        raise ValueError('invalid decimal threshold') from error


def validate_policy(value: dict) -> dict:
    """Bound the dimension's size and require one unambiguous policy per factory."""
    if not isinstance(value, dict) or set(value) != {'version', 'factories'}:
        raise ValueError('policy requires exactly version and factories')
    version = value['version']
    if not isinstance(version, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', version):
        raise ValueError('invalid policy version')
    rows = value['factories']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError('broadcast policy must contain 1..1000 factories')
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'factory_id', 'temperature_limit_c', 'vibration_limit_mm_s'}:
            raise ValueError('invalid factory policy fields')
        key = row['factory_id']
        if not isinstance(key, str) or not re.fullmatch(r'F-[0-9]{2}', key) or key in seen:
            raise ValueError('factory policy key must be valid and unique')
        seen.add(key)
        result.append({'factory_id': key, 'temperature_limit_milli': threshold_milli(row['temperature_limit_c']),
                       'vibration_limit_milli': threshold_milli(row['vibration_limit_mm_s'])})
    return {'version': version, 'factories': sorted(result, key=lambda row: row['factory_id'])}


def load_policy(path: Path) -> dict:
    """Load numeric JSON precisely; the source file remains the versioned configuration artifact."""
    return validate_policy(json.loads(path.read_text(encoding='utf-8'), parse_float=Decimal))
