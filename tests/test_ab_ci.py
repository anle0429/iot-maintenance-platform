"""Check isolated deployment plans, native identity guards and the A/B probe with explicit control-plane fixtures."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import importlib.util,io,json,shutil,sys
from urllib.error import HTTPError
import pytest,yaml
from ml import ab_ci,ab_native
from services.predictor.binding import LEGACY
from services.ab_api.metrics import exposition
from tests.api.test_ab_api import fixture_runtime,GOLDEN,ASOF
from tests.test_serving_ci import plan as pair_plan

ROOT=Path(__file__).resolve().parents[1]


def recipe():
    """Derive a new plan from actual Helm templates; private IPs and node identities are declared test inputs."""
    pair=pair_plan();reference=json.dumps({'lineage':{'protocol_sha256':LEGACY['protocol_sha256']}})
    return ab_ci.deployment_plan(pair,LEGACY,'http://10.53.0.10','http://10.53.0.11',reference,
        yaml.safe_load((ROOT/'infra/ab-api/helm-values.yaml').read_text()),
        '/srv/iot-data/workspace/iot-maintenance-platform/data/ci/releases/serve-jenkins-7/runtime/ab-ledger','iot-cw-vm')


class ProtocolFixture:
    """Use real SQLite/model/drift behavior but explicitly replace HTTP and upstream metrics for unit tests."""
    def __init__(self,path):
        'Build an isolated real A/B SQLite fixture with controlled faults and counters.'
        self.runtime=fixture_runtime(path);self.seen=set();self.fault=None;self.counter_reads={v:0 for v in ab_ci.VARIANTS}
        self.offline={m:{'features':self.runtime.runtimes['baseline'].backend.fetch(m)['values'],'feature_event_ts':ASOF.isoformat()} for m in self.runtime.context['machines']}
    def call(self,path,body=None,headers=None):
        """Return the same typed contract used by the actual private controller transport."""
        if path=='/health/ready':
            result=self.runtime.ready()
            if self.fault=='ready':result['experiment']='other'
            return 200,result
        if path=='/v1/experiment':
            result=self.runtime.snapshot()
            if self.fault=='existing' and not result['observations']:result['observations']=[{'old':True}]
            if result['observations']:
                if self.fault=='ledger':result['observations'][0]['request_id']='forged'
                elif self.fault=='counter':result['metrics']['baseline']['requests']+=1
                elif self.fault=='reference':result['drift']['candidate']['reference_id']='a'*64
            return 200,result
        machine=path.split('/')[3].split(':')[0];identity=headers['X-Request-ID'];retry=identity in self.seen;self.seen.add(identity)
        row=self.runtime.predict(machine,identity)
        if self.fault=='failed':return 503,row
        if self.fault=='trial':row=deepcopy(row);row['variant']='wrong'
        if self.fault=='replay' and retry:row=deepcopy(row);row['latency_ms']+=1
        return 200,row
    def text(self,path):
        """Read the actual ledger exporter; corruption cases verify scrape/readback mismatches fail closed."""
        result=exposition(self.runtime.snapshot()).decode()
        return result.replace('iot_ab_requests_total','iot_ab_wrong_total') if self.fault=='scrape' else result
    def predictor_metrics(self,variant):
        """Count real fixture model calls and optionally represent legitimate background readiness activity."""
        self.counter_reads[variant]+=1;count=self.runtime.runtimes[variant].backend.calls
        if self.fault=='upstream':count=1
        if self.fault=='background' and self.counter_reads[variant]>1:count+=3
        return f'http_other_metric 1\niot_predictor_requests_total{{outcome="success"}} {count}\n'
    def probe(self,cycles=2):
        """Use two cycles here; native CI uses the fixed four-cycle default."""
        return ab_ci.probe(self.call,self.text,self.predictor_metrics,self.runtime.context,self.runtime.experiment,
            self.runtime.pins,self.runtime.detector.reference_id,self.offline,cycles)


def test_baseline_is_discovered_from_active_private_routing():
    'Verify baseline is discovered from active private routing.'
    deployment={'spec':{'template':{'spec':{'containers':[{'name':'api','env':[{'name':'IOT_KSERVE_URL','value':'http://old-v5-predictor.iot-serving.svc.cluster.local'}]}]}}}}
    assert ab_ci.baseline_target(deployment)==('old-v5','old-v5-predictor')
    for value in ['https://old-v5-predictor.iot-serving.svc.cluster.local','http://external.example','http://127.0.0.1',
            'http://user@old-v5-predictor.iot-serving.svc.cluster.local','http://old-v5-predictor.iot-serving.svc.cluster.local:8080',
            'http://old-v5-predictor.iot-serving.svc.cluster.local/path','http://old-v5-predictor.iot-serving.svc.cluster.local?model=other']:
        bad=deepcopy(deployment);bad['spec']['template']['spec']['containers'][0]['env'][0]['value']=value
        with pytest.raises(ValueError):ab_ci.baseline_target(bad)
    deployment['spec']['template']['spec']['containers'][0]['env']=[]
    with pytest.raises(ValueError):ab_ci.baseline_target(deployment)


def test_config_volume_and_template_are_bound_to_release_without_mutating_defaults():
    'Verify config volume and template are bound to release without mutating defaults.'
    plan=recipe();objects=plan['objects'];values=plan['values']
    assert [o['kind'] for o in objects]==['ConfigMap','ConfigMap','PersistentVolume','PersistentVolumeClaim']
    assert all(o['immutable'] for o in objects[:2]) and objects[2]['spec']['persistentVolumeReclaimPolicy']=='Retain'
    assert objects[2]['spec']['local']['path'].startswith('/srv/iot-data/') and objects[3]['spec']['volumeName']==objects[2]['metadata']['name']
    assert objects[3]['spec']['storageClassName']=='' and objects[3]['spec']['accessModes']==['ReadWriteOnce']
    assert values['image']['tag']=='serve-jenkins-7' and values['initContainers'][0]['image']==plan['image']
    assert values['replicas']==1 and values['autoscaling']=={'enabled':False}
    assert json.loads(objects[0]['data']['config.json'])==plan['config']
    assert yaml.safe_load((ROOT/'infra/ab-api/helm-values.yaml').read_text())['image']['tag']=='v1'


@pytest.mark.parametrize('value',['http://8.8.8.8','http://127.0.0.1','http://169.254.169.254','http://0.0.0.0',
    'http://224.1.1.1','http://[::1]','http://10.53.0.1:80','http://user:secret@10.53.0.1','https://10.53.0.1','http://10.53.0.1/x'])
def test_config_endpoints_cannot_escape_observed_cluster_service(value):
    'Verify config endpoints cannot escape observed cluster service.'
    with pytest.raises(ValueError):ab_ci.private_endpoint(value)


def test_recipe_rejects_same_model_wrong_protocol_or_unscoped_storage():
    'Verify recipe rejects same model wrong protocol or unscoped storage.'
    pair=pair_plan();template=yaml.safe_load((ROOT/'infra/ab-api/helm-values.yaml').read_text());path='/srv/iot-data/x/releases/serve-jenkins-7/runtime/ab-ledger'
    for kwargs in [{'baseline':pair['pin']},{'baseline_url':'http://10.53.0.11'},
            {'reference_raw':json.dumps({'lineage':{'protocol_sha256':'f'*64}})},{'reference_raw':'x'*700001},
            {'ledger_path':'/tmp/releases/serve-jenkins-7/runtime/ab-ledger'},{'ledger_path':path.replace('serve-jenkins-7','serve-jenkins-8')},
            {'ledger_path':'/srv/iot-data/../x/releases/serve-jenkins-7/runtime/ab-ledger'},{'node_hostname':'../node'}]:
        arguments=dict(pair=pair,baseline=LEGACY,baseline_url='http://10.53.0.10',candidate_url='http://10.53.0.11',
            reference_raw=json.dumps({'lineage':{'protocol_sha256':LEGACY['protocol_sha256']}}),template=template,ledger_path=path,node_hostname='iot-cw-vm')
        arguments.update(kwargs)
        with pytest.raises(ValueError):ab_ci.deployment_plan(**arguments)


def test_trial_cohorts_use_only_operating_machines_and_bound_repetitions(tmp_path):
    'Verify trial cohorts use only operating machines and bound repetitions.'
    fixture=ProtocolFixture(tmp_path);context=deepcopy(fixture.runtime.context);context['machines']['P-001']['status']='maintenance'
    jobs=ab_ci.trial_jobs(context,fixture.runtime.experiment)
    assert len(jobs)==199*4 and len({j['request_id'] for j in jobs})==len(jobs) and all(j['machine_id']!='P-001' for j in jobs)
    for bad in [True,0,5]:
        with pytest.raises(ValueError):ab_ci.trial_jobs(context,'exp',bad)
    context['machines']={'P-001':context['machines']['P-001']}
    with pytest.raises(ValueError):ab_ci.trial_jobs(context,'exp')
    with pytest.raises(ValueError):ab_ci.trial_jobs(fixture.runtime.context,fixture.runtime.experiment,1)


def test_exact_metric_series_validation():
    'Verify exact metric series validation.'
    assert ab_ci.metric_value('x{variant="baseline"} 3\n','x',{'variant':'baseline'})==3
    assert ab_ci.metric_value('x 1\n','missing',{},True)==0
    for raw in ['x{variant="other"} 3\n','x{variant="baseline"} -1\n','x{variant="baseline"} NaN\n',
            'x{variant="baseline"} +Inf\n','x{variant="baseline"} 3\nx{variant="baseline"} 4\n']:
        with pytest.raises(ValueError):ab_ci.metric_value(raw,'x',{'variant':'baseline'})


def test_actual_ledger_probe_matches_independent_inputs_and_accepts_background_counter_activity(tmp_path):
    'Verify actual ledger probe matches independent inputs and accepts background counter activity.'
    fixture=ProtocolFixture(tmp_path);fixture.fault='background';result=fixture.probe()
    assert result['cohort_counts']=={'baseline':186,'candidate':214} and result['replay_checks']==40
    assert result['predictor_counter_deltas']=={'baseline':189.,'candidate':217.}
    assert len(result['client_observations'])==400 and len(result['readback']['observations'])==400


@pytest.mark.parametrize('fault',['ready','existing','failed','trial','ledger','counter','reference','scrape','upstream','replay'])
def test_probe_refuses_wrong_identity_receipts_metrics_or_replay(tmp_path,fault):
    'Verify probe refuses wrong identity receipts metrics or replay.'
    fixture=ProtocolFixture(tmp_path);fixture.fault=fault
    with pytest.raises(ValueError):fixture.probe()


def test_probe_refuses_a_feature_trace_that_disagrees_with_offline_data(tmp_path):
    'Verify probe refuses a feature trace that disagrees with offline data.'
    fixture=ProtocolFixture(tmp_path)
    for values in fixture.offline.values():values['features']['temperature_mean_10m']+=1
    with pytest.raises(ValueError,match='feature trace'):fixture.probe()


def native_fixture():
    """Supply explicit Kubernetes protocol fixtures; this never represents a deployment or native observation."""
    plan=recipe();values=plan['values'];uid='pvc-fixture';objects={}
    deployment={'metadata':{'uid':'deployment-fixture','generation':1},'status':{'observedGeneration':1,'replicas':1,'updatedReplicas':1,'readyReplicas':1},
        'spec':{'template':{'spec':{'containers':[{'name':'api','image':plan['image'],'env':deepcopy(values['env']),'volumeMounts':deepcopy(values['volumeMounts'])}],
            'initContainers':deepcopy(values['initContainers']),'volumes':deepcopy(values['volumes'])}}}}
    for volume in deployment['spec']['template']['spec']['volumes']:
        if 'configMap' in volume:volume['configMap']['defaultMode']=420
    objects['deployment']=deployment
    objects['pods']={'items':[{'metadata':{'uid':'pod-fixture'},'spec':{'containers':[{'name':'api','image':plan['image']}]},'status':{
        'containerStatuses':[{'name':'api','imageID':'containerd://sha256:'+'f'*64,'containerID':'containerd://fixture','restartCount':0,'ready':True,'state':{'running':{}}}]}}]}
    for obj in plan['objects'][:2]:objects[obj['metadata']['name']]={**deepcopy(obj),'metadata':{**obj['metadata'],'uid':obj['metadata']['name']+'-fixture'}}
    objects['persistentvolume']=deepcopy(plan['objects'][2]);objects['persistentvolume']['metadata']['uid']='pv-fixture';objects['persistentvolume']['status']={'phase':'Bound'}
    objects['persistentvolume']['spec']['claimRef']={'uid':uid};objects['persistentvolume']['spec'].pop('storageClassName')
    objects['persistentvolumeclaim']=deepcopy(plan['objects'][3]);objects['persistentvolumeclaim']['metadata']['uid']=uid;objects['persistentvolumeclaim']['status']={'phase':'Bound'}
    objects['service']={'metadata':{'uid':'service-fixture'}}
    def get(*args):'Return a copied Kubernetes fixture selected by resource name.';return deepcopy(objects[args[2] if args[1]=='configmap' else args[1]])
    context=NS(get=get,service_url=lambda *args:'http://10.53.0.12');build={'images':{'ab':{'config_digest':'sha256:'+'f'*64}}}
    return context,plan,build,objects


def test_native_state_accepts_server_defaults_but_rejects_stale_rollout_or_changed_inputs():
    'Verify native state accepts server defaults but rejects stale rollout or changed inputs.'
    context,plan,build,objects=native_fixture();state=ab_native.workload_state(context,plan,build)
    assert state['pvc_uid']=='pvc-fixture' and state['deployment_uid']=='deployment-fixture'
    original=deepcopy(objects)
    changes=[lambda x:x['deployment']['status'].update(readyReplicas=0),lambda x:x['deployment']['status'].update(observedGeneration=0),
        lambda x:x['deployment']['spec']['template']['spec']['containers'][0].update(image='other'),
        lambda x:x['deployment']['spec']['template']['spec']['volumes'][0]['configMap'].update(defaultMode=511),
        lambda x:x['deployment']['spec']['template']['spec']['volumes'][4]['persistentVolumeClaim'].update(readOnly=True),
        lambda x:x[plan['objects'][0]['metadata']['name']]['data'].update({'config.json':'changed'}),
        lambda x:x['persistentvolumeclaim']['status'].update(phase='Pending'),
        lambda x:x['persistentvolume']['spec']['local'].update(path='/outside'),
        lambda x:x['persistentvolume']['spec']['claimRef'].update(uid='other')]
    for change in changes:
        objects.clear();objects.update(deepcopy(original));change(objects)
        with pytest.raises(ValueError):ab_native.workload_state(context,plan,build)


def test_native_recheck_rejects_old_or_cross_release_receipts_before_querying_cluster(tmp_path):
    'Verify native recheck rejects old or cross release receipts before querying cluster.'
    context=NS(out=tmp_path,commit='a'*40,release='serve-jenkins-7')
    report={'checked_utc':(datetime.now(timezone.utc)-timedelta(seconds=301)).isoformat(),'capture_kind':'native-kubernetes-http',
        'commit':context.commit,'release':context.release,'ab_plan_sha256':'a'*64}
    with pytest.raises(ValueError,match='stale'):ab_native.recheck(context,{}, {},{},report)
    report['checked_utc']=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError,match='stale'):ab_native.recheck(context,{}, {},{},report)


def test_empty_native_pod_inventory_cannot_bind_a_counter_process():
    'Verify empty native pod inventory cannot bind a counter process.'
    with pytest.raises(ValueError):ab_native.pod_identity({'items':[]})
    with pytest.raises(ValueError):ab_native.pod_identity({'items':[{'metadata':{'uid':'terminating','deletionTimestamp':'now'}}]})


def test_model_readback_checks_actual_weights_service_selector_and_running_incarnation():
    'Verify model readback checks actual weights service selector and running incarnation.'
    objects={'inferenceservice':{'metadata':{'uid':'isvc-fixture'},'status':{'conditions':[{'type':'Ready','status':'True'}]},
        'spec':{'predictor':{'containers':[{'image':'model:v1'}]}}},'service':{'metadata':{'uid':'service-fixture'},'spec':{'selector':{'app':'isvc.old-predictor'}}},
        'pods':{'items':[{'metadata':{'uid':'model-pod-fixture','name':'old-predictor-aaa','labels':{'serving.kserve.io/inferenceservice':'old'}},
            'spec':{'nodeName':'node-fixture','containers':[{'name':'kserve-container','image':'model:v1'}]},
            'status':{'containerStatuses':[{'name':'kserve-container','imageID':'containerd://sha256:'+'f'*64,'containerID':'containerd://model-fixture',
                'restartCount':0,'ready':True,'state':{'running':{}}}]}}]}}
    calls=[]
    def get(*args):'Record Kubernetes reads and return the selected copied fixture.';calls.append(args);return deepcopy(objects[args[1]])
    context=NS(k=['kubectl-fixture'],get=get,service_url=lambda *args:'http://10.53.0.10')
    result={'metadata':LEGACY,'portable_sha256':LEGACY['portable_sha256'],'bundle_path':'/app/models/pump-failure-v1','bundle_origin':'IOT_MODEL_BUNDLE'}
    with patch.object(ab_native.subprocess,'check_output',return_value=json.dumps(result)) as command:
        state=ab_native.model_state(context,'old')
        assert state['pin']==LEGACY and state['node_name']=='node-fixture' and state['isvc_uid']=='isvc-fixture'
        assert 'app=isvc.old-predictor' in calls[-1] and 'exec' in command.call_args.args[0]
        assert state['bundle_locations']=={'model-pod-fixture':{'path':'/app/models/pump-failure-v1','origin':'IOT_MODEL_BUNDLE'}}
    with patch.object(ab_native.subprocess,'check_output',return_value=json.dumps({**result,'portable_sha256':'a'*64})):
        with pytest.raises(ValueError,match='weights'):ab_native.model_state(context,'old')
    original=deepcopy(objects)
    for change in [lambda x:x['inferenceservice']['status']['conditions'][0].update(status='False'),
            lambda x:x['service']['spec'].update(selector={}),lambda x:x['pods'].update(items=[]),
            lambda x:x['pods']['items'][0]['status']['containerStatuses'][0].update(imageID='unbound'),
            lambda x:x['pods']['items'][0]['metadata']['labels'].update({'serving.kserve.io/inferenceservice':'other'})]:
        objects.clear();objects.update(deepcopy(original));change(objects)
        with patch.object(ab_native.subprocess,'check_output',return_value=json.dumps(result)),pytest.raises(ValueError):ab_native.model_state(context,'old')


@pytest.mark.parametrize('location',['default','models/pump-failure-v1','model'])
def test_native_model_probe_executes_both_observed_image_layouts(tmp_path,monkeypatch,capsys,location):
    """Execute the real Pod probe against actual portable model files in a declared application fixture."""
    import services.predictor.app as predictor_app
    app_root=tmp_path/'app';app_root.mkdir()
    selected=app_root/('models/pump-failure-v1' if location=='default' else location)
    selected.mkdir(parents=True)
    for name in ['portable.json','lineage.json','model-pin.json']:
        shutil.copyfile(ROOT/'models/pump-failure-v1'/name,selected/name)
    monkeypatch.chdir(app_root);monkeypatch.setattr(predictor_app,'__file__',str(app_root/'services/predictor/app.py'))
    if location=='default':monkeypatch.delenv('IOT_MODEL_BUNDLE',raising=False)
    else:monkeypatch.setenv('IOT_MODEL_BUNDLE',str(selected))
    exec(ab_native.model_probe_program(),{})
    result=json.loads(capsys.readouterr().out)
    assert {n:result['metadata'][n] for n in LEGACY}==LEGACY
    assert result['portable_sha256']==LEGACY['portable_sha256'] and Path(result['bundle_path'])==selected.resolve()
    assert result['bundle_origin']==('application-default' if location=='default' else 'IOT_MODEL_BUNDLE')


def test_native_model_probe_refuses_escaped_or_missing_explicit_bundle(tmp_path,monkeypatch):
    """A bad deployment must not fall back to another bundle or read a path outside the running application."""
    import services.predictor.app as predictor_app
    app_root=tmp_path/'app';app_root.mkdir();monkeypatch.chdir(app_root)
    monkeypatch.setattr(predictor_app,'__file__',str(app_root/'services/predictor/app.py'))
    monkeypatch.setenv('IOT_MODEL_BUNDLE',str(tmp_path/'elsewhere'))
    with pytest.raises(ValueError,match='escaped'):exec(ab_native.model_probe_program(),{})
    monkeypatch.setenv('IOT_MODEL_BUNDLE',str(app_root/'absent'))
    with pytest.raises(FileNotFoundError):exec(ab_native.model_probe_program(),{})


def test_feature_routing_identity_tracks_the_observed_active_generation():
    'Verify feature routing identity tracks the observed active generation.'
    deployment={'metadata':{'uid':'active-fixture','generation':4},'spec':{'template':{'spec':{'containers':[{'name':'api',
        'env':[{'name':'IOT_KSERVE_URL','value':'http://old-predictor.iot-serving.svc.cluster.local'}]}]}}}}
    context=NS(get=lambda *args:deployment)
    assert ab_native.feature_identity(context)=={'uid':'active-fixture','generation':4,'isvc_name':'old'}


def test_native_adapter_command_sequence_and_recheck_with_mocked_control_plane(tmp_path):
    """Exercise adapter orchestration, not Kubernetes deployment; all cluster operations and ownership changes are mocked."""
    fixture=ProtocolFixture(tmp_path/'source');result=fixture.probe();pair=pair_plan();pair['pin']=fixture.runtime.pins['candidate']
    source=tmp_path/'release';out=source/'outputs';out.mkdir(parents=True);state=source/'runtime/state';state.mkdir(parents=True)
    (state/'context.json').write_text(json.dumps(fixture.runtime.context))
    reference=json.dumps({'format':'iot-drift-reference-v1','lineage':fixture.runtime.detector.lineage,'policy':fixture.runtime.detector.policy,
        'samples':[GOLDEN['samples'][0]['features']]*64})
    logs=[];context=NS(directory=source,out=out,release=pair['release'],commit=pair['commit'],helm='helm-fixture',k=['kubectl-fixture'])
    context.log=lambda name,args,*other:logs.append((name,args))
    def get(*args):
        'Return only the source-reference ConfigMap or the expected node identity.'
        if args[1]=='configmap':return {'metadata':{'uid':'source-reference-fixture'},'data':{'reference.json':reference}}
        return {'metadata':{'labels':{'kubernetes.io/hostname':'iot-cw-vm'}}}
    context.get=get
    baseline={'name':'old','pin':LEGACY,'isvc_uid':'baseline-fixture','node_name':'node-fixture','url':'http://10.53.0.10'}
    candidate={'name':pair['isvc']['metadata']['name'],'pin':pair['pin'],'isvc_uid':'candidate-fixture','node_name':'node-fixture',
        'url':'http://10.53.0.11','config_digest':'sha256:'+'f'*64}
    active={'uid':'active-feature-fixture','generation':1,'isvc_name':'old'}
    models=lambda c,name:baseline if name=='old' else candidate
    original_plan=ab_ci.deployment_plan
    def planned(*args):
        'Rebind the fixture ledger path to the expected host release path before planning.'
        values=list(args);assert values[-2]==str(context.directory/'runtime/ab-ledger')
        values[-2]='/srv/iot-data/workspace/iot-maintenance-platform/data/ci/releases/'+pair['release']+'/runtime/ab-ledger'
        return original_plan(*values)
    def output(command,**kwargs):'Return empty Helm history or empty command output for the controlled deployment fixture.';return '[]' if command[0]=='helm-fixture' else ''
    def probed(*args):
        # A real fixture SQLite file exercises read-only retention checks; cluster mounts remain mocked.
        'Retain a real SQLite ledger while keeping the Kubernetes mounts mocked.'
        shutil.copyfile(fixture.runtime.database,context.directory/'runtime/ab-ledger/trials.sqlite')
        return result
    def healthy(c,pin,machines,http,label):
        'Record a fixture health receipt for the deployment orchestration test.'
        ab_native.save(c.out/('baseline-health-'+label+'.json'),{'fixture':True});return {'fixture':True}
    build={'images':{'predictor':{'config_digest':'sha256:'+'f'*64}}};stage={'isvc_uid':'candidate-fixture'}
    with patch.object(ab_native,'model_state',side_effect=models),patch.object(ab_native,'feature_identity',return_value=active),\
            patch.object(ab_native,'pin_scaling') as pinned,\
            patch.object(ab_native,'baseline_health',side_effect=healthy),\
            patch.object(ab_native,'workload_state',return_value={'url':'http://10.53.0.12','uid':'ab-fixture'}),\
            patch.object(ab_native,'offline_lookup',return_value=fixture.offline),patch.object(ab_ci,'deployment_plan',side_effect=planned),\
            patch.object(ab_ci,'probe',side_effect=probed),patch.object(ab_native.subprocess,'check_output',side_effect=output),\
            patch.object(ab_native.os,'chown',create=True) as ownership:
        report=ab_native.run(context,ROOT,pair,stage,build,None,None)
        pinned.assert_called_once_with(context,['old',pair['isvc']['metadata']['name']])
        assert report['staged_isvc_uid']=='candidate-fixture' and report['baseline_pin']==LEGACY
        ownership.assert_called_once_with(source/'runtime/ab-ledger',10001,10001)
        assert [name for name,_ in logs]==['ab-create','ab-lint','ab-deploy']
        assert '--atomic' in logs[-1][1] and 'iot-feature-api' not in logs[-1][1]
        ab_native.recheck(context,pair,stage,build,report)
        with patch.object(ab_native,'model_state',side_effect=lambda c,name:baseline if name=='old' else {**candidate,'isvc_uid':'other'}):
            with pytest.raises(ValueError,match='candidate identity'):ab_native.run(context,ROOT,pair,stage,build,None,None)
        with patch.object(ab_native,'model_state',side_effect=lambda c,name:baseline if name=='old' else {**candidate,'node_name':'other'}):
            with pytest.raises(ValueError,match='single coursework node'):ab_native.run(context,ROOT,pair,stage,build,None,None)
        with patch.object(ab_native.subprocess,'check_output',return_value='existing-resource'):
            with pytest.raises(ValueError,match='Preserve existing'):ab_native.run(context,ROOT,pair,stage,build,None,None)
        with patch.object(ab_native.subprocess,'check_output',side_effect=lambda command,**kw:'[{"name":"existing"}]' if command[0]=='helm-fixture' else ''):
            with pytest.raises(ValueError,match='Helm release'):ab_native.run(context,ROOT,pair,stage,build,None,None)
        with patch.object(ab_native,'feature_identity',return_value={**active,'generation':2}):
            with pytest.raises(ValueError,match='changed'):ab_native.recheck(context,pair,stage,build,report)
        (out/'ab-plan.json').write_text('{}')
        with pytest.raises(ValueError,match='stale'):ab_native.recheck(context,pair,stage,build,report)
        # A new temporary fixture workspace checks mid-experiment changes without deleting the retained first ledger.
        context.directory=tmp_path/'second-release';context.out=context.directory/'outputs';context.out.mkdir(parents=True)
        state=context.directory/'runtime/state';state.mkdir(parents=True);(state/'context.json').write_text(json.dumps(fixture.runtime.context))
        with patch.object(ab_native,'workload_state',side_effect=[{'url':'http://10.53.0.12','uid':'initial'},{'url':'http://10.53.0.12','uid':'replaced'}]):
            with pytest.raises(ValueError,match='changed during'):ab_native.run(context,ROOT,pair,stage,build,None,None)
        assert json.loads((context.out/'ab-initial-identities.json').read_text())['workload']['uid']=='initial'
        assert json.loads((context.out/'ab-final-identities.json').read_text())['workload']['uid']=='replaced'


class ScalingFixture:
    """Model an asynchronous Kubernetes reconciliation boundary without calling a live cluster."""
    def __init__(self,path):
        'Prepare isolated model replica states and patch-failure controls.'
        self.out=path;self.commit='a'*40;self.release='serve-jenkins-7';self.k=['kubectl-fixture'];self.logs=[]
        self.models={n:{'metadata':{'uid':n+'-uid'},'spec':{'predictor':{'minReplicas':1,'maxReplicas':2}}} for n in ['old','new']}
        self.pending=False;self.fail_patch=None
    def get(self,*args):
        """Expose policy reconciliation separately from ready and observed replica counts."""
        _,kind,name,*_=args;model=name.removesuffix('-predictor')
        if kind=='inferenceservice':return deepcopy(self.models[model])
        policy=self.models[model]['spec']['predictor'];replicas=2 if self.pending else 1
        if kind=='hpa':return {'metadata':{'uid':model+'-hpa'},'spec':{**policy,'scaleTargetRef':{'kind':'Deployment','name':name}},
            'status':{'currentReplicas':replicas,'desiredReplicas':replicas}}
        assert kind=='deployment'
        return {'metadata':{'uid':model+'-deployment','name':name,'generation':2},'spec':{'replicas':replicas},
            'status':{'observedGeneration':2,'replicas':replicas,'readyReplicas':replicas,'updatedReplicas':replicas}}
    def log(self,label,command):
        """Enforce JSON-patch tests against the original UID and fail one declared patch if requested."""
        assert (self.out/'ab-scaling-original.json').exists(),'Journal must precede the first policy mutation'
        self.logs.append((label,command));name=command[3]
        if self.fail_patch==name:raise RuntimeError('Declared patch failure')
        obj=self.models[name]
        for op in json.loads(command[-1]):
            if op['path']=='/metadata/uid':assert op['op']=='test' and obj['metadata']['uid']==op['value'];continue
            key=op['path'].split('/')[-1]
            if op['op']=='test':assert obj['spec']['predictor'][key]==op['value']
            else:assert op['op']=='replace';obj['spec']['predictor'][key]=op['value']


def test_two_model_scaling_waits_for_observed_replicas_and_restores_original_policy(tmp_path):
    'Verify two model scaling waits for observed replicas and restores original policy.'
    c=ScalingFixture(tmp_path);original=deepcopy(c.models);c.pending=True
    def reconciled(_):'Mark the fixture model reconciliation as complete.';c.pending=False
    with patch.object(ab_native.time,'sleep',side_effect=reconciled) as waited:
        ab_native.pin_scaling(c,['old','new']);waited.assert_called_once_with(2)
    assert all(x['spec']['predictor']=={'minReplicas':1,'maxReplicas':1} for x in c.models.values())
    journal=(tmp_path/'ab-scaling-original.json').read_bytes()
    with pytest.raises(ValueError,match='Preserve'):ab_native.pin_scaling(c,['old','new'])
    ab_native.restore_scaling(c)
    assert c.models==original and (tmp_path/'ab-scaling-original.json').read_bytes()==journal
    restored=json.loads((tmp_path/'ab-scaling-restored.json').read_text());assert restored['passed']
    assert all(s['hpa']['spec']['maxReplicas']==2 for s in restored['states'].values())


def test_partially_applied_scaling_is_recoverable_without_replacing_original_journal(tmp_path):
    'Verify partially applied scaling is recoverable without replacing original journal.'
    c=ScalingFixture(tmp_path);original=deepcopy(c.models);c.fail_patch='new'
    with pytest.raises(RuntimeError):ab_native.pin_scaling(c,['old','new'])
    assert c.models['old']!=original['old'] and c.models['new']==original['new']
    c.fail_patch=None;ab_native.restore_scaling(c);assert c.models==original
    assert [n for n,_ in c.logs if n.startswith('ab-scale-restore')]==['ab-scale-restore-old']


@pytest.mark.parametrize('conflict',['uid','policy','journal'])
def test_scaling_restore_refuses_operator_changes_before_mutating_either_model(tmp_path,conflict):
    'Verify scaling restore refuses operator changes before mutating either model.'
    c=ScalingFixture(tmp_path);ab_native.pin_scaling(c,['old','new']);before=len(c.logs)
    if conflict=='uid':c.models['new']['metadata']['uid']='replacement-uid'
    elif conflict=='policy':c.models['new']['spec']['predictor']={'minReplicas':2,'maxReplicas':2}
    else:
        path=tmp_path/'ab-scaling-original.json';value=json.loads(path.read_text());value['release']='serve-jenkins-8';path.write_text(json.dumps(value))
    with pytest.raises(ValueError):ab_native.restore_scaling(c)
    assert len(c.logs)==before and not (tmp_path/'ab-scaling-restored.json').exists()


def test_scaling_timeout_keeps_diagnostics_and_restore_is_available(tmp_path):
    'Verify scaling timeout keeps diagnostics and restore is available.'
    c=ScalingFixture(tmp_path);original=deepcopy(c.models);c.pending=True
    with patch.object(ab_native.time,'monotonic',side_effect=[0,181]),patch.object(ab_native.time,'sleep') as slept:
        with pytest.raises(ValueError,match='180seconds'):ab_native.pin_scaling(c,['old','new'])
        slept.assert_not_called()
    assert (tmp_path/'ab-scaling-timeout.json').exists() and not (tmp_path/'ab-scaling-pinned.json').exists()
    ab_native.restore_scaling(c);assert c.models==original


@pytest.mark.parametrize('policy',[{'minReplicas':0,'maxReplicas':2},{'minReplicas':2,'maxReplicas':1},
    {'minReplicas':1,'maxReplicas':3},{'minReplicas':True,'maxReplicas':2}])
def test_scaling_never_expands_the_existing_single_node_replica_budget(tmp_path,policy):
    'Verify scaling never expands the existing single node replica budget.'
    c=ScalingFixture(tmp_path);c.models['new']['spec']['predictor']=policy
    with pytest.raises(ValueError,match='Unexpected'):ab_native.pin_scaling(c,['old','new'])
    assert not c.logs and not (tmp_path/'ab-scaling-original.json').exists()


def test_restore_without_a_scaling_journal_does_not_query_or_change_cluster(tmp_path):
    'Verify restore without a scaling journal does not query or change cluster.'
    c=NS(out=tmp_path);ab_native.restore_scaling(c)


@pytest.mark.parametrize('fault',[None,'not-ready','cohort','expired-context','pin','trace','ttl','key-count','cache-url'])
def test_fallback_health_requires_both_live_cohort_and_real_physical_cache_reserve(tmp_path,monkeypatch,fault):
    """A new context timestamp must not conceal a cache whose original TTL has expired."""
    machines={'P-001':{},'P-002':{}};prepared=(datetime.now(timezone.utc)-timedelta(seconds=100)).isoformat()
    trace={'clock_mode':'simulation','protocol_sha256':LEGACY['protocol_sha256'],'prepared_wall_utc':prepared,
        'operating_context':{'status':'operating'},'features':GOLDEN['samples'][0]['features']}
    traces={m:{**deepcopy(trace),'machine_id':m} for m in machines}
    if fault=='expired-context':traces['P-002']['prepared_wall_utc']=(datetime.now(timezone.utc)-timedelta(seconds=2701)).isoformat()
    if fault=='cohort':traces['P-002']['protocol_sha256']='0'*64
    backend=fixture_runtime(tmp_path/'model-fixture').runtimes['baseline'].backend
    prediction=backend.predict(trace['features']);reply={'model':prediction['model'],'prediction':prediction['predictions'][0],'feature_trace':traces['P-001']}
    if fault=='pin':reply['model']={**reply['model'],'version':'other'}
    if fault=='trace':reply['feature_trace']={**traces['P-001'],'machine_id':'P-999'}
    def http(url,path,body=None):
        'Return controlled readiness, prediction and feature responses for health fault partitions.'
        if path=='/health/ready':return (503,{'ready':False}) if fault=='not-ready' else (200,{'ready':True})
        if path.endswith(':predict'):return 200,reply
        return 200,traces[path.split('/')[3]]
    cache=NS(scan_iter=lambda **kw:iter([b'1iot_serving_demo'] if fault=='key-count' else [b'1iot_serving_demo',b'2iot_serving_demo']),
        pttl=lambda key:899_999 if fault=='ttl' else 3_500_000,close=lambda:None)
    redis=NS(Redis=lambda **kw:cache);monkeypatch.setitem(sys.modules,'redis',redis)
    deployment={'metadata':{'uid':'active-fixture','generation':1},'spec':{'template':{'spec':{'containers':[{'name':'api','env':[
        {'name':'IOT_FEAST_REDIS','value':'online.iot-features.svc.cluster.local:6379' if fault!='cache-url' else 'evil.example:6379'}]}]}}}}
    c=NS(out=tmp_path,commit='a'*40,release='serve-jenkins-7',get=lambda *args:deployment,service_url=lambda *args:'http://10.53.0.5')
    if fault:
        with pytest.raises(ValueError):ab_native.baseline_health(c,LEGACY,machines,http,'before-ab')
        assert not (tmp_path/'baseline-health-before-ab.json').exists()
    else:
        proof=ab_native.baseline_health(c,LEGACY,machines,http,'before-ab')
        assert proof['passed'] and proof['cache_keys']==2 and len(proof['features'])==2 and proof['cache_ttl_ms']==[3_500_000]*2


def test_controller_json_and_metrics_transport_has_an_actual_body_bound():
    'Verify controller json and metrics transport has an actual body bound.'
    spec=importlib.util.spec_from_file_location('ab_ci_transport_test',ROOT/'scripts/ci-serving.py');driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    limit=4_194_304
    assert driver.json_response(io.BytesIO(b' '*(limit-2)+b'{}'))=={}
    with pytest.raises(ValueError,match='4MiB'):driver.json_response(io.BytesIO(b' '*(limit-1)+b'{}'))
    reply=io.BytesIO(b'{"ok":true}');reply.status=200
    with patch.object(driver,'urlopen',return_value=reply) as request:
        assert driver.http('http://service','/predict',{}, {'X-Request-ID':'fixed'})==(200,{'ok':True})
        assert request.call_args.args[0].get_header('X-request-id')=='fixed'
    with patch.object(driver,'urlopen',side_effect=HTTPError('http://service',503,'unavailable',{},io.BytesIO(b'{"detail":"failed"}'))):
        assert driver.http('http://service','/predict')[0]==503
    with patch.object(driver,'urlopen',return_value=io.BytesIO(b'x'*limit)):assert len(driver.http_text('http://service','/metrics'))==limit
    with patch.object(driver,'urlopen',return_value=io.BytesIO(b'x'*(limit+1))):
        with pytest.raises(ValueError,match='4MiB'):driver.http_text('http://service','/metrics')


def test_readonly_hdd_ledger_verifies_trial_rows_and_identity_without_modifying_database(tmp_path):
    'Verify readonly hdd ledger verifies trial rows and identity without modifying database.'
    fixture=ProtocolFixture(tmp_path);result=fixture.probe();database=Path(fixture.runtime.database);before=database.read_bytes()
    state=ab_native.ledger_state(database,result['readback'],fixture.runtime.context)
    assert state['rows']==400 and database.read_bytes()==before
    wrong=deepcopy(result['readback']);wrong['observations'].pop()
    with pytest.raises(ValueError,match='retained ledger'):ab_native.ledger_state(database,wrong,fixture.runtime.context)
    with pytest.raises(ValueError,match='retained ledger'):ab_native.ledger_state(database,result['readback'],{**fixture.runtime.context,'as_of':'other'})
