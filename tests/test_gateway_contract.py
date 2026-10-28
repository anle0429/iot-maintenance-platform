"""Check that gateway manifests cannot expose public endpoints or alter unrelated services."""
import unittest
from infra.gateway.resources import resources,HOSTS

class GatewayContractTests(unittest.TestCase):
    """Exercise unsafe input boundaries and the rendered isolation/auth/TLS contract."""
    def test_private_inputs_and_frozen_image_are_required(self):
        for image,ip in [('nginx/nginx-ingress:latest','10.52.0.1'),('nginx/nginx-ingress@sha256:'+'a'*64,'8.8.8.8'),('nginx/nginx-ingress@sha256:'+'a'*64,'127.0.0.1'),('nginx/nginx-ingress@sha256:'+'a'*64,'0.0.0.0'),('nginx/nginx-ingress@sha256:'+'a'*64,'::1')]:
            with self.subTest(image=image,ip=ip),self.assertRaises(ValueError):resources(image,ip)
    def test_auth_tls_and_rate_limit_cover_intended_private_routes(self):
        docs=resources('nginx/nginx-ingress@sha256:'+'a'*64,'10.52.0.1')
        ing=[d for d in docs if d['kind']=='Ingress'];self.assertEqual({d['spec']['rules'][0]['host'] for d in ing},set(HOSTS.values()))
        for d in ing:
            self.assertEqual(d['metadata']['annotations']['nginx.org/basic-auth-secret'],'gateway-auth');self.assertEqual(d['spec']['tls'][0]['secretName'],'gateway-tls')
        feature=next(d for d in ing if d['metadata']['name']=='features');self.assertEqual(feature['metadata']['annotations']['nginx.org/limit-req-reject-code'],'429')
        for d in docs:
            if d['kind']=='Service':self.assertEqual(d['spec']['type'],'ClusterIP');self.assertNotIn('externalIPs',d['spec'])
        pod=next(d for d in docs if d['kind']=='Deployment')['spec']['template']['spec'];self.assertNotIn('hostNetwork',pod);self.assertNotIn('hostPort',str(pod));self.assertIn('-watch-namespace=iot-gateway,iot-features,iot-observability',pod['containers'][0]['args'])
        for role in [d for d in docs if d['kind'] in ['Role','ClusterRole']]:
            for rule in role['rules']:self.assertNotIn('*',rule['verbs']);self.assertNotIn('*',rule['resources'])

if __name__=='__main__':unittest.main()
