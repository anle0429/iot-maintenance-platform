"""Reject telemetry deployments that alter model identity, private routing or packaged business code."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import importlib.util,json,unittest,tempfile
from ml.observability_ci import release,environment,same_model,application_paths,patch_predictor,registry_address,ENDPOINT


class ObservabilityCITests(unittest.TestCase):
    """Exercise deployment trust boundaries with explicit known model and Kubernetes fixtures."""
    def test_release_numbers_have_a_distinct_namespace(self):
        self.assertEqual(release('9'),'observe-jenkins-9')
        for n in ['0','01','../1','1;id',1,'1'*11]:
            with self.subTest(n=n),self.assertRaises(ValueError):release(n)

    def test_registry_network_is_discovered_without_assuming_a_stale_bridge_name(self):
        for mode in ['iot-mlflow_default','iot-mlflow-restored-28c']:
            value={'HostConfig':{'NetworkMode':mode},'NetworkSettings':{'Networks':{mode:{'IPAddress':'172.25.0.2'}}}}
            self.assertEqual(registry_address(value),'172.25.0.2')
            for address in ['8.8.8.8','127.0.0.1','0.0.0.0','169.254.1.1','::1']:
                bad=deepcopy(value);bad['NetworkSettings']['Networks'][mode]['IPAddress']=address
                with self.subTest(address=address),self.assertRaises(ValueError):registry_address(bad)
        value['NetworkSettings']['Networks']['another']={'IPAddress':'172.25.0.3'}
        with self.assertRaises(ValueError):registry_address(value)

    def test_environment_preserves_store_model_and_fixed_endpoint(self):
        original=[{'name':'IOT_MODEL_BUNDLE','value':'/app/model'}];frozen=deepcopy(original)
        for kind in ['feature','predictor','drift']:
            value=environment(original,kind);self.assertEqual(value[0],original[0]);self.assertEqual(value[-1]['value'],ENDPOINT)
            self.assertEqual(environment(value,kind),value)
        self.assertEqual(original,frozen)
        for env,kind in [([{'name':'IOT_TRACE_ENDPOINT','value':'https://external.example'}],'feature'),
                         ([{'name':'IOT_API_SERVICE','value':'drift'}],'feature'),
                         (original+original,'feature'),(original,'ab')]:
            with self.subTest(env=env,kind=kind),self.assertRaises(ValueError):environment(env,kind)

    def test_model_identity_and_exact_weight_bytes_cannot_change(self):
        root=Path(__file__).resolve().parents[1];bundle=root/'models/pump-failure-v1'
        pin=json.loads((bundle/'model-pin.json').read_bytes());pin['production']=True
        raw=(bundle/'portable.json').read_bytes();self.assertEqual(sha256(raw).hexdigest(),pin['portable_sha256'])
        self.assertEqual(same_model(pin,{**pin,'features':['descriptive']},raw),pin)
        for actual,weights in [({**pin,'production':1},raw),({**pin,'version':'2'},raw),(pin,raw+b' '),({**pin,'run_id':'0'*32},raw)]:
            with self.subTest(actual=actual),self.assertRaises(ValueError):same_model(pin,actual,weights)

    def test_application_scope_never_silently_drops_a_shared_module(self):
        names=['services/feature_api/'+n+'.py' for n in ['__init__','app','runtime']]
        names+=['services/predictor/'+n+'.py' for n in ['__init__','contracts','binding']]+['services/__init__.py','ml/__init__.py','ml/protocol.py']
        files={n:'a'*64 for n in names};self.assertEqual(set(application_paths('feature',files)),set(names))
        with self.assertRaises(ValueError):application_paths('feature',{k:v for k,v in files.items() if k!='ml/protocol.py'})
        with self.assertRaises(ValueError):application_paths('unknown',files)

    def test_predictor_patch_tests_concurrent_identity_and_environment(self):
        obj={'metadata':{'uid':'native-uid','resourceVersion':'39'},'spec':{'predictor':{'containers':[{'image':'iot-coursework-predictor:old','env':[]}]}}}
        frozen=deepcopy(obj);patch=patch_predictor(obj,'iot-coursework-predictor:observe-jenkins-1',[])
        self.assertEqual([p['path'] for p in patch if p['op']=='test'],['/metadata/uid','/metadata/resourceVersion','/spec/predictor/containers/0/image','/spec/predictor/containers/0/env'])
        self.assertEqual(obj,frozen)
        with self.assertRaises(ValueError):patch_predictor(obj,'external.example/model:latest',[])

    def test_controller_keeps_readonly_source_and_uses_telemetry_driver(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('observed_driver_test',root/'scripts/ci-observability.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            context=module.Context.__new__(module.Context);context.out=Path(folder)
            (context.out/'controller.json').write_text('{"id":"sha256:fixture"}')
            context.release='observe-jenkins-1';context.deploy=Path('/srv/iot-data/project');context.directory=context.deploy/'data/ci/releases'/context.release
            context.host_root=context.deploy/'sealed';context.helm='/helm';calls=[]
            context.container=lambda *args:calls.append(args);context.control('select-host',cluster=True)
            command=calls[0][1]
            self.assertEqual(command[-2:],['/src/scripts/ci-observability.py','select-host'])
            self.assertIn('type=bind,src='+str(context.host_root)+',dst=/src,readonly',command)
            self.assertIn('type=bind,src=/etc/rancher/k3s/k3s.yaml,dst=/etc/rancher/k3s/k3s.yaml,readonly',command)


if __name__=='__main__':unittest.main()
