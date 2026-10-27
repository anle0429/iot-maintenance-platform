"""Finish the measured intermediate gate, then authorize one bounded 100GB source run only if all guards pass."""

from datetime import datetime
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator.chunked import ChunkConfig, digest_file
from pipelines.chunk_source import ChunkSource
from pipelines.orchestration_contract import save, utc_now
from pipelines.scale_budget import scale_plan


def resources() -> dict:
    """Read current per-volume free bytes and reclaimable RAM after the intermediate Spark run."""
    values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {'checked_utc': utc_now(), 'memory_available_bytes': int(values['MemAvailable'].split()[0]) * 1024,
            'memory_total_bytes': int(values['MemTotal'].split()[0]) * 1024, 'cpu_count': __import__('os').cpu_count(),
            'free_bytes': {p: shutil.disk_usage(p).free for p in ('/srv/iot-data', '/srv/iot-fast')}}


def run(deadline: str, reuse_intermediate: bool, base_commit: str) -> None:
    """Do not replace failed data or extend the approved eight-hour VM limit."""
    directory = ROOT / ('outputs/scale-protocol-fresh-session-26' if reuse_intermediate else 'outputs/scale-protocol-26')
    directory.mkdir(exist_ok=False)
    save(directory / 'protocol-runtime.json', {'script_sha256': digest_file(Path(__file__)),
         'budget_module_sha256': digest_file(ROOT / 'pipelines/scale_budget.py'),
         'deadline_utc': deadline, 'reuse_intermediate': reuse_intermediate, 'base_commit': base_commit})
    calibrations = []
    for suffix in ('base', 'cached', 'fast'):
        folder = ROOT / f'outputs/scale-calibration-{suffix}-26'
        result = json.loads((folder / 'verification.json').read_text())
        if result.get('passed') is not True:
            raise ValueError('calibration did not pass')
        calibrations.append({'mode': suffix, 'source_manifest_sha256': result['source_manifest_sha256'],
                             'bytes': result['actual_offline_payload_bytes'], 'phase_reports': result['phase_reports']})
    if len({c['source_manifest_sha256'] for c in calibrations}) != 1:
        raise ValueError('optimized source bytes differ from baseline')
    save(directory / 'calibration-comparison.json', {'passed': True, 'runs': calibrations,
         'scope': 'One measured run/mode, same config and byte-identical sources; services/cache not isolated.'})
    intermediate = ROOT / 'outputs/scale-intermediate-26'
    for attempt in range(240):
        if (intermediate / '_RUN_SUCCESS.json').exists():
            break
        if time.time() + 1200 >= datetime.fromisoformat(deadline).timestamp():
            raise RuntimeError('session too short to continue intermediate gate')
        time.sleep(5)
    else:
        raise RuntimeError('intermediate source did not complete within the bounded wait')
    marker = json.loads((intermediate / '_RUN_SUCCESS.json').read_text())
    if marker['verification_sha256'] != digest_file(intermediate / 'verification.json'):
        raise ValueError('intermediate success marker mismatch')
    if reuse_intermediate:
        sample = json.loads((ROOT / 'outputs/scale-ingest-medium-26-resources.json').read_text())
        if sample.get('finished') is not True or sample.get('passed') is not True:
            raise ValueError('the existing intermediate Spark run is not complete')
        source = ChunkSource(ROOT / 'data/source/scale-intermediate-26', intermediate / 'receipt.json')
        save(directory / 'source-after-restart.json', {'all_source_files_rehashed': True,
             'manifest_sha256': source.manifest_sha256, 'offline_bytes': source.offline_bytes, 'checked_utc': utc_now()})
    else:
        with (directory / 'intermediate-ingest-console.txt').open('w') as log:
            subprocess.run([sys.executable, str(ROOT / 'scripts/measure-scale-ingest.py')], cwd=ROOT,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
    measured = json.loads((intermediate / 'verification.json').read_text())
    ingest = json.loads((ROOT / 'outputs/scale-ingest-medium-26/ingest-report.json').read_text())
    actual_resources = resources()
    config = ChunkConfig.load(ROOT / 'generator/configs/scale-100gb.json')
    remaining = datetime.fromisoformat(deadline).timestamp() - time.time()
    plan = scale_plan(measured, ingest, actual_resources, config.unique_rows, remaining)
    save(directory / '100gb-plan.json', plan)
    if not plan['generation_eligible']:
        raise RuntimeError(f'100GB gates failed: {plan["gates"]}; no large source started')
    save(directory / 'large-start.json', {'started_utc': utc_now(), 'plan_sha256': digest_file(directory / '100gb-plan.json'),
                                         'deadline_utc': deadline, 'reason': 'intermediate correctness and all four resource/time gates passed'})
    command = [sys.executable, str(ROOT / 'scripts/run-scale-source.py'), '--run-name', 'scale-100gb-26',
               '--config', 'generator/configs/scale-100gb.json', '--fast-json', '--chunk-rows', '250000',
               '--maximum-bytes', '115000000000', '--source-reserve-bytes', str(plan['source_reserve_bytes']),
               '--target-bytes', '100000000000', '--estimated-seconds', str(plan['estimated_generate_and_validate_seconds']),
               '--deadline-utc', deadline, '--base-commit', base_commit]
    with (directory / 'large-console.txt').open('w') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    save(directory / '_PROTOCOL_SUCCESS.json', {'completed_utc': utc_now(),
         'large_verification_sha256': digest_file(ROOT / 'outputs/scale-100gb-26/verification.json')})
    print('PASS: intermediate Spark and actual >=100GB source generation/full readback completed.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deadline-utc', required=True, help='Actual lastStartTimestamp + maxRunDuration from GCE')
    parser.add_argument('--reuse-intermediate', action='store_true')
    parser.add_argument('--base-commit', default='UNCOMMITTED')
    args = parser.parse_args()
    run(args.deadline_utc, args.reuse_intermediate, args.base_commit)
