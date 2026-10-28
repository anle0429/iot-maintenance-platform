"""Independently audit native Airflow-to-KFP admission, PSI inputs, complete training lineage and replay guards."""
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
import gzip, io, json, math, struct, subprocess, tarfile

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'docs/evidence/drift-retraining-linux'
FIELDS=('temperature_mean_10m','vibration_max_10m','current_mean_10m','current_std_10m',
        'rpm_mean_10m','reading_count_10m','machine_age_days','prior_failures_24h')


def digest(raw):
    """Hash stored bytes without changing line endings or accepting an artifact by filename alone."""
    return sha256(raw).hexdigest()


def read(name):
    """Decode a retained public receipt; this auditor never contacts a service or submits training."""
    return json.loads(payload(name))


def payload(name):
    """Losslessly decode large repeated SDK readbacks while preserving every original receipt hash."""
    path=BASE/name
    return path.read_bytes() if path.is_file() else gzip.decompress((BASE/(name+'.gz')).read_bytes())


def clock(value):
    """Compare explicit audit offsets rather than treating simulated event time as wall time."""
    result=datetime.fromisoformat(value.replace('Z','+00:00'));assert result.tzinfo is not None;return result


def canonical(value):
    """Reconstruct semantic JSON identities independently from production serializers."""
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def git_source(source,packet=False):
    """Read historical Git objects, optionally reconstructing the exact deterministic control packet."""
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        for name,expected in sorted(source['files'].items()):
            path=PurePosixPath(name);assert not path.is_absolute() and '..' not in path.parts
            raw=subprocess.check_output(['git','show',source['commit']+':'+name],cwd=ROOT)
            assert digest(raw)==expected,name
            item=tarfile.TarInfo('tree/'+name);item.mode=0o644;item.size=len(raw);archive.addfile(item,io.BytesIO(raw))
    if packet:assert digest(buffer.getvalue())==source['archive_sha256']


