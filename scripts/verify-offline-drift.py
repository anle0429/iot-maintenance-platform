"""Verify preserved native Airflow/Feast/Pushgateway/Grafana evidence with standard-library arithmetic and Git."""
from datetime import datetime,timezone
from hashlib import sha256
import json,math,re,struct,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.offline_drift_oracle import compare


def verify():
    """Check original bytes, two scheduled runs, independent PSI, raw gauges, every panel and retained failures."""
    evidence=ROOT/'docs/evidence/offline-drift-linux'
    def read(name):return json.loads((evidence/name).read_bytes())
    def digest(path):return sha256(path.read_bytes()).hexdigest()
    def utc(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
    manifest=read('manifest.json');assert manifest['format']=='iot-offline-drift-evidence-v1'
    files=manifest['files']
    assert set(files)=={p.relative_to(evidence).as_posix() for p in evidence.rglob('*') if p.is_file() and p.name!='manifest.json'}
    for name,pin in files.items():
        path=evidence/name;assert path.stat().st_size==pin['bytes'] and digest(path)==pin['sha256'],name
    assert sum(p['bytes'] for p in files.values())==manifest['payload_bytes']
    native=read('native-manifest.json');assert len(native['files'])==60
    for name,pin in native['files'].items():assert files[name]==pin,name
    assert sum(p['bytes'] for p in native['files'].values())==native['payload_bytes']
    bindings=0
    for name,commit_key in [('runtime/execution-source.json','execution_commit'),('runtime/audit-source.json','dashboard_oracle_commit')]:
        source=read(name);assert source['commit']==manifest[commit_key]
        for path,pin in source['files'].items():
            raw=subprocess.check_output(['git','show',source['commit']+':'+path],cwd=ROOT)
            assert sha256(raw).hexdigest()==pin,(path,source['commit'])
            bindings+=1
    assert bindings==26
    reference=read('reference.json');clock=read('runtime/clock.json');deployment=read('runtime/config.json')
    assert digest(evidence/'reference.json')==clock['reference_sha256']
    assert reference['lineage']['partition']=='train' and reference['lineage']['model_version']=='3'
    assert reference['lineage']==read('runs/2/report.json')['lineage']
    assert clock['reference_train_end']=='2026-11-12T00:00:00Z'
    image=read('native/runtime.json')['deployment']['image_id']
    assert image==deployment['image_id']==json.loads((ROOT/'docs/evidence/feast/image/verification.json').read_bytes())['image_id']
    previous=None;source_hashes=set();registry_hashes=set();oracles=[]
    for index in (1,2):
        prefix='runs/'+str(index)+'/'
        airflow=read(prefix+'airflow.json');run=airflow['run'];tasks=airflow['tasks']['task_instances']
        assert run['state']=='success' and run['run_type']=='scheduled' and len(tasks)==1
        assert tasks[0]['state']=='success' and tasks[0]['task_id']=='retrieve_compare_push' and tasks[0]['try_number']==1
        assert not run['conf']
        actual_time=utc(run['logical_date'])
        if previous:assert (actual_time-previous).total_seconds()==300
        previous=actual_time
        container=read(prefix+'container.json');retrieval=read(prefix+'retrieval.json');completion=read(prefix+'completion.json')
        assert container['run_id']==run['dag_run_id'] and container['exit_code']==0 and container['image_id']==image
        limits=container['host_config'];assert limits['Memory']==limits['MemorySwap']==3*1024**3
        assert limits['NanoCpus']==1_000_000_000 and limits['ReadonlyRootfs'] and limits['NetworkMode']=='host'
        assert not container['state']['OOMKilled']
        assert retrieval['passed'] and retrieval['native_feast_retrieval'] and retrieval['all_eight_values_checked'] and retrieval['rows']==1024
        assert retrieval['config_sha256']==digest(evidence/'runtime/clock.json')
        assert retrieval['reference_sha256']==clock['reference_sha256'] and retrieval['source_sha256']==clock['source_sha256']
        source_hashes.add(retrieval['source_sha256']);registry_hashes.add(retrieval['registry_sha256'])
        assert 'label' not in retrieval['sql'].lower() and 'truth' not in retrieval['sql'].lower()
        assert completion['passed'] and completion['push_status']==200
        assert completion['report_sha256']==digest(evidence/(prefix+'report.json')) and completion['metrics_sha256']==digest(evidence/(prefix+'metrics.prom'))
        payload,report=read(prefix+'window.json'),read(prefix+'report.json')
        expected_end=utc(clock['event_origin'])+(utc(retrieval['scheduler_interval_end'])-utc(clock['wall_origin']))
        assert utc(payload['end'])==expected_end and (expected_end-utc(payload['start'])).total_seconds()==clock['width_seconds']
        assert utc(payload['start'])>=utc(clock['reference_train_end']) and expected_end<=utc(clock['data_end'])
        checked=compare(reference,payload,report);assert checked==read(prefix+'independent-oracle.json');oracles.append(checked)
    assert len(source_hashes)==len(registry_hashes)==1
    summary=read('verification.json');assert summary['passed'] and summary['input_bytes_unchanged'] and len(summary['scheduled_runs'])==2
    # Reconstruct every metric from the checked report; do not import the publisher/formatter implementation.
    report=read('runs/2/report.json');retrieval=read('runs/2/retrieval.json');expected={}
    for field,value in report['features'].items():
        expected[('iot_offline_drift_psi',(('feature',field),))]=value['psi']
        expected[('iot_offline_drift_feature_alert',(('feature',field),))]=int(value['drifted'])
    for suffix,value in [('detected',int(report['drift_detected'])),('feature_fraction',report['drifted_feature_fraction']),
            ('sample_rows',1024),('window_start_seconds',utc(report['start']).timestamp()),('window_end_seconds',utc(report['end']).timestamp()),
            ('last_success_seconds',utc(retrieval['completed_wall_utc']).timestamp())]:expected[('iot_offline_drift_'+suffix,())]=value
    expected[('iot_offline_drift_reference_info',(('reference_id',report['reference_id']),))]=1
    raw={}
    for line in (evidence/'runs/2/metrics.prom').read_text().splitlines():
        if line.startswith('#') or not line:continue
        match=re.fullmatch(r'(\w+)(\{.*\})? ([0-9.eE+\-]+)',line);assert match
        labels=tuple(sorted(re.findall(r'(\w+)="([^"\\]*)"',match[2] or '')))
        key=(match[1],labels);assert key not in raw;raw[key]=float(match[3])
    assert raw.keys()==expected.keys() and len(raw)==23
    assert all(math.isclose(raw[k],v,rel_tol=1e-12,abs_tol=1e-9) for k,v in expected.items())
    context=read('query-context.json');t=context['evaluation_utc_seconds'];dashboard=read('dashboard-rendered.json')
    assert dashboard['uid']=='iot-offline-drift' and len(dashboard['panels'])==6
    expressions=['iot_offline_drift_psi{job="iot_offline_drift"}','iot_offline_drift_feature_alert{job="iot_offline_drift"}',
        'iot_offline_drift_sample_rows{job="iot_offline_drift"}','time()-iot_offline_drift_last_success_seconds{job="iot_offline_drift"}',
        'iot_offline_drift_window_start_seconds{job="iot_offline_drift"} * 1000','iot_offline_drift_window_end_seconds{job="iot_offline_drift"} * 1000']
    for index,panel in enumerate(dashboard['panels']):
        assert panel['targets'][0]['expr']==expressions[index]
        if index>=4:assert panel['fieldConfig']['defaults']['unit']=='dateTimeAsIso'
    for engine in ('prometheus','grafana'):
        observed={}
        for row in read('queries/'+engine+'/all.json')['data']['result']:
            labels=dict(row['metric']);name=labels.pop('__name__');assert labels.pop('job')=='iot_offline_drift'
            assert labels.pop('instance','')=='';key=(name,tuple(sorted(labels.items())))
            assert key not in observed;observed[key]=float(row['value'][1])
        assert observed.keys()==expected.keys()
        assert all(math.isclose(observed[k],v,rel_tol=1e-12,abs_tol=1e-9) for k,v in expected.items())
        for index in range(6):
            data=read('queries/'+engine+'/panel-'+str(index+1)+'.json');assert data['status']=='success';vector=data['data']['result']
            assert len(vector)==(8 if index<2 else 1)
            for row in vector:assert math.isclose(float(row['value'][0]),t,rel_tol=0,abs_tol=.001)
            if index<2:
                metric='iot_offline_drift_psi' if index==0 else 'iot_offline_drift_feature_alert'
                for row in vector:assert math.isclose(float(row['value'][1]),expected[(metric,(('feature',row['metric']['feature']),))],rel_tol=1e-12,abs_tol=1e-9)
            else:
                value=[1024,t-utc(retrieval['completed_wall_utc']).timestamp(),utc(report['start']).timestamp()*1000,utc(report['end']).timestamp()*1000][index-2]
                assert math.isclose(float(vector[0]['value'][1]),value,rel_tol=1e-12,abs_tol=1e-5)
    negative=read('negative/verification.json');assert negative['passed'] and negative['exit_code']==1 and not negative['oom_killed']
    assert negative['success_gauges_unchanged'] and digest(evidence/'negative/metrics-before.prom')==digest(evidence/'negative/metrics-after.prom')==negative['metrics_before_sha256']==negative['metrics_after_sha256']
    assert 'Immutable source/reference/protocol binding differs' in (evidence/'negative/console.txt').read_text()
    units=read('units.json');assert units['iot-pushgateway-28']['ActiveState']=='active' and units['iot-pushgateway-28']['MemoryMax']=='268435456'
    assert units['iot-pushgateway-28']['User']=='nobody' and units['iot-pushgateway-28']['NoNewPrivileges']=='yes'
    host_units=json.loads((ROOT/'docs/evidence/host-monitoring-linux/host/units-live.json').read_bytes())
    assert units['iot-prometheus-28']['MainPID']==host_units['prometheus']['MainPID'] and units['iot-grafana-28']['MainPID']==host_units['grafana']['MainPID']
    listeners=(evidence/'listeners.txt').read_text();assert '127.0.0.1:19092' in listeners and '0.0.0.0:19092' not in listeners
    assert 'Ran 7 tests' in (evidence/'native/audit-unit-tests.txt').read_text() and (evidence/'native/audit-unit-tests.txt').read_text().strip().endswith('OK')
    for name in ('dashboard-overview.jpg','dashboard-clocks.jpg','date-before.jpg'):
        raw=(evidence/name).read_bytes();assert raw.startswith(b'\xff\xd8');offset=2;dimensions=None
        while offset<len(raw):
            assert raw[offset]==255
            while raw[offset]==255:offset+=1
            marker=raw[offset];offset+=1
            if marker in (216,217):continue
            size=int.from_bytes(raw[offset:offset+2],'big')
            if marker in (192,193,194):dimensions=struct.unpack('>HH',raw[offset+3:offset+7]);break
            offset+=size
        assert dimensions==(720,1280),name
    return {'passed':True,'payload_files':len(files),'git_source_bindings':bindings,'native_scheduled_runs':2,
            'snapshots_per_run':1024,'gauges_per_engine':23,'panel_values_per_engine':20,'jpeg_captures':3,
            'scope':'Preserved native evidence and independent arithmetic; does not rerun VM services, retrain or claim model accuracy.'}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
