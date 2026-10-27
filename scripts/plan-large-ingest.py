"""Read a bounded sample of sealed source files with O_DIRECT and save a resource/time forecast; never start Spark."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.ingest_budget import large_ingest_plan
from pipelines.orchestration_contract import save, utc_now


def load(path: Path):
    """Read small runtime reports while retaining their exact byte digest in the plan binding."""
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    """Hash only bounded report/catalog files, never the complete source from this forecast helper."""
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError('unexpectedly large metadata file')
    return sha256(path.read_bytes()).hexdigest()


def plan(args) -> None:
    """Preserve source and prior measurements; reject symlinks, unavailable mounts, stale bindings and overwritten plans."""
    os.chdir(ROOT)
    if platform.system() != 'Linux' or any(not Path(p).is_mount() for p in ('/srv/iot-data', '/srv/iot-fast')):
        raise ValueError('run on the existing Linux VM with both coursework mounts')
    if args.output.exists() or not args.output.resolve().is_relative_to((ROOT / 'outputs').resolve()):
        raise ValueError('select a fresh output under outputs/')
    deadline = datetime.fromisoformat(args.deadline_utc.replace('Z', '+00:00'))
    if deadline.tzinfo is None or deadline.timestamp() - time.time() < 600:
        raise ValueError('explicit current UTC deadline and at least ten minutes are required')
    source = ROOT / 'data/source/scale-100gb-26'
    receipt_path = ROOT / 'outputs/scale-100gb-26/receipt.json'
    verification_path = ROOT / 'outputs/scale-100gb-26/verification.json'
    manifest = load(source / 'manifest.json')
    manifest_hash = digest(source / 'manifest.json')
    receipt, verified = load(receipt_path), load(verification_path)
    if (load(source / '_GENERATION_SUCCESS.json') != {'manifest_sha256': manifest_hash} or
            receipt['source_manifest_sha256'] != manifest_hash or verified['source_manifest_sha256'] != manifest_hash or
            receipt['passed'] is not True or not all(receipt['quality']['checks'].values()) or
            verified['actual_offline_payload_bytes'] != manifest['offline_payload_bytes']):
        raise ValueError('large source completion/quality metadata binding differs')
    marker = load(ROOT / 'outputs/scale-100gb-26/_RUN_SUCCESS.json')
    if marker['verification_sha256'] != digest(verification_path) or manifest['catalog']['sha256'] != digest(source / 'files.jsonl'):
        raise ValueError('large source marker or catalog changed')
    entries = [json.loads(line) for line in (source / 'files.jsonl').read_text().splitlines()]
    chosen = [item for item in entries if item['table'] == 'v1' and item['bytes'] >= 64 * 1024**2][:8]
    if len(chosen) != 8:
        raise ValueError('eight sealed v1 parts of at least 64MiB are required')
    samples = []
    for item in chosen:
        if time.time() + 140 > deadline.timestamp():
            raise ValueError('insufficient session time to finish a bounded read probe')
        path = source / item['path']
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()) or path.stat().st_size != item['bytes']:
            raise ValueError('unsafe or changed source file')
        before = path.stat()
        start = time.perf_counter()
        # O_DIRECT avoids falsely reporting a page-cache hit as HDD throughput; no cache eviction or source writes.
        result = subprocess.run(['dd', f'if={path}', 'of=/dev/null', 'bs=4M', 'count=16',
                                 'iflag=direct,fullblock', 'status=none'], capture_output=True, text=True, timeout=120)
        elapsed = time.perf_counter() - start
        if result.returncode:
            raise RuntimeError(f'direct read failed: {result.stderr[:300]}')
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('source changed during read-only probe')
        samples.append({'path': item['path'], 'catalog_sha256': item['sha256'], 'file_bytes': after.st_size,
                        'actual_read_bytes': 64 * 1024**2, 'seconds': elapsed})
    total_bytes, total_seconds = sum(s['actual_read_bytes'] for s in samples), sum(s['seconds'] for s in samples)
    probe = {'passed': True, 'checked_utc': utc_now(), 'method': 'GNU dd iflag=direct,fullblock bs=4M count=16, eight existing parts',
             'actual_read_bytes': total_bytes, 'seconds': total_seconds, 'bytes_per_second': total_bytes / total_seconds,
             'source_manifest_sha256': manifest_hash, 'samples': samples,
             'scope': 'Read-only 512MiB sample; file identity/size/mtime unchanged. Catalog hashes are prior sealed values, '
                      'not new full part hashes. No global cache eviction. Not full-source validation or mixed-IO benchmark.'}
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    resources = {'checked_utc': utc_now(), 'cpu_count': os.cpu_count(),
                 'memory_available_bytes': int(mem['MemAvailable'].split()[0]) * 1024,
                 'free_bytes': {p: shutil.disk_usage(p).free for p in ('/srv/iot-data', '/srv/iot-fast')}}
    reports = {'source': verification_path, 'intermediate': ROOT / 'outputs/scale-intermediate-26/verification.json',
               'ingest': ROOT / 'outputs/scale-ingest-medium-26/ingest-report.json',
               'metrics': ROOT / 'outputs/scale-ingest-medium-26/spark-metrics.json'}
    outcome = large_ingest_plan(verified, load(reports['intermediate']), load(reports['ingest']),
                                load(reports['metrics']), probe, resources, deadline.timestamp() - time.time())
    outcome.update(checked_utc=utc_now(), deadline_utc=deadline.astimezone(timezone.utc).isoformat(),
                   actual_resources=resources, probe=probe,
                   report_bindings={key: {'path': p.relative_to(ROOT).as_posix(), 'sha256': digest(p)} for key, p in reports.items()},
                   code_bindings={p.relative_to(ROOT).as_posix(): digest(p) for p in (Path(__file__), ROOT / 'pipelines/ingest_budget.py')})
    if digest(source / 'manifest.json') != manifest_hash:
        raise ValueError('source manifest changed during probe')
    save(args.output, outcome)
    print(json.dumps({k: outcome[k] for k in ('gates', 'estimated_job_seconds', 'remaining_session_seconds', 'execution_ready')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deadline-utc', required=True)
    parser.add_argument('--output', required=True, type=Path)
    plan(parser.parse_args())
