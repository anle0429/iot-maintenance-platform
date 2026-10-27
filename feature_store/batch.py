"""Prepare the immutable batch feature view, verify native historical retrieval and materialize Redis incrementally."""

import argparse
from datetime import datetime, timedelta
import fcntl
from importlib.metadata import version
import json
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd
from google.protobuf.json_format import MessageToDict

from feature_store.common import create_store, digest, save, utc_microseconds, utc_now
from feature_store.repository import SERVICE, VIEW, prepare_source, register_batch
from ml.protocol import PROTOCOL_SHA256, load_protocol


def compare_features(actual, expected, fields):
    """Require the same entity grain and every numeric feature, rather than only row counts."""
    keys = ['machine_id', 'event_timestamp']
    actual = utc_microseconds(actual, ['event_timestamp']).sort_values(keys).reset_index(drop=True)
    expected = utc_microseconds(expected, ['event_timestamp']).sort_values(keys).reset_index(drop=True)
    if (actual.duplicated(keys).any() or expected.duplicated(keys).any()
            or len(actual) != len(expected)
            or actual['machine_id'].tolist() != expected['machine_id'].tolist()
            or not actual['event_timestamp'].equals(expected['event_timestamp'])):
        raise ValueError('Feast retrieval changed the entity grain')
    if not np.allclose(actual[fields].to_numpy(dtype='float64'), expected[fields].to_numpy(dtype='float64'), rtol=1e-12, atol=1e-12, equal_nan=False):
        raise ValueError('Feast values differ from independently joined immutable feature rows')


def native_probe(store, backend, service, entities: Path, compact: Path, fields: list, count: int):
    """Probe dates across the entire history, not only the cheap first few minutes."""
    queries = backend.con.execute('SELECT machine_id,event_timestamp FROM read_parquet(?) ORDER BY hash(machine_id,event_timestamp) LIMIT ?',
                                  [str(entities / '*.parquet'), count]).fetchdf()
    queries = utc_microseconds(queries, ['event_timestamp'])
    backend.con.register('expected_entities', queries)
    expected = backend.con.execute('SELECT e.machine_id,e.event_timestamp,' + ','.join('f.' + name for name in fields)
                                   + ' FROM expected_entities e JOIN read_parquet(?) f USING(machine_id,event_timestamp)', [str(compact)]).fetchdf()
    started = time.monotonic()
    actual = store.get_historical_features(entity_df=queries, features=service, filter_by_created_timestamp=False).to_df()
    seconds = time.monotonic() - started
    compare_features(actual, expected, fields)
    return {'passed': True, 'rows': len(actual), 'seconds': seconds,
            'first_event': queries['event_timestamp'].min().to_pydatetime(),
            'last_event': queries['event_timestamp'].max().to_pydatetime(),
            'scope': 'Native Feast registered-service lookup, exact grid keys and all eight values versus a direct immutable-row join; one cold/warm observation, not a throughput guarantee.'}


def online_matches(store, backend, service, compact: Path, fields: list, at):
    """Compare every real machine's online vector to the latest source row at the simulated cutoff."""
    expected = backend.con.execute('SELECT machine_id,' + ','.join(fields)
        + ' FROM read_parquet(?) WHERE event_timestamp<=? QUALIFY row_number() OVER(PARTITION BY machine_id ORDER BY event_timestamp DESC)=1 ORDER BY machine_id',
        [str(compact), at.replace(tzinfo=None)]).fetchdf()
    machines = [{'machine_id': name} for name in expected['machine_id']]
    actual = store.get_online_features(features=service, entity_rows=machines).to_df().sort_values('machine_id').reset_index(drop=True)
    if (actual['machine_id'].tolist() != expected['machine_id'].tolist()
            or not np.allclose(actual[fields].to_numpy(dtype='float64'), expected[fields].to_numpy(dtype='float64'), rtol=1e-12, atol=1e-12, equal_nan=False)):
        raise ValueError('Online feature values differ from the source cutoff')
    # Small rows are boxed into ordinary Python values by pandas before JSON evidence is written.
    return {'passed': True, 'machines': len(actual), 'as_of_simulated_utc': at,
            'sample': actual[['machine_id', *fields]].head(3).to_dict(orient='records')}


