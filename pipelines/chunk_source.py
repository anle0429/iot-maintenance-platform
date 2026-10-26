"""Bind bounded chunk validation to a source manifest and expose only catalog-approved offline tables."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import platform

from generator.chunked import ChunkConfig, TABLES, digest_file
from generator.chunk_quality import inspect_chunked, require, verify_files
from generator.storage import write_json


OFFLINE_TABLES = ('machines','maintenance','failures','v1','v2')


def outside_source(source: Path, target: Path):
    """Reject reports or outputs inside an immutable source, including paths resolved through symlinks."""
    require(not target.resolve().is_relative_to(source.resolve()),'target must be outside the immutable source')


def validator_hashes() -> list[dict]:
    """Fingerprint the actual standard-library validator modules without hashing generated data into RAM."""
    directory=Path(__file__).resolve().parents[1]/'generator'
    return [{'path':f'generator/{path.name}','sha256':digest_file(path)} for path in sorted(directory.glob('*.py'))]


def validation_receipt(source: Path, report: Path) -> dict:
    """Run semantic readback and bind its result to exact source/validator versions for later Spark ingest."""
    outside_source(source,report)
    require(not report.exists(),'validation receipt exists; preserve it and choose a new report')
    manifest_hash=digest_file(source/'manifest.json')
    quality=inspect_chunked(source)
    require(quality['passed'],'source quality failed')
    require(manifest_hash==digest_file(source/'manifest.json'),'source manifest changed during validation')
    receipt={'receipt_version':1,'passed':True,'validated_utc':datetime.now(timezone.utc).isoformat(),
             'python':platform.python_version(),'source_manifest_sha256':manifest_hash,
             'validator_sha256':validator_hashes(),'quality':quality}
    write_json(report,receipt)
    return receipt


class ChunkSource:
    """Retain constant-size source metadata and table iterators, never whole telemetry rows or ID sets."""

    def __init__(self, source: Path, receipt_path: Path):
        """Require a matching semantic receipt and independently recheck current catalog/file bytes."""
        self.root=source.resolve()
        outside_source(source,receipt_path)
        self.manifest_sha256=digest_file(self.root/'manifest.json')
        self.manifest=json.loads((self.root/'manifest.json').read_text(encoding='utf-8'))
        marker=json.loads((self.root/'_GENERATION_SUCCESS.json').read_text(encoding='utf-8'))
        require(marker=={'manifest_sha256':self.manifest_sha256},'source generation incomplete or manifest changed')
        self.receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
        require(self.receipt.get('receipt_version')==1 and self.receipt.get('passed') is True,'invalid validation receipt')
        require(self.receipt['source_manifest_sha256']==self.manifest_sha256,'receipt belongs to a different source manifest')
        require(self.receipt['validator_sha256']==validator_hashes(),'receipt validator version differs from current code')
        current=verify_files(self.root,self.manifest)
        quality=self.receipt['quality']
        require(quality['passed'] is True and all(quality['checks'].values()),'receipt does not contain successful readback')
        require(current['tables']==quality['tables'] and self.manifest['config']==quality['config'], 'receipt table/config binding differs')
        self.config=ChunkConfig(**self.manifest['config'])
        self.receipt_sha256=digest_file(receipt_path)
        self.offline_bytes=current['offline_payload_bytes']
        self.total_bytes=current['source_total_bytes']

    def paths(self, table: str):
        """Yield contiguous catalog-approved paths lazily; the streaming table is excluded from batch ingest."""
        require(table in OFFLINE_TABLES,'only offline tables may be ingested through this adapter')
        for number in range(self.manifest['tables'][table]['parts']):
            yield self.root/TABLES[table]/f'part-{number:06d}.jsonl'

    def directory(self, table: str) -> Path:
        """Return a verified table directory for Spark's distributed file reader, avoiding a driver path list."""
        require(table in OFFLINE_TABLES,'only offline tables may be ingested through this adapter')
        return self.root/TABLES[table]

    def unchanged(self):
        """Check source bytes again after ingest; input directories are immutable by contract."""
        require(digest_file(self.root/'manifest.json')==self.manifest_sha256,'source manifest changed during ingest')
        verify_files(self.root,self.manifest)


def main() -> int:
    """Create a validation receipt as a public host-side command with no Spark dependency."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=validation_receipt(args.source,args.report)
        print(f"PASS: bound validation receipt; unique={result['quality']['unique_telemetry_rows']}; report={args.report}")
        return 0
    except (OSError,ValueError,TypeError,KeyError) as error:
        print(f'ERROR: {error}')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
