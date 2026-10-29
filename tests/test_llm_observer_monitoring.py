"""Check discovery scope and preservation of original jobs, including failing historical targets."""
from copy import deepcopy
import unittest
from infra.llm_observer_monitoring import extended_configuration,pod_jobs


class LlmMonitoringPlanTests(unittest.TestCase):
    """Exercise monitoring boundaries rather than pretending to run Prometheus or Grafana."""
    def test_extension_keeps_original_jobs_and_rejects_duplicate_installation(self):
        """A new monitoring checkpoint must not hide old failing targets or rewrite their configuration."""
        existing={'global':{'scrape_interval':'2s'},'scrape_configs':[{'job_name':'old-ab','static_configs':[{'targets':['old-down:80']}]}]}
        original=deepcopy(existing);planned=extended_configuration(existing,'/private/token','/public/ca.crt')
        self.assertEqual(existing,original);self.assertEqual(planned['global'],existing['global']);self.assertEqual(planned['scrape_configs'][:1],existing['scrape_configs'])
        self.assertEqual(len(planned['scrape_configs']),5)
        with self.assertRaises(ValueError):extended_configuration(planned,'/private/token','/public/ca.crt')

    def test_every_target_is_an_actual_selected_container_port(self):
        """Avoid round-robin Service undercounts, unrelated sidecars and silent removal of unready applications."""
        jobs=pod_jobs('/private/token','/public/ca.crt');self.assertEqual(len({j['job_name'] for j in jobs}),4)
        for job in jobs:
            with self.subTest(job=job['job_name']):
                discovery=job['kubernetes_sd_configs'];self.assertEqual(len(discovery),1);self.assertEqual(discovery[0]['role'],'pod')
                self.assertIn(discovery[0]['namespaces']['names'],[['iot-llm'],['iot-agents']])
                self.assertEqual(discovery[0]['authorization'],{'credentials_file':'/private/token'})
                self.assertEqual(discovery[0]['tls_config'],{'ca_file':'/public/ca.crt'})
                kept={tuple(v['source_labels']):v['regex'] for v in job['relabel_configs'] if v.get('action')=='keep'}
                self.assertIn(('__meta_kubernetes_pod_container_name',),kept);self.assertIn(('__meta_kubernetes_pod_container_port_number',),kept)
                self.assertNotIn(('__meta_kubernetes_pod_ready',),kept)
                self.assertNotIn('static_configs',job)


if __name__=='__main__':unittest.main()
