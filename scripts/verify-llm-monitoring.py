"""Verify sealed native LLM dashboards, independent GPU host readings and real streamed-generation load without cloud access."""
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from io import BytesIO,StringIO
from pathlib import PurePosixPath
import csv,json,re,sys,tarfile
from integration_evidence import ROOT,capsule,read

MANIFEST='f722010e03205b56ba6252165b820a0bfef91e29730b738714649a6f6e4d1afd'


def unpack(raw,root,inventory_hash,payloads):
    """Authenticate every original native file without extracting paths or trusting archive links."""
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=PurePosixPath(member.name).relative_to(root);assert not name.is_absolute() and '..' not in name.parts
            name=name.as_posix();assert name not in bodies and member.size<10000000
            bodies[name]=archive.extractfile(member).read()
    assert sha256(bodies['manifest.json']).hexdigest()==inventory_hash
    manifest=json.loads(bodies['manifest.json']);assert len(manifest['files'])==payloads and set(bodies)==set(manifest['files'])|{'manifest.json'}
    for name,item in manifest['files'].items():assert len(bodies[name])==item['bytes'] and sha256(bodies[name]).hexdigest()==item['sha256'],name
    return bodies


def scalars(raw,name):
    """Parse the independent exporter sample identities without importing a model/exporter dependency."""
    result={}
    for line in raw.decode().splitlines():
        match=re.fullmatch(re.escape(name)+r'(?:\{([^}]*)\})? ([-+0-9.eE]+)',line)
        if match:
            labels=tuple(sorted(re.findall(r'([a-z_]+)="([^"\\]*)"',match[1] or '')))
            assert labels not in result;result[labels]=float(match[2])
    return result


def current_text(name):
    """Normalize only Windows working-tree newlines when comparing maintained text with original Git blob bytes."""
    return (ROOT/name).read_bytes().replace(b'\r\n',b'\n')


