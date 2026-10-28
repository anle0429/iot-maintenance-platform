"""Retrieve a bounded, independently checked Feast offline window and publish its fixed-reference PSI."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from urllib.request import Request, urlopen

from feature_store.common import create_store, digest, save, utc_microseconds
from feature_store.repository import SERVICE, VIEW
from feature_store.batch import compare_features
from ml.drift_monitoring import FIELDS, window, exposition, push_url
from ml.protocol import PROTOCOL_SHA256, load_protocol
from services.drift_api.contracts import CompareRequest
from services.drift_api.detector import DriftDetector, canonical_bytes


def run(args):
    """Preserve every attempt; never push success gauges when binding, native lookup or PSI validation fails."""
    config = json.loads(args.config.read_bytes())
    protocol = load_protocol(Path(__file__).resolve().parents[1] / 'ml/protocols/medium-60d-v1.json')
    if config['reference_train_end'] != protocol['chronological_split']['train_end_exclusive']:
        raise ValueError('Monitoring clock must use the frozen reference train boundary')
    start, end = window(config, args.interval_end)
    endpoint = push_url(args.pushgateway)
    source = args.state / 'machine_rolling.parquet'
    reference = json.loads(args.reference.read_bytes())
    detector = DriftDetector(reference)
    batch = json.loads(args.batch_report.read_bytes())
    marker = json.loads((args.state / '_BATCH_READY.json').read_bytes())
    if (not batch['passed'] or marker['report_sha256'] != digest(args.batch_report)
            or batch['protocol_sha256'] != PROTOCOL_SHA256 or batch['compact_sha256'] != config['source_sha256']
            or digest(source) != config['source_sha256'] or digest(args.reference) != config['reference_sha256']
            or reference['lineage']['protocol_sha256'] != PROTOCOL_SHA256 or reference['lineage']['partition'] != 'train'):
        raise ValueError('Immutable source/reference/protocol binding differs')
    args.output.mkdir(parents=True, exist_ok=False)
    registry = args.output / 'registry'
    registry.mkdir()
    shutil.copyfile(args.state / 'registry/registry.db', registry / 'registry.db')
    store, backend = create_store('iot_maintenance', registry)
    backend.raw_sql("SET memory_limit='1GB'")
    service = store.get_feature_service(SERVICE)
    view = store.get_feature_view(VIEW)
    if (set(f.name for f in view.features) != set(FIELDS) or view.batch_source.path != str(source)
            or view.tags.get('compact_sha256') != config['source_sha256']):
        raise ValueError('Registered feature view differs from the sealed source')
    columns = ','.join(FIELDS)
    # Read only entity keys, event time and eight numeric inputs; no label/status/ground-truth columns.
    query = ('SELECT machine_id,event_timestamp,' + columns + ' FROM read_parquet(?) '
             'WHERE event_timestamp>=? AND event_timestamp<? AND reading_count_10m>=1 '
             "ORDER BY sha256(machine_id || '|' || CAST(event_timestamp AS VARCHAR)) LIMIT ?")
    expected = backend.con.execute(query, [str(source), start.replace(tzinfo=None), end.replace(tzinfo=None), config['sample_limit']]).fetchdf()
    expected = utc_microseconds(expected, ['event_timestamp'])
    if len(expected) < 32 or expected.duplicated(['machine_id', 'event_timestamp']).any():
        raise ValueError('Offline window has insufficient or nonunique observations')
    actual = store.get_historical_features(entity_df=expected[['machine_id', 'event_timestamp']], features=service,
                                          filter_by_created_timestamp=False).to_df()
    compare_features(actual, expected, list(FIELDS))
    observations = []
    for row in actual.to_dict(orient='records'):
        clock = row['event_timestamp'].to_pydatetime().astimezone(timezone.utc).isoformat()
        key = sha256((row['machine_id'] + '|' + clock).encode()).hexdigest()
        observations.append({'id': key, 'event_timestamp': clock, 'available_at': clock,
                             'features': {name: row[name] for name in FIELDS}})
    payload = {'reference_id': detector.reference_id, 'window_id': 'offline-' + sha256((start.isoformat() + end.isoformat()).encode()).hexdigest(),
               'start': start.isoformat(), 'end': end.isoformat(), 'observations': observations}
    payload = CompareRequest.model_validate(payload).canonical()
    report = detector.compare(payload)
    if digest(source) != config['source_sha256'] or digest(args.reference) != config['reference_sha256']:
        raise ValueError('Inputs changed during native retrieval')
    completed = datetime.now(timezone.utc).isoformat()
    metrics = exposition(report, completed)
    save(args.output / 'window.json', payload)
    save(args.output / 'report.json', report)
    (args.output / 'metrics.prom').write_text(metrics, encoding='utf-8')
    save(args.output / 'retrieval.json', {'passed': True, 'native_feast_retrieval': True, 'all_eight_values_checked': True,
        'source_sha256': config['source_sha256'], 'reference_sha256': config['reference_sha256'], 'config_sha256': digest(args.config),
        'registry_sha256': digest(args.state / 'registry/registry.db'), 'rows': len(actual), 'completed_wall_utc': completed,
        'scheduler_interval_end': args.interval_end, 'sql': query,
        'reference_lineage': reference['lineage'],
        'scope': 'Simulated event-time features reconstructed with upstream arrival checks. created is build audit time, so historical backfill lookup explicitly disables created filtering. No ground truth selected; no current production-model quality claim.'})
    request = Request(endpoint, data=metrics.encode(), method='PUT', headers={'Content-Type': 'text/plain; version=0.0.4; charset=utf-8'})
    with urlopen(request, timeout=15) as response:
        code = response.status
        response.read()
    if code != 200:
        raise ValueError('Pushgateway did not accept the complete stable metric group')
    save(args.output / 'completion.json', {'passed': True, 'push_status': code,
        'report_sha256': digest(args.output / 'report.json'), 'metrics_sha256': digest(args.output / 'metrics.prom'),
        'completed_wall_utc': datetime.now(timezone.utc).isoformat(), 'reference_id': detector.reference_id})
    print(json.dumps({'passed': True, 'rows': len(actual), 'drift_detected': report['drift_detected'], 'output': str(args.output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('state', 'batch-report', 'reference', 'config', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--interval-end', required=True)
    parser.add_argument('--pushgateway', default='http://127.0.0.1:19092')
    run(parser.parse_args())
