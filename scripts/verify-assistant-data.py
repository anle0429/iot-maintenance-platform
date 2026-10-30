"""Verify paired generator truth, separate ID/target joins, native stream CI and the original continued GPU answers offline."""
from collections import Counter,defaultdict
from bisect import bisect_right
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import json,math,sys,tarfile
from integration_evidence import ROOT,capsule,read
from evidence_integrity import documentation_equivalent
sys.path.insert(0,str(ROOT))
from feature_store.training_samples import FEATURES
from llm.data_validation import cases,request_body,score_answer,resume_prefix
from llm.ab_experiment import reply_text
from llm_observability.contracts import StreamObservation
MANIFEST='70d1c1e67301ca479cea53d0ec8e606c3d43a8c2ff47e79df186ecee1f55e427'


def timestamp(value):
    """Normalize captured UTC source times without depending on this machine's timezone."""
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)


def generated(raw):
    """Read the actual single served model's output-token counter."""
    values=[float(line.split()[-1]) for line in raw.decode().splitlines() if line.startswith('llamacpp:tokens_predicted_total ')]
    assert len(values)==1;return values[0]


def verify():
    """Authenticate original bytes and recompute domain labels/features and factual acceptance independently of passing receipts."""
    base,count=capsule('assistant-data',MANIFEST);assert count==15
    meta=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert len(raw)==meta['archive_bytes']==2482082 and sha256(raw).hexdigest()==meta['archive_sha256']=='753959eb7b6235468b0c98e41eab85d06b5ea053d8534415488a5cc07d61def0'
    assert meta['current_native_credentials_scanned'] and meta['vault_root_and_unseal_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    assert not meta['recompressed_or_rewritten_on_pc'] and not meta['workloads_rerun_for_capture']
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if not member.isfile():assert member.isdir();continue
            name=PurePosixPath(member.name).relative_to('assistant-data-capture-30b')
            assert not name.is_absolute() and '..' not in name.parts and member.size<10000000 and name.as_posix() not in bodies
            bodies[name.as_posix()]=archive.extractfile(member).read()
    def native(name):
        """Decode an authenticated captured JSON payload."""
        return json.loads(bodies[name])
    def lines(name):
        """Read actual source/export rows from the original JSONL table."""
        return [json.loads(v) for v in bodies[name].splitlines()]
    inventory=native('manifest.json')
    assert sha256(bodies['manifest.json']).hexdigest()==meta['original_inventory_sha256']=='320465cf56c865d26455755abc85d229960c562d9753da168c7ce3009b9bcc3f'
    assert len(inventory['files'])==meta['native_payloads']==457 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,pin in inventory['files'].items():assert len(bodies[name])==pin['bytes'] and sha256(bodies[name]).hexdigest()==pin['sha256']
    folders={'assistant-samples-5e5872a6':14,'assistant-samples-5e5872a6-b':14,'assistant-data-eval-6896cb13':11,
             'assistant-data-eval-6896cb13-b':11,'assistant-data-eval-3985fd22':11,'stream-ci':124}
    for folder,size in folders.items():
        prefix='source/'+folder+'/';seal=native(prefix+'manifest.json');assert len(seal['files'])==size
        for name,pin in seal['files'].items():
            original=bodies[prefix+'tree/'+name];assert sha256(original).hexdigest()==pin
            if folder in {'assistant-samples-5e5872a6-b','assistant-data-eval-3985fd22'}:
                current=(ROOT/name).read_bytes();assert current==original or current.replace(b'\r\n',b'\n')==original,name
    source_keys=['feature_store/staged_job.py','feature_store/staged_push.py','feature_store/staging_contract.py',
                 'feature_store/outbox.py','feature_store/push.py','feature_store/common.py','scripts/ci-stream-push.py',
                 'infra/ci/StreamPush.Jenkinsfile','infra/ci/iot_ci_stream_push.py']
    for name in source_keys:
        current=(ROOT/name).read_bytes();old=bodies['source/stream-ci/tree/'+name]
        if name=='scripts/ci-stream-push.py':
            assert (sha256(old).hexdigest(),sha256(current.replace(b'\r\n',b'\n')).hexdigest())==(
                'fce025aae2c647a5c393fcf9e6d62441d6e6d41a6313f9718f155f5cd9d0d3b3','622ef60f8d7449b316e5b0063046094444e17286d64e35329a9586b7dc980414')
            assert documentation_equivalent(old,current,'.py'),name
        else:assert current==old or current.replace(b'\r\n',b'\n')==old,name
    settings=native('samples/settings.json');profile=settings['sensor_drift'];end=timestamp(settings['simulation']['start_ts'])+timedelta(hours=48)
    assert settings['simulation']['machine_count']==3 and settings['simulation']['wear_per_hour']==.07
    assert profile=={'start_ts':'2026-10-02T00:00:00Z','ramp_seconds':3600,'temperature_offset_c':15,'vibration_multiplier':1.8}
    summaries={};truths={};telemetry_versions={};sample_ids={};maximum_error=0.
    for version in ['baseline','drift']:
        prefix='samples/run/'+version+'/';manifest=native(prefix+'source/manifest.json');dataset_id=sha256(bodies[prefix+'source/manifest.json']).hexdigest()
        for file in manifest['files']:
            original=bodies[prefix+'source/'+file['path']];assert len(original)==file['bytes'] and sha256(original).hexdigest()==file['sha256']
        telemetry=[]
        for name in bodies:
            if name.startswith(prefix+'source/telemetry/') and name.endswith('.jsonl'):telemetry.extend(lines(name))
        unique={}
        for row in telemetry:
            key=row['event_id']
            if key not in unique or timestamp(row['created_ts'])>timestamp(unique[key]['created_ts']):unique[key]=row
        assert len(unique)==8640
        by_machine=defaultdict(list)
        for row in unique.values():by_machine[row['machine_id']].append(row)
        ordered={machine:sorted([(timestamp(r['event_ts']),r) for r in values],key=lambda item:item[0]) for machine,values in by_machine.items()}
        event_times={machine:[stamp for stamp,row in values] for machine,values in ordered.items()}
        telemetry_versions[version]={(r['machine_id'],r['event_ts']):r for r in unique.values()}
        failures=lines(prefix+'source/failure_events.jsonl');maintenance=lines(prefix+'source/maintenance_events.jsonl');machines={r['machine_id']:r for r in lines(prefix+'source/machines.jsonl')}
        assert len(failures)==13 and len(maintenance)==6
        feature_inputs=lines(prefix+'feature-inputs.jsonl');label_inputs=lines(prefix+'label-inputs.jsonl')
        assert len(feature_inputs)==len(label_inputs)==1728
        labels={(r['machine_id'],timestamp(r['event_timestamp'])):r for r in label_inputs};assert len(labels)==1728
        features=lines(prefix+'features.jsonl');targets=lines(prefix+'labels.jsonl');joined=lines(prefix+'joined-training.jsonl')
        by_id={r['sample_id']:r for r in features};target_by_id={r['sample_id']:r['failure_in_next_1h'] for r in targets}
        sample_ids[version]=set(by_id)
        assert len(features)==len(targets)==len(joined)==len(by_id)==len(target_by_id)==1485
        assert all(set(r)=={'sample_id','failure_in_next_1h'} for r in targets)
        assert all(set(r)=={'sample_id','machine_id','event_timestamp',*FEATURES} for r in features)
        assert {r['sample_id'] for r in joined}==set(by_id)==set(target_by_id)
        selected={};excluded=Counter()
        for feature in feature_inputs:
            machine=feature['machine_id'];stamp=timestamp(feature['event_timestamp']);label=labels[(machine,stamp)]
            assert label['operating_status']==telemetry_versions[version][(machine,stamp.strftime('%Y-%m-%dT%H:%M:%SZ'))]['operating_status']
            horizon=stamp+timedelta(hours=1)
            future=[r for r in failures if r['machine_id']==machine and stamp<timestamp(r['failure_start_ts'])<=horizon]
            intervened=any(r['machine_id']==machine and stamp<timestamp(r['start_ts'])<=horizon for r in maintenance)
            status=('stopped' if label['operating_status']!='operating' else 'censored' if horizon>=end else 'maintenance' if intervened else 'eligible')
            expected=None if status!='eligible' else int(bool(future))
            assert label['label_status']==status and label['failure_in_next_1h']==expected
            lower=bisect_right(event_times[machine],stamp-timedelta(minutes=10));upper=bisect_right(event_times[machine],stamp)
            available=[r for event_time,r in ordered[machine][lower:upper] if timestamp(r['received_ts'])<=stamp]
            assert feature['reading_count_10m']==len(available)
            if available:
                mean=sum(r['current_a'] for r in available)/len(available)
                actual={'temperature_mean_10m':sum(r['temperature_c'] for r in available)/len(available),
                        'vibration_max_10m':max(r['vibration_rms_mm_s'] for r in available),'current_mean_10m':mean,
                        'current_std_10m':math.sqrt(sum((r['current_a']-mean)**2 for r in available)/len(available)),
                        'rpm_mean_10m':sum(r['rpm'] for r in available)/len(available)}
                for key,value in actual.items():maximum_error=max(maximum_error,abs(feature[key]-value))
                assert timestamp(feature['max_received_ts'])==max(timestamp(r['received_ts']) for r in available)
                assert timestamp(feature['max_feature_event_ts'])==max(timestamp(r['event_ts']) for r in available)
            assert abs(feature['machine_age_days']-(stamp-timestamp(machines[machine]['installed_at'])).total_seconds()/86400)<1e-12
            assert feature['prior_failures_24h']==sum(r['machine_id']==machine and stamp-timedelta(hours=24)<timestamp(r['failure_start_ts'])<=stamp and timestamp(r['recorded_ts'])<=stamp for r in failures)
            if status!='eligible':excluded[status]+=1;continue
            if len(available)<5:excluded['insufficient_readings']+=1;continue
            identifier=sha256(json.dumps(['iot-training-sample-v1',dataset_id,machine,stamp.isoformat(timespec='microseconds')],separators=(',',':')).encode()).hexdigest()
            assert identifier in by_id and target_by_id[identifier]==expected
            assert all(by_id[identifier][field]==feature[field] for field in FEATURES)
            selected[identifier]=dict(by_id[identifier],failure_in_next_1h=expected)
        assert sorted(joined,key=lambda r:r['sample_id'])==[selected[k] for k in sorted(selected)]
        assert dict(excluded)=={'insufficient_readings':3,'maintenance':72,'stopped':132,'censored':36}
        assert Counter(target_by_id.values())=={0:1341,1:144}
        assert all(bodies[n][:4]==b'PAR1' and bodies[n][-4:]==b'PAR1' for n in bodies if n.startswith(prefix+'labels.parquet/') and n.endswith('.parquet'))
        summaries[version]=native(prefix+'verification.json');assert summaries[version]['dataset_id']==dataset_id and summaries[version]['parquet_readback_rows']==1485
        truths[version]={(r['machine_id'],r['event_timestamp']):(r['label_status'],r['failure_in_next_1h']) for r in label_inputs}
    assert maximum_error<1e-10 and truths['baseline']==truths['drift']
    assert sample_ids['baseline'].isdisjoint(sample_ids['drift'])
    assert summaries['baseline']['dataset_id']!=summaries['drift']['dataset_id']
    for name in ['failure_events.jsonl','maintenance_events.jsonl','machines.jsonl']:
        assert bodies['samples/run/baseline/source/'+name]==bodies['samples/run/drift/source/'+name]
    for key,left in telemetry_versions['baseline'].items():
        right=telemetry_versions['drift'][key];elapsed=(timestamp(left['event_ts'])-timestamp(profile['start_ts'])).total_seconds();fraction=max(0,min(1,elapsed/3600))
        assert right['temperature_c']==round(left['temperature_c']+15*fraction,3)
        assert right['vibration_rms_mm_s']==round(left['vibration_rms_mm_s']*(1+.8*fraction),3)
        assert {k:v for k,v in left.items() if k not in {'event_id','temperature_c','vibration_rms_mm_s'}}=={k:v for k,v in right.items() if k not in {'event_id','temperature_c','vibration_rms_mm_s'}}
    assert native('execution/assistant-samples-30a/container.json')['state']['ExitCode']!=0
    assert 'failed to map segment' in bodies['execution/assistant-samples-30a/console.txt'].decode()
    container=native('execution/assistant-samples-30b/container.json');assert container['state']['ExitCode']==0 and not container['state']['OOMKilled'] and container['memory_bytes']==4*1024**3 and container['nano_cpus']==2_000_000_000
    for destination,number in [('offline',4),('online',3)]:
        build=native('jenkins/'+destination+'/build.json');assert build['number']==number and build['result']=='SUCCESS' and not build['building']
        stages=native('jenkins/'+destination+'/stages.json')['stages'];assert len(stages)==7 and all(s['status']=='SUCCESS' for s in stages)
        proof=native('stream/'+destination+'/outputs/verification.json');assert proof['passed'] and proof['destination']==destination
        airflow=native('stream/'+destination+'/outputs/airflow.json');assert airflow['run']['state']=='success' and len(airflow['tasks']['task_instances'])==2 and all(t['state']=='success' for t in airflow['tasks']['task_instances'])
        for operation in ['write','replay']:
            state=native('stream/'+destination+'/runs/'+operation+'-container.json');assert state['exit_code']==0 and not state['oom_killed']
        assert bodies['operator/stream/'+destination+'-job-before.xml']==bodies['operator/stream/'+destination+'-job-after.xml']
    prefix='execution/assistant-data-eval-30c/'
    cohort=cases(native(prefix+'generator-input.json'),native(prefix+'offline-input.json'),native(prefix+'online-input.json'))
    assert native(prefix+'cases.json')==cohort and native('execution/assistant-data-eval-30b/cases.json')==cohort
    carried={'request':native(prefix+'case-0-request.json'),'result':native(prefix+'case-0-result.json'),'wire':bodies[prefix+'case-0.wire']}
    assert len(resume_prefix(cohort,[carried],cohort))==1
    origin=native(prefix+'resume-origin.json');assert origin['carried_completed_responses']==1 and not origin['selection_by_correctness']
    for name,pin in origin['original_bytes'].items():assert sha256(bodies[prefix+name]).hexdigest()==pin and bodies[prefix+name]==bodies['execution/assistant-data-eval-30b/'+name]
    results=[]
    for index,case in enumerate(cohort):
        label='case-'+str(index);record=native(prefix+label+'-result.json');wire=bodies[prefix+label+'.wire']
        assert native(prefix+label+'-request.json')==request_body(case) and sha256(wire).hexdigest()==record['wire_sha256']
        parser=StreamObservation();offset=0
        for chunk in record['chunks']:
            assert chunk['offset']==offset;end=offset+chunk['bytes'];parser.feed(wire[offset:end],chunk['elapsed_seconds']);offset=end
        assert offset==len(wire) and parser.finish(record['elapsed_seconds'])==record['observation']
        events=[json.loads(line[5:].strip()) for line in wire.decode().splitlines() if line.startswith('data:') and line[5:].strip()!='[DONE]']
        text=reply_text(events);assert text==record['reply'] and score_answer(text,case['expected'])==record['score'];results.append(record)
    assert [r['score']['correct'] for r in results]==[True,False,True]
    assert results[1]['score']['actual']['monitoring_is_complete_predictor_input'] is True
    assert native(prefix+'pods-before.json')==native(prefix+'pods-after.json')==native('execution/assistant-data-eval-30b/pods-before.json')
    ledger={r['event_id']:r['facts'] for r in native(prefix+'ledger-new.json')};assert set(ledger)=={r['request_id'] for r in results}
    for row in results:assert ledger[row['request_id']]['usage']==row['observation']['usage'] and ledger[row['request_id']]['status']==200
    assert generated(bodies[prefix+'gpu-after.prom'])-generated(bodies[prefix+'gpu-before.prom'])==sum(r['observation']['usage']['completion_tokens'] for r in results)==137
    assert generated(bodies[prefix+'gpu-at-entry.prom'])-generated(bodies[prefix+'gpu-before.prom'])==results[0]['observation']['usage']['completion_tokens']==41
    guard=native('execution/assistant-data-input-guard-30a/verification.json');assert guard['passed'] and len(guard['results'])==2
    for row in guard['results']:
        outcome=row['result'];assert not outcome['predictor']['accepted'] and len(outcome['predictor']['errors'])==5 and not outcome['machine_tool']['accepted']
        for module,name in [('services.predictor.contracts','services/predictor/contracts.py'),('services.mcp_tools.contracts','services/mcp_tools/contracts.py')]:
            assert sha256((ROOT/name).read_bytes()).hexdigest()==outcome['schemas'][module]
    for name in ['stream-offline','stream-online']:assert (base/'ui'/(name+'.jpg')).read_bytes()[:3]==b'\xff\xd8\xff'
    return {'passed':True,'native_payloads':457,'generator_versions':2,'unique_events_per_version':8640,'eligible_samples_per_version':1485,
            'positive_targets_per_version':144,'separate_target_columns':2,'independent_max_feature_error':maximum_error,
            'stream_ci_builds':{'offline':4,'online':3},'successful_airflow_tasks':4,'original_gpu_responses':3,'factual_contracts_matched':2,
            'carried_completed_answers':1,'gpu_output_tokens':137,'deployed_contract_rejections':2,'original_screenshots':2,'coursework_complete':False}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
