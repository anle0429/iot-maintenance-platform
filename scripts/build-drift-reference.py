"""Select a deterministic label-free sample only from the sealed training split for drift monitoring."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ml.training_contract import verify_descriptor, digest, save
from ml.protocol import load_protocol, PROTOCOL_SHA256
from services.drift_api.detector import DriftDetector, canonical_bytes
import duckdb


def run(args):
    """Bind reference to registered training/data identity; never select reference rows from holdout."""
    if args.output.exists():
        raise ValueError('Preserve existing reference')
    partitions = verify_descriptor(args.partitions, 'temporal-split')
    protocol = load_protocol(args.protocol)
    registration = json.loads(args.registration.read_text())
    link = registration['incremental_data_reference']
    if not registration['passed'] or link['native_descriptor_sha256'] != partitions['input_descriptor_sha256'] or link['native_report_sha256'] != partitions['native_report_sha256']:
        raise ValueError('Registered model belongs to a different training pull')
    fields = protocol['feature_allowlist']
    with duckdb.connect() as connection:
        connection.execute("SET threads=1")
        connection.execute("SET memory_limit='1GB'")
        # Only feature columns are selected; no label is included in the drift artifact or detector.
        selected = ','.join('"'+field+'"' for field in fields)
        source = str(Path(partitions['split_paths']['train'])/'*.parquet')
        count, first, last = connection.execute('SELECT count(*),min(event_timestamp),max(event_timestamp) FROM read_parquet(?)', [source]).fetchone()
        if count != partitions['counts']['train']['rows'] or last.isoformat() >= protocol['chronological_split']['train_end_exclusive'].replace('Z',''):
            raise ValueError('Reference source crosses the frozen train boundary')
        rows = connection.execute(f'SELECT {selected} FROM read_parquet(?) ORDER BY sha256(machine_id || \'|\' || CAST(event_timestamp AS VARCHAR)) LIMIT 1024', [source]).fetchall()
    samples = [dict(zip(fields, row)) for row in rows]
    bundle = {'format': 'iot-drift-reference-v1',
              'lineage': {'model_name': registration['model_name'], 'model_version': registration['version'],
                          'model_run_id': registration['model_run_id'], 'data_version': link['data_version'],
                          'protocol_sha256': PROTOCOL_SHA256, 'partitions_sha256': digest(args.partitions),
                          'native_report_sha256': partitions['native_report_sha256'], 'partition': 'train',
                          'selection': 'lowest1024 SHA256(machine_id|event_timestamp); no label selected'},
              'policy': {'bins': 10, 'psi_threshold': 0.2, 'drifted_feature_fraction': 0.25, 'pseudocount': 0.5},
              'samples': samples}
    detector = DriftDetector(bundle)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(bundle)+b'\n')
    save(args.output.with_suffix('.verification.json'), {'passed': True, 'reference_id': detector.reference_id,
         'file_sha256': digest(args.output), 'reference_rows': len(samples), 'train_rows': count,
         'train_first': first.isoformat(), 'train_last': last.isoformat(), 'lineage': bundle['lineage'], 'code_sha256': digest(Path(__file__)),
         'scope': 'Training-only reference; no validation/test rows or labels enter detector. PSI policy is an operational threshold, not a calibrated error probability.'})
    print(json.dumps({'passed': True, 'reference_id': detector.reference_id, 'rows': len(samples)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['partitions', 'protocol', 'registration', 'output']:
        parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
