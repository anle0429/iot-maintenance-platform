"""Protect production selection, paired rollout and strict CI gates with known fixtures and actual manifest templates."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import base64,importlib.util,io,json,subprocess,sys,tarfile,unittest
from pathlib import Path,PurePosixPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch
import yaml
from ml import serving_ci as ci
from services.predictor.binding import LEGACY

ROOT=Path(__file__).resolve().parents[1]


def pin():
    """Represent an explicitly authorized fixture release, not an actual registry promotion."""
    return {**LEGACY,'version':'2','production':True}


def plan():
    """Parse the real templates rather than mirroring their structure in a second implementation."""
    model=next(x for x in yaml.safe_load_all((ROOT/'infra/kserve/pump-failure.yaml').read_text()) if x['kind']=='InferenceService')
    redis=[x for x in yaml.safe_load_all((ROOT/'infra/feature-api/redis.yaml').read_text()) if x['kind']!='Namespace']
    values=yaml.safe_load((ROOT/'infra/feature-api/helm-values.yaml').read_text())
    return ci.deployment_plan('7','a'*40,pin(),model,redis,values)


def gate(killed=81):
    """Make transparent gate counts and a complete all-mutant inventory across every allowed module."""
    coverage={'totals':{'num_statements':100,'covered_lines':91,'num_branches':100,'covered_branches':91,'excluded_lines':0}}
    stats=dict(killed=killed,survived=100-killed,total=100,no_tests=0,skipped=0,suspicious=0,timeout=0,check_was_interrupted_by_user=0,segfault=0)
    entries=[]
    for i in range(100):
        module=sorted(ci.MUTATION_FILES)[i%len(ci.MUTATION_FILES)].removesuffix('.py').replace('/','.')
        entries.append('    '+module+'.x_probe__mutmut_'+str(i)+': '+('killed' if i<killed else 'survived'))
    return coverage,stats,'\n'.join(entries)


class ServingCITests(unittest.TestCase):
    """Check observable failure boundaries, independent identities and compatible existing deployment templates."""
    def test_canonical_identities_and_daemon_workspace_mapping(self):
        self.assertEqual(ci.identity('7','a'*40,'2'),'serve-jenkins-7')
        for build,commit,version in [('0','a'*40,'2'),('01','a'*40,'2'),('../1','a'*40,'2'),('1','A'*40,'2'),('1','a'*40,'latest'),('1','a'*40,'02')]:
            with self.subTest(build=build,version=version),self.assertRaises(ValueError):ci.identity(build,commit,version)
        deploy='/srv/iot-data/workspace/iot-maintenance-platform'
        self.assertEqual(str(ci.host_source_path('/var/jenkins_home/workspace/iot-serving/build-7',deploy)),deploy+'/data/runtime/jenkins-home-27/workspace/iot-serving/build-7')
        self.assertEqual(str(ci.host_source_path(deploy+'/data/ci/source/x/tree',deploy)),deploy+'/data/ci/source/x/tree')
        for source in ['relative/tree','/var/jenkins_home/../outside']:
            with self.assertRaises(ValueError):ci.host_source_path(source,deploy)

    def test_real_session_resource_boundaries(self):
        now=datetime(2026,10,27,10,tzinfo=timezone.utc)
        self.assertTrue(ci.admission('2026-10-27T11:00:00Z',now,12*1024**3,[20_000_000_000]*2)['passed'])
        for stop,ram,disks in [('2026-10-27T10:59:59Z',12*1024**3,[20_000_000_000]*2),
                ('2026-10-27T11:00:00Z',12*1024**3-1,[20_000_000_000]*2),
                ('2026-10-27T11:00:00Z',12*1024**3,[19_999_999_999,20_000_000_000]),
                ('2026-10-27T11:00:00',12*1024**3,[20_000_000_000]*2),
                ('2026-10-27T11:00:00Z',12*1024**3,[20_000_000_000])]:
            with self.subTest(stop=stop,ram=ram),self.assertRaises(ValueError):ci.admission(stop,now,ram,disks)

    def test_strict_thresholds_and_every_mutant_are_required(self):
        self.assertEqual(ci.test_gate(*gate())['mutation_score_percent'],81)
        for value in [80,79]:
            with self.assertRaises(ValueError):ci.test_gate(*gate(value))
        for key,value in [('covered_lines',90),('covered_branches',90),('num_statements',0),('num_branches',0),('excluded_lines',1)]:
            coverage,stats,results=gate();coverage['totals'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):ci.test_gate(coverage,stats,results)
        for key in ['no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']:
            coverage,stats,results=gate();stats[key]=1;stats['total']+=1
            with self.subTest(key=key),self.assertRaises(ValueError):ci.test_gate(coverage,stats,results)
        for value in [False,-1,None]:
            coverage,stats,results=gate();stats['killed']=value
            with self.assertRaises(ValueError):ci.test_gate(coverage,stats,results)
        coverage,stats,results=gate()
        for changed in [results.rsplit('\n',1)[0],results+'\n'+results.splitlines()[0],results.replace('services.feature_api.app','services.drift_api.app')]:
            with self.assertRaises(ValueError):ci.test_gate(coverage,stats,changed)

    def test_model_and_feature_routes_switch_as_a_pair(self):
        value=plan();self.assertEqual(value,plan())
        self.assertEqual(value['isvc']['metadata']['name'],'iot-pump-serve-jenkins-7')
        self.assertNotEqual(value['isvc']['metadata']['name'],'iot-pump-failure-v1')
        self.assertTrue(value['pin']['production']);self.assertEqual(value['pin']['training_code_commit'],LEGACY['training_code_commit'])
        self.assertNotEqual(value['pin']['training_code_commit'],value['commit'])
        env={v['name']:v['value'] for v in value['feature_values']['env']}
        self.assertEqual(env['IOT_KSERVE_URL'],value['predictor_url'])
        self.assertEqual(env['IOT_FEAST_REDIS'],'online-serve-jenkins-7.iot-features.svc.cluster.local:6379')
        self.assertEqual(value['feature_values']['initContainers'][0]['image'],value['feature_image'])
        self.assertEqual(value['redis'][0]['spec']['selector']['matchLabels'],value['redis'][1]['spec']['selector'])
        self.assertEqual(value['redis'][1]['spec']['type'],'ClusterIP')
        self.assertEqual(value['feature_values']['volumes'][0]['configMap']['name'],value['seed_configmap'])

    def test_nonproduction_or_unexpected_resource_cannot_form_a_plan(self):
        value=plan();model=value['isvc'];redis=value['redis'];feature=value['feature_values']
        with self.assertRaises(ValueError):ci.deployment_plan('7','a'*40,LEGACY,model,redis,feature)
        bad=deepcopy(redis);bad[0]['kind']='Secret'
        with self.assertRaises(ValueError):ci.deployment_plan('7','a'*40,pin(),model,bad,feature)
        before=deepcopy((model,redis,feature));ci.deployment_plan('8','a'*40,pin(),model,redis,feature)
        self.assertEqual((model,redis,feature),before)

    def test_http_identity_and_exact_score_boundary(self):
        expected=pin();ci.check_prediction({'risk_score':.7,'predicted_failure':1},expected,expected)
        ci.check_prediction({'risk_score':.699,'predicted_failure':0},expected,expected)
        for name in expected:
            metadata={**expected,name:None}
            with self.subTest(name=name),self.assertRaises(ValueError):ci.check_prediction({'risk_score':.7,'predicted_failure':1},metadata,expected)
        for score,decision in [(True,1),(float('nan'),0),(float('inf'),1),(-.01,0),(1.01,1),(.7,0),(.7,True)]:
            with self.subTest(score=score),self.assertRaises(ValueError):ci.check_prediction({'risk_score':score,'predicted_failure':decision},expected,expected)

    def test_imported_config_and_real_ready_containers_are_required(self):
        digest='sha256:'+'a'*64;pod={'metadata':{'uid':'new-uid'},'spec':{'containers':[{'name':'api','image':'release:7'}]},
            'status':{'containerStatuses':[{'name':'api','ready':True,'imageID':'containerd://'+digest,'state':{'running':{}}}]}}
        self.assertEqual(ci.check_pods([pod],'release:7',digest,'api'),['new-uid'])
        for change in [lambda p:p['status']['containerStatuses'][0].update(ready=False),lambda p:p['status']['containerStatuses'][0].update(imageID='sha256:'+'b'*64),
                lambda p:p['spec']['containers'][0].update(image='wrong'),lambda p:p['status']['containerStatuses'][0].update(state={'waiting':{}})]:
            bad=deepcopy(pod);change(bad)
            with self.assertRaises(ValueError):ci.check_pods([bad],'release:7',digest,'api')
        with self.assertRaises(ValueError):ci.check_pods([],'release:7',digest,'api')
        terminated=deepcopy(pod);terminated['metadata']['deletionTimestamp']='now'
        with self.assertRaises(ValueError):ci.check_pods([terminated],'release:7',digest,'api')

    def test_immutable_seed_hashes_and_native_uid_cannot_be_swapped(self):
        raw=b'\x00SQLite fixture';hashes={'context.json':sha256(b'{}').hexdigest(),'registry.db':sha256(raw).hexdigest()}
        value={'metadata':{'uid':'seed-uid'},'immutable':True,'data':{'context.json':'{}'},'binaryData':{'registry.db':base64.b64encode(raw).decode()}}
        self.assertEqual(ci.check_seed_configmap(value,hashes),'seed-uid')
        for change in [lambda v:v.update(immutable=False),lambda v:v['metadata'].update(uid='different'),
                lambda v:v['data'].update(extra='x'),lambda v:v['data'].update(**{'context.json':'changed'}),
                lambda v:v['binaryData'].update(**{'registry.db':'invalid base64'})]:
            bad=deepcopy(value);change(bad)
            with self.assertRaises(ValueError):ci.check_seed_configmap(bad,hashes,'seed-uid')

    def test_full_cutover_requires_two_real_cohorts_and_monitoring_readbacks(self):
        """Use explicit synthetic receipts to test the gate, without claiming a live A/B experiment."""
        drift={'unique_rows':100,'reference_id':'r','features':{str(i):{'psi':0.0} for i in range(8)}}
        report={'passed':True,'candidate_pin':pin(),'baseline_pin':LEGACY,'staged_isvc_uid':'new-model-uid','ground_truth_used':False,
            'observations':[{'variant':'baseline' if i%2==0 else 'candidate','request_id':str(i),'status':200,'latency_ms':1.0,
                'prediction':{'risk_score':.5,'predicted_failure':0},'model':LEGACY if i%2==0 else pin()} for i in range(200)],
            'metrics':{'baseline':{'requests':100,'failures':0},'candidate':{'requests':100,'failures':0}},
            'drift':{'baseline':deepcopy(drift),'candidate':deepcopy(drift)}}
        self.assertEqual(ci.ab_gate(report,pin(),'new-model-uid'),{'baseline':100,'candidate':100})
        for change in [lambda r:r.update(passed=False),lambda r:r.update(candidate_pin=LEGACY),lambda r:r.update(baseline_pin=pin()),
                lambda r:r.update(staged_isvc_uid='old'),lambda r:r.update(ground_truth_used=True),
                lambda r:r['observations'][0].update(status=503),lambda r:r['observations'][0].update(latency_ms=float('nan')),
                lambda r:r['observations'][0].update(request_id='1'),lambda r:r['observations'][0].update(variant='unknown'),
                lambda r:r['observations'].pop(),lambda r:r['metrics']['baseline'].update(requests=99),
                lambda r:r['drift']['candidate'].update(unique_rows=31),lambda r:r['drift']['candidate']['features']['0'].update(psi=-1)]:
            bad=deepcopy(report);change(bad)
            with self.assertRaises(ValueError):ci.ab_gate(bad,pin(),'new-model-uid')

    def test_rollout_requires_paired_images_env_seed_and_complete_generation(self):
        value=plan();f=value['feature_values']
        deployment={'metadata':{'generation':3},'status':{'observedGeneration':3,'replicas':1,'updatedReplicas':1,'readyReplicas':1},
            'spec':{'template':{'spec':{'containers':[{'name':'api','image':value['feature_image'],'env':deepcopy(f['env'])}],
                'initContainers':deepcopy(f['initContainers']),'volumes':deepcopy(f['volumes'])}}}}
        ci.check_deployment(deployment,value)
        for change in [lambda d:d['status'].update(observedGeneration=2),lambda d:d['status'].update(readyReplicas=0),
                lambda d:d['status'].update(updatedReplicas=0),lambda d:d['status'].update(unavailableReplicas=1),
                lambda d:d['spec']['template']['spec']['containers'][0].update(image='old'),
                lambda d:d['spec']['template']['spec']['initContainers'][0].update(image='old'),
                lambda d:d['spec']['template']['spec']['containers'][0]['env'][2].update(value='http://old-predictor'),
                lambda d:d['spec']['template']['spec']['volumes'][0]['configMap'].update(name='old-seed')]:
            bad=deepcopy(deployment);change(bad)
            with self.assertRaises(ValueError):ci.check_deployment(bad,value)

    def test_docker_save_config_is_checked_instead_of_an_unrelated_manifest_id(self):
        raw=b'{"architecture":"amd64","config":{"source":"release"}}';expected='sha256:'+sha256(raw).hexdigest()
        for path in ['config.json','blobs/sha256/'+sha256(raw).hexdigest()]:
            stream=io.BytesIO()
            with tarfile.open(fileobj=stream,mode='w') as archive:
                member=tarfile.TarInfo('layers');member.type=tarfile.DIRTYPE;archive.addfile(member)
                large=tarfile.TarInfo('layer.tar');large.size=1_000_001;archive.addfile(large,io.BytesIO(b'0'*large.size))
                for name,value in [(path,raw),('manifest.json',json.dumps([{'Config':path}]).encode())]:
                    member=tarfile.TarInfo(name);member.size=len(value);archive.addfile(member,io.BytesIO(value))
            stream.seek(0);self.assertEqual(ci.docker_config_digest(stream),expected)
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w'):pass
        stream.seek(0)
        with self.assertRaises(ValueError):ci.docker_config_digest(stream)

    def test_native_jenkins_driver_imports_with_stdlib_only(self):
        completed=subprocess.run([sys.executable,'-S',str(ROOT/'scripts/ci-serving.py'),'--help'],capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertIn('select-host',completed.stdout)

    def test_sdk_control_mounts_host_source_readonly_and_kubeconfig_only_for_cluster_calls(self):
        """Inspect the actual argument builder without starting Docker or touching a cluster."""
        spec=importlib.util.spec_from_file_location('serving_ci_driver_test',ROOT/'scripts/ci-serving.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with TemporaryDirectory() as folder:
            context=module.Context.__new__(module.Context);context.out=Path(folder)
            (context.out/'controller.json').write_text('{"id":"sha256:fixture-controller"}')
            context.release='serve-jenkins-7';context.deploy=PurePosixPath('/srv/iot-data/workspace/iot-maintenance-platform')
            context.directory=context.deploy/'data/ci/releases/serve-jenkins-7';context.host_root=context.deploy/'data/runtime/jenkins-home-27/workspace/iot-serving/build-7'
            context.helm=str(context.deploy/'data/runtime/kubernetes-assets-27/helm/helm');calls=[]
            context.container=lambda *args:calls.append(args)
            context.control('select-host')
            self.assertNotIn('type=bind,src=/etc/rancher/k3s/k3s.yaml,dst=/etc/rancher/k3s/k3s.yaml,readonly',calls[-1][1])
            context.control('stage-host',cluster=True,memory='8g')
            command=calls[-1][1]
            self.assertIn('type=bind,src='+str(context.host_root)+',dst=/src,readonly',command)
            self.assertIn('type=bind,src=/etc/rancher/k3s/k3s.yaml,dst=/etc/rancher/k3s/k3s.yaml,readonly',command)
            self.assertEqual(command[command.index('--memory')+1],'8g')

    def test_staged_routing_requires_exact_build_seals_and_a_truthful_lease(self):
        """Reject stale or edited generated artifacts before invoking the Helm switch."""
        spec=importlib.util.spec_from_file_location('serving_ci_staged_test',ROOT/'scripts/ci-serving.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with TemporaryDirectory() as folder:
            out=Path(folder);p=plan();module.save(out/'plan.json',p);module.save(out/'feature-values.json',p['feature_values'])
            now=datetime.now(timezone.utc);empty={'metadata':{'uid':'seed'},'immutable':True,'data':{},'binaryData':{}}
            stage={'passed':True,'commit':p['commit'],'release':p['release'],'model_pin':p['pin'],
                'plan_sha256':module.digest(out/'plan.json'),'values_sha256':module.digest(out/'feature-values.json'),
                'prepared_wall_utc':now.isoformat(),'seed_sha256':{},'seed_uid':'seed'}
            context=NS(out=out,commit=p['commit'],release=p['release'],selected=lambda:{'pin':p['pin']},get=lambda *args:empty)
            module.save(out/'staged.json',stage);self.assertEqual(module.staged_plan(context)[0],p)
            for change in [{'commit':'b'*40},{'release':'old'},{'passed':False},{'plan_sha256':'bad'},
                    {'prepared_wall_utc':(now-timedelta(hours=1)).isoformat()},{'prepared_wall_utc':(now+timedelta(minutes=1)).isoformat()}]:
                module.save(out/'staged.json',{**stage,**change})
                with self.assertRaises(ValueError):module.staged_plan(context)


if __name__=='__main__':unittest.main()
