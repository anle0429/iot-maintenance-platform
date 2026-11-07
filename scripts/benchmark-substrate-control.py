"""Compare a matched retrieval SandboxAgent with a prepared ordinary Agent using alternating real calls."""
from pathlib import Path
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4
from urllib.request import build_opener, ProxyHandler
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.probe import QUESTIONS, request
from agents.answer_acceptance import verify_specialist
from infra.substrate_comparison import summarize


def run(root, source, output, feature_run):
    """Require matched native specs, fresh data and an idle machine; retain every attempt and original reply."""
    root, source, output = root.resolve(), source.resolve(), output.resolve()
    if os.geteuid() != 0 or not output.is_relative_to(root / 'outputs') or not source.is_relative_to(root / 'data/llm-source'):
        raise ValueError('Require sudo, a sealed project source and a new project output')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Source packet changed')
    lease_path = (root / 'outputs' / feature_run / 'verification.json').resolve()
    if not lease_path.is_relative_to(root / 'outputs'): raise ValueError('Require an owned feature lease')
    lease = json.loads(lease_path.read_bytes()); now = datetime.now(timezone.utc)
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (not lease['passed'] or lease['machines'] != 200
            or (datetime.fromisoformat(lease['lease_expires_utc']) - now).total_seconds() < 600
            or (datetime.fromisoformat(session['auto_stop_utc']) - now).total_seconds() < 1200):
        raise ValueError('Require genuinely fresh features and session time for all ten calls')
    output.mkdir(exist_ok=False); private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        private.extend(value.encode() for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values())

    def keep(name, raw):
        """Preserve native bytes after refusing actual runtime credential values."""
        if any(value in raw for value in private): raise ValueError('Credential-bearing receipt refused')
        (output / name).write_bytes(raw)

    def get(kind, name='', selector=None):
        """Read bounded Kubernetes objects without changing either deployment mode."""
        args = ['/usr/local/bin/k3s', 'kubectl', 'get', kind]
        if name: args.append(name)
        args += ['-n', 'iot-agents', '-o', 'json']
        if selector: args += ['-l', selector]
        return json.loads(subprocess.check_output(args, timeout=30))

    a = get('sandboxagent', 'iot-retrieval'); b = get('agent', 'iot-retrieval-plain')
    model = get('modelconfig', 'default-model-config')
    if (any(b['spec'].get(k) != v for k, v in a['spec'].items() if k not in {'substrate', 'declarative'})
            or any(b['spec']['declarative'].get(k) != v for k, v in a['spec']['declarative'].items())
            or model['spec']['openAI']['baseUrl'] != 'http://iot-agentgateway-candidate.iot-agents.svc.cluster.local:18080/v1'):
        raise ValueError('Control must use the same current model, prompt, tools and execution contract')
    pods = get('pods', selector='iot.project/substrate-control=20261106')['items']
    ready = [p for p in pods if not p['metadata'].get('deletionTimestamp') and
             any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status'].get('conditions', []))]
    if len(ready) != 1: raise ValueError('Require one identifiable Ready ordinary control Pod')
    pools = get('workerpools')['items']
    if any(p['spec']['replicas'] != 2 for p in pools): raise ValueError('Begin only with idle worker pools')
    config = get('deployment', 'kagent-controller')
    controller_env = {e['name']: e.get('value') for c in config['spec']['template']['spec']['containers'] for e in c.get('env', [])}
    plain_env = {e['name']: e.get('value') for c in ready[0]['spec']['containers'] for e in c.get('env', [])}
    if controller_env.get('OTEL_TRACING_ENABLED') != 'true' or plain_env.get('OTEL_TRACING_ENABLED') != 'true':
        raise ValueError('Both execution modes must use enabled controller tracing')
    for name, value in [('substrate-before', a), ('plain-before', b), ('model-before', model),
                        ('control-pods-before', pods), ('controller-config-before', config), ('source', seal), ('feature-lease', lease)]:
        keep(name + '.json', (json.dumps(value, indent=2) + '\n').encode())
    opener = build_opener(ProxyHandler({})); base = 'http://' + get('service', 'kagent-controller')['spec']['clusterIP'] + ':8083'
    code, body, _ = request(opener, base + '/api/me')
    if code != 200: raise ValueError('Controller identity failed')
    user = json.loads(body)['sub']; rows = []
    paths = {'substrate': '/api/a2a-sandboxes/iot-agents/iot-retrieval', 'plain': '/api/a2a/iot-agents/iot-retrieval-plain'}
    for pair in range(5):
        for arm in (['substrate', 'plain'] if pair % 2 == 0 else ['plain', 'substrate']):
            prefix = str(pair) + '-' + arm
            payload = {'jsonrpc': '2.0', 'id': str(uuid4()), 'method': 'message/send', 'params': {'message': {
                'role': 'user', 'messageId': str(uuid4()), 'contextId': str(uuid4()),
                'parts': [{'kind': 'text', 'text': QUESTIONS['retrieval']}]}, 'configuration': {'blocking': True}}}
            keep(prefix + '-request.json', (json.dumps(payload, indent=2) + '\n').encode())
            row = {'pair': pair, 'arm': arm, 'warmup': pair == 0, 'started_utc': datetime.now(timezone.utc).isoformat(),
                   'question_sha256': sha256(QUESTIONS['retrieval'].encode()).hexdigest(), 'accepted': False}
            try:
                code, body, seconds = request(opener, base + paths[arm], payload, user_id=user, timeout=180)
                keep(prefix + '-response.json', body)
                accepted = verify_specialist(json.loads(body), 'retrieval', knowledge_root=source / 'knowledge')
                row.update(http_status=code, elapsed_seconds=seconds, task=accepted['task_id'], acceptance=accepted,
                           accepted=code == 200 and accepted['passed'])
                if not row['accepted']: raise ValueError('Actual tool-grounded task was not accepted')
            except Exception as error:
                row['error_type'] = type(error).__name__; rows.append(row)
                keep('results.json', (json.dumps(rows, indent=2) + '\n').encode()); raise
            rows.append(row); keep('results.json', (json.dumps(rows, indent=2) + '\n').encode())
    for kind, name, before in [('sandboxagent', 'iot-retrieval', a), ('agent', 'iot-retrieval-plain', b),
                                ('modelconfig', 'default-model-config', model)]:
        after = get(kind, name)
        keep(kind + '-after.json', (json.dumps(after, indent=2) + '\n').encode())
        if after['metadata']['uid'] != before['metadata']['uid'] or after['spec'] != before['spec']:
            raise ValueError('The compared configuration changed during measurement')
    report = summarize(rows)
    report.update(utc=datetime.now(timezone.utc).isoformat(), shared_gateway_model_prompt_tools_unchanged=True,
                  scope='Four alternating retrieval pairs, fresh contexts and a fixed question. End-to-end latency includes allocation, MCP, generation and transport. The ordinary Deployment and Substrate workers have different execution wrappers; this is not an isolated startup, cost or general speed ranking.')
    keep('verification.json', (json.dumps(report, indent=2) + '\n').encode()); print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ['runtime-root', 'source-root', 'output']: parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--feature-run', required=True)
    args = parser.parse_args(); run(args.runtime_root, args.source_root, args.output, args.feature_run)