def verify():
    """Recompute workload/log/metric relationships and retain native failure and trace-warning limits."""
    base,count=capsule('llm-monitoring',MANIFEST);assert count==23
    meta=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert len(raw)==meta['archive_bytes']==1822675 and sha256(raw).hexdigest()==meta['archive_sha256']=='f933cfb6a54ca8817992ff1d36c84aadba1b4ca753ccc3ebcbbf41151d8dc51f'
    assert meta['actual_native_credentials_scanned'] and meta['pc_actual_gpu_join_credential_scanned'] and not meta['recompressed_or_rewritten_on_pc'] and not meta['workloads_rerun_for_capture']
    bodies=unpack(raw,'llm-monitoring-capture-29a','049795bffd006304efd0920706a098e579bcf0051572bc5128004d9df8ae79af',403)
    native=lambda n:json.loads(bodies[n])
    gpu_meta=native('gpu/native-archive.json');gpu_raw=bodies['gpu/original-native.tar.gz']
    assert len(gpu_raw)==gpu_meta['bytes']==15091 and sha256(gpu_raw).hexdigest()==gpu_meta['sha256']=='98939992e98240057534e711cf709dcb4d109958a7938c2dadc87a78c516579a'
    gpu=unpack(gpu_raw,'gpu-host-capture-29a','37d3ab5704598a89b71699e5d43dd784b3a2b080ff5897a823f76942fd84381f',20)
    prefix='native/gpu-host-exporter-probe-29a/';before=json.loads(gpu[prefix+'before.json']);after=json.loads(gpu[prefix+'after.json']);metrics=gpu[prefix+'metrics.txt']
    report=json.loads(gpu[prefix+'verification.json']);assert report['passed'] and report['physical_interface']=='ens5'
    assert (base/'gpu-host-verification.json').read_bytes()==gpu[prefix+'verification.json']
    assert (ROOT/'scripts/probe-gpu-host-telemetry.py').read_bytes()==gpu['source/probe-gpu-host-telemetry.py']
    assert (ROOT/'scripts/import-gpu-host-exporter.py').read_bytes()==gpu['source/import-gpu-host-exporter.py']
    assert before['hostname']==after['hostname']=='iot-cw-gpu' and before['logical_processors']==after['logical_processors']==4
    idle={dict(k)['cpu']:v for k,v in scalars(metrics,'node_cpu_seconds_total').items() if dict(k)['mode']=='idle'};assert len(idle)==4
    for cpu,value in idle.items():assert before['idle_seconds'][cpu]-.01<=value<=after['idle_seconds'][cpu]+.01
    assert list(scalars(metrics,'node_memory_MemTotal_bytes').values())==[before['memory_bytes']['MemTotal']]==[after['memory_bytes']['MemTotal']]==[15705165824]
    value=next(iter(scalars(metrics,'node_memory_MemAvailable_bytes').values()))
    assert min(before['memory_bytes']['MemAvailable'],after['memory_bytes']['MemAvailable'])-4*1024**2<=value<=max(before['memory_bytes']['MemAvailable'],after['memory_bytes']['MemAvailable'])+4*1024**2
    for metric,field,tolerance in [('node_filesystem_size_bytes','size_bytes',0),('node_filesystem_avail_bytes','available_bytes',1024**2)]:
        values=[v for k,v in scalars(metrics,metric).items() if dict(k)['mountpoint']=='/'];assert len(values)==1
        assert min(before['root_filesystem'][field],after['root_filesystem'][field])-tolerance<=values[0]<=max(before['root_filesystem'][field],after['root_filesystem'][field])+tolerance
    assert before['root_filesystem']['size_bytes']==after['root_filesystem']['size_bytes']==50884108288
    for metric,field in [('node_network_receive_bytes_total','receive_bytes'),('node_network_transmit_bytes_total','transmit_bytes')]:
        values=[v for k,v in scalars(metrics,metric).items() if dict(k)['device']=='ens5'];assert len(values)==1
        assert before['network']['ens5'][field]<=values[0]<=after['network']['ens5'][field]
    for metric,field in [('node_disk_read_bytes_total','read_bytes'),('node_disk_written_bytes_total','written_bytes')]:
        for labels,value in scalars(metrics,metric).items():
            device=dict(labels)['device'];assert before['disk'][device][field]<=value<=after['disk'][device][field]
    assert {dict(k)['collector']:v for k,v in scalars(metrics,'node_scrape_collector_success').items()}==dict.fromkeys(['cpu','meminfo','filesystem','diskstats','netdev'],1)
    for folder,files in [('gpu-host-telemetry-cc6465bc',10),('llm-monitoring-5db51fa7',3),('llm-dashboards-41e38b3f',5),('llm-load-faba88cb',2),('llm-load-f802aae6',2)]:
        seal=native('source/'+folder+'/manifest.json');assert len(seal['files'])==files
        for name,digest in seal['files'].items():assert sha256(bodies['source/'+folder+'/tree/'+name]).hexdigest()==digest
        if folder!='llm-load-faba88cb':
            for name in seal['files']:
                if name=='infra/llm-observability/dashboards/gpu-host.json':continue
                assert current_text(name)==bodies['source/'+folder+'/tree/'+name],name
    assert (ROOT/'scripts/view-llm-monitoring.py').read_bytes()==bodies['helpers/view-llm-monitoring-29a.py']
    assert (ROOT/'scripts/probe-llm-dashboards.py').read_bytes()==bodies['helpers/probe-llm-dashboards-29a.py']
    assert current_text('infra/llm-observability/dashboards/gpu-host.json')==bodies['source/gpu-host-readable.json']
    deployment='native/gpu-host-exporter-deploy-29a/'
    assert native(deployment+'verification.json')['passed']
    assert native(deployment+'model-before.json')['spec']==native(deployment+'model-after.json')['spec']
    pod=native(deployment+'pods.json')['items'][0];spec=pod['spec'];assert not spec.get('hostNetwork') and not spec.get('hostPID') and spec['automountServiceAccountToken'] is False
    exporter=spec['containers'][0];assert exporter['securityContext']['readOnlyRootFilesystem'] and exporter['securityContext']['allowPrivilegeEscalation'] is False
    assert '--no-collector.netdev.netlink' in exporter['args'] and not any(v.get('hostPort') for v in exporter['ports'])
    network=native(deployment+'network-policy.json')['spec'];assert 'Egress' in network['policyTypes'] and not network.get('egress')
    discovery='native/llm-metrics-configure-29a/';old=native(discovery+'prometheus-before.json');new=native(discovery+'prometheus-planned.json')
    restored=dict(new);restored['scrape_configs']=new['scrape_configs'][:-4];assert restored==old
    wanted={'iot-agent-mcp':4,'iot-llm-observer':1,'iot-llm-model':1,'iot-llm-gpu-host':1}
    targets=native(discovery+'targets-4.json')['data']['activeTargets'];selected=[v for v in targets if v['labels'].get('job') in wanted]
    assert Counter(v['labels']['job'] for v in selected)==wanted and all(v['health']=='up' for v in selected)
    assert sum(v['health']=='down' for v in targets if v['labels'].get('job')=='iot-ab')==2
    identity=native(discovery+'identity.json');assert identity['subject']=='system:serviceaccount:iot-observability:iot-llm-metrics-reader' and identity['token_mode']=='0o640'
    for namespace in ['iot-llm','iot-agents']:
        assert native(discovery+'permission-'+namespace+'-pods-status.json')['status']==200
        assert native(discovery+'permission-'+namespace+'-secrets-status.json')['status']==403
    for item in native(discovery+'discovery-resources.json')['items']:
        if item['kind']=='Role':assert item['rules']==[{'apiGroups':[''],'resources':['pods'],'verbs':['get','list','watch']}]
    collector='native/llm-dashboards-configure-29a/'
    assert bodies[collector+'alloy-planned.alloy'].startswith(bodies[collector+'alloy-before.alloy']+b'\n')
    assert native(collector+'alloy-validate-exit.json')['exit_code']==0
    assert bodies[collector+'alloy-pid-before.stdout']==bodies[collector+'alloy-pid-after.stdout']
    assert bodies[collector+'grafana-pid-before.stdout']==bodies[collector+'grafana-pid-after.stdout']
    probe='native/llm-dashboard-probe-29b/';checked=native(probe+'verification.json');assert checked['passed'] and checked['request_rows']==128
    assert (base/'dashboard-verification.json').read_bytes()==bodies[probe+'verification.json']
    requests={v['event_id']:v['facts'] for v in native(probe+'ledger.json') if v['kind']=='request'};assert len(requests)==128
    expected=[]
    for name,raw_cri in bodies.items():
        if name.startswith(probe+'observer-') and name.endswith('.cri'):
            for line in raw_cri.decode().splitlines():
                ts,stream,flag,message=line.split(' ',3)
                if stream!='stdout' or flag!='F':continue
                try:event=json.loads(message)
                except ValueError:continue
                if event.get('event')!='model_request':continue
                whole,_,fraction=ts.removesuffix('Z').partition('.');ns=int(datetime.fromisoformat(whole).replace(tzinfo=timezone.utc).timestamp())*10**9+int(fraction.ljust(9,'0'))
                assert {k:event[k] for k in requests[event['request_id']]}==requests[event['request_id']]
                expected.append((str(ns),message))
    assert len(expected)==128
    for name in ['loki-native.json','gateway-loki.json']:
        actual=native(probe+name);assert actual['status']=='success'
        assert Counter(tuple(v) for stream in actual['data']['result'] for v in stream['values'])==Counter(expected)
        assert all(not {'request_id','span_id','trace_id'}&set(stream['stream']) for stream in actual['data']['result'])
    tokens={kind:sum(v['usage'][field] for v in requests.values() if v['usage']) for kind,field in [('input','prompt_tokens'),('output','completion_tokens'),('total','total_tokens')]}
    assert tokens==checked['provider_reported_tokens']=={'input':17648,'output':3045,'total':20693}
    for label,value in [('request-count',128),('agent-count',3),('targets-up',7),*[('tokens-'+n,v) for n,v in tokens.items()]]:
        assert float(native(probe+label+'.json')['data']['result'][0]['value'][1])==value
    panels=native(probe+'panels.json');assert len(panels)==24
    for p in panels:
        label=p['dashboard']+'-'+str(p['panel']);direct=native(probe+label+'-prometheus.json');proxy=native(probe+label+'-grafana.json')
        assert direct['status']==proxy['status']=='success' and direct['data']==proxy['data']
    for uid,name in [('iot-llm-runtime','runtime'),('iot-llm-gpu-host','gpu-host'),('iot-llm-requests','requests')]:
        assert native(probe+'grafana-'+uid+'.json')['dashboard']['panels']==json.loads((ROOT/('infra/llm-observability/dashboards/'+name+'.json')).read_bytes())['panels']
    routing=native(probe+'gateway-routing.json');assert len(routing)==13
    assert sum(v['status']==401 for v in routing)==6 and all(v['ca_verified'] and v['hostname_verified'] and v['tls_version'] in {'TLSv1.2','TLSv1.3'} for v in routing)
    for host in ['metrics.iot.test','logs.iot.test','traces.iot.test']:assert {v['auth']:v['status'] for v in routing if v['host']==host and v['auth']!='correct'}=={'none':401,'incorrect':401}
    load='native/llm-observer-load-29b/';accepted=native(load+'verification.json');assert accepted['passed'] and accepted['request_delta']==108
    assert (base/'load-verification.json').read_bytes()==bodies[load+'verification.json']
    previous={v['event_id'] for v in native(load+'ledger-before.json') if v['kind']=='request'}
    new_requests={v['event_id']:v['facts'] for v in native(load+'ledger-after.json') if v['kind']=='request' and v['event_id'] not in previous};assert len(new_requests)==108
    observed=[];periodic={}
    for users,count,p95 in [(1,30,970),(4,78,2300)]:
        summary=native(load+'users-'+str(users)+'-summary.json');assert summary['passed'] and summary['requests']==count and summary['failures']==0 and summary['p95_ms']==p95
        assert summary['sla']=={'minimum_requests':20,'maximum_failures':0,'p95_ms':5000}
        rows=list(csv.DictReader(StringIO(bodies[load+'users-'+str(users)+'_stats.csv'].decode())))
        total=next(v for v in rows if v['Name']=='Aggregated');periodic[str(users)]=int(total['Request Count'])
        assert 20<=int(total['Request Count'])<=count and int(total['Failure Count'])==0
        assert (base/('load/users-'+str(users)+'.html')).read_bytes()==bodies[load+'users-'+str(users)+'.html']
        assert native(load+'users-'+str(users)+'-exit.json')['exit_code']==0
        for row in summary['records']:
            fact=new_requests[row['request_id']];assert not row['failed'] and row['status']==fact['status']==200 and row['usage']==fact['usage']
            assert fact['result']=='ok' and fact['streaming'] and 0<=fact['ttft_seconds']<=fact['duration_seconds']<=row['response_ms']/1000+.05
            assert fact['usage']['completion_tokens']==16 and fact['usage']['total_tokens']==fact['usage']['prompt_tokens']+16
            observed.append(row['request_id'])
    assert len(observed)==len(set(observed))==108 and set(observed)==set(new_requests)
    a=native(load+'pods-before.json')['items'][0];b=native(load+'pods-after.json')['items'][0]
    assert a['metadata']['uid']==b['metadata']['uid']==accepted['observer_pod_uid'] and a['spec']==b['spec']
    assert native('native/llm-observer-load-29a/users-1-summary.json')['requests']==0
    for unit,invocation in [('iot-llm-dashboards-configure-29a','4c469fa207a442d08228fb56c2588120'),('iot-llm-observer-load-29b','7b413b4eb06c4615a79596afab386eb5')]:
        journal=[json.loads(v) for v in bodies['units/'+unit+'/journal.stdout'].splitlines()]
        own=[v for v in journal if v.get('INVOCATION_ID')==invocation]
        assert any('Started ' in v.get('MESSAGE','') for v in own) and any('Deactivated successfully.' in v.get('MESSAGE','') for v in own)
    index=read(base,'ui/index.json');assert len(index['files'])==10 and 'original JPEG bytes' in index['capture_method']
    for item in index['files']:
        photo=(base/'ui'/item['file']).read_bytes();assert photo.startswith(b'\xff\xd8\xff') and len(photo)==item['bytes'] and sha256(photo).hexdigest()==item['sha256']
    assert 'Incomplete' in (base/'ui/trace.ax.txt').read_text(encoding='utf-8') and 'missing-service-name' in (base/'ui/trace.ax.txt').read_text(encoding='utf-8')
    print(json.dumps({'passed':True,'native_payloads':403,'independent_gpu_payloads':20,'new_targets_up':7,'exact_cri_loki_request_events':128,
        'grafana_prometheus_panel_matches':24,'real_gpu_load_requests':108,'load_p95_ms':{'1':970,'4':2300},'last_periodic_csv_counts':periodic,
        'original_ui_captures':10,'scope':'Offline recomputation of sealed original native receipts and unedited captures. No live cloud rerun, whole-coursework completion or complete SDK trace claim.'},indent=2))


if __name__=='__main__':verify()
