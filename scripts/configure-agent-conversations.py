"""Promote a sealed compact-answer policy and bounded response budget on the existing private agent releases."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import yaml

REQUIRED_SOURCE = {
    'agents/response-policy.json', 'infra/agents/conversation-budget.yaml', 'infra/agents/versions.lock.json',
    'scripts/configure-agent-conversations.py', 'scripts/render-agent-responses.py',
    'infra/charts/iot-agents/Chart.yaml', 'infra/charts/iot-agents/values.yaml',
    'infra/charts/iot-agents/templates/agents.yaml', 'infra/charts/iot-agents/templates/network.yaml',
    'infra/charts/iot-agents/templates/pools.yaml', 'infra/charts/iot-agents/templates/tools.yaml',
}


def approved_source(root, source):
    """Reject escaped or modified source members before any cluster mutation."""
    if not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Use a sealed source under the project data/llm-source directory')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    if not REQUIRED_SOURCE.issubset(seal.get('files', {})):
        raise ValueError('The source seal must cover every operator, policy and chart input')
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Sealed source differs: ' + name)
    return seal


def resources(root, now):
    """Measure the actual approved session, available RAM and existing SSD reserve without extending them."""
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    remaining = (datetime.fromisoformat(session['auto_stop_utc']) - now).total_seconds()
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    free = shutil.disk_usage('/srv/iot-fast').free
    if remaining < 4500 or available < 16 * 1024**3 or free < 25 * 1024**3:
        raise ValueError('Require 75 approved session minutes, 16 GiB available RAM and 25 GiB free SSD')
    return {'remaining_session_seconds': remaining, 'available_ram_bytes': available, 'ssd_free_bytes': free,
        'new_cloud_resources': False, 'session_extended': False}


def reconciled_agents(agents, expected_names):
    """Reject stale Ready conditions inherited from an earlier agent generation."""
    if {a['metadata']['name'] for a in agents} != expected_names:
        return False
    for agent in agents:
        generation = agent['metadata']['generation']; status = agent.get('status', {})
        if status.get('observedGeneration') != generation:
            return False
        for name in ['Accepted', 'Ready']:
            if not any(c.get('type') == name and c.get('status') == 'True'
                       and c.get('observedGeneration') == generation for c in status.get('conditions', [])):
                return False
    return True


def validate_runtime(current, properties):
    """Require the owned local endpoint, verified substrate TLS and actual model context before changing response budgets."""
    provider = current['providers']['openAI']; substrate = current['controller']['substrate']
    if (not substrate['enabled'] or substrate['ateApiInsecure'] or provider['model'] != 'iot-qwen2.5-7b'
            or provider['config']['baseUrl'] != 'http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1'):
        raise ValueError('Preserve the existing internal model and TLS-verified substrate configuration')
    context = properties.get('default_generation_settings', {}).get('n_ctx', 0)
    if type(context) is not int or context < 8192:
        raise ValueError('Require at least 8192 actual model context tokens before increasing output allowance')


def run(root, source, run_name):
    """Apply two separately atomic Helm upgrades and retain applied-object evidence without claiming chat acceptance."""
    root = root.resolve(); source = source.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not re.fullmatch(r'agent-conversations-[a-z0-9-]+', run_name):
        raise ValueError('Run a fresh agent-conversations identifier with sudo on the retained VM')
    seal = approved_source(root, source); budget = resources(root, datetime.now(timezone.utc))
    policy_bytes = (source / 'agents/response-policy.json').read_bytes(); policy = json.loads(policy_bytes)
    desired = yaml.safe_load((source / 'infra/agents/conversation-budget.yaml').read_text())
    if (policy['version'] != '0.2.0' or desired['controller']['a2aClientTimeout'] != '1800s'
            or desired['providers']['openAI']['config'] != {'maxTokens': 768, 'timeout': 600}):
        raise ValueError('Use the reviewed response-policy version and budget')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    os.environ['HOME'] = str(root / 'data/runtime/agent-operator-home')
    os.environ['PATH'] = str(Path(sys.executable).parent) + ':' + os.environ['PATH']
    k = ['/usr/local/bin/k3s', 'kubectl']; helm = str(root / 'data/runtime/kubernetes-assets-27/helm/helm')
    active = subprocess.check_output(['systemctl', 'list-units', '--state=running', '--no-legend', 'iot-agent-chat-*'])
    if active.strip():
        raise ValueError('Preserve the running agent chat before changing its policy')
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            private.extend([value.encode(), base64.b64encode(value.encode()), base64.b64encode(('Bearer ' + value).encode())])
    output = root / 'outputs' / run_name; output.mkdir(exist_ok=False)

    def command(label, args, *, payload=None, timeout=660):
        """Retain exact bounded command receipts after checking actual credential values and encodings."""
        result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout)
        if any(secret in result.stdout or secret in result.stderr for secret in private):
            raise ValueError('Refuse a credential-bearing command receipt: ' + label)
        (output / (label + '.stdout')).write_bytes(result.stdout)
        (output / (label + '.stderr')).write_bytes(result.stderr)
        (output / (label + '.exit')).write_text(str(result.returncode) + '\n')
        if result.returncode:
            raise RuntimeError(label + ' failed; retain the failed run')
        return result.stdout

    (output / 'source.json').write_text(json.dumps(seal, indent=2) + '\n')
    (output / 'budget.json').write_text(json.dumps(budget, indent=2) + '\n')
    phase = 'preflight'
    try:
        before = json.loads(command('agents-before', k + ['get', 'sandboxagents', '-n', 'iot-agents', '-o', 'json']))['items']
        expected_names = {'iot-' + role for role in policy['prompts']}
        if {a['metadata']['name'] for a in before} != expected_names or any(a['spec']['version'] != '0.1.0' for a in before):
            raise ValueError('Preserve a different live agent set or policy version instead of overwriting it')
        current = json.loads(command('model-before-values', [helm, 'get', 'values', 'kagent', '-n', 'iot-agents', '-o', 'json']))
        service = json.loads(command('model-service', k + ['get', 'service', 'iot-model', '-n', 'iot-llm', '-o', 'json']))
        with build_opener(ProxyHandler({})).open('http://' + service['spec']['clusterIP'] + ':8080/props', timeout=30) as response:
            properties = response.read()
        if any(secret in properties for secret in private):
            raise ValueError('Refuse credential-bearing model properties')
        (output / 'model-properties.json').write_bytes(properties)
        validate_runtime(current, json.loads(properties))
        chart = root / 'outputs/agent-foundation-research/kagent-0.10.3.tgz'
        lock = json.loads((source / 'infra/agents/versions.lock.json').read_bytes())
        expected_chart = next(v for v in lock['charts'] if v['name'] == 'kagent')
        if sha256(chart.read_bytes()).hexdigest() != expected_chart['sha256']:
            raise ValueError('The pinned kagent chart differs')
        renderer = source / 'scripts/render-agent-responses.py'; renderer.chmod(0o755)
        agent_chart = str(source / 'infra/charts/iot-agents')
        agent_values = command('agent-before-values', [helm, 'get', 'values', 'iot-agents', '-n', 'iot-agents', '-o', 'json'])
        agent_values_path = output / 'agent-values.json'
        agent_values_path.write_text(json.dumps(json.loads(agent_values) or {}, indent=2) + '\n')
        rendered = command('rendered', [helm, 'template', 'iot-agents', agent_chart, '-n', 'iot-agents',
            '-f', str(agent_values_path), '--post-renderer', str(renderer)])
        command('server-validation', k + ['apply', '--dry-run=server', '-f', '-'], payload=rendered)
        current['controller']['a2aClientTimeout'] = '1800s'
        current['providers']['openAI']['config'].update(desired['providers']['openAI']['config'])
        values_path = output / 'model-values.json'; values_path.write_text(json.dumps(current, indent=2) + '\n')
        phase = 'model-budget'
        command('model-upgrade', [helm, 'upgrade', 'kagent', str(chart), '-n', 'iot-agents', '-f', str(values_path),
            '--atomic', '--wait', '--timeout', '5m'])
        phase = 'agent-prompts'
        command('agent-upgrade', [helm, 'upgrade', 'iot-agents', agent_chart, '-n', 'iot-agents', '-f', str(agent_values_path),
            '--post-renderer', str(renderer), '--atomic', '--wait', '--timeout', '8m'])
        deadline = time.monotonic() + 600; attempt = 0
        while True:
            attempt += 1
            after = json.loads(command('agents-reconcile-' + str(attempt),
                k + ['get', 'sandboxagents', '-n', 'iot-agents', '-o', 'json']))['items']
            if reconciled_agents(after, expected_names):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('The new agent generation did not become Accepted and Ready')
            time.sleep(5)
        (output / 'agents-after.json').write_text(json.dumps(after, indent=2) + '\n')
        actual = {a['metadata']['name']: a for a in after}
        for role, prompt in policy['prompts'].items():
            spec = actual['iot-' + role]['spec']
            if spec['version'] != policy['version'] or spec['declarative']['systemMessage'] != prompt:
                raise ValueError('Applied agent prompt/version differs from source')
        observed = json.loads(command('model-config', k + ['get', 'modelconfig', 'default-model-config', '-n', 'iot-agents', '-o', 'json']))
        if observed['spec']['openAI']['maxTokens'] != 768 or observed['spec']['openAI']['timeout'] != 600:
            raise ValueError('Applied model response budget differs')
        report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'policy_version': policy['version'],
            'policy_sha256': sha256(policy_bytes).hexdigest(), 'chat_acceptance_verified': False,
            'registry_republication_verified': False, 'coursework_complete': False,
            'scope': 'Applied compact prompts and generation/delegation budgets only. Fresh data, complete native conversations, registry version publication and acceptance remain required.'}
    except Exception as error:
        report = {'passed': False, 'phase': phase, 'error_type': type(error).__name__,
            'chat_acceptance_verified': False, 'coursework_complete': False,
            'scope': 'Two Helm releases are individually atomic, not a cross-release transaction. Retain original receipts and inspect both releases before retry.'}
        (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
        raise
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n'); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.run)
