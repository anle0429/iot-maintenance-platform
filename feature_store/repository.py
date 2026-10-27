"""Bind the frozen batch feature dataset to a compact immutable Feast source and eight-field service."""

from datetime import timedelta
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from feast import Entity, FeatureService, FeatureView, Field, FileSource
from feast.data_format import ParquetFormat
from feast.types import Float64, Int64
from feast.value_type import ValueType

from feature_store.common import digest, save, utc_now
from ml.protocol import PROTOCOL_SHA256, load_protocol


VIEW = 'machine_rolling_10m'
SERVICE = 'iot_failure_prediction_v1'


def prepare_source(artifacts: Path, report_path: Path, output: Path, backend) -> dict:
    """Compact physical files losslessly; keep the upstream feature/label artifacts untouched."""
    report = json.loads(report_path.read_text())
    marker = json.loads((artifacts / '_FEATURES_SUCCESS.json').read_text())
    if (not report['passed'] or marker['report_sha256'] != digest(report_path)
            or report['protocol_sha256'] != PROTOCOL_SHA256 or not report['class_support']['passed']):
        raise ValueError('Feature preparation/protocol success binding differs')
    parts = [p for p in report['files'] if p['path'].startswith('features/feat_machine_10m/')]
    source = artifacts / 'features/feat_machine_10m'
    if {p['path'] for p in parts} != {p.relative_to(artifacts).as_posix() for p in source.glob('*.parquet')}:
        raise ValueError('Feature input inventory differs')
    for part in parts:
        path = artifacts / part['path']
        if path.is_symlink() or path.stat().st_size != part['bytes'] or digest(path) != part['sha256']:
            raise ValueError('Feature input bytes changed')
    target = output / 'machine_rolling.parquet'
    if target.exists():
        raise ValueError('Compact source exists; do not overwrite an existing data version')
    data = ds.dataset(source, format='parquet')
    schema = pa.schema([pa.field(field.name, pa.timestamp('us', tz=field.type.tz)
                               if pa.types.is_timestamp(field.type) else field.type,
                               nullable=field.nullable, metadata=field.metadata) for field in data.schema],
                       metadata=data.schema.metadata)
    pending, pending_rows, rows = [], 0, 0
    with pq.ParquetWriter(target, schema, compression='snappy', coerce_timestamps='us', allow_truncated_timestamps=False) as writer:
        for batch in data.to_batches(batch_size=262144, use_threads=False):
            batch = batch.cast(schema, safe=True)
            pending.append(batch)
            pending_rows += batch.num_rows
            rows += batch.num_rows
            if pending_rows >= 500000:
                writer.write_table(pa.Table.from_batches(pending, schema=schema), row_group_size=500000)
                pending, pending_rows = [], 0
        if pending:
            writer.write_table(pa.Table.from_batches(pending, schema=schema), row_group_size=500000)
    if rows != report['snapshot_rows']:
        raise ValueError('Compact source row count differs')
    # Exact multiset comparison also catches duplicate loss, null changes and timestamp rounding.
    connection = backend.con
    original = str(source / '*.parquet')
    for left, right in ((original, str(target)), (str(target), original)):
        bad = connection.execute('SELECT * FROM (SELECT * FROM read_parquet(?) EXCEPT ALL SELECT * FROM read_parquet(?)) LIMIT 1',
                                 [left, right]).fetchone()
        if bad is not None:
            raise ValueError('Compaction changed the feature multiset')
    for part in parts:
        if digest(artifacts / part['path']) != part['sha256']:
            raise ValueError('Input changed during compaction')
    before_groups = sum(pq.ParquetFile(artifacts / p['path']).metadata.num_row_groups for p in parts)
    footer = pq.ParquetFile(target).metadata
    outcome = {'passed': True, 'protocol_sha256': PROTOCOL_SHA256, 'source_manifest_sha256': report['source_manifest_sha256'],
               'feature_report_sha256': digest(report_path), 'input_files': parts, 'input_files_unchanged': True,
               'rows': rows, 'before_files': len(parts), 'before_bytes': sum(p['bytes'] for p in parts),
               'before_row_groups': before_groups, 'after_files': 1, 'after_bytes': target.stat().st_size,
               'after_row_groups': footer.num_row_groups, 'compact_sha256': digest(target), 'exact_multiset_equal': True,
               'timestamp_precision': 'UTC-compatible microseconds, lossless casts only', 'checked_utc': utc_now(),
               'scope': 'Physical compaction of the verified medium feature table; not a query performance, transactional lakehouse or100GB claim.'}
    save(output / 'compaction.json', outcome)
    return outcome


def register_batch(store, compact: Path, binding: dict, protocol_path: Path):
    """Expose only the fixed eight model inputs; future truth stays outside the feature view."""
    protocol = load_protocol(protocol_path)
    machine = Entity(name='machine', join_keys=['machine_id'], value_type=ValueType.STRING)
    schema = [Field(name=name, dtype=Int64 if name in ('reading_count_10m', 'prior_failures_24h') else Float64)
              for name in protocol['feature_allowlist']]
    source = FileSource(name='machine_rolling_batch', path=str(compact), file_format=ParquetFormat(),
                        timestamp_field='event_timestamp', created_timestamp_column='created',
                        description='Immutable arrival-aware snapshots reconstructed on the simulated event clock; created is build audit time.')
    view = FeatureView(name=VIEW, entities=[machine], ttl=timedelta(minutes=10), schema=schema, source=source, online=True,
                       tags={'protocol_sha256': PROTOCOL_SHA256, 'source_manifest_sha256': binding['source_manifest_sha256'],
                             'feature_report_sha256': binding['feature_report_sha256'], 'compact_sha256': binding['compact_sha256']})
    service = FeatureService(name=SERVICE, features=[view], tags={'protocol_sha256': PROTOCOL_SHA256})
    store.apply([machine, view, service])
    return service, [VIEW + ':' + name for name in protocol['feature_allowlist']]
