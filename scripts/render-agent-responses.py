#!/usr/bin/env python3
"""Apply versioned compact-answer prompts to only the three owned SandboxAgents in a Helm render."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import sys
import yaml


def render(documents, policy_bytes):
    """Preserve tools, networks, pools and other resources; reject an incomplete or unexpected agent set."""
    policy = json.loads(policy_bytes)
    if policy['version'] != '0.2.2' or set(policy['prompts']) != {'retrieval', 'drift', 'coordinator'}:
        raise ValueError('Use the reviewed three-role response policy')
    if any(not isinstance(p, str) or not 1 <= len(p) <= 3000 for p in policy['prompts'].values()):
        raise ValueError('Each role requires one bounded, nonempty prompt')
    documents = deepcopy(list(documents)); seen = set()
    for body in documents:
        if not body or body.get('kind') != 'SandboxAgent':
            continue
        name = body.get('metadata', {}).get('name', '')
        role = name.removeprefix('iot-')
        if name != 'iot-' + role or role not in policy['prompts'] or role in seen:
            raise ValueError('Unexpected or repeated SandboxAgent in the owned release')
        if body.get('metadata', {}).get('namespace', 'iot-agents') != 'iot-agents':
            raise ValueError('The response policy is scoped to iot-agents')
        seen.add(role)
        body['spec']['version'] = policy['version']
        body['spec']['declarative']['systemMessage'] = policy['prompts'][role]
        body['metadata'].setdefault('annotations', {})['iot.coursework/response-policy-sha256'] = sha256(policy_bytes).hexdigest()
    if seen != set(policy['prompts']):
        raise ValueError('All three owned agent roles are required')
    return documents


if __name__ == '__main__':
    policy_path = Path(__file__).resolve().parents[1] / 'agents/response-policy.json'
    result = render(yaml.safe_load_all(sys.stdin), policy_path.read_bytes())
    yaml.safe_dump_all(result, sys.stdout, sort_keys=False)
