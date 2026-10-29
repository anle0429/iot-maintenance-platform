"""Reject unsafe target, metric and scale-in changes before native agent autoscaler deployment."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('agent_scaling', ROOT / 'scripts/deploy-agent-scaling.py')
scaling = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(scaling)
BURST_SPEC = importlib.util.spec_from_file_location('agent_burst', ROOT / 'scripts/benchmark-agent-scaling.py')
burst = importlib.util.module_from_spec(BURST_SPEC); BURST_SPEC.loader.exec_module(burst)
PARK_SPEC = importlib.util.spec_from_file_location('park_candidates', ROOT / 'scripts/park-inactive-ab-candidates.py')
park = importlib.util.module_from_spec(PARK_SPEC); PARK_SPEC.loader.exec_module(park)


def chart_documents():
    """Expand the small chart fixture while native Helm remains the authoritative rendering check."""
    folder = ROOT / 'infra/charts/iot-agent-scaling'; values = yaml.safe_load((folder / 'values.yaml').read_text())
    text = (folder / 'templates/scaledobjects.yaml').read_text().split('}}', 1)[1].rsplit('{{- end }}', 1)[0]
    documents = []
    for role in sorted(scaling.ROLES):
        body = text.replace('{{ $role }}', role)
        body = re.sub(r'{{\s*\$\.Values\.(\w+)(?:\s*\|\s*quote)?\s*}}', lambda m: json.dumps(values[m[1]]), body)
        documents.extend(yaml.safe_load_all(body))
    return documents


class AgentScalingTests(unittest.TestCase):
    """Keep namespace/role selection, capacity and metric provenance explicit."""

    def test_reviewed_three_pool_scaling_configuration(self):
        """The three native scale subresources retain two warm workers and a bounded maximum."""
        documents = chart_documents()
        for doc in documents: doc['metadata']['labels']['app.kubernetes.io/managed-by'] = 'Helm'
        self.assertTrue(scaling.validate_scalers(documents))

    def test_wrong_targets_and_fabricated_or_broad_metrics_are_rejected(self):
        """A valid-looking scaler cannot count another namespace, use an external API or disable guards."""
        for case in ['target', 'zero-minimum', 'excess-maximum', 'global-count', 'external-url', 'scale-in']:
            documents = copy.deepcopy(chart_documents()); spec = documents[0]['spec']
            if case == 'target': spec['scaleTargetRef']['name'] = 'unrelated-pool'
            elif case == 'zero-minimum': spec['minReplicaCount'] = 0
            elif case == 'excess-maximum': spec['maxReplicaCount'] = 100
            elif case == 'global-count': spec['triggers'][0]['metadata']['valueLocation'] = 'data.actors.#'
            elif case == 'external-url': spec['triggers'][0]['metadata']['url'] = 'https://example.com/fake-count'
            elif case == 'scale-in': spec['advanced']['horizontalPodAutoscalerConfig']['behavior']['scaleDown']['stabilizationWindowSeconds'] = 0
            with self.subTest(case=case), self.assertRaises(ValueError): scaling.validate_scalers(documents)

    def test_missing_or_duplicate_roles_are_rejected(self):
        """The installer cannot quietly omit one of the required specialist/coordinator pools."""
        documents = chart_documents()
        with self.assertRaises(ValueError): scaling.validate_scalers(documents[:2])
        with self.assertRaises(ValueError): scaling.validate_scalers([documents[0], documents[0], documents[2]])

    def test_compatibility_patch_preserves_scale_paths_and_requires_version_identity(self):
        """Only the pinned CRD gains a string selector; unrelated versions or scale fields are protected."""
        crd = {'metadata': {'name': 'workerpools.ate.dev', 'resourceVersion': '123'}, 'spec': {'versions': [{
            'name': 'v1alpha1', 'subresources': {'scale': {'specReplicasPath': '.spec.replicas', 'statusReplicasPath': '.status.replicas'}},
            'schema': {'openAPIV3Schema': {'properties': {'status': {'properties': {'replicas': {'type': 'integer'}}},
                                                       'spec': {'properties': {'replicas': {'type': 'integer'}}}}}}}]}}
        patch = scaling.selector_patch(crd)
        self.assertEqual(patch[0], {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '123'})
        self.assertEqual(patch[-1]['value'], '.spec.selector')
        for case in ['name', 'version', 'path', 'schema']:
            changed = copy.deepcopy(crd)
            if case == 'name': changed['metadata']['name'] = 'unrelated.example'
            elif case == 'version': changed['spec']['versions'][0]['name'] = 'v1beta1'
            elif case == 'path': changed['spec']['versions'][0]['subresources']['scale']['specReplicasPath'] = '.spec.somethingElse'
            elif case == 'schema': changed['spec']['versions'][0]['schema']['openAPIV3Schema']['properties']['spec']['properties']['selector'] = {'type': 'object'}
            with self.subTest(case=case), self.assertRaises(ValueError): scaling.selector_patch(changed)

    def test_capacity_measurement_excludes_other_roles_golden_actors_and_terminating_pods(self):
        """A claimed Ready worker or busy actor must belong to this role and its active namespace."""
        pools = [{'metadata': {'name': 'iot-' + r + '-pool'}, 'spec': {'replicas': 2}, 'status': {'replicas': 2}} for r in burst.ROLES]
        pod = {'metadata': {'uid': 'worker-1', 'labels': {'ate.dev/worker-pool': 'iot-retrieval-pool'}}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        terminating = copy.deepcopy(pod); terminating['metadata'].update({'uid': 'worker-2', 'deletionTimestamp': '2026-10-29T15:00:00Z'})
        actor = {'atespace': 'iot-agents', 'status': 'Running', 'actorTemplateName': 'iot-retrieval-hash', 'ateomPodName': 'retrieval-1'}
        golden = {**actor, 'atespace': 'ate-golden'}; suspended = {**actor, 'status': 'Suspended'}
        result = burst.pool_metrics(pools, [pod, terminating], [actor, golden, suspended])
        self.assertEqual(result['retrieval']['ready'], 1); self.assertEqual(result['retrieval']['running'], 1)
        self.assertEqual(result['drift']['running'], 0); self.assertEqual(result['coordinator']['ready'], 0)

    def test_only_explicit_predispatch_capacity_errors_are_retryable(self):
        """Timeouts, executed tasks and arbitrary tool/RPC errors must not trigger blind re-execution."""
        error = {'code': -32603, 'message': 'failed to send HTTP request: Post "http://iot-coordinator.iot-agents:8080": substrate worker pool has no free workers; try again later or increase WorkerPool replicas'}
        self.assertTrue(burst.retryable_capacity_error({'error': error}))
        for reply in [{'result': {'id': 'already-executed'}, 'error': error}, {'error': {'code': -32603, 'message': 'timeout'}},
                      {'error': {'code': -32602, 'message': error['message']}}, {'result': {'status': {'state': 'failed'}}}, {}]:
            with self.subTest(reply=reply): self.assertFalse(burst.retryable_capacity_error(reply))

    def test_candidate_parking_refuses_serving_recent_or_unrelated_workloads(self):
        """Reclaim only the three known long-failing candidates with no ready endpoint."""
        name = 'iot-ab-serve-jenkins-6'
        deployment = {'metadata': {'name': name, 'namespace': 'iot-features', 'annotations': {'meta.helm.sh/release-name': name},
                                   'labels': {'app.kubernetes.io/managed-by': 'Helm'}}, 'spec': {'replicas': 1}, 'status': {'availableReplicas': 0}}
        pod = {'metadata': {'uid': 'old-candidate'}, 'status': {'containerStatuses': [{'ready': False, 'restartCount': 400}]}}
        self.assertTrue(park.reviewed_candidate(deployment, [pod], set()))
        for case in ['available', 'ready-pod', 'recent', 'endpoint', 'foreign']:
            changed = copy.deepcopy(deployment); changed_pod = copy.deepcopy(pod); endpoints = set()
            if case == 'available': changed['status']['availableReplicas'] = 1
            elif case == 'ready-pod': changed_pod['status']['containerStatuses'][0]['ready'] = True
            elif case == 'recent': changed_pod['status']['containerStatuses'][0]['restartCount'] = 1
            elif case == 'endpoint': endpoints.add('old-candidate')
            elif case == 'foreign': changed['metadata']['namespace'] = 'another-project'
            with self.subTest(case=case), self.assertRaises(ValueError): park.reviewed_candidate(changed, [changed_pod], endpoints)


if __name__ == '__main__': unittest.main()
