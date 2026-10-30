"""Bind original infrastructure run captures to the retained private GPU worker and executed Ansible role."""
from hashlib import sha256
import json
from integration_evidence import ROOT, capsule, read


def verify():
    """Authenticate three untouched images and full displayed logs without claiming a new infrastructure run."""
    base, count = capsule('llm-iac-ui', '2ae6e31791a3713c59569979db06d61734e2cb9d2e6f41ffd58130687f1aa28b')
    assert count == 7
    gpu, _ = capsule('gpu-inference', '23cc15623f3f123b304c04e6c8ef467e0807b1f966460ccde55614ab3ee77a5e')
    agents, _ = capsule('gpu-agents', '0abdbfa0787b041fcde0487995500c216849ea009dada0cff83b23160043d2d1')
    index = read(base, 'index.json'); assert len(index['captures']) == 3 and not index['cloud_resources_added'] and not index['configuration_rerun']
    expected = {
        'terraform-noop': ('docs/evidence/gpu-inference/cloud/gpu-worker-29r-noop-plan.txt', '378a72df0ffb199a15a115a4bb20a46e1be56c1450891b7b9b8531826350876c'),
        'ansible-runtime': ('docs/evidence/gpu-inference/runtime/native/ansible.stdout', '2297e6a2a0b311baa8121e756d6d774e2fb73e8341f152d31e67ac1e5ae53f3b'),
        'ansible-node-role': ('docs/evidence/gpu-agents/gpu/native/ansible.stdout', '91ce72410cb7beea32a5035a1e016022bf1f2a6d8300a43595dc86e9df824353')}
    for row in index['captures']:
        name = row['name']; source, digest = expected[name]
        assert row['source'] == source and row['source_sha256'] == digest and row['unedited'] and row['format'] == 'JPEG'
        raw = (ROOT/source).read_bytes(); assert sha256(raw).hexdigest() == digest
        assert raw.decode().replace('\r\n', '\n').strip() in (base/'ui'/(name+'.ax.txt')).read_text(encoding='utf-8')
        image = (base/'ui'/(name+'.jpg')).read_bytes(); assert image.startswith(b'\xff\xd8') and image.endswith(b'\xff\xd9')
    vm = read(gpu, 'cloud/gpu-worker-29t-readback.json')
    assert vm['name'] == 'iot-cw-gpu' and vm['id'] == '11538226507322059' and vm['status'] == 'RUNNING'
    assert not any(v.get('accessConfigs') for v in vm['networkInterfaces'])
    assert vm['scheduling']['instanceTerminationAction'] == 'STOP' and vm['scheduling']['maxRunDuration']['seconds'] == '28800'
    assert 'No changes. Your infrastructure matches the configuration.' in (ROOT/expected['terraform-noop'][0]).read_text()
    for filename in ['main.tf', '.terraform.lock.hcl']:
        assert (gpu/'infrastructure'/filename).read_bytes() == (ROOT/'infra/gcp/inference'/filename).read_bytes()
    for folder, recap in [(gpu/'runtime/native', 'ok=20   changed=10'), (agents/'gpu/native', 'ok=22   changed=8')]:
        text = (folder/'ansible.stdout').read_text()
        assert recap in text and 'unreachable=0    failed=0' in text and (folder/'ansible.exit').read_text().strip() == '0'
        assert read(folder, 'verification.json')['passed']
    seal = read(agents, 'gpu/source/manifest.json')
    for name, pin in seal['files'].items():
        if name == 'bootstrap.py': continue
        original = (agents/'gpu/source/tree'/name).read_bytes()
        assert sha256(original).hexdigest() == pin and original == (ROOT/name).read_bytes()
    nodes = read(agents, 'cpu/readback/nodes.json')['items']; worker = next(v for v in nodes if v['metadata']['name'] == 'iot-cw-gpu')
    assert any(v['type'] == 'Ready' and v['status'] == 'True' for v in worker['status']['conditions'])
    assert worker['status']['capacity']['nvidia.com/gpu'] == '1'
    return {'passed': True, 'original_captures': 3, 'terraform': 'Retained private V100 allocation and no-op plan',
            'ansible_runtime': '20 tasks; 10 changed; no failure', 'ansible_cluster_role': '22 tasks; 8 changed; no failure',
            'scope': 'Historical successful execution and original readbacks; screenshots add no new infrastructure or rerun.'}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
