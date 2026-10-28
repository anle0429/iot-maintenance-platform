"""Check source ownership boundaries and known LogQL/configuration contracts before native binary validation."""
from copy import deepcopy
import unittest
from ml.log_monitoring import targets,render,dashboard,HOST_SERVICES


def observed():
    """Use declared fixture identities; these paths are not evidence that a native Pod exists."""
    sources=[{'service':s,'path':'/srv/coursework/outputs/linux-monitoring-28/'+s+'.txt','time_key':'time' if s!='grafana' else 't'} for s in HOST_SERVICES]
    pods=[]
    for service,namespace in [('predictor','iot-serving'),('feature-api','iot-features')]:
        pod={'namespace':namespace,'service':service,'pod':service+'-fixture','uid':'12345678-1234-1234-1234-123456789abc','container':'api','rotation':0}
        pod['path']='/var/log/pods/'+namespace+'_'+pod['pod']+'_'+pod['uid']+'/api/0.log';pods.append(pod)
    return {'sources':sources,'pods':pods}


class LogMonitoringTests(unittest.TestCase):
    def test_exact_observed_source_paths_and_service_labels(self):
        result=targets('/srv/coursework',observed(),'vm-linux')
        self.assertEqual(len(result['host_files']),3);self.assertEqual(len(result['cri_files']),2)
        self.assertEqual({s['labels']['service'] for s in result['cri_files']},{'predictor','feature-api'})
        self.assertEqual({s['labels']['source'] for s in result['host_files']},{'host-file'})

    def test_unrelated_files_or_missing_application_service_are_refused(self):
        for change in [lambda v:v['sources'][0].update(path='/etc/passwd'),
                lambda v:v['sources'][0].update(path='/srv/coursework/outputs/linux-monitoring-28/../secrets/node-exporter.txt'),
                lambda v:v['sources'][0].update(time_key='unverified'),
                lambda v:v['sources'].pop(),lambda v:v['pods'][0].update(service='ab-api')]:
            value=observed();change(value)
            with self.subTest(value=value),self.assertRaises(ValueError):targets('/srv/coursework',value,'vm-linux')

    def test_pod_path_cannot_outlive_its_identity_or_select_a_glob(self):
        for field,value in [('uid','changed'),('namespace','kube-system'),('pod','../other'),('container','api/*'),
                ('rotation',True),('rotation',-1),('path','/var/log/pods/other/api/0.log')]:
            source=observed();source['pods'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):targets('/srv/coursework',source,'vm-linux')
        source=observed();source['pods'].append(deepcopy(source['pods'][0]))
        with self.assertRaises(ValueError):targets('/srv/coursework',source,'vm-linux')

    def test_known_log_queries_preserve_host_and_separate_log_formats(self):
        result=dashboard('vm-linux');panels=result['panels']
        self.assertEqual([p['type'] for p in panels],['timeseries','timeseries','logs','logs'])
        self.assertEqual(panels[2]['targets'][0]['expr'],'{host="$host",collection="$collection",source="host-file"}')
        self.assertEqual(panels[3]['targets'][0]['expr'],'{host="$host",collection="$collection",source="cri"}')
        self.assertTrue(all(p['datasource']['uid']=='iot-logs-loki' for p in panels))
        with self.assertRaises(ValueError):dashboard('vm"} |= "all')

    def test_loopback_small_caches_and_explicit_timestamp_fallback(self):
        value=render('/srv/runtime/logs','/srv/coursework',observed())
        server=value['loki']['server'];self.assertEqual(server['http_listen_address'],'127.0.0.1')
        self.assertEqual(server['grpc_listen_address'],'127.0.0.1');self.assertEqual(server['http_listen_port'],13100)
        self.assertEqual(value['loki']['limits_config']['retention_period'],'24h')
        self.assertEqual(value['loki']['query_range']['results_cache']['cache']['embedded_cache']['max_size_mb'],32)
        self.assertFalse(value['loki']['analytics']['reporting_enabled'])
        self.assertEqual(value['alloy'].count('action_on_failure = "skip"'),3)
        self.assertEqual(value['alloy'].count('action_on_duplicate_timestamp = "keep"'),3)
        self.assertEqual({s['labels']['collection'] for s in value['sources']['host_files']},{'native-28-keep'})
        self.assertIn('stage.cri {}',value['alloy']);self.assertIn('http://127.0.0.1:13100/loki/api/v1/push',value['alloy'])
        self.assertNotIn('/etc/',value['alloy']);self.assertNotIn('password',value['alloy'])


if __name__=='__main__':unittest.main()
