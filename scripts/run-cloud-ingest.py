"""Run the existing chunk ingest on the Linux VM with recorded baseline or reviewed cloud settings."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pipelines.chunk_source import ChunkSource


def write_json(path: Path, value: dict):
    """Save finite runtime evidence without credentials or invented measurements."""
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def run(args):
    """Use the same immutable source/job for two resource profiles, with separate fresh outputs."""
    if platform.system() != 'Linux':
        raise ValueError('Use this launcher on the approved Linux VM; the PC PowerShell launcher remains separate')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',args.run_name):
        raise ValueError('Invalid RunName')
    os.chdir(ROOT)
    source=args.source.resolve()
    receipt=args.receipt.resolve()
    if not source.is_relative_to(ROOT) or not receipt.is_relative_to(ROOT):
        raise ValueError('Source and receipt must be inside this project')
    catalog=ChunkSource(source,receipt)
    profile='basic' if args.mode=='baseline' else 'accelerated'
    capacity=json.loads((ROOT/'infra/gcp/capacity-profiles.json').read_text())[profile]
    threads=capacity['suggested_spark_threads']
    heap=capacity['suggested_spark_heap_gib']
    memory=int(capacity['suggested_spark_container_gib']*1024**3)
    available=int(re.search(r'^MemAvailable:\s+(\d+)',Path('/proc/meminfo').read_text(),re.M)[1])*1024
    if os.cpu_count()<threads+1 or available<memory+4*1024**3:
        raise ValueError('Insufficient actual CPU/free memory for the chosen cloud profile')
    if shutil.disk_usage(ROOT).free<max(8*1024**3,catalog.offline_bytes*4):
        raise ValueError('Insufficient data/output space for this bounded run')
    output=ROOT/'outputs'/args.run_name
    lakehouse=ROOT/'data/lakehouse'/args.run_name
    scratch=(ROOT/'data/cloud-scratch'/args.run_name if profile=='basic'
             else Path('/srv/iot-fast/spark-scratch')/args.run_name)
    if any(p.exists() for p in (output,lakehouse,scratch)):
        raise ValueError('Run exists; retain it and choose another name')
    if profile!='basic' and not Path('/srv/iot-fast').is_mount():
        raise ValueError('SSD must be mounted by the bootstrap playbook before optimized ingest')
    if shutil.disk_usage(scratch.parent if scratch.parent.exists() else ROOT).free<8*1024**3:
        raise ValueError('Insufficient scratch space for the pilot')
    output.mkdir(parents=True)
    output.chmod(0o777)
    for folder in (ROOT/'data/lakehouse',ROOT/'data/ingest-events'):
        folder.mkdir(parents=True,exist_ok=True)
        folder.chmod(0o1777)
    scratch.mkdir(parents=True)
    scratch.chmod(0o1777)
    shutil.copyfile(receipt,output/'receipt.json')
    env=os.environ.copy()
    env.update(IOT_INGEST_CPUS=str(threads),IOT_INGEST_MEMORY=str(memory),IOT_INGEST_SCRATCH=str(scratch))
    compose=['docker','compose','-f','infra/ingest.compose.yaml','-f','infra/ingest.cloud.compose.yaml']
    records=[]
    def command(argv: list[str], log: str):
        """Record each real command, exit status and elapsed wall time, stopping on failure."""
        started=time.perf_counter()
        with (output/log).open('w') as handle:
            result=subprocess.run(argv,env=env,stdout=handle,stderr=subprocess.STDOUT)
        records.append({'argv':argv,'exit_code':result.returncode,'elapsed_seconds':time.perf_counter()-started})
        write_json(output/'commands.json',records)
        if result.returncode:
            raise RuntimeError(f'Command failed; inspect {output/log}')
    command([sys.executable,'-m','unittest','discover','-s','tests','-v'],'unit-tests.txt')
    if not args.skip_build:
        command(compose+['build','ingest'],'build.txt')
    command(compose+['config','--format','json'],'resolved-compose.json')
    resolved=json.loads((output/'resolved-compose.json').read_text())['services']['ingest']
    if int(resolved['mem_limit'])!=memory or float(resolved['cpus'])!=threads:
        raise ValueError('Resolved Docker limits do not match the selected profile')
    partitions=args.partitions or (8 if profile=='basic' else 16)
    if not 1<=partitions<=64:
        raise ValueError('Partition count must be 1..64')
    command(compose+['run','--rm','--service-ports','--name',f'iot-{args.run_name}','ingest',
        '--master',f'local[{threads}]','--driver-memory',f'{heap}g',
        '--conf','spark.driver.bindAddress=0.0.0.0','--conf','spark.driver.host=127.0.0.1',
        '--conf',f'spark.local.dir=/scratch/{args.run_name}',
        '--conf','spark.eventLog.enabled=true','--conf','spark.eventLog.dir=file:///workspace/data/ingest-events',
        'pipelines/chunk_ingest.py','--source',str(source.relative_to(ROOT)),
        '--receipt',f'outputs/{args.run_name}/receipt.json','--output',str(lakehouse.relative_to(ROOT)),
        '--report',f'outputs/{args.run_name}/ingest-report.json','--partitions',str(partitions),
        '--query-repetitions','3'],'spark-console.txt')
    report=json.loads((output/'ingest-report.json').read_text())
    if not report['passed'] or not (lakehouse/'_RUN_SUCCESS.json').is_file():
        raise ValueError('Successful report and marker are both required')
    # Spark event logs are private to the image's UID; summarize with that same UID instead of widening permissions.
    command(compose+['run','--rm','--entrypoint','python3','ingest','-m','pipelines.ingest_metrics',
        '--log',f"data/ingest-events/{report['application_id']}",
        '--report',f'outputs/{args.run_name}/spark-metrics.json'],'metrics-console.txt')
    context={'captured_utc':datetime.now(timezone.utc).isoformat(),'host':platform.node(),
        'platform':platform.platform(),'cpu_count':os.cpu_count(),'host_python':platform.python_version(),
        'mode':args.mode,'profile':profile,'threads':threads,'heap_gib':heap,'container_bytes':memory,
        'shuffle_partitions':partitions,'free_ram_bytes_before':available,'scratch':str(scratch),
        'source_manifest_sha256':catalog.manifest_sha256,
        'image_id':subprocess.check_output(['docker','image','inspect','iot-coursework-spark:3.5.9','--format','{{.Id}}']).decode().strip(),
        'launcher_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_bundle_manifest_sha256':hashlib.sha256((ROOT/'runtime-source-manifest.json').read_bytes()).hexdigest(),
        'scope':'One bounded cloud run with unchanged chunk ingest. Baseline uses HDD scratch/local[2]/1GiB heap; optimized uses SSD scratch/local[6]/20GiB heap and 16 partitions. No isolated attribution, stable speedup or 100GB claim.'}
    write_json(output/'cloud-context.json',context)
    print(f"PASS: {args.run_name}; {len(report['checks'])} checks; {report['silver_unique_rows']} unique; {report['job_wall_seconds']:.3f}s")


def main():
    """Launch a new local VM run; make resource choices explicit instead of inheriting PC limits."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-name',required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--mode',choices=['baseline','optimized'],required=True)
    parser.add_argument('--partitions',type=int)
    parser.add_argument('--skip-build',action='store_true')
    args=parser.parse_args()
    run(args)


if __name__=='__main__':
    main()
