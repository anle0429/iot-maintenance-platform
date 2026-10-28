"""Recalculate fixed-reference PSI from raw samples without importing the detector or publisher."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import math


def canonical(value):
    """Reconstruct the documented identity serialization independently of runtime helpers."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def compare(reference, payload, report):
    """Check all eight bins/counts/decisions using raw numeric samples and literal policy boundaries."""
    fields = ('temperature_mean_10m','vibration_max_10m','current_mean_10m','current_std_10m',
              'rpm_mean_10m','reading_count_10m','machine_age_days','prior_failures_24h')
    expected_reference = sha256(canonical(reference)).hexdigest()
    assert expected_reference == payload['reference_id'] == report['reference_id']
    assert set(report['features']) == set(fields)
    assert len({r['id'] for r in payload['observations']}) == len(payload['observations']) == report['unique_rows']
    assert 32 <= report['unique_rows'] <= 2048
    assert report['reference_rows'] == len(reference['samples'])
    def clock(value):
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        assert parsed.tzinfo is not None
        return parsed.astimezone(timezone.utc)
    start,end=clock(payload['start']),clock(payload['end'])
    assert start < end and report['start']==payload['start'] and report['end']==payload['end']
    for row in payload['observations']:
        assert set(row)=={'id','event_timestamp','available_at','features'}
        assert set(row['features'])==set(fields)
        assert start <= clock(row['event_timestamp']) < end
        assert clock(row['event_timestamp']) <= clock(row['available_at']) <= end
    checked={}
    flags=0
    for name in fields:
        values=sorted(r[name] for r in reference['samples'])
        cuts=sorted({values[min(len(values)-1,(len(values)*i)//10)] for i in range(1,10)})
        def counts(sample):
            bins=[0]*(len(cuts)+1)
            for value in sample:
                assert math.isfinite(value)
                bins[sum(value>=edge for edge in cuts)]+=1
            return bins
        p_counts=counts(values)
        q_counts=counts([r['features'][name] for r in payload['observations']])
        n=len(p_counts)
        distance=0.
        for a,b in zip(p_counts,q_counts):
            p=(a+.5)/(len(values)+.5*n)
            q=(b+.5)/(report['unique_rows']+.5*n)
            distance+=(q-p)*math.log(q/p)
        native=report['features'][name]
        assert native['cuts']==cuts and native['reference_counts']==p_counts and native['current_counts']==q_counts
        assert math.isclose(native['psi'],distance,rel_tol=1e-12,abs_tol=1e-12)
        assert native['drifted'] is (distance>=.2)
        flags+=distance>=.2
        checked[name]=distance
    assert report['drifted_feature_fraction']==flags/8
    assert report['drift_detected'] is (flags>=2)
    assert report['policy']=={'bins':10,'psi_threshold':.2,'drifted_feature_fraction':.25,'pseudocount':.5}
    return {'passed':True,'rows':report['unique_rows'],'psi':checked,'drifted_features':flags,
            'scope':'Independent raw-sample marginal PSI arithmetic; no ground truth, native source lookup or model accuracy claim.'}
