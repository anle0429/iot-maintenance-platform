"""Retrieve every eligible frozen entity through Feast, sealing independently verified daily parts for safe retries."""

import argparse
from datetime import datetime, timedelta
import fcntl
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import time

import pandas as pd
import pyarrow.parquet as pq

from feature_store.batch import compare_features
from feature_store.common import create_store, digest, save, utc_microseconds, utc_now
from feature_store.repository import SERVICE
from ml.protocol import PROTOCOL_SHA256, load_protocol


def run(args):
    """Load the complete cohort; reuse only sealed parts with identical source, protocol, code and SDK."""
    protocol = load_protocol(args.protocol)
    batch = json.loads(args.batch_report.read_text())
    if (not batch['passed'] or batch['protocol_sha256'] != PROTOCOL_SHA256
            or json.loads((args.state / '_BATCH_READY.json').read_text())['report_sha256'] != digest(args.batch_report)
            or digest(args.state / 'machine_rolling.parquet') != batch['compact_sha256']):
        raise ValueError('Registered batch/protocol/source binding differs')
    feature_report = json.loads(args.feature_report.read_text())
    if (not feature_report['passed'] or feature_report['protocol_sha256'] != PROTOCOL_SHA256
            or json.loads((args.artifacts / '_FEATURES_SUCCESS.json').read_text())['report_sha256'] != digest(args.feature_report)
            or batch['compaction_sha256'] != digest(args.batch_report.parent / 'compaction.json')
            or json.loads((args.batch_report.parent / 'compaction.json').read_text())['feature_report_sha256'] != digest(args.feature_report)):
        raise ValueError('Upstream feature preparation or compaction lineage differs')
    entity_parts = [p for p in feature_report['files'] if p['path'].startswith('training/eligible_entities/')]
    entities = args.artifacts / 'training/eligible_entities'
    if {p['path'] for p in entity_parts} != {p.relative_to(args.artifacts).as_posix() for p in entities.glob('*.parquet')}:
        raise ValueError('Eligible entity inventory differs')
    for p in entity_parts:
        if digest(args.artifacts / p['path']) != p['sha256']:
            raise ValueError('Eligible entity bytes changed')
    code = {'feature_store/' + n: digest(Path(__file__).with_name(n)) for n in ('common.py', 'repository.py', 'batch.py', 'load.py')}
    versions = {p: version(p) for p in ('feast', 'duckdb', 'ibis-framework', 'numpy', 'pandas', 'pyarrow')}
    binding = {'protocol_sha256': PROTOCOL_SHA256, 'compact_sha256': batch['compact_sha256'],
               'batch_report_sha256': digest(args.batch_report), 'feature_report_sha256': digest(args.feature_report),
               'entities': entity_parts, 'code_sha256': code, 'sdk_versions': versions,
               'dependency_lock_sha256': digest(Path('/tmp/feast-requirements.txt'))}
    signature = sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    args.destination.mkdir(parents=True, exist_ok=True)
    args.reports.mkdir(parents=True, exist_ok=True)
    with (args.destination / '.load.lock').open('a') as lock, (args.state.parent / 'write.lock').open('a') as registry_lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(registry_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        binding_path = args.destination / '_INPUT_BINDING.json'
        if binding_path.exists() and json.loads(binding_path.read_text()) != {'signature': signature, 'binding': binding}:
            raise ValueError('Existing retrieval belongs to different input/code/SDK; choose a new destination')
        save(binding_path, {'signature': signature, 'binding': binding})
        store, backend = create_store('iot_maintenance', args.state / 'registry')
        temp_root = os.environ.get('IOT_FEAST_TEMP')
        if temp_root:
            temp = Path(temp_root) / args.destination.name
            temp.mkdir(parents=True, exist_ok=True)
            backend.raw_sql("SET temp_directory='" + str(temp).replace("'", "''") + "'")
        service = store.get_feature_service(SERVICE)
        fields = protocol['feature_allowlist']
        start = datetime.fromisoformat(protocol['chronological_split']['start_inclusive'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(protocol['chronological_split']['observation_end_exclusive'].replace('Z', '+00:00'))
        summary, overall = [], time.monotonic()
        day = start
        while day < end:
            next_day = min(day + timedelta(days=1), end)
            queries = backend.con.execute('SELECT * FROM read_parquet(?) WHERE event_timestamp>=? AND event_timestamp<? ORDER BY machine_id,event_timestamp',
                [str(entities / '*.parquet'), day.replace(tzinfo=None), next_day.replace(tzinfo=None)]).fetchdf()
            queries = utc_microseconds(queries, ['event_timestamp', 'label_available_ts'])
            folder = args.destination / ('day=' + day.date().isoformat())
            folder.mkdir(exist_ok=True)
            data, marker = folder / 'data.parquet', folder / '_SUCCESS.json'
            if marker.exists():
                previous = json.loads(marker.read_text())
                if (previous['signature'] != signature or previous['rows'] != len(queries)
                        or not data.is_file() or digest(data) != previous['parquet_sha256']
                        or pq.ParquetFile(data).metadata.num_rows != len(queries)):
                    raise ValueError('Sealed day part changed; refuse retry')
                record = {**previous, 'reused': True}
            else:
                if data.exists() or (folder / 'data.pending.parquet').exists():
                    raise ValueError('Unsealed partial day retained; use a new destination or inspect it explicitly')
                backend.con.register('expected_entities', queries[['machine_id', 'event_timestamp']])
                expected = backend.con.execute('SELECT e.machine_id,e.event_timestamp,' + ','.join('f.' + n for n in fields)
                    + ' FROM expected_entities e JOIN read_parquet(?) f USING(machine_id,event_timestamp)',
                    [str(args.state / 'machine_rolling.parquet')]).fetchdf()
                started = time.monotonic()
                loaded = store.get_historical_features(entity_df=queries.copy(), features=service, filter_by_created_timestamp=False).to_df()
                seconds = time.monotonic() - started
                loaded = utc_microseconds(loaded, ['event_timestamp', 'label_available_ts'])
                compare_features(loaded, expected, fields)
                # Labels/cohort identity must survive the native lookup unchanged.
                joined = loaded.merge(queries, on=['machine_id', 'event_timestamp'], suffixes=('', '_expected'), validate='one_to_one')
                for name in ('failure_in_next_1h', 'failure_episode_id', 'split', 'label_available_ts'):
                    if not ((joined[name] == joined[name + '_expected'])
                            | (joined[name].isna() & joined[name + '_expected'].isna())).all():
                        raise ValueError('Feast changed an entity label/cohort field')
                loaded = utc_microseconds(loaded, ['event_timestamp', 'label_available_ts'])
                pending = folder / 'data.pending.parquet'
                loaded.to_parquet(pending, index=False, compression='snappy', coerce_timestamps='us', allow_truncated_timestamps=False)
                pending.replace(data)
                record = {'signature': signature, 'day': day.date().isoformat(), 'rows': len(loaded), 'parquet_sha256': digest(data),
                          'native_feast_retrieval': True, 'native_query_seconds': seconds, 'completed_utc': utc_now(), 'reused': False}
                save(marker, record)
            summary.append(record)
            save(args.reports / 'progress.json', {'completed_days': len(summary), 'rows': sum(x['rows'] for x in summary),
                 'last_day': day.date().isoformat(), 'elapsed_seconds': time.monotonic() - overall, 'updated_utc': utc_now()})
            print({'completed_day': day.date().isoformat(), 'rows': record['rows'], 'reused': record['reused']}, flush=True)
            day = next_day
        total = sum(x['rows'] for x in summary)
        if total != feature_report['cohort_exclusion_counts']['included'] or digest(args.state / 'machine_rolling.parquet') != batch['compact_sha256']:
            raise ValueError('Complete cohort count or immutable compact source differs')
        for p in entity_parts:
            if digest(args.artifacts / p['path']) != p['sha256']:
                raise ValueError('Entity input changed during retrieval')
        splits = backend.con.execute('SELECT split,count(*) rows,sum(failure_in_next_1h) positive_rows FROM read_parquet(?) GROUP BY split ORDER BY split',
                                     [str(args.destination / 'day=*/data.parquet')]).fetchdf().to_dict(orient='records')
        for row in splits:
            row['rows'], row['positive_rows'] = int(row['rows']), int(row['positive_rows'])
            expected = feature_report['splits'][row['split']]
            if row['rows'] != expected['rows'] or row['positive_rows'] != expected['positive_rows']:
                raise ValueError('Retrieved class/split counts differ from the frozen cohort')
        report = {'passed': True, 'signature': signature, 'protocol_sha256': PROTOCOL_SHA256, 'feature_columns': fields,
                  'total_rows': total, 'daily_parts': summary, 'split_counts': splits, 'binding': binding,
                  'native_rows_this_invocation': sum(x['rows'] for x in summary if not x['reused']),
                  'reused_rows': sum(x['rows'] for x in summary if x['reused']), 'seconds': time.monotonic() - overall,
                  'completed_utc': utc_now(), 'scope': 'Every eligible60-day entity retrieved through the registered Feast service and checked against immutable feature values. Reuse applies only to sealed matching daily parts. No training metrics, KFP or full100GB feature claim.'}
        save(args.reports / 'report.json', report)
        save(args.destination / '_LOADED_SUCCESS.json', {'report_sha256': digest(args.reports / 'report.json'), 'signature': signature})
        backend.disconnect()
        print({'passed': True, 'rows': total, 'native_rows_this_invocation': report['native_rows_this_invocation'], 'reused_rows': report['reused_rows']}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('state', 'batch-report', 'artifacts', 'feature-report', 'destination', 'reports', 'protocol'):
        parser.add_argument('--' + name, type=Path, required=True)
    run(parser.parse_args())