def run(args):
    """Stop at a verified real-data probe; full-cohort retrieval remains a separate pipeline step."""
    if args.state.exists() or args.reports.exists():
        raise ValueError('Choose fresh state and report directories; do not overwrite a data version')
    args.state.mkdir(parents=True)
    args.reports.mkdir(parents=True)
    started = time.monotonic()
    protocol = load_protocol(args.protocol)
    fields = protocol['feature_allowlist']
    lock_path = args.state.parent / 'write.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store, backend = create_store('iot_maintenance', args.state / 'registry')
        temp_root = os.environ.get('IOT_FEAST_TEMP')
        temp = Path(temp_root) / args.state.name if temp_root else args.state / 'duckdb-temp'
        temp.mkdir(parents=True, exist_ok=True)
        backend.raw_sql("SET temp_directory='" + str(temp).replace("'", "''") + "'")
        binding = prepare_source(args.artifacts, args.feature_report, args.state, backend)
        compact = args.state / 'machine_rolling.parquet'
        service, refs = register_batch(store, compact, binding, args.protocol)
        entities = args.artifacts / 'training/eligible_entities'
        entity_parts = [item for item in json.loads(args.feature_report.read_text())['files']
                        if item['path'].startswith('training/eligible_entities/')]
        if {item['path'] for item in entity_parts} != {p.relative_to(args.artifacts).as_posix() for p in entities.glob('*.parquet')}:
            raise ValueError('Eligible entity inventory differs')
        for item in entity_parts:
            if digest(args.artifacts / item['path']) != item['sha256']:
                raise ValueError('Eligible entity input changed')
        probes = [native_probe(store, backend, service, entities, compact, fields, n) for n in (1000, 4000)]
        # Native created is audit wall-clock on this synthetic backfill. Availability is enforced upstream on the separate simulated event clock.
        end = datetime.fromisoformat(protocol['chronological_split']['observation_end_exclusive'].replace('Z', '+00:00'))
        first_end = end - timedelta(hours=1)
        store.materialize(start_date=first_end - timedelta(hours=1), end_date=first_end, feature_views=[VIEW])
        initial = online_matches(store, backend, service, compact, fields, first_end)
        store.materialize_incremental(end_date=end, feature_views=[VIEW])
        incremental = online_matches(store, backend, service, compact, fields, end)
        view = store.get_feature_view(VIEW)
        if set(f.name for f in view.features) != set(fields):
            raise ValueError('The registered view differs from the fixed eight-feature allowlist')
        if digest(compact) != binding['compact_sha256']:
            raise ValueError('Materialization changed the immutable offline source')
        registration = {'view': MessageToDict(view.to_proto()), 'service': MessageToDict(store.get_feature_service(SERVICE).to_proto()),
                        'protocol_sha256': PROTOCOL_SHA256, 'compact_sha256': binding['compact_sha256']}
        save(args.reports / 'registration.json', registration)
        save(args.reports / 'compaction.json', binding)
        report = {'passed': True, 'protocol_sha256': PROTOCOL_SHA256, 'compact_sha256': binding['compact_sha256'],
                  'source_feature_rows': binding['rows'], 'native_probes': probes, 'initial_materialization': initial,
                  'incremental_materialization': incremental, 'registration_sha256': digest(args.reports / 'registration.json'),
                  'compaction_sha256': digest(args.reports / 'compaction.json'), 'feature_columns': fields,
                  'code_sha256': {'feature_store/' + n: digest(Path(__file__).with_name(n)) for n in ('common.py', 'repository.py', 'batch.py')},
                  'versions': {p: version(p) for p in ('feast', 'duckdb', 'ibis-framework', 'numpy', 'pandas', 'pyarrow', 'redis')},
                  'seconds': time.monotonic() - started, 'completed_utc': utc_now(),
                  'scope': 'All3.456Mfeatures compacted and registered, 1000/4000 native historical probes and200-machine initial/incremental online checks. Not full3.132Mhistorical retrieval, streaming/Airflow/KFP or ML quality proof.'}
        save(args.reports / 'report.json', report)
        save(args.reports / '_SUCCESS.json', {'report_sha256': digest(args.reports / 'report.json')})
        save(args.state / '_BATCH_READY.json', {'report_sha256': digest(args.reports / 'report.json'),
                                               'protocol_sha256': PROTOCOL_SHA256, 'compact_sha256': binding['compact_sha256']})
        backend.disconnect()
        print({'passed': True, 'probes': probes, 'online_machines': incremental['machines']}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifacts', 'feature-report', 'state', 'reports', 'protocol'):
        parser.add_argument('--' + name, required=True, type=Path)
    run(parser.parse_args())