def psi(label,reference,policy):
    """Recalculate all eight marginal PSI values and thresholds from raw retained samples."""
    payload,report,retrieval,done=(read('runs/'+label+'/'+name+'.json') for name in ['window','report','retrieval','completion'])
    assert digest(canonical(reference))==report['reference_id']==payload['reference_id']==policy['reference_id']==done['reference_id']
    assert done['passed'] and done['push_status']==200 and done['report_sha256']==digest((BASE/'runs'/label/'report.json').read_bytes())
    assert done['metrics_sha256']==digest((BASE/'runs'/label/'metrics.prom').read_bytes())
    assert retrieval['passed'] and retrieval['native_feast_retrieval'] and retrieval['all_eight_values_checked']
    assert retrieval['reference_sha256']==policy['reference_sha256'] and retrieval['source_sha256']==policy['source_sha256']
    assert not any(word in retrieval['sql'].lower() for word in ['failure_in_next','label','truth'])
    rows=payload['observations'];assert len(rows)==report['unique_rows']==retrieval['rows'] and 32<=len(rows)<=2048
    assert len({r['id'] for r in rows})==len(rows) and report['reference_rows']==len(reference['samples'])
    assert report['window_id']==payload['window_id'] and report['start']==payload['start'] and report['end']==payload['end']
    assert set(report['features'])==set(FIELDS)
    for row in rows:
        assert set(row)=={'id','event_timestamp','available_at','features'} and set(row['features'])==set(FIELDS)
        assert clock(payload['start'])<=clock(row['event_timestamp'])<clock(payload['end'])
        assert clock(row['event_timestamp'])<=clock(row['available_at'])<=clock(payload['end'])
    flags=0
    for field in FIELDS:
        train=sorted(r[field] for r in reference['samples']);values=[r['features'][field] for r in rows]
        assert all(math.isfinite(x) for x in train+values)
        cuts=sorted({train[min(len(train)-1,len(train)*i//10)] for i in range(1,10)})
        def bins(sample):
            """Count inclusive lower boundaries with unbounded tails, including repeated quantile cuts."""
            counts=[0]*(len(cuts)+1)
            for value in sample:counts[sum(value>=edge for edge in cuts)]+=1
            return counts
        a,b=bins(train),bins(values);n=len(a)
        score=sum(((y+.5)/(len(values)+n*.5)-(x+.5)/(len(train)+n*.5))*
                  math.log(((y+.5)/(len(values)+n*.5))/((x+.5)/(len(train)+n*.5))) for x,y in zip(a,b))
        actual=report['features'][field]
        assert actual['cuts']==cuts and actual['reference_counts']==a and actual['current_counts']==b
        assert math.isclose(actual['psi'],score,rel_tol=1e-12,abs_tol=1e-12) and actual['drifted'] is (score>=.2)
        flags+=score>=.2
    assert report['drift_detected'] is (flags>=2) and report['drifted_feature_fraction']==flags/8
    assert report['policy']=={'bins':10,'psi_threshold':.2,'drifted_feature_fraction':.25,'pseudocount':.5}
    return report,done


def successful_airflow(label,probe):
    """Require actual scheduler task instances, keeping manual probes distinct from scheduled monitoring."""
    run,tasks=read('airflow/'+label+'-run.json'),read('airflow/'+label+'-tasks.json')
    assert run['dag_id']=='iot_offline_drift' and run['state']=='success'
    items=tasks['task_instances'];assert len(items)==2 and {x['task_id'] for x in items}=={'retrieve_compare_push','admit_kfp_retrain'}
    assert all(x['state']=='success' and x['try_number']==1 for x in items)
    if probe:assert run['conf']['retrain_probe'] is True and run['dag_run_id'].startswith('manual-')
    else:assert run['dag_run_id'].startswith('scheduled__') and not run['conf']
    return run


def jpeg_dimensions(raw):
    """Check original browser JPEG headers without altering or rendering pixels."""
    assert raw[:2]==b'\xff\xd8';offset=2
    while offset<len(raw):
        assert raw[offset]==255;marker=raw[offset+1];offset+=2;size=struct.unpack('>H',raw[offset:offset+2])[0]
        if marker in [0xc0,0xc1,0xc2]:
            height,width=struct.unpack('>HH',raw[offset+3:offset+7]);return width,height
        offset+=size
    raise AssertionError('Missing JPEG dimensions')


def main():
    """Bind saved control/training sources to original outcomes, arithmetic, backend identities and guard receipts."""
    manifest=read('manifest.json')
    assert {p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file()}==set(manifest['files'])|{'manifest.json'}
    for name,pin in manifest['files'].items():
        raw=(BASE/name).read_bytes();assert len(raw)==pin['bytes'] and digest(raw)==pin['sha256'],name
    native_capture=read('native-capture.json')
    for name,pin in native_capture['files'].items():
        raw=payload(name);assert len(raw)==pin['bytes'] and digest(raw)==pin['sha256'],name
    current,initial=read('source/current.json'),read('source/initial.json')
    assert len(manifest['files'])==118 and sum(p['bytes'] for p in manifest['files'].values())==5018983
    assert len(native_capture['files'])==113 and sum(p['bytes'] for p in native_capture['files'].values())==7943861
    assert current['commit']=='b41d6549cba09daad7bf377c6ed6c8fd9c549c51' and len(current['files'])==len(initial['files'])==7
    git_source(current,True);git_source(initial,True);git_source(read('source/monitoring.json'))
    snapshot=read('training/source-snapshot.json');assert len(snapshot['files'])==44 and snapshot['commit']=='ce510c6bcccd58235fbc78d22cd90570934c50a6'
    git_source(snapshot);assert read('source/training-readback.json')['files']==snapshot['files']
    assert 'Ran 13 tests' in (BASE/'checks/upgrade-b41d654/native-unit-tests.txt').read_text() and '\nOK\n' in (BASE/'checks/upgrade-b41d654/native-unit-tests.txt').read_text()
    policy=read('runtime/policy.json');assert policy['cooldown_seconds']==3600 and policy['max_report_age_seconds']==600
    assert policy['enabled'] is True and policy['allow_manual_probe'] is True
    controller=read('runs/trained/adapter-container.json');config=read('runtime/config.json')
    assert controller['source_commit']==current['commit'] and controller['run_id']=='manual-retrain-probe-28-v2'
    assert controller['image_id']==config['controller_image_id']==read('training/build.json')['controller_image_id']
    assert controller['exit_code']==controller['state']['ExitCode']==0 and controller['state']['OOMKilled'] is False
    assert controller['host_config']=={'Memory':2147483648,'MemorySwap':2147483648,'NanoCpus':1000000000,'ReadonlyRootfs':True,'NetworkMode':'host'}
    assert all(policy['training_files'][n]==h for n,h in snapshot['files'].items()) and len(policy['training_files'])==48
    reference=read('runtime/reference.json');assert digest((BASE/'runtime/reference.json').read_bytes())==policy['reference_sha256']
    reports={label:psi(label,reference,policy) for label in ['natural','trained','cooldown']}
    natural,trigger,cooldown=successful_airflow('natural',False),successful_airflow('trained',True),successful_airflow('cooldown',True)
    decisions={label:read('runs/'+label+'/adapter-decision.json') for label in ['natural','trained','cooldown']}
    assert decisions['natural']['action']=='skipped' and decisions['natural']['reason']=='no_detected_drift'
    assert decisions['cooldown']['action']=='skipped' and decisions['cooldown']['reason']=='cooldown'
    intent,result=read('admission/intent.json'),read('admission/result.json')
    assert result==decisions['trained'] and result['action']=='submitted_and_verified' and result['passed'] and result['production'] is False
    assert intent['manual_probe'] is True and intent['drift_detected'] is False and result['manual_probe'] is True
    assert result['policy_sha256']==intent['policy_sha256']==digest((BASE/'runtime/policy.json').read_bytes())
    assert result['report_sha256']==intent['report_sha256']==digest((BASE/'runs/trained/report.json').read_bytes())
    name='drift-'+digest(canonical([intent['reference_id'],intent['window_id'],intent['policy_sha256']]))[:24]
    assert name==intent['run_name']==result['run_name']=='drift-f48fc15fd07b639121198636'
    run=result['run_id'];assert run=='7780560d-f944-4303-9e4e-792f35d742ad'
    backend=read('admission/backend-run.json');assert backend['run_id']==run and backend['display_name']==name and backend['state']=='SUCCEEDED'
    assert result['backend_run_sha256']==digest((BASE/'admission/backend-run.json').read_bytes())
    pre=read('training/native/preflight.json');assert clock(backend['created_at'])>clock(pre['checked_utc'])>clock(trigger['logical_date'])
    assert 0<=(clock(intent['checked_utc'])-clock(reports['trained'][1]['completed_wall_utc'])).total_seconds()<=600
    native=read('training/native/verification.json');assert native['passed'] and native['run_id']==run and native['production'] is False
    assert result['verification_sha256']==digest((BASE/'training/native/verification.json').read_bytes())
    assert pre['source_snapshot_commit']==snapshot['commit'] and pre['code_sha256']==snapshot['files']
    assert clock(pre['deadline_utc'])==clock(read('runtime/session.json')['auto_stop_utc'])
    assert (clock(pre['deadline_utc'])-clock(pre['checked_utc'])).total_seconds()>=5400 and pre['ram_available_bytes']>=24*1024**3
    expected={'versioned-load','cluster-split','distributed-train','validate-saved-model','registered-evaluate'}
    tasks=backend['pipeline_spec']['pipeline_spec']['root']['dag']['tasks'];assert set(tasks)==expected
    assert all(not t.get('cachingOptions',{}).get('enableCache',False) for t in tasks.values())
    assert all(any(t['display_name']==n+'(0)' and t['state']=='SUCCEEDED' for t in backend['run_details']['task_details']) for n in expected)
    assert clock(pre['checked_utc'])>clock(read('checks/upgrade-b41d654/verification.json')['deployed_utc'])
    images=read('training/build.json')['images'];expected_images={v['tag']:v['cri_image_id'] for v in images.values()}
    mains=[]
    for pod in read('training/native/pipeline-pods.json')['items']:
        statuses={s['name']:s for s in pod.get('status',{}).get('containerStatuses',[])}
        for container in pod['spec']['containers']:
            if container['image'] not in expected_images:continue
            status=statuses[container['name']];assert pod['metadata']['labels']['pipeline/runid']==run
            assert status['imageID']==expected_images[container['image']] and status['state']['terminated']['exitCode']==0
            assert pod['status']['phase']=='Succeeded' and any(v['name']=='iot-ml-code' and v['mountPath']=='/workspace' for v in container['volumeMounts'])
            mains.append(pod['metadata']['uid'])
    assert len(mains)==len(set(mains))==5
    executors=read('training/native/two-executors-running.json')['items'];assert len(executors)==2
    assert {p['metadata']['labels']['spark-exec-id'] for p in executors}=={'1','2'}
    assert len({p['spec']['nodeName'] for p in executors})==1
    assert all(p['status']['phase']=='Running' and p['metadata']['labels']['iot-coursework-run']==name for p in executors)
    events=[json.loads(line) for line in (BASE/'training/spark-selected.jsonl').read_bytes().splitlines()]
    counts=Counter(e['Event'] for e in events);summary=read('training/event-summary.json')
    for kind,count in counts.items():assert count==summary['all_event_counts'][kind]
    assert counts['SparkListenerApplicationEnd']==1 and counts['SparkListenerTaskEnd']>0 and not summary['failed_attempts']
    task_counts=defaultdict(Counter);groups=set();stages={};ids=set()
    for event in events:
        if event['Event']=='SparkListenerExecutorAdded':ids.add(event['Executor ID'])
        elif event['Event']=='SparkListenerJobStart' and event.get('Properties',{}).get('spark.jobGroup.id')=='iot-distributed-fit':groups.update(event['Stage IDs'])
        elif event['Event'] in ['SparkListenerStageSubmitted','SparkListenerStageCompleted']:stages[event['Stage Info']['Stage ID']]=event['Stage Info']
        elif event['Event']=='SparkListenerTaskEnd':
            assert event['Task End Reason']['Reason']=='Success';task_counts[str(event['Stage ID'])][event['Task Info']['Executor ID']]+=1
    assert dict(task_counts)==summary['tasks_by_stage_executor'] and ids=={'1','2'}
    distributed=read('training/native/distributed-verification.json');assert distributed['passed'] and not distributed['failed_task_attempts']
    assert {n:v['sha256'] for n,v in summary['raw_event_logs'].items()}==distributed['event_logs']
    for item in distributed['logistic_regression_stages']:
        sid=item['stage_id'];assert sid in groups and 'LogisticRegression' in json.dumps(stages[sid])
        assert item['successful_executor_ids']==sorted(set(task_counts[str(sid)])-{'driver'})
    assert any(len(item['successful_executor_ids'])>=2 for item in distributed['logistic_regression_stages'])
    assert native['distributed_proof_sha256']==digest((BASE/'training/native/distributed-verification.json').read_bytes())
    assert native['source_preflight_sha256']==digest((BASE/'training/native/preflight.json').read_bytes())
    reg=read('training/lineage/registration.json');auto=read('training/lineage/automatic-data.json')
    canonical_data=read('training/lineage/canonical.json');local_data=read('training/lineage/local-version.json');remote=read('training/lineage/remote-version.json')
    entries={x['path']:{'sha256':x['sha256'],'size':x['bytes']} for x in canonical_data['files']}
    assert len(entries)==180 and sum(v['size'] for v in entries.values())==740003698 and canonical_data['rows']==3132184
    document={'format':'iot-content-dataset-v1','metadata':canonical_data['metadata'],'files':entries}
    version=digest(canonical(document)+b'\n')
    assert version==auto['data_version']==native['data_version']==reg['incremental_data_reference']['data_version']==local_data['version']==remote['version']
    assert auto['new_remote_blob_payload_bytes']==local_data['new_blob_bytes']==remote['new_blob_payload_bytes']==0
    assert auto['remote_restore']['all_file_bytes_verified'] and auto['remote_restore']['files']==180 and auto['remote_restore']['bytes']==740003698
    model=read('registry/model-version.json')['model_version'];model_run=read('registry/model-run.json')['run'];data_run=read('registry/data-run.json')['run']
    tags=lambda values:{v['key']:v['value'] for v in values}
    mt,vt,dt=tags(model_run['data']['tags']),tags(model['tags']),tags(data_run['data']['tags'])
    assert model['version']==reg['version']==native['registered_model_version']==result['registered_model_version'] and model['version']!='6'
    assert model['run_id']==reg['model_run_id']==model_run['info']['run_id']==result['model_run_id']
    assert model['status']=='READY' and model_run['info']['status']=='FINISHED' and model_run['info']['run_name']==name
    assert vt['production']=='false' and reg['production'] is False and 'candidate' in model['aliases']
    assert mt['source_snapshot_commit']==reg['source_snapshot_commit']==auto['source_snapshot_commit']==snapshot['commit']
    assert vt['data_version']==mt['training_data_version']==dt['data_version']==version
    assert model_run['info']['start_time']>=clock(pre['checked_utc']).timestamp()*1000 and model['creation_timestamp']>=model_run['info']['start_time']
    assert mt['dataset_run_id']==data_run['info']['run_id']==auto['data_run_id']==native['data_run_id']
    assert data_run['info']['status']=='FINISHED' and dt['complete']=='true'
    assert reg['model_data_artifact_payload_bytes']==0 and reg['registry_model_native_spark_probe_rows']==200 and reg['max_abs_probability_difference']<=1e-12
    evaluation=read('training/lineage/evaluation.json');metrics={m['key']:m['value'] for m in model_run['data']['metrics']}
    assert mt['evaluation_sha256']==digest((BASE/'training/lineage/evaluation.json').read_bytes()) and evaluation['fixed_threshold']==.7
    assert len(model_run['data']['params'])==11 and len(metrics)==27
    for partition in ['validation','test']:
        values=evaluation[partition];assert sum(values[k] for k in ['tp','fp','fn','tn'])==evaluation['split_counts'][partition]['rows']
        p=values['tp']/(values['tp']+values['fp']);r=values['tp']/(values['tp']+values['fn'])
        assert math.isclose(values['f1'],2*p*r/(p+r),rel_tol=1e-14)
        for key,value in values.items():assert metrics[partition+'.'+key]==value
    old=read('reconciled/intent.json');reconciliation=read('reconciled/reconciled-no-submission.json');zero=read('reconciled/reconciliation-backend.json')
    assert reconciliation['outcome']=='confirmed_not_submitted' and reconciliation['runner_failed_before_api'] is True
    assert reconciliation['intent_sha256']==digest((BASE/'reconciled/intent.json').read_bytes())
    assert reconciliation['controller_sha256']==digest((BASE/'reconciled/controller.txt').read_bytes())
    assert reconciliation['backend_readback_sha256']==digest(payload('reconciled/reconciliation-backend.json'))
    assert zero['pagination_complete'] and not zero['matching_run_names'] and old['run_name'] not in zero['all_run_names']
    assert 'train-jenkins-2' in zero['all_run_names'] and len(zero['all_run_names'])==11
    assert 'run-versioned-training.py", line 50' in (BASE/'reconciled/controller.txt').read_text()
    assert 'Permission denied' in (BASE/'checks/deployment-eba2187/failed-scheduled-0535/airflow-task.txt').read_text()
    for label in ['permission-failure','failed-probe']:assert read('airflow/'+label+'-run.json')['state']=='failed'
    inflight=read('checks/inflight-probe/verification.json');assert inflight['passed'] and inflight['intents_before']==inflight['intents_after']
    assert 'sqlite3.OperationalError: database is locked' in (BASE/'checks/inflight-probe/controller.txt').read_text()
    replay=read('checks/completed-replay/decision.json');audit=read('checks/completed-replay/verification.json')
    assert replay['action']==audit['action']=='reused_completed_submission' and replay['run_id']==audit['run_id']==run
    assert audit['passed'] and audit['network_mode']=='none' and audit['intents_before']==audit['intents_after']
    for when in ['before','after']:
        queried=read('checks/completed-replay/backend-'+when+'.json')
        matched=[r for page in queried['pages'] for r in (page.get('runs') or []) if r['display_name']==name]
        assert len(matched)==1 and matched[0]['run_id']==queried['run']['run_id']==run and queried['run']['state']=='SUCCEEDED'
    assert reports['cooldown'][0]['window_id']!=reports['trained'][0]['window_id']
    assert 0<=(clock(decisions['cooldown']['checked_utc'])-clock(result['completed_utc'])).total_seconds()<3600
    bounded=read('checks/cooldown-proof/verification.json');assert bounded['passed'] and bounded['reason']=='cooldown'
    assert bounded['backend_runs_before']==bounded['backend_runs_after'] and bounded['intents_before']==bounded['intents_after']==audit['intents_after']
    queried=read('checks/cooldown-proof/backend-readback.json')
    actual={r['run_id']:r['display_name'] for page in queried['pages'] for r in (page.get('runs') or [])}
    assert actual==bounded['backend_runs_after'] and {r:n for r,n in actual.items() if n.startswith('drift-')}=={run:name}
    live=read('serving/model6-isvc.json');previous=json.loads((BASE.parent/'ci-serving-linux/ci/inferenceservice.json').read_bytes())
    assert live['metadata']['uid']==previous['metadata']['uid'] and live['metadata']['name']=='iot-pump-serve-jenkins-8'
    deployment=read('serving/feature-deployment.json');env={e['name']:e.get('value') for e in deployment['spec']['template']['spec']['containers'][0]['env']}
    assert live['metadata']['name']+'-predictor' in env['IOT_KSERVE_URL']
    assert len(read('screen-captures.json'))==2
    for name,item in read('screen-captures.json').items():
        raw=(BASE/'screenshots'/name).read_bytes();assert digest(raw)==item['sha256'] and jpeg_dimensions(raw)==(item['width'],item['height'])
    print('PASS native evidence hashes/Git controls+44ML/24raw PSI/Airflow+5freshKFPmains/twoexecutors+rawTaskEnds/data180files+candidate registry/inflight+completed replay+cooldown/original failures+zero-match reconciliation/screenshots. Manual probe with actual drift=false; no quality or automatic promotion claim.')


if __name__=='__main__':main()
