"""Run protocol-bound medium features on the existing VM with frozen code, read-only input and live limits."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ml.protocol import PROTOCOL_SHA256, load_protocol
from pipelines.ingest_control import deadline_timestamp, resources
from pipelines.ingest_metrics import summarize_events
from pipelines.orchestration_contract import digest, inventory, save, utc_now


def run(args):
    """Preserve every failed attempt and stop only this run when its resource/deadline guard fires."""
    if platform.system() != 'Linux' or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', args.run_name):
        raise ValueError('Use a fresh simple run name on the existing Linux VM')
    os.chdir(ROOT)
    for mount in ('/srv/iot-data', '/srv/iot-fast'):
        if not Path(mount).is_mount():
            raise ValueError('Required coursework filesystem is not mounted')
    deadline = deadline_timestamp(args.deadline_utc)
    protocol_path = ROOT / 'ml/protocols/medium-60d-v1.json'
    protocol = load_protocol(protocol_path)
    source_run = 'large-ingest-medium-27-v4'
    source = ROOT / 'data/lakehouse' / source_run
    pilot = ROOT / 'outputs' / source_run
    verification = json.loads((pilot / 'verification.json').read_text())
    report = json.loads((pilot / 'ingest-report.json').read_text())
    image = subprocess.check_output(['docker', 'image', 'inspect', 'iot-coursework-spark:3.5.9', '--format', '{{.Id}}'], text=True).strip()
    if (not verification['passed'] or not report['passed'] or verification['image_id'] != image
            or json.loads((pilot / '_RUN_SUCCESS.json').read_text())['verification_sha256'] != digest(pilot / 'verification.json')
            or verification['ingest_report_sha256'] != digest(pilot / 'ingest-report.json')
            or json.loads((source / '_RUN_SUCCESS.json').read_text())['report_sha256'] != digest(pilot / 'ingest-report.json')
            or report['source_manifest_sha256'] != protocol['source']['manifest_sha256']):
        raise ValueError('Completed Silver/image/protocol bindings differ')
    fixture = json.loads((ROOT / 'docs/evidence/feature-buckets/report.json').read_text())
    fixture_receipt = json.loads((ROOT / 'docs/evidence/feature-buckets/verification.json').read_text())
    if (not fixture['passed'] or fixture_receipt['report_sha256'] != digest(ROOT / 'docs/evidence/feature-buckets/report.json')
            or fixture_receipt['image_id'] != image or any(digest(ROOT / p) != h for p, h in fixture['code_sha256'].items())):
        raise ValueError('Bounded-feature correctness proof differs from current code/image')
    name = args.run_name
    output, data = ROOT / 'outputs' / name, ROOT / 'data/feature-datasets' / name
    scratch = Path('/srv/iot-data/spark-scratch') / name
    if any(not p.resolve().is_relative_to(ROOT.resolve()) for p in (source, output, data)):
        raise ValueError('Project path escaped through a symlink')
    if not scratch.resolve().is_relative_to(Path('/srv/iot-data')):
        raise ValueError('Scratch escaped the approved HDD mount')
    if any(p.exists() for p in (output, data, scratch)):
        raise ValueError('Output already exists; retain it and choose a new run name')
    before = resources(scratch, data, output)
    if (before['memory_available_bytes'] < 36 * 1024**3 or os.cpu_count() < 7
            or before['free_bytes']['/srv/iot-data'] < 56_000_000_000
            or before['free_bytes']['/srv/iot-fast'] < 35_000_000_000 or time.time() + 2700 + 600 > deadline):
        raise ValueError('45-minute pilot plus finish reserve does not fit resource/session limits')
    lock_path = ROOT / 'data/runtime/large-ingest.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    active = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True).split()
    if any(n.startswith(('iot-large-', 'iot-feature-', 'iot-skew-join-')) for n in active):
        raise ValueError('Another coursework Spark job is active')
    output.mkdir(parents=True)
    output.chmod(0o777)
    for directory in (data, scratch):
        directory.mkdir(parents=True)
        directory.chmod(0o1777)
    snapshot = output / 'code'
    snapshot.mkdir()
    for folder in ('generator', 'pipelines', 'ml'):
        (snapshot / folder).mkdir()
        for path in sorted((ROOT / folder).glob('*.py')):
            shutil.copyfile(path, snapshot / folder / path.name)
    (snapshot / 'ml/protocols').mkdir()
    shutil.copyfile(protocol_path, snapshot / 'ml/protocols' / protocol_path.name)
    # Nested bind mountpoints must exist before Docker makes their parent code mount read-only.
    for mountpoint in ('input', 'result', 'reports', 'events'):
        (snapshot / mountpoint).mkdir()
    code = {p.relative_to(snapshot).as_posix(): digest(p) for p in sorted(snapshot.rglob('*')) if p.is_file()}
    original_files = inventory(source)
    if not original_files:
        raise ValueError('No verified input Parquet files')
    binding = {'source_run': source_run, 'source_manifest_sha256': protocol['source']['manifest_sha256'],
               'raw_offline_bytes': report['source_offline_bytes'], 'protocol_sha256': PROTOCOL_SHA256,
               'pilot_verification_sha256': digest(pilot / 'verification.json'), 'input_files': original_files}
    save(output / 'input-binding.json', binding)
    events = ROOT / 'data/ingest-events'
    events.mkdir(exist_ok=True)
    events.chmod(0o1777)
    previous_events = {p.name for p in events.iterdir()}
    container = 'iot-feature-' + name
    command = ['docker', 'run', '--rm', '--name', container, '--memory', '28g', '--cpus', '6',
               '--network', 'none', '--hostname', 'localhost', '-e', 'SPARK_LOCAL_IP=127.0.0.1',
               '-e', 'SPARK_LOCAL_HOSTNAME=localhost', '-v', str(snapshot) + ':/workspace:ro',
               '-v', str(source) + ':/workspace/input:ro', '-v', str(data) + ':/workspace/result',
               '-v', str(output) + ':/workspace/reports', '-v', str(scratch) + ':/scratch',
               '-v', str(events) + ':/workspace/events', image, '--master', 'local[6]', '--driver-memory', '20g',
               '--conf', 'spark.sql.shuffle.partitions=128', '--conf', 'spark.sql.adaptive.coalescePartitions.enabled=false',
               '--conf', 'spark.default.parallelism=6', '--conf', 'spark.local.dir=/scratch',
               '--conf', 'spark.ui.enabled=false', '--conf', 'spark.eventLog.enabled=true',
               '--conf', 'spark.eventLog.compress=false', '--conf', 'spark.eventLog.rolling.enabled=false',
               '--conf', 'spark.eventLog.dir=file:/workspace/events', '/workspace/pipelines/feature_dataset.py',
               '--input', '/workspace/input', '--output', '/workspace/result/artifacts',
               '--reports', '/workspace/reports', '--protocol', '/workspace/ml/protocols/medium-60d-v1.json',
               '--binding', '/workspace/reports/input-binding.json']
    preflight = {'checked_utc': utc_now(), 'before': before, 'deadline_utc': args.deadline_utc,
                 'base_commit': json.loads((ROOT / 'runtime-source-manifest.json').read_text())['git_commit'],
                 'controller_sha256': digest(Path(__file__)), 'snapshot_sha256': code, 'image_id': image,
                 'command': command, 'input_binding_sha256': digest(output / 'input-binding.json'),
                 'max_job_seconds': 2700, 'scratch_cap_bytes': 20_000_000_000, 'output_cap_bytes': 5_000_000_000,
                 'scope': 'Bounded medium feature preparation within current authorized VM session; no source regeneration or VM extension.'}
    save(output / 'preflight.json', preflight)
    samples = []
    process = None
    started = time.monotonic()

    def stop(*_):
        """Terminate only this named container; keep its partial data and reports."""
        subprocess.run(['docker', 'stop', '-t', '20', container], capture_output=True)
        raise SystemExit(143)

    old_signal = signal.signal(signal.SIGTERM, stop)
    peak_memory = 0
    try:
        with (output / 'spark-console.txt').open('w') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            inspected = None
            while process.poll() is None:
                current = resources(scratch, data, output)
                event_bytes = 0
                for event in events.iterdir():
                    try:
                        if event.name not in previous_events and event.is_file():
                            event_bytes += event.stat().st_size
                    except FileNotFoundError:
                        pass
                current['log_file_bytes'] += event_bytes
                samples.append(current)
                if inspected is None:
                    raw = subprocess.run(['docker', 'inspect', container], capture_output=True, text=True)
                    if raw.returncode == 0:
                        item = json.loads(raw.stdout)[0]
                        if item['State']['Pid']:
                            assert item['HostConfig']['Memory'] == 28 * 1024**3 and item['HostConfig']['NanoCpus'] == 6_000_000_000
                            mounts = {x['Destination']: x for x in item['Mounts']}
                            assert mounts['/workspace/input']['RW'] is False and mounts['/workspace']['RW'] is False
                            assert mounts['/scratch']['Source'] == str(scratch)
                            inspected = {'memory_bytes': item['HostConfig']['Memory'], 'nano_cpus': item['HostConfig']['NanoCpus'],
                                         'input_read_only': True, 'code_read_only': True, 'scratch': str(scratch), 'pid': item['State']['Pid']}
                            save(output / 'container-limits.json', inspected)
                if inspected:
                    try:
                        cgroup = next(line.split(':', 2)[2] for line in Path(f"/proc/{inspected['pid']}/cgroup").read_text().splitlines() if line.startswith('0::'))
                        peak_memory = max(peak_memory, int((Path('/sys/fs/cgroup') / cgroup.lstrip('/') / 'memory.peak').read_text()))
                    except FileNotFoundError:
                        pass
                if (current['scratch_file_bytes'] > 20_000_000_000 or current['output_file_bytes'] > 5_000_000_000
                        or current['log_file_bytes'] > 1_000_000_000 or current['free_bytes']['/srv/iot-data'] < 30_000_000_000
                        or current['free_bytes']['/srv/iot-fast'] < 35_000_000_000 or current['memory_available_bytes'] < 4 * 1024**3
                        or time.monotonic() - started > 2700 or time.time() + 600 > deadline):
                    raise RuntimeError('Feature job resource/session guard fired')
                time.sleep(2)
            if process.returncode != 0 or inspected is None:
                raise RuntimeError(f'Spark fixture/job failed: {process.returncode}; inspect retained console')
        result = json.loads((output / 'feature-report.json').read_text())
        if (not result['passed'] or digest(output / 'feature-report.json') != json.loads((data / 'artifacts/_FEATURES_SUCCESS.json').read_text())['report_sha256']
                or result['protocol_sha256'] != PROTOCOL_SHA256 or result['input_binding_sha256'] != digest(output / 'input-binding.json')
                or inventory(source) != original_files or any(digest(snapshot / p) != h for p, h in code.items())):
            raise ValueError('Input, code, protocol or output marker binding changed')
        metrics = summarize_events(events / result['application_id'])
        if not metrics['application_ended'] or metrics['failed_attempts'] or metrics['task_attempts'] < 1:
            raise ValueError('Spark event log did not complete successfully')
        save(output / 'spark-metrics.json', metrics)
        save(output / 'resource-samples.json', {'samples': samples, 'finished': True, 'cgroup_memory_peak_bytes': peak_memory,
             'peak_scratch_file_bytes': max(s['scratch_file_bytes'] for s in samples),
             'peak_output_file_bytes': max(s['output_file_bytes'] for s in samples),
             'scope': 'Two-second samples can miss transient peaks; cgroup memory.peak sampled before shutdown.'})
        receipt = {'passed': True, 'completed_utc': utc_now(), 'wrapper_seconds': time.monotonic() - started,
                   'source_input_unchanged': True, 'protocol_sha256': PROTOCOL_SHA256, 'image_id': image,
                   'report_sha256': digest(output / 'feature-report.json'), 'metrics_sha256': digest(output / 'spark-metrics.json'),
                   'resources_sha256': digest(output / 'resource-samples.json'), 'preflight_sha256': digest(output / 'preflight.json'),
                   'scope': 'Medium features/cohort/class support on actual 60-day source; no training, Feast or100GB feature claim.'}
        save(output / 'verification.json', receipt)
        save(output / '_RUN_SUCCESS.json', {'verification_sha256': digest(output / 'verification.json')})
        print(json.dumps(receipt, indent=2), flush=True)
    except BaseException as error:
        save(output / 'failure.json', {'passed': False, 'checked_utc': utc_now(), 'type': type(error).__name__, 'message': str(error)})
        raise
    finally:
        if process is not None and process.poll() is None:
            subprocess.run(['docker', 'stop', '-t', '20', container], capture_output=True)
            process.wait(timeout=45)
        signal.signal(signal.SIGTERM, old_signal)
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--deadline-utc', required=True)
    run(parser.parse_args())
