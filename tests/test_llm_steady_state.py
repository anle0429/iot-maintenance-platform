"""Protect targets, credentials and probe semantics when adjusting the retained observer's monitoring budget."""
from copy import deepcopy
import unittest
from infra.llm_steady_state import monitoring_configuration, observer_deployment


class SteadyStatePlanTests(unittest.TestCase):
    """Allow only the measured operator configuration changes, including safe idempotent reapplication."""

    def test_only_observer_cadence_changes(self):
        """Keep unrelated failing targets and authorization references byte-for-value, without mutating caller inputs."""
        old = {'global': {'scrape_interval': '2s'}, 'scrape_configs': [
            {'job_name': 'old-ab', 'static_configs': [{'targets': ['down:80']}]},
            {'job_name': 'iot-llm-observer', 'scrape_interval': '2s', 'scrape_timeout': '1s', 'authorization': {'credentials_file': '/private/token'}, 'sample_limit': 5000}]}
        copy = deepcopy(old); planned = monitoring_configuration(old)
        self.assertEqual(old, copy); expected = deepcopy(copy)
        expected['scrape_configs'][1].update(scrape_interval='15s', scrape_timeout='10s')
        self.assertEqual(planned, expected); self.assertEqual(monitoring_configuration(planned), planned)
        for jobs in [[], [old['scrape_configs'][1]]*2, [{'job_name': 'iot-llm-observer', 'scrape_interval': '1h', 'scrape_timeout': '1s'}]]:
            with self.subTest(jobs=jobs), self.assertRaises(ValueError): monitoring_configuration({'scrape_configs': jobs})

    def test_probe_response_and_workload_semantics_remain(self):
        """Adjust only two timeout fields, retaining endpoints, credentials, sidecars, limits and failure thresholds."""
        old = {'metadata': {'name': 'iot-llm-observer', 'namespace': 'iot-llm', 'uid': 'same'}, 'spec': {'replicas': 1, 'template': {'spec': {'volumes': [{'name': 'client', 'secret': {'secretName': 'same'}}], 'containers': [
            {'name': 'model-control', 'image': 'same-control'}, {'name': 'observer', 'image': 'same-observer', 'resources': {'limits': {'cpu': '1', 'memory': '512Mi'}},
             'livenessProbe': {'httpGet': {'path': '/health/live', 'port': 'observe', 'scheme': 'HTTP'}, 'timeoutSeconds': 1, 'failureThreshold': 4},
             'readinessProbe': {'httpGet': {'path': '/health/ready', 'port': 'observe', 'scheme': 'HTTP'}, 'timeoutSeconds': 3}}]}}}}
        copy = deepcopy(old); planned = observer_deployment(old); expected = deepcopy(old)
        for key in ['livenessProbe', 'readinessProbe']: expected['spec']['template']['spec']['containers'][1][key]['timeoutSeconds'] = 5
        self.assertEqual(old, copy); self.assertEqual(planned, expected); self.assertEqual(observer_deployment(planned), planned)
        wrong = deepcopy(old); wrong['spec']['template']['spec']['containers'][1]['readinessProbe']['httpGet']['path'] = '/always-ready'
        with self.assertRaises(ValueError): observer_deployment(wrong)
        wrong = deepcopy(old); wrong['metadata']['name'] = 'other'
        with self.assertRaises(ValueError): observer_deployment(wrong)


if __name__ == '__main__': unittest.main()
