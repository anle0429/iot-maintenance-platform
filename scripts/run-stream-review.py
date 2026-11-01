"""Run a bounded known-answer Flink study on the approved Linux CPU VM without changing the CI-pinned stream runner."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generator.storage import write_json
from pipelines.stream_contract import fixture_phases, window_truth
from pipelines.stream_verification import StreamRunner, compare_windows


def reserve(descriptor):
    """Validate the actual CPU-session identity and retain RAM, storage and time headroom before each finite scenario."""
    assert descriptor['name'] == 'iot-cw-vm' and descriptor['machineType'].endswith('/e2-highmem-8')
    assert descriptor['scheduling']['maxRunDuration']['seconds'] == '28800'
    assert descriptor['scheduling']['instanceTerminationAction'] == 'STOP'
    deadline = datetime.fromisoformat(descriptor['lastStartTimestamp']) + timedelta(hours=8)
    assert datetime.now(timezone.utc) + timedelta(minutes=15) < deadline
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(memory['MemAvailable'].split()[0]) * 1024
    free = {p: shutil.disk_usage(p).free for p in ['/srv/iot-data', '/srv/iot-fast']}
    assert available > 8 * 1024**3 and min(free.values()) > 15 * 1024**3
    return {'checked_utc': datetime.now(timezone.utc).isoformat(), 'available_ram_bytes': available,
            'free_bytes': free, 'deadline_utc': deadline.isoformat()}


def prepare_permissions(runner, docker):
    """Assign only this run's fresh directories to the image's actual non-root identity, never an existing tree."""
    identity = []
    for flag in ['-u', '-g']:
        raw = subprocess.check_output([docker, 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                                       '--entrypoint', 'id', 'iot-coursework-flink:1.20.3', flag], text=True).strip()
        assert raw.isdigit() and int(raw) > 0
        identity.append(int(raw))
    for directory in [runner.data, runner.output]:
        assert not directory.is_symlink()
        os.chown(directory, *identity); directory.chmod(0o750)
    write_json(runner.output / 'host-permissions.json', {'uid': identity[0], 'gid': identity[1],
               'scope': 'Only the two directories just created by StreamRunner; no recursive permission change'})


def main():
    """Bind source bytes, reuse the original runtime/oracle and verify the same pair plus retention without requiring Git on the VM."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--source-manifest', type=Path, required=True)
    parser.add_argument('--descriptor', type=Path, required=True)
    parser.add_argument('--docker', default='docker')
    parser.add_argument('--ui-pause-seconds', type=int, choices=range(61), default=0)
    args = parser.parse_args()
    assert os.name == 'posix' and os.geteuid() == 0, 'Use a Linux root launcher for bind-mount ownership preparation'
    os.chdir(ROOT)
    source = json.loads(args.source_manifest.read_text())
    assert source['files'] and any(row['path'] == 'scripts/run-stream-review.py' for row in source['files'])
    for row in source['files']:
        path = ROOT / row['path']
        assert not Path(row['path']).is_absolute() and '..' not in Path(row['path']).parts and not path.is_symlink()
        assert sha256(path.read_bytes()).hexdigest() == row['sha256'], row['path']
    descriptor = json.loads(args.descriptor.read_text(encoding='utf-8-sig'))
    checks = [reserve(descriptor)]
    runner = StreamRunner(args.docker, args.run_id, args.ui_pause_seconds)
    prepare_permissions(runner, args.docker)
    write_json(runner.output / 'source-manifest.json', source)
    baseline = runner.scenario('baseline', baseline=True)
    checks.append(reserve(descriptor))
    corrected = runner.scenario('corrected')
    events = [r['event'] for phase in fixture_phases() for r in phase if r['kind'] == 'telemetry' and r['event']['event_id'] != 'E']
    compare_windows(corrected['windows'], window_truth(events))
    assert corrected['audit_counts']['unique'] == 6 and corrected['audit_counts']['duplicate'] == 13
    assert corrected['late_event_ids'] == ['E'] and 'D' in baseline['late_event_ids']
    assert baseline['windows'][0]['reading_count'] == 6 and baseline['windows'][0]['temperature_sum'] == 100
    checks.append(reserve(descriptor))
    retention = runner.scenario('retention-probe', ttl_probe=True)
    assert len(retention['windows']) == 1 and retention['windows'][0]['reading_count'] == 1
    assert retention['audit_counts']['duplicate'] == 2 and retention['audit_counts'].get('expired', 0) >= 1
    assert retention['duplicates_from_durable_ledger'] == 1
    assert all(sha256((ROOT / r['path']).read_bytes()).hexdigest() == r['sha256'] for r in source['files'])
    report = {'passed': True, 'completed_utc': datetime.now(timezone.utc).isoformat(), 'source': source,
              'resource_checks': checks, 'baseline': baseline, 'corrected': corrected, 'retention_probe': retention,
              'source_unchanged': True, 'scope': 'Finite 22-message pair and retention study. Explicit source manifest; VM may have no Git checkout. No full-source replay, performance comparison, crash recovery or exactly-once claim.'}
    write_json(runner.output / 'verification.json', report)
    print(json.dumps({'passed': True, 'run': args.run_id, 'completed_utc': report['completed_utc']}, indent=2), flush=True)


if __name__ == '__main__':
    main()
