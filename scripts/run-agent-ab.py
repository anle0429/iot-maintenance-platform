"""Measure paired real retrieval-agent answers under two applied temperatures and retain every native tool history."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener, Request
from urllib.parse import urlencode
from uuid import uuid4
import argparse
import base64
import json
import os
import subprocess
import sys
import time
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from agents.ab_configuration import VARIANTS, comparison_summary
from agents.answer_acceptance import verify_specialist
from agents.probe import QUESTIONS, request, TransportFailure


def run(args):
    """Use the same three genuinely available machine snapshots; retain wrong answers instead of retrying for a better score."""
    root = args.runtime_root.resolve(); out = args.output.resolve()
    assert os.geteuid() == 0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root / 'outputs')
    seal = json.loads((ROOT.parent / 'manifest.json').read_bytes()); assert seal['format'] == 'iot-agent-ab-study-source-v1'
    for name, pin in seal['files'].items(): assert sha256((ROOT / name).read_bytes()).hexdigest() == pin, name
    deployment = json.loads(args.deployment.joinpath('verification.json').read_bytes()); assert deployment['passed']
    lease = json.loads(args.feature_lease.joinpath('verification.json').read_bytes()); now = datetime.now(timezone.utc)
    assert lease['passed'] and lease['machines'] == 200 and datetime.fromisoformat(lease['prepared_wall_utc']) <= now
    assert (datetime.fromisoformat(lease['lease_expires_utc']) - now).total_seconds() > 1200
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    assert (datetime.fromisoformat(session['auto_stop_utc']) - now).total_seconds() > 1800
    os.umask(0o077); out.mkdir(exist_ok=False); k = ['/usr/local/bin/k3s', 'kubectl']; opener = build_opener(ProxyHandler({})); private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            if isinstance(value, str) and len(value) >= 16: private.extend([value.encode(), base64.b64encode(value.encode())])
    def keep(name, body):
        """Retain original successful or failed replies only after checking the actual service credentials."""
        assert not any(value in body for value in private), name
        target = out / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body)
    def save(name, value):
        """Keep finite request clocks, explicit outcomes and original identities."""
        keep(name, (json.dumps(value, indent=2, allow_nan=False) + '\n').encode())
    def get(label, *query):
        """Read only non-secret native resources to bind each compared configuration to its actual UID/spec."""
        raw = subprocess.check_output(k + ['get', *query, '-o', 'json'], timeout=30, stderr=subprocess.PIPE)
        keep(label + '.json', raw); return json.loads(raw)
    save('source.json', seal); save('deployment-acceptance.json', deployment); save('feature-lease.json', lease)
    service = get('controller-service', 'service', 'kagent-controller', '-n', 'iot-agents'); assert service['spec']['type'] == 'ClusterIP'
    base = 'http://' + service['spec']['clusterIP'] + ':8083'
    status, body, _ = request(opener, base + '/api/me'); keep('identity.json', body); identity = json.loads(body)['sub']; assert status == 200
    status, body, _ = request(opener, base + '/api/substrate/status'); keep('actors-before.json', body); assert status == 200
    assert not any(v['atespace'] == 'iot-agents' and v['status'] == 'Running' for v in json.loads(body)['data']['actors'])
    originals = get('production-agents-before', 'sandboxagents', '-n', 'iot-agents')
    shared = get('shared-model-before', 'modelconfig', 'default-model-config', '-n', 'iot-agents')
    variants = {}
    for variant, settings in VARIANTS.items():
        agent = get('agent-' + variant + '-before', 'sandboxagent', settings['agent'], '-n', 'iot-agents')
        model = get('model-' + variant + '-before', 'modelconfig', settings['model_config'], '-n', 'iot-agents')
        assert agent['metadata']['uid'] == deployment['variants'][variant]['agent_uid']
        assert model['metadata']['uid'] == deployment['variants'][variant]['model_config_uid'] and model['spec']['openAI']['temperature'] == settings['temperature']
        variants[variant] = {'agent': agent, 'model': model}
        status, body, _ = request(opener, base + '/api/a2a-sandboxes/iot-agents/' + settings['agent'] + '/.well-known/agent-card.json', user_id=identity)
        keep('agent-' + variant + '-card.json', body); card = json.loads(body)
        assert status == 200 and card['name'] == settings['agent'].replace('-', '_') and card['version'] == '0.2.2'
    feature_service = get('feature-service', 'service', 'iot-feature-api', '-n', 'iot-features'); machines = []
    for index in range(1, 11):
        machine = 'P-' + str(index).zfill(3)
        status, body, _ = request(opener, 'http://' + feature_service['spec']['clusterIP'] + '/v1/machines/' + machine + '/features')
        keep('feature-preflight-' + machine + '.json', body); assert status == 200
        if json.loads(body)['operating_context']['status'] == 'operating': machines.append(machine)
        if len(machines) == 3: break
    assert len(machines) == 3; save('machines.json', machines); rows = []
    for case, machine in enumerate(machines):
        for variant in (['a', 'b'] if case % 2 == 0 else ['b', 'a']):
            settings = VARIANTS[variant]; label = str(case) + '-' + variant; context = str(uuid4()); request_id = str(uuid4())
            payload = {'jsonrpc': '2.0', 'id': request_id, 'method': 'message/send',
                'params': {'message': {'role': 'user', 'messageId': str(uuid4()), 'contextId': context,
                    'parts': [{'kind': 'text', 'text': QUESTIONS['retrieval'].replace('P-001', machine)}]}, 'configuration': {'blocking': True}}}
            save(label + '/request.json', payload)
            row = {'variant': variant, 'machine_id': machine, 'context_id': context, 'request_id': request_id, 'passed': False}
            try:
                status, body, elapsed = request(opener, base + '/api/a2a-sandboxes/iot-agents/' + settings['agent'], payload, user_id=identity, timeout=240)
                keep(label + '/response.json', body); row.update(http_status=status, elapsed_seconds=elapsed, response_complete=True)
                reply = json.loads(body)
                assert status == 200 and reply.get('result', {}).get('contextId') == context
                accepted = verify_specialist(reply, 'retrieval', machine_id=machine, knowledge_root=ROOT / 'knowledge')
                save(label + '/answer-acceptance.json', accepted); row.update(passed=True, task_id=accepted['task_id'], successful_tool_events=accepted['successful_tool_events'], tool_error_events=accepted['tool_error_events'])
            except TransportFailure as error:
                keep(label + '/partial-response.bin', error.partial); row.update(http_status=error.status, elapsed_seconds=error.elapsed, response_complete=False, failure_type=error.error_type)
            except (ValueError, AssertionError) as error:
                row['failure_type'] = type(error).__name__
            save(label + '/outcome.json', row); rows.append(row); save('outcomes.json', rows); print(json.dumps(row), flush=True)
    assert len({r['context_id'] for r in rows}) == len({r['request_id'] for r in rows}) == 6
    summary = comparison_summary(rows, machines); save('summary.json', summary)
    after = get('production-agents-after', 'sandboxagents', '-n', 'iot-agents')
    assert {v['metadata']['uid']: v['spec'] for v in originals['items']} == {v['metadata']['uid']: v['spec'] for v in after['items']}
    assert get('shared-model-after', 'modelconfig', 'default-model-config', '-n', 'iot-agents')['spec'] == shared['spec']
    for variant, settings in VARIANTS.items():
        for kind, name in [('sandboxagent', settings['agent']), ('modelconfig', settings['model_config'])]:
            current = get(variant + '-' + kind + '-after', kind, name, '-n', 'iot-agents')
            assert current['spec'] == variants[variant]['agent' if kind == 'sandboxagent' else 'model']['spec']
    mapping = {'requests': 'requests', 'grounded_answers': 'tool_grounded_answers', 'median_complete_seconds': 'median_complete_seconds'}; lines = []
    for suffix, field in mapping.items():
        lines.append('# TYPE iot_agent_ab_' + suffix + ' gauge')
        for variant in VARIANTS: lines.append('iot_agent_ab_' + suffix + '{variant="' + variant + '"} ' + str(summary[variant][field]))
    metrics = ('\n'.join(lines) + '\n').encode(); keep('experiment.prom', metrics)
    with opener.open(Request('http://127.0.0.1:19092/metrics/job/iot-agent-ab/run/' + out.name, data=metrics, method='PUT', headers={'Content-Type': 'text/plain; version=0.0.4'}), timeout=15) as response:
        save('pushgateway.json', {'status': response.status}); assert response.status == 200
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_commit': seal['commit'], 'summary': summary,
        'requests': 6, 'distinct_machine_questions': 3, 'failed_answers_retained': True, 'production_specs_unchanged': True,
        'scope': 'Paired retrieval-agent temperature configurations on the same 7B model and real feature lease. Outcomes use original A2A/MCP histories, not fluent text alone. Six requests do not establish a general agent-quality ranking; no automatic promotion.'}
    save('verification.json', report); print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root', 'output', 'deployment', 'feature-lease']: parser.add_argument('--' + name, type=Path, required=True)
    run(parser.parse_args())
