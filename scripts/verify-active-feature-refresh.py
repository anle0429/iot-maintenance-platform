"""Audit the real model6 cache refresh, corrected metadata check and preserved failed rollback offline."""
from git_sources import historical_blob
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import importlib.util,json,subprocess

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'docs/evidence/active6-feature-refresh'


def read(name):
    """Decode one retained receipt after the complete byte manifest has been checked."""
    return json.loads((BASE/name).read_bytes())


def main():
    """Independently calculate every prediction and enforce unchanged model/image/seed identities."""
    manifest=read('manifest.json')
    assert set(manifest['files'])=={p.name for p in BASE.iterdir() if p.name!='manifest.json'}
    for name,sealed in manifest['files'].items():
        assert Path(name).name==name
        raw=(BASE/name).read_bytes()
        assert len(raw)==sealed['bytes'] and sha256(raw).hexdigest()==sealed['sha256'],name
    source=read('source-manifest.json')
    assert source['commit']=='99d6dcc2994f55f06133eace53ecff523f6fc3cb'
    for name,wanted in source['files'].items():
        assert sha256(historical_blob(ROOT, source['commit']+':'+name)).hexdigest()==wanted,name
    spec=importlib.util.spec_from_file_location('native_serving_audit',ROOT/'scripts/verify-native-serving.py')
    audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
    report=read('verification.json');pin=read('model-pin.json');model=read('portable.json')
    assert report['passed'] is True and pin['version']==report['model_version']=='6'
    assert pin['production'] is True and report['model7_promoted'] is False and len(pin)==report['pin_fields']==9
    reference=ROOT/'docs/evidence/ci-serving-linux/selected-model6'
    assert pin==json.loads((reference/'model-pin.json').read_bytes()) and model==json.loads((reference/'portable.json').read_bytes())
    audit.all_machine_rows(read('http-all-machines.json'),model,pin)
    assert (report['operating_predictions'],report['stopped_conflicts'],report['max_abs_probability_difference'])==(195,5,0)
    seed=read('context.json');ready=read('state-ready.json');state=read('state-verification.json')
    for public,original in [('context.json','context.json'),('state-ready.json','_READY.json'),('state-verification.json','verification.json')]:
        assert sha256((BASE/public).read_bytes()).hexdigest()==report['seed_hashes'][original]
    assert state['passed'] is True and ready['context_sha256']==state['context_sha256']==report['seed_hashes']['context.json']
    assert ready['verification_sha256']==report['seed_hashes']['verification.json']
    assert seed['prepared_wall_utc']==report['prepared_wall_utc']
    assert 0<=(datetime.fromisoformat(report['checked_utc'])-datetime.fromisoformat(report['prepared_wall_utc'])).total_seconds()<3600
    before=read('preflight.json');values=read('values-verified.json')
    assert before['immutable'] is True and before['seed_metadata']['uid']==report['seed_uid']
    assert before['state_hashes']==report['seed_hashes'] and before['remaining_context_seconds']>900
    assert before['original_status']['info']['status']=='failed' and before['original_values']['revisionMarker']=='serve-jenkins-8'
    assert read('original-physical-ttl-before.json')=={'keys':0,'pttl_ms':[]}
    for name in ['original-physical-ttl-after.json','physical-ttl-before.json','physical-ttl-after.json']:
        ttl=read(name);assert ttl['keys']==len(ttl['pttl_ms'])==200 and min(ttl['pttl_ms'])>900000
    assert min(read('physical-ttl-after.json')['pttl_ms'])==report['minimum_pttl_ms']
    assert min(read('original-physical-ttl-after.json')['pttl_ms'])>3300000
    assert values['revisionMarker']=='active6-refresh-after-retrain28-verified'
    assert values['image']==before['original_values']['image'] and values['initContainers']==before['original_values']['initContainers']
    assert values['env']==before['original_values']['env']
    assert next(v for v in values['volumes'] if v['name']=='seed')['configMap']['name']=='iot-feature-active6-refresh-after-retrain28'
    assert read('helm-after.json')['info']['status']=='deployed'
    deployment=read('deployment-after.json');assert deployment['metadata']['uid']==report['deployment_uid']
    assert deployment['status']['observedGeneration']==deployment['metadata']['generation'] and deployment['status']['availableReplicas']>=1
    assert read('isvc-after.json')['metadata']['uid']==report['model6_isvc_uid']
    active=[p for p in read('pods-after.json')['items'] if not p['metadata'].get('deletionTimestamp') and any(c['type']=='Ready' and c['status']=='True' for c in p['status']['conditions'])]
    assert active
    for pod in active:
        for container in pod['status']['containerStatuses']+pod['status']['initContainerStatuses']:
            assert container['imageID']=='sha256:c35ae82be5c564761e721f2c9d4bc838067ad93d7fd55988b224ea47a5504e02'
    failure=read('original-failure-scope.json');assert failure['failed_expression']=="reply['model']==pin"
    assert set(failure['retained_model_keys'])==set(pin)|{'features','score_kind','data_scope'}
    assert 'context deadline exceeded' in (BASE/'original-recover-original-on-failure.txt').read_text()
    command=read('original-materialization-command.json')
    assert 'feature_store.serving' in command and '--memory' in command and '8g' in command
    assert report['ready_response']['ready'] is True
    print(json.dumps({'passed':True,'payload_files':len(manifest['files']),'source_files':len(source['files']),'predictions':195,'stopped_conflicts':5,'max_abs_probability_difference':0,'model_version':'6','scope':'Historical point-in-time proof; one-hour demo lease is not perpetual availability.'}))


if __name__=='__main__':main()
