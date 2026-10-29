"""Bind LLM pipeline releases to exact source and preserve accepted agent capabilities during deployment."""
from hashlib import sha256
from pathlib import Path
import json
import re

COMPONENTS = ('rag', 'retrieval', 'drift', 'coordinator')
PHASES = ('test', 'build', 'deploy', 'verify')
ROLES = ('retrieval', 'drift', 'coordinator')


def release_identity(component, build, commit):
    """Give each Jenkins job/build/source a distinct bounded release rather than sharing numbered output paths."""
    if component not in COMPONENTS or not re.fullmatch(r'[1-9][0-9]{0,8}', str(build)) or isinstance(build, bool):
        raise ValueError('Use one declared component and a positive bounded Jenkins build number')
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Pin the full source commit')
    return {'component': component, 'build_number': str(build), 'source_commit': commit,
            'release': 'llm-' + component + '-jenkins-' + str(build)}


def validate_source(root, seal, commit):
    """Reject escaped, changed, secret or runtime-state files before testing or deploying a sealed CI source."""
    root = Path(root).resolve()
    if seal.get('format') != 'iot-sealed-llm-ci-v1' or seal.get('commit') != commit or not seal.get('files'):
        raise ValueError('The CI source seal belongs to another revision or format')
    prohibited = {'data', 'outputs', '.git', '.venv', '.terraform', 'AGENTS.md', 'START_HERE.md',
                  'status.md', 'decisions.md', 'roadmap.md', 'two-machine-workflow.md', '.env'}
    for name, digest in seal['files'].items():
        relative = Path(name); path = (root / relative).resolve()
        if (relative.is_absolute() or '..' in relative.parts or prohibited.intersection(relative.parts)
                or not path.is_relative_to(root) or not path.is_file()
                or name.endswith(('.tfstate', '.tfvars', '.key', '.pem', '.gguf', '.safetensors'))):
            raise ValueError('CI source must contain only project code, configuration and selected evidence')
        if not re.fullmatch(r'[a-f0-9]{64}', digest) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Sealed CI source bytes changed: ' + name)
    return sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def require_receipt(value, identity, phase):
    """Reject success copied from a different job, build, source or stage; native artifact checks remain separate."""
    if phase not in PHASES or value.get('schema_version') != 1 or value.get('phase') != phase:
        raise ValueError('CI phase receipt has an unexpected schema or stage')
    if value.get('passed') is not True or any(value.get(key) != expected for key, expected in identity.items()):
        raise ValueError('CI phase receipt is unsuccessful or belongs to another release')
    return value


def validate_agents(documents, policy):
    """Require the current three-role policy and exact read-only tool/delegation capabilities in a packaged chart."""
    agents = [doc for doc in documents if doc and doc.get('kind') == 'SandboxAgent']
    expected = {'iot-' + role for role in ROLES}
    if (len(agents) != 3 or {doc['metadata']['name'] for doc in agents} != expected
            or policy.get('version') != '0.2.2' or set(policy.get('prompts', {})) != set(ROLES)):
        raise ValueError('Preserve all three accepted policy 0.2.2 agents')
    for doc in agents:
        role = doc['metadata']['name'].removeprefix('iot-'); spec = doc['spec']; declarative = spec['declarative']
        if (doc.get('apiVersion') != 'kagent.dev/v1alpha2' or doc['metadata'].get('namespace', 'iot-agents') != 'iot-agents'
                or spec.get('version') != policy['version'] or spec.get('type') != 'Declarative'
                or spec.get('allowedNamespaces') != {'from':'Same'}
                or declarative.get('runtime') != 'go' or declarative.get('executeCodeBlocks') is not False
                or declarative.get('modelConfig') != 'default-model-config'
                or declarative.get('systemMessage') != policy['prompts'][role]
                or spec.get('substrate', {}).get('workerPoolRef') != {'name':'iot-' + role + '-pool'}):
            raise ValueError('The chart would change an accepted agent identity, runtime or capability')
        tools = declarative['tools']
        if role == 'coordinator':
            expected_tools = [{'type':'Agent', 'isolateSessions':True,
                'agent':{'name':'iot-' + other, 'namespace':'iot-agents', 'kind':'SandboxAgent', 'apiGroup':'kagent.dev'}}
                for other in ['retrieval', 'drift']]
        else:
            names = ['get_machine_features','predict_failure','search_knowledge'] if role == 'retrieval' else ['assess_feature_drift']
            expected_tools = [{'type':'McpServer', 'mcpServer':{'name':'iot-' + role + '-tools', 'kind':'RemoteMCPServer',
                                'apiGroup':'kagent.dev', 'toolNames':names}}]
        if tools != expected_tools: raise ValueError('The chart changes the reviewed read-only tool or delegation set')
        domains = spec.get('sandbox', {}).get('network', {}).get('allowedDomains', [])
        expected_domains = {'iot-router-epp.iot-llm.svc.cluster.local'}
        if role == 'coordinator': expected_domains |= {'kagent-controller.iot-agents.svc.cluster.local', 'atenet-router.ate-system.svc'}
        else: expected_domains.add('iot-' + role + '-mcp.iot-agents.svc.cluster.local')
        if len(domains) != len(set(domains)) or set(domains) != expected_domains:
            raise ValueError('The chart changes the declared private network destinations')
    return True
