"""Register and incrementally materialize an immutable backfill through native Feast with durable checkpoints."""

import argparse
from datetime import datetime, timedelta
import fcntl
from hashlib import sha256
import json
from pathlib import Path
import re

from feature_store.batch import online_matches
from feature_store.common import create_store, digest, save, utc_now
from feature_store.repository import SERVICE, VIEW, register_batch
from ml.protocol import PROTOCOL_SHA256, load_protocol


PROJECT = 'iot_materialization'


def run(args):
    """Keep the training registry untouched; refuse time regression and unsealed writer attempts."""
    protocol = load_protocol(args.protocol)
    project = getattr(args, 'project', PROJECT)
    if project != PROJECT and not re.fullmatch(r'iot_ci_mat_[1-9][0-9]*', project):
        raise ValueError('Only the existing project or an isolated CI materialization project is allowed')
    end = datetime.fromisoformat(args.end.replace('Z', '+00:00'))
    if end.tzinfo is None or end.utcoffset() != timedelta(0):
        raise ValueError('Materialization cutoff must explicitly use UTC')
    observation_end = datetime.fromisoformat(protocol['chronological_split']['observation_end_exclusive'].replace('Z', '+00:00'))
    start = datetime.fromisoformat(protocol['chronological_split']['start_inclusive'].replace('Z', '+00:00'))
    if not start + timedelta(hours=1) <= end <= observation_end:
        raise ValueError('Cutoff lies outside the verified immutable source horizon')
    batch = json.loads(args.batch_report.read_text())
    compact = args.source_state / 'machine_rolling.parquet'
    if (not batch['passed'] or batch['protocol_sha256'] != PROTOCOL_SHA256
            or json.loads((args.source_state / '_BATCH_READY.json').read_text())['report_sha256'] != digest(args.batch_report)
            or digest(args.batch_report.parent / 'compaction.json') != batch['compaction_sha256']
            or digest(compact) != batch['compact_sha256']):
        raise ValueError('Immutable batch binding differs')
    binding = {'project': project, 'protocol_sha256': PROTOCOL_SHA256, 'batch_report_sha256': digest(args.batch_report),
               'compact_sha256': batch['compact_sha256'], 'source': str(compact), 'features': protocol['feature_allowlist']}
    args.state.mkdir(parents=True, exist_ok=True)
    args.reports.mkdir(parents=True, exist_ok=True)
    with (args.state.parent / 'write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding_path = args.state / 'binding.json'
        if binding_path.exists() and json.loads(binding_path.read_text()) != binding:
            raise ValueError('Existing materialization state belongs to another immutable source')
        if args.operation != 'register' and not binding_path.exists():
            raise ValueError('Register the bound feature repository before materializing')
        store, backend = create_store(project, args.state / 'registry')
        try:
            if args.operation == 'register':
                register_batch(store, compact, json.loads((args.batch_report.parent / 'compaction.json').read_text()), args.protocol)
                save(binding_path, binding)
                save(args.reports / 'registration.json', {'passed': True, 'binding': binding,
                     'cutoff': end, 'code_sha256': digest(Path(__file__)), 'checked_utc': utc_now()})
                return
            checkpoint_path = args.state / 'checkpoint.json'
            checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else None
            report_path = args.reports / 'materialization.json'
            seal_path = args.reports / '_MATERIALIZED_SUCCESS.json'
            attempt = args.state / '_MATERIALIZING.json'
            if args.operation == 'materialize':
                if report_path.exists():
                    if not seal_path.exists() or json.loads(seal_path.read_text())['report_sha256'] != digest(report_path):
                        raise ValueError('An unsealed materialization report exists')
                    previous = json.loads(report_path.read_text())
                    if previous['binding'] != binding or previous['cutoff'] != end.isoformat():
                        raise ValueError('Run output belongs to another source or cutoff')
                    return
                if attempt.exists():
                    raise ValueError('An unsealed native materialization attempt requires inspection; refuse automatic resume')
                old_end = datetime.fromisoformat(checkpoint['cutoff']) if checkpoint else None
                if old_end and end < old_end:
                    raise ValueError('Cannot move the online materialization checkpoint backwards')
                mode = 'checkpoint_replay' if old_end == end else 'incremental' if old_end else 'initial'
                if mode != 'checkpoint_replay':
                    save(attempt, {'cutoff': end, 'reports': str(args.reports), 'started_utc': utc_now()})
                    if old_end:
                        store.materialize_incremental(end_date=end, feature_views=[VIEW])
                    else:
                        store.materialize(start_date=end - timedelta(hours=1), end_date=end, feature_views=[VIEW])
                result = online_matches(store, backend, store.get_feature_service(SERVICE), compact, protocol['feature_allowlist'], end)
                expected = backend.con.execute('SELECT machine_id,event_timestamp,' + ','.join(protocol['feature_allowlist'])
                    + ' FROM read_parquet(?) WHERE event_timestamp<=? QUALIFY row_number() OVER(PARTITION BY machine_id ORDER BY event_timestamp DESC)=1 ORDER BY machine_id',
                    [str(compact), end.replace(tzinfo=None)]).fetchdf()
                ages = [(end.replace(tzinfo=None) - x.to_pydatetime().replace(tzinfo=None)).total_seconds() for x in expected.event_timestamp]
                if len(expected) != 200 or not ages or max(ages) > 600 or min(ages) < 0:
                    raise ValueError('The simulated cutoff lacks a fresh feature vector for every real machine')
                hashes = {row['machine_id']: sha256(json.dumps({n: row[n] for n in protocol['feature_allowlist']}, sort_keys=True).encode()).hexdigest()
                          for row in expected.to_dict(orient='records')}
                changed = sum(checkpoint['value_sha256_by_machine'].get(n) != h for n,h in hashes.items()) if checkpoint else None
                report = {'passed': True, 'mode': mode, 'cutoff': end, 'previous_cutoff': old_end,
                          'binding': binding, 'online_comparison': result, 'changed_machine_vectors': changed,
                          'value_sha256_by_machine': hashes, 'max_simulated_feature_age_seconds': max(ages),
                          'native_registry_materialization_intervals': [[a.isoformat(), b.isoformat()] for a,b in store.get_feature_view(VIEW).materialization_intervals],
                          'code_sha256': digest(Path(__file__)), 'completed_utc': utc_now(),
                          'scope': 'Native Feast initial/incremental backfill and all200 online vectors; simulated cutoff, not live wall-clock freshness or training metrics.'}
                save(report_path, report)
                save(seal_path, {'report_sha256': digest(report_path)})
                save(checkpoint_path, {'cutoff': end, 'report_sha256': digest(report_path), 'reports': str(args.reports), 'value_sha256_by_machine': hashes})
                if attempt.exists():
                    attempt.unlink()
                print({'passed': True, 'mode': mode, 'machines': len(expected), 'changed_vectors': changed}, flush=True)
            else:
                report = json.loads(report_path.read_text())
                checks = {'sealed_report': json.loads(seal_path.read_text())['report_sha256'] == digest(report_path),
                          'source_binding': report['binding'] == binding and digest(compact) == binding['compact_sha256'],
                          'requested_cutoff': report['cutoff'] == end.isoformat(),
                          'durable_checkpoint': checkpoint is not None and checkpoint['cutoff'] == report['cutoff'] and checkpoint['report_sha256'] == digest(report_path),
                          'no_unsealed_writer': not attempt.exists()}
                actual = online_matches(store, backend, store.get_feature_service(SERVICE), compact, protocol['feature_allowlist'], end)
                checks['all_online_vectors_match_source'] = actual['passed'] and actual['machines'] == 200
                result = {'passed': all(checks.values()), 'checks': checks, 'cutoff': end, 'binding': binding,
                          'materialization_report_sha256': digest(report_path), 'online_comparison': actual,
                          'code_sha256': digest(Path(__file__)), 'validated_utc': utc_now()}
                save(args.reports / 'validation.json', result)
                if not result['passed']:
                    raise ValueError('Persisted materialization validation failed')
                print({'passed': True, 'checks': len(checks)}, flush=True)
        finally:
            backend.disconnect()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', choices=('register', 'materialize', 'validate'), required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--project', default=PROJECT)
    for name in ('source-state', 'batch-report', 'state', 'reports', 'protocol'):
        parser.add_argument('--' + name, required=True, type=Path)
    run(parser.parse_args())
