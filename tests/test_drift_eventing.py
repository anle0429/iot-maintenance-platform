"""Check event transport and deployment boundaries before creating any namespace or persistent ledger."""
from copy import deepcopy
from pathlib import Path
import json,unittest
from ml.drift_eventing import plan,event_headers,private_services,EVENT_TYPE


class DriftEventingTests(unittest.TestCase):
    """Require matching production data, bounded headers and private retained storage."""
    def fixture(self):
        """Use already retained synthetic native model/reference bytes, without claiming a new registry call."""
        root=Path(__file__).resolve().parents[1]
        return {'release':'drift-event-pilot-28','image':'iot-coursework-drift-api:observe-jenkins-2','node':'iot-cw-vm',
            'reference_raw':(root/'docs/evidence/offline-drift-linux/reference.json').read_text(encoding='utf-8'),
            'pin':json.loads((root/'docs/evidence/ci-serving-linux/selected-model6/model-pin.json').read_bytes()),
            'portable':json.loads((root/'docs/evidence/ci-serving-linux/selected-model6/portable.json').read_bytes()),
            'ledger':'/srv/iot-data/project/data/eventing/drift-event-pilot-28'}

    def test_private_kserve_sink_uses_a_separate_retained_ledger(self):
        args=self.fixture();before=deepcopy(args);value=plan(**args);self.assertEqual(args,before)
        kinds={o['kind'] for o in value['objects']};self.assertIn('Broker',kinds);self.assertIn('InferenceService',kinds)
        isvc=next(o for o in value['objects'] if o['kind']=='InferenceService');self.assertEqual(isvc['spec']['predictor']['maxReplicas'],1)
        self.assertEqual(next(o for o in value['objects'] if o['kind']=='PersistentVolume')['spec']['persistentVolumeReclaimPolicy'],'Retain')
        trigger=next(o for o in value['objects'] if o['kind']=='Trigger');self.assertEqual(trigger['spec']['filter']['attributes']['type'],EVENT_TYPE)
        self.assertTrue(trigger['spec']['subscriber']['uri'].endswith('.svc.cluster.local/v1/drift:compare'))

    def test_other_data_version_or_schema_cannot_silently_reset_the_baseline(self):
        for edit in ['data_version','protocol_sha256','samples','production']:
            args=self.fixture();ref=json.loads(args['reference_raw'])
            if edit=='production':args['pin']['production']=False
            elif edit=='samples':ref['samples'][0]['label']=1
            else:ref['lineage'][edit]='0'*64
            args['reference_raw']=json.dumps(ref)
            with self.subTest(edit=edit),self.assertRaises(ValueError):plan(**args)

    def test_public_image_arbitrary_ledger_and_header_injection_are_rejected(self):
        for key,value in [('release','../x'),('image','external.example/model:latest'),('ledger','/etc/other'),('node','host;id')]:
            args=self.fixture();args[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):plan(**args)
        for identity in ['', 'id\r\nAuthorization:secret', 'x'*101]:
            with self.subTest(identity=identity),self.assertRaises(ValueError):event_headers(identity)
        self.assertEqual(event_headers('pilot-1')['ce-type'],EVENT_TYPE)

    def test_only_an_internal_alias_to_a_private_clusterip_is_allowed(self):
        target={'metadata':{'name':'receiver','namespace':'iot-drift-events'},'spec':{'type':'ClusterIP','clusterIP':'10.53.1.2'}}
        alias={'metadata':{'name':'isvc','namespace':'iot-drift-events'},'spec':{'type':'ExternalName','externalName':'receiver.iot-drift-events.svc.cluster.local'}}
        self.assertEqual(private_services([alias],lambda name,ns:target)['isvc']['cluster_ip'],'10.53.1.2')
        for value in ['outside.example','receiver.default.svc.cluster.local']:
            bad=deepcopy(alias);bad['spec']['externalName']=value
            with self.subTest(value=value),self.assertRaises(ValueError):private_services([bad],lambda *a:target)
        with self.assertRaises(ValueError):private_services([alias],lambda *a:{**target,'spec':{'type':'LoadBalancer','clusterIP':'10.53.1.2'}})


if __name__=='__main__':unittest.main()
