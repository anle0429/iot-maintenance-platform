"""Exercise guarded gateway updates and the private authentication boundary without a cluster or private credentials."""
from copy import deepcopy
import unittest
from infra.gateway.agents import controller_patch,access_resources,ingresses,WATCH,ROUTES


class AgentGatewayTests(unittest.TestCase):
    """Use altered controller identities and cross-namespace capabilities as negative acceptance cases."""
    def setUp(self):
        """Build a small existing deployment with unrelated settings that must survive extension."""
        self.image='nginx/nginx-ingress@sha256:'+'a'*64
        self.deployment={'metadata':{'name':'iot-gateway','namespace':'iot-gateway','uid':'existing-controller'},
            'spec':{'replicas':1,'selector':{'matchLabels':{'app':'iot-gateway'}},'template':{'spec':{'containers':[
                {'name':'controller','image':self.image,'args':['-ingress-class=iot-gateway',WATCH,'-enable-custom-resources=false'],'resources':{'limits':{'memory':'512Mi'}}}]}}}}

    def test_patch_preserves_input_and_is_guarded_against_concurrent_replacement(self):
        """A valid extension changes only the watch argument and is idempotent after that update."""
        before=deepcopy(self.deployment);patch=controller_patch(self.deployment,self.image)
        self.assertEqual(self.deployment,before);self.assertEqual(patch[0],{'op':'test','path':'/metadata/uid','value':'existing-controller'})
        self.assertEqual(patch[1]['value'],before['spec']['template']['spec']['containers'][0]['args'])
        changed=deepcopy(before);changed['spec']['template']['spec']['containers'][0]['args']=patch[2]['value']
        self.assertEqual(controller_patch(changed,self.image),[])
        self.assertEqual(patch[2]['value'],['-ingress-class=iot-gateway',WATCH+',iot-agents','-enable-custom-resources=false'])

    def test_unexpected_identity_image_or_namespace_scope_is_refused(self):
        """Do not update an unrelated controller, mutable image or pre-expanded namespace scope."""
        for field,value in [('name','other-controller'),('namespace','other-namespace'),('uid','')]:
            candidate=deepcopy(self.deployment);candidate['metadata'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):controller_patch(candidate,self.image)
        for value in ['nginx/nginx-ingress:latest','nginx/nginx-ingress@sha256:'+'b'*64]:
            with self.subTest(image=value),self.assertRaises(ValueError):controller_patch(self.deployment,value)
        candidate=deepcopy(self.deployment);candidate['spec']['template']['spec']['containers'][0]['args'][1]=WATCH+',unrelated'
        with self.assertRaises(ValueError):controller_patch(candidate,self.image)

    def test_both_private_ui_routes_have_auth_tls_rate_and_streaming_controls(self):
        """Guard the user-visible security boundary, including static assets under the same authenticated prefix."""
        docs=ingresses();self.assertEqual(len(docs),2)
        for doc in docs:
            self.assertEqual(doc['metadata']['namespace'],'iot-agents');spec=doc['spec'];host=spec['rules'][0]['host']
            self.assertIn(host,{r[0] for r in ROUTES.values()});self.assertEqual(spec['tls'],[{'hosts':[host],'secretName':'agent-gateway-tls'}])
            self.assertEqual(spec['rules'][0]['http']['paths'][0]['path'],'/');a=doc['metadata']['annotations']
            self.assertEqual(a['nginx.org/basic-auth-secret'],'agent-gateway-auth');self.assertEqual(a['nginx.org/limit-req-reject-code'],'429')
            self.assertEqual(a['nginx.org/proxy-buffering'],'false');self.assertIn('Authorization:',a['nginx.org/proxy-set-headers'])
            self.assertIn('X-User-Id: $remote_user',a['nginx.org/proxy-set-headers'])

    def test_controller_does_not_get_workload_or_secret_write_permissions(self):
        """Restrict controller access to its reviewed namespace and discovery/status duties."""
        role,binding=access_resources();self.assertEqual(role['metadata']['namespace'],'iot-agents')
        self.assertEqual(binding['subjects'],[{'kind':'ServiceAccount','name':'iot-gateway','namespace':'iot-gateway'}])
        for rule in role['rules']:
            self.assertNotIn('*',rule['resources']);self.assertNotIn('*',rule['verbs'])
            if 'secrets' in rule['resources']:self.assertEqual(set(rule['verbs']),{'get','list','watch'})
            self.assertNotIn('deployments',rule['resources']);self.assertNotIn('delete',rule['verbs'])


if __name__=='__main__':unittest.main()
