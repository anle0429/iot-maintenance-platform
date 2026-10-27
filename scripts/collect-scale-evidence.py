"""Export small measured source/ingest/resource reports, never datasets, virtualenvs or cloud credentials."""

import argparse
from datetime import datetime
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator.chunked import digest_file
from generator.chunk_quality import records, walk_files
from pipelines.orchestration_contract import save, utc_now


def source_inventory(source: Path, report: dict, receipt: dict) -> dict:
    """Audit current file sizes/mtime after full readback, without adding a third 100GB data scan."""
    manifest = json.loads((source / 'manifest.json').read_text())
    fingerprint = digest_file(source / 'manifest.json')
    if fingerprint != report['source_manifest_sha256'] or fingerprint != receipt['source_manifest_sha256']:
        raise ValueError('source manifest binding differs')
    if json.loads((source / '_GENERATION_SUCCESS.json').read_text()) != {'manifest_sha256': fingerprint}:
        raise ValueError('generation marker differs')
    if digest_file(source / 'files.jsonl') != manifest['catalog']['sha256']:
        raise ValueError('catalog changed after readback')
    validated = datetime.fromisoformat(receipt['validated_utc']).timestamp()
    count = payload = allocated = 0
    for item in records(source / 'files.jsonl'):
        path = source / item['path']
        stat = path.stat()
        if stat.st_size != item['bytes'] or stat.st_mtime > validated + 1:
            raise ValueError('source file changed after full semantic readback')
        count += 1
        payload += stat.st_size
        allocated += stat.st_blocks * 512
    actual_files = sum(1 for _ in walk_files(source))
    if payload != manifest['offline_payload_bytes'] or actual_files != count + 3 or manifest['stream_generated']:
        raise ValueError('offline-only file/byte inventory differs')
    return {'offline_payload_bytes': payload, 'allocated_payload_file_bytes': allocated,
            'part_files': count, 'total_files': actual_files, 'manifest_sha256': fingerprint,
            'scope': 'Actual file sizes/allocation/catalog hash and unchanged mtimes following the completed full checksum+semantic readback; part hashes are not reread a third time.'}


def collect(final: bool) -> None:
    """Require measured correctness before exporting evidence and distinguish partial from actual >=100GB success."""
    target = ROOT / ('outputs/scale-evidence-final' if final else 'outputs/scale-evidence-intermediate')
    target.mkdir(exist_ok=False)

    def copy(source: Path, relative: str) -> None:
        """Copy a specifically allowlisted report while preserving the original evidence bytes."""
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    runs = ['scale-calibration-base-26', 'scale-calibration-cached-26', 'scale-calibration-fast-26', 'scale-intermediate-26']
    if final:
        runs.append('scale-100gb-26')
    summary = {}
    for name in runs:
        folder = ROOT / 'outputs' / name
        report = json.loads((folder / 'verification.json').read_text())
        receipt = json.loads((folder / 'receipt.json').read_text())
        marker = json.loads((folder / '_RUN_SUCCESS.json').read_text())
        if report.get('passed') is not True or not all(receipt['quality']['checks'].values()):
            raise ValueError('source verification failed')
        if marker['verification_sha256'] != digest_file(folder / 'verification.json'):
            raise ValueError('source wrapper success marker differs')
        source = ROOT / 'data/source' / name
        observed = source_inventory(source, report, receipt)
        if final and name == 'scale-100gb-26' and observed['offline_payload_bytes'] < 100_000_000_000:
            raise ValueError('actual source is below 100GB')
        save(target / name / 'source-inventory.json', observed)
        for artifact in ('preflight.json', 'generate.json', 'validate.json', 'receipt.json', 'verification.json', '_RUN_SUCCESS.json'):
            copy(folder / artifact, f'{name}/{artifact}')
        for artifact in ('manifest.json', 'files.jsonl', '_GENERATION_SUCCESS.json'):
            copy(source / artifact, f'{name}/source-{artifact}')
        summary[name] = {'offline_bytes': observed['offline_payload_bytes'], 'unique_rows': report['unique_rows'],
                         'phases': {key: {'seconds': value['seconds'], 'cpu_seconds': value['cpu_seconds'],
                                         'peak_rss_bytes': value['peak_rss_bytes']} for key, value in report['phase_reports'].items()}}
    ingest = ROOT / 'outputs/scale-ingest-medium-26'
    for artifact in ('ingest-report.json', 'spark-metrics.json', 'cloud-context.json', 'commands.json', 'unit-tests.txt'):
        copy(ingest / artifact, f'intermediate-spark/{artifact}')
    copy(ROOT / 'outputs/scale-ingest-medium-26-resources.json', 'intermediate-spark/sampled-resources.json')
    for name in ('before-restart.json', 'after-restart.json'):
        copy(ROOT / 'outputs/scale-restart-26' / name, f'vm-restart/{name}')
    copy(ROOT / 'outputs/scale-protocol-26/100gb-plan.json', 'vm-restart/rejected-old-session-plan.json')
    fresh = ROOT / 'outputs/scale-protocol-fresh-session-26'
    for name in ('100gb-plan.json', 'source-after-restart.json', 'protocol-runtime.json', 'large-start.json'):
        copy(fresh / name, f'fresh-session/{name}')
    copy(ROOT / 'outputs/scale-protocol-26/calibration-comparison.json', 'calibration-comparison.json')
    if final:
        copy(fresh / '_PROTOCOL_SUCCESS.json', 'fresh-session/_PROTOCOL_SUCCESS.json')
    runtime_files = [ROOT / 'pipelines/source_runtime.py', ROOT / 'pipelines/scale_budget.py',
                     *sorted((ROOT / 'scripts').glob('*scale*.py')), ROOT / 'requirements-scale.txt',
                     *sorted((ROOT / 'generator').glob('*.py')), *sorted((ROOT / 'generator/configs').glob('scale-*.json')),
                     ROOT / 'tests/test_source_runtime.py', ROOT / 'tests/test_scale_budget.py']
    save(target / 'runtime.json', {'captured_utc': utc_now(), 'host': platform.node(), 'python': platform.python_version(),
         'platform': platform.platform(), 'orjson': importlib.metadata.version('orjson'),
         'cpu_model': next(line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')),
         'files': [{'path': path.relative_to(ROOT).as_posix(), 'sha256': digest_file(path)} for path in sorted(set(runtime_files))],
         'scope': 'Working-tree source runtime fingerprint; commit mapping and fresh-clone verification follow on PC. Linux host Python scale venv is isolated from Airflow/Spark.'})
    save(target / 'verification.json', {'passed': True, 'actual_100gb_complete': final, 'collected_utc': utc_now(),
         'source_runs': summary, 'scope': 'Full actual source quality and intermediate Spark only; no full 100GB Spark/Gold/feature performance claim.'})
    print(json.dumps({'exported': str(target), 'actual_100gb_complete': final, 'runs': summary}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--final', action='store_true')
    args = parser.parse_args()
    collect(args.final)
