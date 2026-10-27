"""Check sensor drift analytically while retaining original physical labels and transport anomalies."""
from dataclasses import replace
import pytest
from generator.config import Config
from generator.simulation import generate
from generator.drift import SensorDrift, generate_drift
from generator.quality import summarize


def test_step_ramp_boundaries_and_retry_consistency():
    row={'event_ts':'2026-10-01T01:00:00Z','temperature_c':10.0,'vibration_rms_mm_s':2.0}
    ramp=SensorDrift('2026-10-01T01:00:00Z',3600,20,2)
    assert ramp.transform(row)==row
    assert ramp.transform({**row,'event_ts':'2026-10-01T00:59:59Z'})['temperature_c']==10
    half=ramp.transform({**row,'event_ts':'2026-10-01T01:30:00Z'})
    assert half['temperature_c']==20 and half['vibration_rms_mm_s']==3
    end=ramp.transform({**row,'event_ts':'2026-10-01T02:00:00Z'})
    assert end['temperature_c']==30 and end['vibration_rms_mm_s']==4
    assert SensorDrift(ramp.start_ts,0,20,2).transform(row)['temperature_c']==30
    config=Config(machine_count=2,duration_hours=2)
    profile=SensorDrift('2026-10-01T01:00:00Z',0,20,2)
    base=generate(config);changed=generate_drift(config,profile)
    assert changed==generate_drift(config,profile)
    assert (changed.failures,changed.maintenance,changed.machines)==(base.failures,base.maintenance,base.machines)
    assert summarize(changed,config)['passed']
    assert set(r['event_id'] for r in base.telemetry).isdisjoint(r['event_id'] for r in changed.telemetry)
    before=[r for r in changed.telemetry if r['event_ts']<profile.start_ts]
    assert [{k:v for k,v in r.items() if k!='event_id'} for r in before]==[{k:v for k,v in r.items() if k!='event_id'} for r in base.telemetry if r['event_ts']<profile.start_ts]


@pytest.mark.parametrize('kwargs',[{'ramp_seconds':-1},{'ramp_seconds':True},{'temperature_offset_c':101},
    {'temperature_offset_c':float('nan')},{'vibration_multiplier':0},{'vibration_multiplier':'2'},
    {'start_ts':'2026-10-01T00:00:00'}])
def test_invalid_drift_configuration(kwargs):
    with pytest.raises(ValueError):SensorDrift(**{'start_ts':'2026-10-01T01:00:00Z',**kwargs})


def test_drift_outside_observation_rejected():
    with pytest.raises(ValueError):generate_drift(Config(machine_count=1,duration_hours=1),SensorDrift('2026-10-02T00:00:00Z'))
