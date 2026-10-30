"""Reject changed baselines and incomplete or favorably filtered agent comparisons."""
from copy import deepcopy
import unittest
from agents.ab_configuration import VARIANTS, configuration_plan, comparison_summary


class AgentConfigurationTests(unittest.TestCase):
    """Use synthetic public configuration fixtures without live credentials or model-generated expectations."""
    def setUp(self):
        """Keep the minimum reviewed production configuration and an explicitly shared prompt."""
        self.model = {'metadata': {'name': 'default-model-config'}, 'spec': {'model': 'iot-qwen2.5-7b', 'openAI': {'temperature': '0', 'seed': 7, 'maxTokens': 768}}}
        self.policy = {'version': '0.2.2', 'prompts': {'retrieval': 'Use actual tools.'}}
        self.agent = {'spec': {'version': '0.2.2', 'declarative': {'systemMessage': 'Use actual tools.', 'modelConfig': 'default-model-config', 'executeCodeBlocks': False},
                               'substrate': {'workerPoolRef': {'name': 'iot-retrieval-pool'}}, 'sandbox': {'network': {'allowedDomains': ['private.test']}}}}

    def test_only_temperature_and_new_resource_references_change(self):
        """Keep prompts, network boundaries, worker pool and the selected production model untouched."""
        originals = deepcopy((self.model, self.agent)); plan = configuration_plan(self.model, self.agent, self.policy)
        self.assertEqual((self.model, self.agent), originals)
        for i, (variant, settings) in enumerate(VARIANTS.items()):
            model, agent = plan[i * 2:i * 2 + 2]
            self.assertEqual(model['metadata']['name'], settings['model_config'])
            self.assertEqual(model['spec']['openAI']['temperature'], settings['temperature'])
            model['spec']['openAI']['temperature'] = '0'; self.assertEqual(model['spec'], self.model['spec'])
            agent['spec']['declarative']['modelConfig'] = 'default-model-config'; self.assertEqual(agent['spec'], self.agent['spec'])

    def test_unreviewed_configuration_is_rejected(self):
        """A different prompt, model, sampling baseline or code permission needs review rather than silent replacement."""
        for field, value in [('temperature', '0.1'), ('seed', 8), ('maxTokens', 2048)]:
            model = deepcopy(self.model); model['spec']['openAI'][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError): configuration_plan(model, self.agent, self.policy)
        self.agent['spec']['declarative']['executeCodeBlocks'] = True
        with self.assertRaises(ValueError): configuration_plan(self.model, self.agent, self.policy)

    def test_failed_outcomes_and_pair_boundaries(self):
        """One failure stays in its three-request denominator; duplicates, missing pairs and invalid clocks fail acceptance."""
        machines = ['P-001', 'P-002', 'P-003']
        rows = [{'variant': v, 'machine_id': m, 'passed': not (v == 'b' and m == 'P-003'), 'elapsed_seconds': 2., 'context_id': v + m, 'request_id': v + m} for m in machines for v in VARIANTS]
        result = comparison_summary(rows, machines); self.assertEqual(result['b']['requests'], 3); self.assertEqual(result['b']['tool_grounded_answers'], 2)
        for changed in [rows[:-1], rows + [rows[0]], rows[:-1] + [rows[0]]]:
            with self.assertRaises(ValueError): comparison_summary(changed, machines)
        rows[0]['elapsed_seconds'] = float('nan')
        with self.assertRaises(ValueError): comparison_summary(rows, machines)
