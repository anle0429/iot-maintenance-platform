"""Run two bounded native Locust profiles and reconcile every completed model request with the durable observer ledger."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,base64,json,os,subprocess,time


def run(args):
    """Use existing VM/model resources and preserve original HTML, CSV, counters and same-Pod identities."""
    os.umask(0o077);root=args.runtime_root.resolve();source=args.source_root.resolve();out=args.output.resolve()
    assert os.geteuid()==0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    seal=json.loads((source.parent/'manifest.json').read_bytes());assert seal['format']=='iot-llm-load-source-v1'
    for n,digest in seal['files'].items():assert sha256((source/n).read_bytes()).hexdigest()==digest
    out.mkdir(exist_ok=False);os.chown(out,10001,10001);k=['/usr/local/bin/k3s','kubectl']
    def save(name,value):
        """Retain public workload receipts with finite JSON numbers."""
        (out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    def get(*a):
        """Read actual native deployment/Pod identities; Secret material uses a private in-memory path."""
        return json.loads(subprocess.check_output(k+['get',*a,'-o','json'],timeout=30,stderr=subprocess.PIPE))
    def command(name,argv,env=None,user=None,group=None):
        """Save original exits and output streams for independently reviewable load phases."""
        with (out/(name+'.txt')).open('x') as stream:
            p=subprocess.run(argv,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=110,user=user,group=group)
        save(name+'-exit.json',{'exit_code':p.returncode});assert p.returncode==0,name
    before=get('pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-llm-observer');assert len(before['items'])==1;pod=before['items'][0]
    assert all(v['ready'] for v in pod['status']['containerStatuses']);save('pods-before.json',before)
    service=get('service','iot-llm-observer','-n','iot-llm');assert service['spec']['type']=='ClusterIP';save('service.json',service)
    def ledger(name):
        """Read the running SQLite through its actual runtime rather than copying an active WAL database."""
        code='from llm_observability.ledger import TelemetryLedger; import json; s=TelemetryLedger("/var/lib/iot-llm-observer/telemetry.sqlite"); print(json.dumps(s.rows())); s.close()'
        raw=subprocess.check_output(k+['exec','-n','iot-llm',pod['metadata']['name'],'-c','observer','--','python','-B','-c',code],timeout=30,stderr=subprocess.PIPE)
        (out/name).write_bytes(raw);return json.loads(raw)
    old=ledger('ledger-before.json');old_ids={v['event_id'] for v in old if v['kind']=='request'}
    secret=get('secret','iot-llm-observer-client','-n','iot-llm');token=base64.b64decode(secret['data']['API_KEY'])
    private=root/'data/runtime/llm-monitoring-29a/load-token';assert not private.exists();private.write_bytes(token);os.chown(private,10001,10001);private.chmod(0o600)
    executable=root/'data/runtime/locust-venv/bin/locust';command('locust-version',[str(executable),'--version'])
    save('source.json',seal)
    try:
        for users in [1,4]:
            prefix=out/('users-'+str(users));env={**os.environ,'HOME':'/tmp','PYTHONDONTWRITEBYTECODE':'1',
                'IOT_LLM_LOAD_TOKEN_FILE':str(private),'IOT_LLM_LOAD_SUMMARY':str(prefix)+'-summary.json'}
            argv=[str(executable),'-f',str(source/'tests/load/llm_observer.py'),'--headless','--host','http://'+service['spec']['clusterIP']+':8081',
                '--users',str(users),'--spawn-rate',str(users),'--run-time','30s','--stop-timeout','10','--only-summary','--html',str(prefix)+'.html','--csv',str(prefix)]
            save('users-'+str(users)+'-command.json',argv);command('users-'+str(users),argv,env,10001,10001)
        after=ledger('ledger-after.json');new={v['event_id']:v['facts'] for v in after if v['kind']=='request' and v['event_id'] not in old_ids}
        records=[];summaries={}
        for users in [1,4]:
            summary=json.loads((out/('users-'+str(users)+'-summary.json')).read_bytes());assert summary['passed'];summaries[str(users)]={n:v for n,v in summary.items() if n!='records'}
            records.extend(summary['records'])
        assert len(new)==len(records)==sum(v['requests'] for v in summaries.values()) and len({v['request_id'] for v in records})==len(records)
        for row in records:
            fact=new[row['request_id']];assert row['status']==fact['status']==200 and not row['failed'] and fact['result']=='ok'
            assert row['usage']==fact['usage'] and fact['streaming'] and fact['ttft_seconds'] is not None and fact['ttft_seconds']<=fact['duration_seconds']
            assert fact['duration_seconds']<=row['response_ms']/1000+.05
        current=get('pods','-n','iot-llm','-l','app.kubernetes.io/instance=iot-llm-observer');save('pods-after.json',current)
        assert len(current['items'])==1 and current['items'][0]['metadata']['uid']==pod['metadata']['uid'] and current['items'][0]['spec']==pod['spec']
        assert {v['name']:v['restartCount'] for v in current['items'][0]['status']['containerStatuses']}=={v['name']:v['restartCount'] for v in pod['status']['containerStatuses']}
        report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'source_commit':seal['commit'],'profiles':summaries,
            'request_delta':len(new),'all_request_ids_usage_and_timings_reconciled':True,'observer_pod_uid':pod['metadata']['uid'],'source_files':seal['files'],
            'scope':'Two paced 30-second profiles with 1 and 4 clients, actual GPU SSE generation, native Locust HTML/CSV and independent durable request reconciliation. No maximum capacity or whole-agent load claim.'}
        save('verification.json',report)
        for p in out.rglob('*'):
            if p.is_file():assert token not in p.read_bytes() and base64.b64encode(token) not in p.read_bytes()
        print(json.dumps(report))
    finally:private.unlink()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['runtime-root','source-root','output']:p.add_argument('--'+n,type=Path,required=True)
    run(p.parse_args())
