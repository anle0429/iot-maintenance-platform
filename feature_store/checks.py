"""Exercise native Feast PIT/availability/TTL and Redis replay behavior on independent known answers."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import time

import pandas as pd
from feast import Entity, FeatureView, Field, FileSource
from feast.data_format import ParquetFormat
from feast.types import Float64
from feast.value_type import ValueType

from feature_store.common import create_store, digest, save, utc_microseconds, utc_now


def run(output: Path):
    """Verify real provider results; a default PIT lookup alone is not an availability proof."""
    if output.exists():
        raise ValueError('Choose a fresh fixture directory; retain failed outputs')
    output.mkdir(parents=True)
    started = time.monotonic()
    at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = [('A', at, at, 1.0),
            ('A', at + timedelta(minutes=5), at + timedelta(minutes=12), 2.0),
            ('A', at + timedelta(minutes=10), at + timedelta(minutes=10), 3.0),
            ('A', at + timedelta(minutes=10), at + timedelta(minutes=13), 30.0)]
    source = output / 'fixture.parquet'
    source_frame = utc_microseconds(pd.DataFrame(rows, columns=['machine_id', 'feature_ts', 'available_at', 'signal']),
                                    ['feature_ts', 'available_at'])
    source_frame.to_parquet(source, index=False, coerce_timestamps='us', allow_truncated_timestamps=False)
    project = 'iot_fixture_' + sha256(str(output).encode()).hexdigest()[:12]
    store, backend = create_store(project, output / 'registry', key_ttl_seconds=3)
    entity = Entity(name='machine', join_keys=['machine_id'], value_type=ValueType.STRING)
    view = FeatureView(name='fixture_stats', entities=[entity], ttl=timedelta(minutes=10),
                       schema=[Field(name='signal', dtype=Float64)], online=True,
                       source=FileSource(name='fixture_source', path=str(source), file_format=ParquetFormat(),
                                         timestamp_field='feature_ts', created_timestamp_column='available_at'))
    store.apply([entity, view])
    cases = [('before_first', 'A', at - timedelta(microseconds=1), None),
             ('at_first', 'A', at, 1.0),
             ('late_unavailable', 'A', at + timedelta(minutes=6), 1.0),
             ('correction_unavailable', 'A', at + timedelta(minutes=11), 3.0),
             ('correction_available', 'A', at + timedelta(minutes=13), 30.0),
             ('ttl_inclusive', 'A', at + timedelta(minutes=20), 30.0),
             ('ttl_expired', 'A', at + timedelta(minutes=20, microseconds=1), None),
             ('unknown_machine', 'unknown', at + timedelta(minutes=2), None)]
    queries = pd.DataFrame([(name, machine, ts) for name, machine, ts, _ in cases],
                           columns=['case_id', 'machine_id', 'event_timestamp'])
    queries = utc_microseconds(queries, ['event_timestamp'])
    refs = ['fixture_stats:signal']
    result = store.get_historical_features(entity_df=queries.copy(), features=refs,
                                            filter_by_created_timestamp=True).to_df()
    actual = {row.case_id: None if pd.isna(row.signal) else float(row.signal) for row in result.itertuples()}
    expected = {name: value for name, _, _, value in cases}
    checks = {'native_pit_known_answers': actual == expected, 'native_pit_grain': len(result) == len(cases)}
    try:
        utc_microseconds(pd.DataFrame({'ts': pd.to_datetime(['2026-10-01T00:00:00.000000001Z'])}), ['ts'])
    except ValueError:
        checks['submicrosecond_input_rejected'] = True
    else:
        checks['submicrosecond_input_rejected'] = False
    if not all(checks.values()):
        raise AssertionError(f'Native Feast availability/TTL differs: {actual}; expected {expected}')
    ordinary = store.get_historical_features(entity_df=queries.copy(), features=refs).to_df()
    unfiltered = {row.case_id: None if pd.isna(row.signal) else float(row.signal) for row in ordinary.itertuples()}
    checks['default_pit_does_not_prove_arrival_availability'] = (unfiltered['late_unavailable'] == 2.0
                                                                and unfiltered['correction_unavailable'] == 30.0)
    # This end time is after every correction is available; it is not a historical serving replay before arrival.
    store.materialize(start_date=at, end_date=at + timedelta(minutes=14), feature_views=['fixture_stats'])

    def online():
        """Read the real Redis provider through the Feast SDK, including a missing entity."""
        return store.get_online_features(features=refs, entity_rows=[{'machine_id': 'A'}, {'machine_id': 'unknown'}]).to_dict()['signal']

    first = online()
    checks['online_latest_and_missing'] = first == [30.0, None]
    stale = pd.DataFrame([('A', at + timedelta(minutes=5), at + timedelta(minutes=5), 999.0)],
                         columns=['machine_id', 'feature_ts', 'available_at', 'signal'])
    stale = utc_microseconds(stale, ['feature_ts', 'available_at'])
    store.write_to_online_store(feature_view_name='fixture_stats', df=stale)
    after_stale = online()
    checks['older_online_write_cannot_regress'] = after_stale == first
    replay = pd.DataFrame([rows[-1]], columns=['machine_id', 'feature_ts', 'available_at', 'signal'])
    replay = utc_microseconds(replay, ['feature_ts', 'available_at'])
    store.write_to_online_store(feature_view_name='fixture_stats', df=replay)
    after_replay = online()
    checks['same_event_replay_idempotent'] = after_replay == first
    end = time.monotonic() + 8
    expired = online()
    while expired[0] is not None and time.monotonic() < end:
        time.sleep(0.3)
        expired = online()
    checks['native_redis_key_ttl_expires'] = expired == [None, None]
    if not all(checks.values()):
        raise AssertionError(checks)
    report = {'passed': True, 'checks': checks, 'known_answers': expected, 'native_pit': actual,
              'ordinary_pit': unfiltered, 'online_before': first, 'online_after_old': after_stale,
              'online_after_replay': after_replay, 'online_after_expiry': expired,
              'fixture_source_sha256': digest(source), 'registry_sha256': digest(output / 'registry/registry.db'),
              'code_sha256': {'feature_store/' + name: digest(Path(__file__).with_name(name)) for name in ('checks.py', 'common.py')},
              'versions': {p: version(p) for p in ('feast', 'duckdb', 'ibis-framework', 'numpy', 'pandas', 'pyarrow', 'redis')},
              'elapsed_seconds': time.monotonic() - started, 'completed_utc': utc_now(),
              'scope': 'Native Feast providers on a tiny independent fixture; not medium retrieval, streaming deployment, production latency or full100GB proof.'}
    save(output / 'report.json', report)
    save(output / '_SUCCESS.json', {'report_sha256': digest(output / 'report.json')})
    backend.disconnect()
    print({'passed': True, 'checks': checks}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    run(parser.parse_args().output)
