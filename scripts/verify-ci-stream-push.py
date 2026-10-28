"""Audit genuine source lineage, separate native stream-store jobs, process-boundary faults and exact runtime parity offline."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import json,subprocess
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'docs/evidence/ci-stream-push'


def read(name):
    """Read original JSON after byte hashes have been verified."""
    return json.loads((BASE/name).read_bytes())


def utc(value):
    """Compare native timestamp encodings without changing their event or availability clocks."""
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)


def main():
    """Recalculate known window answers, image/runtime identity, actual tasks and replay effects from original evidence."""
    manifest=read('manifest.json');files={p.relative_to(BASE).as_posix() for p in BASE.rglob('*') if p.is_file() and p!=BASE/'manifest.json'}
    assert files==set(manifest['files']) and len(files)==225
    for n,v in manifest['files'].items():
        assert not Path(n).is_absolute() and '..' not in Path(n).parts
        raw=(BASE/n).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'],n
    native=read('native-manifest.json');assert len(native['files'])==220 and all(manifest['files'][n]==v for n,v in native['files'].items())
    commit=manifest['ci_source_commit'];seal=read('sources/'+commit+'.json');assert len(seal['files'])==124
    for path in [*sorted(BASE.rglob('sources/*.json')),BASE/'history/prior/native-prototype/source-manifest.json']:
        source=json.loads(path.read_bytes())
        for n,h in source['files'].items():assert sha256(subprocess.check_output(['git','show',source['commit']+':'+n],cwd=ROOT)).hexdigest()==h
    fixture=json.loads((ROOT/'tests/fixtures/stream-finalized-native.json').read_bytes());parent=(ROOT/fixture['parent']).read_bytes()
    assert sha256(parent).hexdigest()==fixture['parent_sha256'] and json.loads(parent)['windows']==fixture['windows']
    for n,h in fixture['code_sha256'].items():assert sha256((ROOT/n).read_bytes()).hexdigest()==h
    expected=sorted(fixture['windows'],key=lambda r:(r['machine_id'],utc(r['event_timestamp'])))
    assert [(r['machine_id'],r['reading_count'],r['temperature_sum'],r['temperature_mean'],r['vibration_max']) for r in expected]==[('P-001',3,60,20,3),('P-001',1,40,40,4),('P-002',1,100,100,10)]
    assert sum(r['reading_count'] for r in expected)==5
    for mode,number,duration in [('offline',3,77814),('online',2,77355)]:
        key=mode+'-'+str(number);release='stream-'+mode+'-jenkins-'+str(number)
        job=read('jenkins/'+key+'.json');assert job['result']=='SUCCESS' and not job['building'] and job['duration']==duration
        params={p['name']:p['value'] for a in job['actions'] for p in a.get('parameters',[])}
        assert params=={'SOURCE_COMMIT':commit,'OFFLINE_RELEASE':'stream-offline-jenkins-3' if mode=='online' else ''}
        stages=read('jenkins/'+key+'-stages.json');assert stages['status']=='SUCCESS' and len(stages['stages'])==7 and all(s['status']=='SUCCESS' for s in stages['stages'])
        assert read(key+'/source-after.json')['files']==seal['files']
        proof=read(key+'/ci/verification.json');config=read(key+'/release.json');image=config['image']
        assert proof['passed'] is True and proof['commit']==config['commit']==commit and config['destination']==mode and config['release']==release
        assert proof['reports']=={op:read(key+'/jobs/'+op+'.json') for op in ['write','replay']}
        air=read(key+'/ci/airflow.json');assert air['run']['state']=='success' and air['run']['dag_id']=='iot_ci_stream_'+mode and air['run']['dag_run_id']==release
        tasks=air['tasks']['task_instances'];assert len(tasks)==2 and {t['task_id'] for t in tasks}=={'push_finalized_windows','replay_without_new_writes'} and all(t['state']=='success' for t in tasks)
        assert config['dag_sha256']==seal['files']['infra/ci/iot_ci_stream_push.py']
        assert read(key+'/ci/deployment.json')['dag_sha256']==config['dag_sha256']
        parity=read(key+'/runtime-parity.json');assert parity['tested']==parity['deployed'] and parity['tested']['RootFS']['Layers'] and parity['tested']['Config']['User']=='10001:10001'
        assert parity['tested_image_id']==read(key+'/ci/tests-container.json')['image'] and parity['deployed_image_id']==image['id']
        # The distinct original image IDs are deliberately retained; rootfs and full execution Config match.
        assert parity['tested_image_id']!=parity['deployed_image_id']
        assert image['packaged_sha256']==read(key+'/ci/test.json')['packaged_sha256']
        for n,h in image['packaged_sha256'].items():assert seal['files'][n]==h
        assert 'feast==0.67.0' in (BASE/key/'requirements-installed.lock').read_text(encoding='utf-8').lower()
        tests=read(key+'/tests/verification.json');assert tests['passed'] is True
        for operation,value in tests['reports'].items():
            assert value['passed'] is True and all(value['checks'].values())
            assert operation in ['offline-fault','offline-finish','online-fault','online-finish']
        assert tests['reports']['offline-fault']['checks']['one_real_offline_row'] and tests['reports']['offline-fault']['checks']['online_still_missing']
        assert tests['reports']['online-fault']['checks']['real_latest_online_write'] and tests['reports']['online-fault']['checks']['offline_unchanged']
        for op in ['write','replay']:
            receipt=read(key+'/jobs/'+op+'-container.json');value=read(key+'/jobs/'+op+'.json')
            assert receipt['image_id']==image['id'] and receipt['exit_code']==0 and receipt['oom_killed'] is False and receipt['memory_bytes']==receipt['memory_swap_bytes']==4*1024**3 and receipt['nano_cpus']==1_000_000_000
            assert receipt['commit']==commit and receipt['release']==release and receipt['destination']==mode
            assert value['passed'] is True and value['fixture_parent_sha256']==fixture['parent_sha256'] and value['machines']==2
            state=next(m for m in receipt['mounts'] if m['destination']=='/workspace/state');assert state['rw']==(mode=='offline')
            if mode=='online':assert next(m for m in receipt['mounts'] if m['destination']=='/workspace/online-state')['rw'] is True
        assert read(key+'/jobs/replay.json')['modes']==['completed_'+mode+'_replay_noop']*3
    offline=read('offline-3/jobs/replay.json');initial=read('offline-3/jobs/write.json')
    assert offline['offline_rows']==offline['historical_rows']==3 and initial['offline_sha256']==offline['offline_sha256'] and offline['online_all_missing'] is True and initial['online_all_missing'] is True
    for actual,wanted in zip(offline['rows'],expected,strict=True):
        assert actual['machine_id']==wanted['machine_id'] and utc(actual['event_timestamp'])==utc(wanted['event_timestamp']) and utc(actual['created'])==utc(wanted['created'])
        for n in ['reading_count','temperature_sum','temperature_mean','vibration_max']:assert actual[n]==wanted[n]
    online=read('online-2/jobs/replay.json');assert online['acknowledged_windows']==3 and online['offline_sha256_unchanged']==offline['offline_sha256']
    assert online['project']==offline['project']==read('online-2/jobs/write.json')['project']
    assert [r['temperature_mean'] for r in online['online_rows']]==[40.0,100.0]
    assert read('online-2/release.json')['offline_release']=='stream-offline-jenkins-3'
    assert read('online-2/release.json')['redis_service_uid']==read('offline-3/release.json')['redis_service_uid']
    failure=read('history/prior/jenkins/offline-1.json');assert failure['result']=='FAILURE' and 'KeyError' in (BASE/'history/prior/offline-1/ci/discover.txt').read_text(encoding='utf-8') and 'IOT_STREAM_DESTINATION' in (BASE/'history/prior/offline-1/ci/discover.txt').read_text(encoding='utf-8')
    assert not (BASE/'history/prior/offline-1/release.json').exists()
    assert all((BASE/'screenshots'/name).read_bytes().startswith(b'\xff\xd8\xff') for name in ['jenkins-offline2-success.jpg','jenkins-online1-success.jpg','jenkins-offline3-success.jpg','jenkins-online2-success.jpg'])
    prior=read('history/prior/native-manifest.json');assert len(prior['files'])==124
    for n,v in prior['files'].items():assert manifest['files']['history/prior/'+n]==v
    precise=read('history/precise-readback/precise-offline-readback.json');inspection=read('history/precise-readback/inspection.json')
    assert inspection['exit_code']==0 and inspection['oom_killed'] is False and '--read-only' in inspection['command'] and 'none' in inspection['command']
    assert precise['parquet_sha256']==inspection['source_sha256_unchanged']==offline['offline_sha256']
    for actual,wanted in zip(precise['rows'],expected,strict=True):
        assert actual['machine_id']==wanted['machine_id'] and utc(actual['created'])==utc(wanted['created'])
    for mode,number in [('offline',2),('online',1)]:assert read('history/prior/jenkins/'+mode+'-'+str(number)+'.json')['result']=='SUCCESS'
    print('PASS225payload/4originalJPEG;124Gitinputs/twoactualsource-after;Jenkinsoffline3+online2SUCCESS/7stages each;2+2Airflowtasks;freshnative4phasefault/replay per job;three exact offline rows including microseconds/online initially empty/latest40+100;readonlyupstream/ParquetSHAunchanged;fullrootfs+runtimeConfigparity;all original prior builds and precise read-only inspection retained.')


if __name__=='__main__':main()
