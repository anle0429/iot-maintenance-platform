"""Publish observed sandbox-agent identities and exact deployment hashes to the private native catalog."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
import argparse
import json
import subprocess
import yaml


def run(root, output):
    """Bind catalog entries to actual sandbox resources without claiming the catalog deploys their configuration."""
    output = output.resolve(); root = root.resolve()
    if not output.is_relative_to(root / 'outputs'):
        raise ValueError('Keep catalog receipts in a fresh project output directory')
    output.mkdir(exist_ok=False)
    k = ['/usr/local/bin/k3s', 'kubectl']

    def read(kind):
        """Read native objects privately; publish only the specifically selected public identities."""
        return json.loads(subprocess.check_output(k + ['get', kind, '-n', 'iot-agents', '-o', 'json']))

    service = json.loads(subprocess.check_output(k + ['get', 'service', 'iot-agent-registry', '-n', 'iot-agents', '-o', 'json']))
    base = 'http://' + service['spec']['clusterIP'] + ':12121'
    agents = {r['metadata']['name']: r for r in read('sandboxagents')['items']}
    templates = read('actortemplates')['items']; opener = build_opener(ProxyHandler({})); results = []
    for role in ['retrieval', 'drift', 'coordinator']:
        name = 'iot-' + role; agent = agents[name]
        selected = [t for t in templates if any(o.get('uid') == agent['metadata']['uid'] for o in t['metadata'].get('ownerReferences', []))]
        if len(selected) != 1 or selected[0].get('status', {}).get('phase') != 'Ready':
            raise ValueError('Require the single current Ready native template before catalog publication: ' + name)
        template = selected[0]
        image = template['spec']['containers'][0].get('image')
        if not image:
            raise ValueError('The native actor template has no observed runtime image')
        spec_bytes = json.dumps(agent['spec'], sort_keys=True, separators=(',', ':')).encode()
        entry = {'apiVersion': 'ar.dev/v1alpha1', 'kind': 'Agent',
            'metadata': {'name': name, 'tag': '0.1.0', 'annotations': {
                'iot.coursework/resource-uid': agent['metadata']['uid'],
                'iot.coursework/spec-sha256': sha256(spec_bytes).hexdigest(),
                'iot.coursework/actor-template': template['metadata']['name'],
                'iot.coursework/deployment-source': 'infra/charts/iot-agents'}},
            'spec': {'title': 'IoT ' + role + ' agent', 'description': agent['spec']['description'],
                     'source': {'image': image, 'protocol': 'A2A'}}}
        payload = yaml.safe_dump(entry, sort_keys=False).encode()
        (output / (name + '-entry.yaml')).write_bytes(payload)
        started = datetime.now(timezone.utc).isoformat()
        try:
            with opener.open(Request(base + '/v0/apply', data=payload, headers={'Content-Type': 'application/yaml'}), timeout=60) as response:
                status, body = response.status, response.read()
        except HTTPError as error:
            status, body = error.code, error.read()
        (output / (name + '-apply.json')).write_bytes(body)
        results.append({'name': name, 'applied_utc': started, 'status': status, 'resource_uid': agent['metadata']['uid'],
                        'spec_sha256': sha256(spec_bytes).hexdigest(), 'observed_image': image})
        (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        if status not in [200, 201]:
            raise ValueError('Registry rejected ' + name + ': HTTP ' + str(status))
        with opener.open(base + '/v0/agents/' + name + '/0.1.0', timeout=30) as response:
            observed = response.read()
        (output / (name + '-readback.json')).write_bytes(observed)
        actual = json.loads(observed)
        # The versioned API may wrap its artifact; retain that native shape and check exact binding values.
        if agent['metadata']['uid'].encode() not in observed or sha256(spec_bytes).hexdigest().encode() not in observed:
            raise ValueError('Catalog readback did not preserve deployment provenance')
    with opener.open(base + '/v0/agents', timeout=30) as response:
        (output / 'catalog.json').write_bytes(response.read())
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'entries': results,
        'scope': 'Private catalog publication of observed runtime image and native configuration identity. This does not make the catalog a deployment controller.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); run(args.runtime_root, args.output)
