"""Audit retained native Jenkins training evidence and historical Git source without starting services or retraining."""
from git_sources import historical_blob
from datetime import datetime,timedelta
from hashlib import sha256
from pathlib import Path,PurePosixPath
import io,json,math,re,subprocess,tarfile


def main():
    """Cross-check source/image/pod/data/model identities, native task execution and independent arithmetic."""
    root=Path(__file__).resolve().parents[1];base=root/'docs/evidence/ci-training'
    read=lambda name:json.loads((base/name).read_bytes())
    digest=lambda raw:sha256(raw).hexdigest()
    manifest=read('manifest.json');proof=read('verification.json');commit=proof['source_commit']
    assert commit==manifest['source_commit'] and re.fullmatch('[a-f0-9]{40}',commit)
    assert len(manifest['files'])==47
    for name,item in manifest['files'].items():
        assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
        raw=(base/name).read_bytes();assert len(raw)==item['bytes']<1_000_000 and digest(raw)==item['sha256'],name
    assert sum(x['bytes'] for x in manifest['files'].values())==manifest['payload_bytes']
    for name,item in proof['origins'].items():
        if item['transformation'].startswith('none;'):assert digest((base/name).read_bytes())==item['raw_sha256'],name
    source=read('source-manifest.json');assert source['commit']==commit and len(source['files'])==93
    # Reconstruct the original tar in memory from the named Git commit, not today's working files.
    packet=io.BytesIO()
    with tarfile.open(fileobj=packet,mode='w') as archive:
        for name,expected in sorted(source['files'].items()):
            raw=historical_blob(root, commit+':'+name);assert digest(raw)==expected,name
            member=tarfile.TarInfo('tree/'+name);member.size=len(raw);member.mode=0o644;archive.addfile(member,io.BytesIO(raw))
    assert len(packet.getvalue())==481280 and digest(packet.getvalue())==source['archive_sha256']=='58ab60b566a7278463a8cf811a9f0e9543d3a0e037b14e48b40509d6394c5526'
    build=read('ci/build.json');deploy=read('ci/deployment.json');snapshot=read('source-snapshot.json');pre=read('ci/preflight.json')
    assert build['commit']==deploy['commit']==snapshot['commit']==pre['source_snapshot_commit']==commit
    runtime=build['runtime_source_sha256'];guard=build['source_sha256'];assert len(runtime)==78 and len(guard)==44
    assert all(source['files'][n]==v for n,v in runtime.items()) and guard==deploy['source_sha256']==snapshot['files']==pre['code_sha256']
    captured=read('runtime-source-readback.json');assert captured['passed'] and captured['commit']==commit
    assert captured['mounted_source']==captured['image_source']['feast']==captured['image_source']['spark']==runtime
    pipeline=digest((base/'pipeline.yaml').read_bytes());assert pipeline==deploy['pipeline_sha256']==read('ci/compiled.json')['pipeline_sha256']==read('ci/ci-deployment.json')['pipeline_sha256']
    assert read('ci/compiled.json')['tasks']==5 and read('ci/test.json')['source_files']==93
    assert read('ci/ci-deployment.json')['manifest_sha256']==digest((base/'ci/deployment.json').read_bytes())
    jenkins=read('ci/jenkins-build.json');assert jenkins['number']==2 and jenkins['result']=='SUCCESS' and not jenkins['building']
    assert jenkins['duration']==1188615 and jenkins['queueId']==10
    assert next(p['value'] for a in jenkins['actions'] for p in a.get('parameters',[]) if p['name']=='SOURCE_COMMIT')==commit
    console=(base/'ci/jenkins-build2-console.txt').read_text(encoding='utf-8');assert 'Ran 22 tests in' in console and '\nOK\n' in console and console.rstrip().endswith('Finished: SUCCESS')
    failure=read('ci/jenkins-build1.json');diff=read('failed-image-source-diff.json')
    assert failure['number']==1 and failure['result']=='FAILURE' and not failure['building']
    assert diff['expected_files']==74 and diff['actual_files']==83 and len(diff['extra'])==9 and not diff['missing'] and not diff['mismatched']
    assert 'Built training source differs from tested source' in (base/'ci/jenkins-build1-console.txt').read_text(encoding='utf-8')
    vm=read('vm-session.json');started=datetime.fromisoformat(vm['lastStartTimestamp']);stop=started+timedelta(seconds=int(vm['scheduling']['maxRunDuration']['seconds']))
    admit=read('ci/admission.json');assert vm['scheduling']['instanceTerminationAction']=='STOP' and (stop-started).total_seconds()==28800
    assert datetime.fromisoformat(admit['auto_stop_utc'])==datetime.fromisoformat(pre['deadline_utc'])==stop
    assert admit['passed'] and admit['seconds_before_actual_stop']>=6000 and admit['ram_available_bytes']>=25769803776 and all(x>=40000000000 for x in admit['disk_free_bytes'])
    kfp=read('ci/kfp-run.json');run=proof['kfp_run_id'];assert kfp['run_id']==read('ci/submission.json')['run_id']==run
    assert kfp['display_name']==build['release']==deploy['release']=='train-jenkins-2' and kfp['state']=='SUCCEEDED'
    names={'versioned-load','cluster-split','distributed-train','validate-saved-model','registered-evaluate'}
    spec=kfp['pipeline_spec']['pipeline_spec'];assert set(spec['root']['dag']['tasks'])==names
    for name in names:assert any(t['display_name']==name+'(0)' and t['state']=='SUCCEEDED' for t in kfp['run_details']['task_details'])
    assert datetime.fromisoformat(kfp['created_at'])>datetime.fromisoformat(pre['checked_utc'])>datetime.fromisoformat(deploy['deployed_utc'])
    mains=read('ci/verified-main-containers.json')['containers'];assert len(mains)==5
    pod_projection=read('pod-main-bindings.json')['pods'];pods={p['metadata']['name']:p for p in pod_projection}
    expected={x['tag']:x for x in build['images'].values()}
    for item in mains:
        image=expected[item['image']];p=pods[item['pod']];status=p['main'];assert p['metadata']['labels']['pipeline/runid']==run
        assert image['packaged_source_verified'] and image['containerd_imported'] and image['docker_export_config_digest']==image['cri_image_id']==item['image_id']==status['imageID']
        assert item['exit_code']==status['state']['terminated']['exitCode']==0 and p['phase']=='Succeeded'
        assert p['main_spec'][0]['image']==item['image'] and any(x['mountPath']=='/workspace' and x['name']=='iot-ml-code' for x in p['main_spec'][0]['volumeMounts'])
        assert any(v['name']=='iot-ml-code' and v['persistentVolumeClaim']['claimName']=='iot-ml-code' for v in p['volumes'])
    live=read('two-executors-running.json')['items'];assert len(live)==2
    assert {p['metadata']['labels']['spark-exec-id'] for p in live}=={'1','2'} and len({p['spec']['nodeName'] for p in live})==1
    assert all(p['status']['phase']=='Running' and p['metadata']['labels']['iot-coursework-run']=='train-jenkins-2' for p in live)
    distributed=read('ci/distributed-verification.json');events=read('event-summary.json');assert distributed['passed'] and distributed['application_ended'] and not distributed['failed_task_attempts'] and not events['failed_attempts']
    assert events['event_counts']['SparkListenerTaskEnd']==sum(sum(c.values()) for c in events['task_counts_by_stage_and_executor'].values())==79
    assert {n:v['sha256'] for n,v in events['raw_event_logs'].items()}==distributed['event_logs']
    for sid in range(6,18):
        assert set(events['task_counts_by_stage_and_executor'][str(sid)])=={'1','2'}
        assert any(s['stage_id']==sid and s['successful_executor_ids']==['1','2'] for s in distributed['logistic_regression_stages'])
    native=read('ci/native-verification.json');ci=read('ci/verification.json');reg=read('ci/lineage/registration.json');auto=read('ci/lineage/automatic-data.json')
    assert native['passed'] and ci['passed'] and ci['run_id']==native['run_id']==run and ci['commit']==commit
    assert native['distributed_proof_sha256']==digest((base/'ci/distributed-verification.json').read_bytes()) and native['source_preflight_sha256']==digest((base/'ci/preflight.json').read_bytes())
    data=proof['data_version'];assert auto['passed'] and reg['passed'] and auto['source_snapshot_commit']==reg['source_snapshot_commit']==commit
    canonical=read('ci/lineage/canonical.json');local=read('ci/lineage/local-version.json');remote=read('ci/lineage/remote-version.json')
    entries={x['path']:{'sha256':x['sha256'],'size':x['bytes']} for x in canonical['files']};assert len(entries)==180 and sum(x['size'] for x in entries.values())==740003698
    document={'format':'iot-content-dataset-v1','metadata':canonical['metadata'],'files':entries}
    assert digest((json.dumps(document,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())==data
    assert data==local['version']==remote['version']==auto['data_version']==reg['incremental_data_reference']['data_version']==native['data_version']
    assert canonical['rows']==local['rows_in_version']==reg['incremental_data_reference']['rows']==3132184
    assert set(local['reused_blob_sha256'])==set(remote['blob_locations'])=={v['sha256'] for v in entries.values()}
    assert local['new_blob_bytes']==remote['new_blob_payload_bytes']==auto['new_remote_blob_payload_bytes']==0
    assert remote['existing_version_reconciliation'] and remote['remote_restore']==auto['remote_restore'] and auto['remote_restore']['all_file_bytes_verified']
    assert auto['remote_restore']['files']==180 and auto['remote_restore']['bytes']==740003698
    model=read('registry/model-version.json')['model_version'];modelrun=read('registry/model-run.json')['run'];datarun=read('registry/data-run.json')['run']
    tags=lambda values:{x['key']:x['value'] for x in values}
    mt=tags(modelrun['data']['tags']);dt=tags(datarun['data']['tags']);vt=tags(model['tags'])
    assert model['name']==reg['model_name']=='iot-pump-failure-v1' and model['version']==reg['version']==native['registered_model_version']=='6' and model['status']=='READY'
    assert model['run_id']==modelrun['info']['run_id']==reg['model_run_id']==native['model_run_id'] and modelrun['info']['status']=='FINISHED'
    assert mt['source_snapshot_commit']==commit and mt['training_data_version']==dt['data_version']==vt['data_version']==data
    assert datarun['info']['run_id']==mt['dataset_run_id']==auto['data_run_id']==native['data_run_id'] and datarun['info']['status']=='FINISHED' and dt['complete']=='true'
    assert modelrun['info']['run_name']=='train-jenkins-2' and modelrun['info']['start_time']>jenkins['timestamp']
    assert model['aliases']==['candidate'] and vt['production']=='false' and not reg['production'] and not native['production'] and not ci['production']
    assert len(modelrun['data']['params'])==11 and len(modelrun['data']['metrics'])==27
    assert reg['model_data_artifact_payload_bytes']==0 and reg['registry_model_native_spark_probe_rows']==200 and reg['max_abs_probability_difference']<=1e-12
    evaluation=read('ci/lineage/evaluation.json');assert mt['evaluation_sha256']==digest((base/'ci/lineage/evaluation.json').read_bytes())
    assert evaluation['protocol_sha256']==mt['protocol_sha256']==dt['protocol_sha256']==source['files']['ml/protocols/medium-60d-v1.json']
    assert evaluation['split_counts']['train']['rows']==2195668 and evaluation['fixed_threshold']==.7
    metrics={x['key']:x['value'] for x in modelrun['data']['metrics']}
    for partition in ['validation','test']:
        e=evaluation[partition];precision=e['tp']/(e['tp']+e['fp']);recall=e['tp']/(e['tp']+e['fn'])
        assert math.isclose(e['f1'],2*precision*recall/(precision+recall),rel_tol=1e-14)
        assert sum(e[k] for k in ['tp','fp','fn','tn'])==evaluation['split_counts'][partition]['rows']
        for k in e:assert metrics[partition+'.'+k]==e[k]
    for name in [n for n in manifest['files'] if n.endswith('.jpg')]:assert (base/name).read_bytes()[:2]==b'\xff\xd8'
    print('PASS47payload hashes/93historical Git files+byte-exact packet/78source per image+mount/44MLguard/22native tests/5KFPmains/2executors+79tasks/incremental180files/actual model6+data registry linkage. Candidate only; no production/100GB-ML claim.')


if __name__=='__main__':main()
