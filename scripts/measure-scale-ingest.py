"""Run the existing intermediate Spark verification while sampling real RAM and scratch usage."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.orchestration_contract import save, utc_now


def tree_bytes(root: Path) -> int:
    """Sample file sizes without retaining paths, tolerating temporary Spark files disappearing."""
    total = 0
    for directory, _, files in os.walk(root):
        for name in files:
            try:
                total += (Path(directory) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def run() -> None:
    """Keep this fixed intermediate workload separate from any claim of a full 100GB Spark run."""
    name = 'scale-ingest-medium-26'
    sample_path = ROOT / 'outputs' / (name + '-resources.json')
    if sample_path.exists():
        raise ValueError('resource report exists; preserve the original run')
    scratch = Path('/srv/iot-fast/spark-scratch') / name
    command = [sys.executable, 'scripts/run-cloud-ingest.py', '--run-name', name,
               '--mode', 'optimized', '--source', 'data/source/scale-intermediate-26',
               '--receipt', 'outputs/scale-intermediate-26/receipt.json', '--partitions', '32', '--skip-build']
    samples = 0
    peak = 0
    minimum_ram = minimum_ssd = None
    started = time.perf_counter()
    with (ROOT / 'outputs' / (name + '-launcher.txt')).open('w') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
                available = int(values['MemAvailable'].split()[0]) * 1024
                free = __import__('shutil').disk_usage('/srv/iot-fast').free
                minimum_ram = available if minimum_ram is None else min(minimum_ram, available)
                minimum_ssd = free if minimum_ssd is None else min(minimum_ssd, free)
                peak = max(peak, tree_bytes(scratch))
                samples += 1
                if available < 4 * 1024**3 or free < 35_000_000_000:
                    raise RuntimeError('RAM/SSD reserve reached')
                save(sample_path, {'finished': False, 'sampled_utc': utc_now(), 'samples': samples,
                     'observed_peak_scratch_file_bytes': peak, 'minimum_available_ram_bytes': minimum_ram,
                     'minimum_ssd_free_bytes': minimum_ssd, 'elapsed_seconds': time.perf_counter() - started})
                time.sleep(1)
        finally:
            if process.poll() is None:
                subprocess.run(['docker', 'stop', '--time', '20', f'iot-{name}'], capture_output=True)
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=30)
    report = json.loads(sample_path.read_text())
    report.update(finished=True, passed=process.returncode == 0, exit_code=process.returncode,
                  finished_utc=utc_now(), elapsed_seconds=time.perf_counter() - started,
                  scope='1-second sampled intermediate Spark scratch and host memory; short spikes may be missed. Not a 100GB run or isolated platform measurement.')
    save(sample_path, report)
    if process.returncode:
        raise RuntimeError('intermediate Spark verification failed; inspect retained logs')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    os.chdir(ROOT)
    run()
