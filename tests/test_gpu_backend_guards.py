"""Reject unreserved hardware, changed router ownership and mismatched Pod-mounted model artifacts before promotion."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gpu_backend', ROOT / 'scripts/deploy-gpu-backend.py')
backend = importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)


def node():
    """Represent a reserved single-GPU node for guard tests only; this fixture is not hardware evidence."""
    return {'metadata': {'name': 'iot-cw-gpu', 'labels': {'iot.coursework/role': 'inference-gpu'}},
        'spec': {'taints': [{'key': 'iot.coursework/inference-gpu', 'value': 'true', 'effect': 'NoSchedule'}]},
        'status': {'conditions': [{'type': 'Ready', 'status': 'True'}], 'allocatable': {'nvidia.com/gpu': '1'},
            'nodeInfo': {'kubeletVersion': 'v1.35.9+k3s1', 'architecture': 'amd64'}}}


class GpuBackendGuardTests(unittest.TestCase):
    """Deployment guards prevent unrelated cluster or weight identities from becoming a success claim."""

    def test_only_the_ready_reserved_worker_is_accepted(self):
        """A matching node name alone is insufficient without version, Ready and a reservation taint."""
        backend.validate_node(node(), require_gpu=True)
        for mode in ['foreign', 'wrong-role', 'unready', 'missing-taint', 'wrong-taint', 'wrong-version', 'wrong-architecture']:
            value = node()
            if mode == 'foreign': value['metadata']['name'] = 'iot-cw-vm'
            elif mode == 'wrong-role': value['metadata']['labels']['iot.coursework/role'] = 'data'
            elif mode == 'unready': value['status']['conditions'][0]['status'] = 'False'
            elif mode == 'missing-taint': value['spec']['taints'] = []
            elif mode == 'wrong-taint': value['spec']['taints'][0]['effect'] = 'PreferNoSchedule'
            elif mode == 'wrong-version': value['status']['nodeInfo']['kubeletVersion'] = 'v1.34.0+k3s1'
            else: value['status']['nodeInfo']['architecture'] = 'arm64'
            with self.subTest(mode=mode), self.assertRaises(ValueError): backend.validate_node(value)

    def test_device_discovery_must_finish_before_model_scheduling(self):
        """Discovery may start without a resource, but promotion cannot treat zero, missing or two GPUs as one."""
        for quantity in [None, '0', '2', 1, True]:
            value = node()
            if quantity is None: value['status']['allocatable'] = {}
            else: value['status']['allocatable']['nvidia.com/gpu'] = quantity
            backend.validate_node(value)
            with self.subTest(quantity=quantity), self.assertRaises(ValueError): backend.validate_node(value, require_gpu=True)

    def test_an_unrelated_router_selector_is_preserved(self):
        """The operator cannot overwrite another model route or silently reuse a partially promoted state."""
        value = {'router': {'modelServers': {'matchLabels': {
            'app.kubernetes.io/name': 'iot-llm-model', 'app.kubernetes.io/instance': 'iot-model'}}}}
        backend.validate_router(value)
        for selector in [{}, {'app.kubernetes.io/name': 'another-model'}, {'app.kubernetes.io/name': 'iot-llm-model-gpu'},
                         {'app.kubernetes.io/name': 'iot-llm-model', 'unreviewed': 'selector'}]:
            changed = deepcopy(value); changed['router']['modelServers']['matchLabels'] = selector
            with self.subTest(selector=selector), self.assertRaises(ValueError): backend.validate_router(changed)

    def test_weight_path_hash_and_complete_inventory_are_required(self):
        """A checksum command can exit zero for the wrong files; exact model provenance remains mandatory."""
        model = {'files': [{'file': 'a.gguf', 'sha256': 'a' * 64}, {'file': 'b.gguf', 'sha256': 'b' * 64}]}
        good = ('a' * 64 + '  /models/a.gguf\n' + 'b' * 64 + '  /models/b.gguf\n').encode()
        backend.validate_weights(good, model)
        for bad in [b'', good.splitlines(keepends=True)[0], good.replace(b'/models/a.gguf', b'/wrong/a.gguf'),
                    good.replace(b'a' * 64, b'c' * 64), good + good.splitlines(keepends=True)[0]]:
            with self.subTest(body=bad[:18]), self.assertRaises(ValueError): backend.validate_weights(bad, model)

    def test_escaped_or_changed_sealed_input_is_rejected(self):
        """Every source input must remain inside the owned sealed tree and retain its reviewed bytes."""
        names = ['scripts/deploy-gpu-backend.py', 'infra/llm/gpu-device-plugin.yaml', 'infra/llm/gpu-cluster.lock.json',
            'infra/llm/gpu-runtime.lock.json', 'infra/llm/assistant.lock.json', 'infra/llm/model.lock.json',
            'infra/charts/iot-gpu-model/Chart.yaml', 'infra/charts/iot-gpu-model/values.yaml',
            'infra/charts/iot-gpu-model/templates/model.yaml', 'llm/__init__.py', 'llm/benchmark.py']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source = root / 'data/llm-source/gpu/tree'; source.mkdir(parents=True)
            seal = {'files': {name: sha256(b'fixture').hexdigest() for name in names}}
            for name in names:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
            manifest = source.parent / 'manifest.json'; manifest.write_text(json.dumps(seal))
            self.assertEqual(backend.source_seal(root, source), seal)
            (source / names[0]).write_bytes(b'changed')
            with self.assertRaises(ValueError): backend.source_seal(root, source)
            (source / names[0]).write_bytes(b'fixture')
            outside = source.parent / 'outside'; outside.write_bytes(b'fixture')
            seal['files']['../outside'] = sha256(b'fixture').hexdigest(); manifest.write_text(json.dumps(seal))
            with self.assertRaises(ValueError): backend.source_seal(root, source)


if __name__ == '__main__':
    unittest.main()
