"""Verify every complete Feast training artifact row against the immutable feature and label sources."""

import argparse
import json
from pathlib import Path

from feature_store.common import create_store, digest, save, utc_now
from ml.protocol import PROTOCOL_SHA256, load_protocol


def run(args):
    """Compare every value and label in both directions, with explicit UTC/microsecond timestamps."""
    protocol = load_protocol(args.protocol)
    if args.output.exists():
        raise ValueError('Choose a fresh exact-verification report')
    report = json.loads(args.load_report.read_text())
    marker = json.loads((args.dataset / '_LOADED_SUCCESS.json').read_text())
    if (not report['passed'] or report['protocol_sha256'] != PROTOCOL_SHA256
            or marker['report_sha256'] != digest(args.load_report) or marker['signature'] != report['signature']):
        raise ValueError('Full cohort retrieval marker differs')
    for part in report['daily_parts']:
        folder = args.dataset / ('day=' + part['day'])
        sealed = json.loads((folder / '_SUCCESS.json').read_text())
        if sealed['parquet_sha256'] != digest(folder / 'data.parquet') or sealed['signature'] != report['signature']:
            raise ValueError('A retrieved daily part changed')
    compact = args.state / 'machine_rolling.parquet'
    if digest(compact) != report['binding']['compact_sha256']:
        raise ValueError('Offline source changed')
    store, backend = create_store('iot_maintenance', args.state / 'registry')
    fields = protocol['feature_allowlist']
    # Explicit projections avoid Hive partition columns and avoid nanosecond/TZ coercion differences.
    expected = ('SELECT e.machine_id,CAST(e.event_timestamp AS TIMESTAMP) AS event_timestamp,'
        'e.failure_in_next_1h,CAST(e.label_available_ts AS TIMESTAMP) AS label_available_ts,e.failure_episode_id,e.split,'
        + ','.join('f.' + n for n in fields)
        + ' FROM read_parquet(?) e JOIN read_parquet(?) f ON e.machine_id=f.machine_id AND e.event_timestamp=f.event_timestamp')
    actual = ('SELECT machine_id,CAST(event_timestamp AS TIMESTAMP) AS event_timestamp,'
        'failure_in_next_1h,CAST(label_available_ts AS TIMESTAMP) AS label_available_ts,failure_episode_id,split,'
        + ','.join(fields) + ' FROM read_parquet(?,hive_partitioning=false)')
    entity_glob = str(args.artifacts / 'training/eligible_entities/*.parquet')
    loaded_glob = str(args.dataset / 'day=*/data.parquet')
    parameters = [entity_glob, str(compact), loaded_glob]
    missing = backend.con.execute('SELECT * FROM (' + expected + ' EXCEPT ALL ' + actual + ') LIMIT 1', parameters).fetchone()
    extra = backend.con.execute('SELECT * FROM (' + actual + ' EXCEPT ALL ' + expected + ') LIMIT 1',
                                [loaded_glob, entity_glob, str(compact)]).fetchone()
    if missing is not None or extra is not None:
        raise ValueError('The full retrieved feature/label multiset differs')
    rows = backend.con.execute('SELECT count(*) FROM read_parquet(?,hive_partitioning=false)', [loaded_glob]).fetchone()[0]
    if rows != report['total_rows']:
        raise ValueError('Retrieved row count differs')
    proof = {'passed': True, 'rows': rows, 'days': len(report['daily_parts']), 'signature': report['signature'],
             'both_exact_multiset_differences_empty': True,
             'protocol_sha256': PROTOCOL_SHA256, 'load_report_sha256': digest(args.load_report),
             'compact_sha256': digest(compact), 'verification_code_sha256': digest(Path(__file__)),
             'checked_utc': utc_now(), 'scope': 'Every loaded row, all eight feature values and label/cohort metadata equal the immutable source join exactly. No model metrics or100GB feature claim.'}
    save(args.output, proof)
    backend.disconnect()
    print({'passed': True, 'exact_multiset_rows': rows}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'load-report', 'artifacts', 'state', 'protocol', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    run(parser.parse_args())
