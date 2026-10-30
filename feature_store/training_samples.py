"""Export version-scoped feature IDs and a separate two-column target table without exposing future labels as features."""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re

FEATURES = ('temperature_mean_10m', 'vibration_max_10m', 'current_mean_10m', 'current_std_10m',
            'rpm_mean_10m', 'reading_count_10m', 'machine_age_days', 'prior_failures_24h')


def grain(row):
    """Normalize an aware machine/time key so equivalent timestamp spellings join identically."""
    machine = row['machine_id']; stamp = row['event_timestamp']
    if not isinstance(machine,str) or re.fullmatch(r'P-[0-9]{3}', machine) is None:
        raise ValueError('A declared pump ID is required')
    if isinstance(stamp,str): stamp = datetime.fromisoformat(stamp.replace('Z','+00:00'))
    if not isinstance(stamp,datetime) or stamp.tzinfo is None: raise ValueError('An aware sample timestamp is required')
    return machine, stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')


def keyed(rows):
    """Reject duplicate machine/time rows rather than multiply a training join or silently discard a conflicting label."""
    result = {}
    for row in rows:
        key = grain(row)
        if key in result: raise ValueError('Duplicate sample grain')
        result[key] = row
    if not result: raise ValueError('A nonempty source table is required')
    return result


def sample_id(dataset_id, key):
    """Namespace the stable sample identity by its immutable dataset version, machine and prediction time."""
    if not isinstance(dataset_id,str) or re.fullmatch(r'[0-9a-f]{64}',dataset_id) is None:
        raise ValueError('Pin the dataset manifest SHA-256')
    return sha256(json.dumps(['iot-training-sample-v1',dataset_id,*key],separators=(',',':')).encode()).hexdigest()


def separate_tables(features, labels, dataset_id):
    """Require complete one-to-one inputs, then export eligible well-observed features and targets in distinct tables."""
    left,right = keyed(features),keyed(labels)
    if set(left) != set(right): raise ValueError('Feature and label grains must match exactly; orphan rows are not dropped')
    output_features = []; output_labels = []; excluded = Counter()
    for key in sorted(left):
        row,target = left[key],right[key]; status = target['label_status']; value = target['failure_in_next_1h']
        if status not in {'eligible','stopped','censored','maintenance'}: raise ValueError('Unknown eligibility state')
        if status != 'eligible':
            if value is not None: raise ValueError('Stopped or unobserved horizons cannot carry a binary target')
            excluded[status] += 1; continue
        if type(value) is not int or value not in {0,1}: raise ValueError('An eligible target must be integer zero or one')
        count = row['reading_count_10m']
        if isinstance(count,bool) or not isinstance(count,(int,float)) or not math.isfinite(count) or count < 0:
            raise ValueError('A finite nonnegative reading count is required')
        if count < 5: excluded['insufficient_readings'] += 1; continue
        vector = {name:row[name] for name in FEATURES}
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in vector.values()):
            raise ValueError('Eligible features must be finite numeric values')
        identifier = sample_id(dataset_id,key)
        output_features.append({'sample_id':identifier,'machine_id':key[0],'event_timestamp':key[1],**vector})
        output_labels.append({'sample_id':identifier,'failure_in_next_1h':value})
    if not output_features: raise ValueError('The run contains no eligible well-observed training samples')
    return {'features':output_features,'labels':output_labels,'dataset_id':dataset_id,'source_grains':len(left),
            'excluded':dict(excluded),'target_counts':dict(Counter(str(r['failure_in_next_1h']) for r in output_labels)),
            'scope':'Training-table export only; temporal split/purge and model evaluation are separate required stages.'}


def join_tables(features, labels):
    """Reconstruct a checked one-to-one training table by sample_id with exact two-column target schema."""
    left = {}; right = {}
    for row in features:
        if set(row) != {'sample_id','machine_id','event_timestamp',*FEATURES}: raise ValueError('Feature export schema changed')
        identifier = row['sample_id']
        if identifier in left: raise ValueError('Duplicate feature sample ID')
        left[identifier] = row
    for row in labels:
        if set(row) != {'sample_id','failure_in_next_1h'}: raise ValueError('Targets must have exactly two columns')
        identifier = row['sample_id']; value = row['failure_in_next_1h']
        if identifier in right or type(value) is not int or value not in {0,1}: raise ValueError('Invalid or duplicate target')
        right[identifier] = value
    if not left or set(left) != set(right): raise ValueError('The sample ID join is not a complete one-to-one match')
    return [dict(left[key],failure_in_next_1h=right[key]) for key in sorted(left)]
