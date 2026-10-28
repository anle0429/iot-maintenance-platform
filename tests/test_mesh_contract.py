"""Guard isolation, distinct identities and the known genuine model input before native mesh execution."""
from hashlib import sha256
from pathlib import Path
import json,unittest,yaml,importlib.util
from infra.mesh.resources import resources

class MeshContractTests(unittest.TestCase):
    """Check that policy cannot accidentally admit another identity or mutate an existing production namespace."""
    def test_isolated_revisions_and_distinct_callers(self):
        'Verify isolated revisions and distinct callers.'
        docs=resources();self.assertEqual(docs[0]['metadata']['labels'],{'istio.io/rev':'iot-1313'})
        self.assertTrue(all(d['metadata'].get('namespace','iot-mesh')=='iot-mesh' for d in docs))
        pods={d['metadata']['name']:d['spec']['template']['spec'] for d in docs if d['kind']=='Deployment'}
        self.assertEqual(len({p['serviceAccountName'] for p in pods.values()}),4)
        for pod in pods.values():self.assertNotIn('hostNetwork',pod);self.assertEqual(pod['containers'][0]['securityContext']['capabilities']['drop'],['ALL'])
        self.assertTrue(all(d['spec']['type']=='ClusterIP' for d in docs if d['kind']=='Service'))
    def test_only_running_regular_or_restartable_proxy_counts(self):
        'Verify only running regular or restartable proxy counts.'
        spec=importlib.util.spec_from_file_location('mesh_checker','scripts/check-private-mesh.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for kind,restart,want in [('regular',None,True),('init','Always',True),('init',None,False),('none',None,False)]:
            pod={'spec':{'containers':[],'initContainers':[]},'status':{'containerStatuses':[],'initContainerStatuses':[]}}
            if kind!='none':
                key='containers' if kind=='regular' else 'initContainers';statuskey='containerStatuses' if kind=='regular' else 'initContainerStatuses';pod['spec'][key]=[{'name':'istio-proxy',**({'restartPolicy':restart} if restart else {})}];pod['status'][statuskey]=[{'name':'istio-proxy','ready':True,'started':True,'state':{'running':{'startedAt':'fixed'}}}]
            with self.subTest(kind=kind,restart=restart):self.assertEqual(module.proxy_ready(pod),want)

    def test_strict_single_principal_and_exact_model_route(self):
        'Verify strict single principal and exact model route.'
        authn,authz=list(yaml.safe_load_all(Path('infra/mesh/policies.yaml').read_text()))
        self.assertEqual(authn['spec']['mtls']['mode'],'STRICT');self.assertEqual(authz['spec']['action'],'ALLOW');self.assertEqual(authz['spec']['selector']['matchLabels'],{'app':'mesh-predictor'})
        rules=authz['spec']['rules'];self.assertEqual(len(rules),1);self.assertEqual(rules[0]['from'][0]['source']['principals'],['cluster.local/ns/iot-mesh/sa/feature-caller'])
        pin=json.loads(Path('docs/evidence/ci-api-observability/bundle/model-pin.json').read_bytes());self.assertEqual(rules[0]['to'][0]['operation'],{'ports':['8080'],'methods':['POST'],'paths':['/v1/models/'+pin['name']+':predict']})
        fixture=json.loads(Path('tests/fixtures/mesh-predict.json').read_bytes());parent=Path(fixture['parent']);self.assertEqual(sha256(parent.read_bytes()).hexdigest(),fixture['parent_sha256']);self.assertEqual(fixture['body']['instances'],[json.loads(json.loads(parent.read_bytes())['body'])['features']])

if __name__=='__main__':unittest.main()
