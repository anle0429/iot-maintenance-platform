"""Protect the model-router monitoring transition against duplicate installation and unrelated-job changes."""
from copy import deepcopy
import unittest
from infra.llm_ab_models import monitoring_configuration,MODELS


class ModelMonitoringTests(unittest.TestCase):
    """Use original unrelated failures/settings as invariants, not a generated-output mirror."""
    def test_only_required_primary_query_and_distinct_jobs_are_added(self):
        """A router requires a model query; changing it must not erase unrelated monitoring or its discovery identity."""
        old={'global':{'scrape_interval':'17s'},'scrape_configs':[{'job_name':'unrelated-down','static_configs':[{'targets':['192.168.1.9:9999']}]},
            {'job_name':'iot-llm-model','scrape_interval':'2s','kubernetes_sd_configs':[{'role':'pod','authorization':{'credentials_file':'/private/token'}}]}]}
        snapshot=deepcopy(old);new=monitoring_configuration(old)
        self.assertEqual(old,snapshot);self.assertEqual(new['global'],old['global']);self.assertEqual(new['scrape_configs'][0],old['scrape_configs'][0])
        primary=new['scrape_configs'][1];small=new['scrape_configs'][2]
        self.assertEqual(primary['params'],{'model':[MODELS[0]]});self.assertEqual(small['params'],{'model':[MODELS[1]]})
        self.assertEqual(primary['kubernetes_sd_configs'],old['scrape_configs'][1]['kubernetes_sd_configs'])
        self.assertEqual(new['scrape_configs'][-1]['static_configs'],[{'targets':['127.0.0.1:19092']}])
        with self.assertRaises(ValueError):monitoring_configuration(new)
    def test_existing_or_ambiguous_model_job_is_not_replaced(self):
        """Reject foreign parameters, duplicate jobs and preexisting A/B job names."""
        for jobs in [[],[{'job_name':'iot-llm-model','params':{'model':['foreign']}}],
            [{'job_name':'iot-llm-model'},{'job_name':'iot-llm-model'}],[{'job_name':'iot-llm-model'},{'job_name':'iot-llm-ab'}]]:
            with self.subTest(jobs=jobs),self.assertRaises(ValueError):monitoring_configuration({'scrape_configs':jobs})


if __name__=='__main__':unittest.main()
