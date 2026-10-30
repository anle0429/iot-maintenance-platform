"""Create two scoped retrieval-agent ModelConfigs and verify the reconciled sandbox templates and compiled temperatures."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from agents.ab_configuration import VARIANTS, configuration_plan


def run(args):
    """Preserve production resources, read credentials only in memory and require actual current-generation Ready templates."""
    root = args.runtime_root.resolve(); out = args.output.resolve()
    assert os.geteuid() == 0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root / 'outputs')
    seal = json.loads((ROOT.parent / 'manifest.json').read_bytes()); assert seal['format'] == 'iot-agent-ab-source-v1'
    for name, pin in seal['files'].items(): assert sha256((ROOT / name).read_bytes()).hexdigest() == pin, name
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    assert (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() > 1800
    ram = int(next(v.split()[1] for v in Path('/proc/meminfo').read_text().splitlines() if v.startswith('MemAvailable:'))) * 1024
    assert ram > 8 * 1024 ** 3 and shutil.disk_usage('/srv/iot-fast').free > 20 * 1024 ** 3
    os.umask(0o077); out.mkdir(exist_ok=False); k = ['/usr/local/bin/k3s', 'kubectl']
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            if isinstance(value, str) and len(value) >= 16: private.extend([value.encode(), base64.b64encode(value.encode())])
    def keep(name, body):
        """Save public native metadata after scanning actual service credentials, without ever writing a Secret body."""
        assert not any(value in body for value in private), name
        (out / name).write_bytes(body)
    def save(name, value):
        """Retain finite structured observations and native configuration identities."""
        keep(name, (json.dumps(value, indent=2, allow_nan=False) + '\n').encode())
    def get(label, *query):
        """Capture only specified non-Secret cluster resources."""
        raw = subprocess.check_output(k + ['get', *query, '-o', 'json'], timeout=30, stderr=subprocess.PIPE)
        keep(label + '.json', raw); return json.loads(raw)
    model = get('model-before', 'modelconfig', 'default-model-config', '-n', 'iot-agents')
    agent = get('retrieval-before', 'sandboxagent', 'iot-retrieval', '-n', 'iot-agents')
    original = get('agents-before', 'sandboxagents', '-n', 'iot-agents')
    pools = get('pools-before', 'workerpools', '-n', 'iot-agents')
    assert model['spec']['openAI']['baseUrl'] == 'http://iot-router-epp.iot-llm.svc.cluster.local:8081/v1'
    assert model['spec']['apiKeySecret'] == 'iot-local-model-client'
    policy = json.loads((ROOT / 'agents/response-policy.json').read_bytes())
    planned = configuration_plan(model, agent, policy); save('planned.json', planned); save('source.json', seal)
    if args.resume_from is None:
        for item in planned:
            assert not subprocess.check_output(k + ['get', item['kind'], item['metadata']['name'], '-n', 'iot-agents', '--ignore-not-found', '-o', 'name']).strip()
        result = subprocess.run(k + ['create', '-f', '-', '-o', 'json'], input=json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': planned}).encode(), capture_output=True, timeout=60)
        keep('create.stdout', result.stdout); keep('create.stderr', result.stderr); save('create-exit.json', {'exit': result.returncode}); assert result.returncode == 0
    else:
        prior = args.resume_from.resolve(); assert prior.is_relative_to(root / 'outputs') and prior != out
        assert json.loads((prior / 'planned.json').read_bytes()) == planned
        assert json.loads((prior / 'create-exit.json').read_bytes())['exit'] == 0
        original_source = json.loads((prior / 'source.json').read_bytes()); assert original_source['format'] == seal['format']
        original_tree = root / 'data/llm-source' / ('agent-ab-' + original_source['commit'][:8]) / 'tree'
        for name, pin in original_source['files'].items(): assert sha256((original_tree / name).read_bytes()).hexdigest() == pin
        # kubectl can render a List or consecutive JSON objects. Read its original
        # server response, then require the same four UIDs and exact desired specs.
        remaining = (prior / 'create.stdout').read_text().strip(); created = []
        while remaining:
            item, end = json.JSONDecoder().raw_decode(remaining)
            created.extend(item['items'] if item.get('kind') == 'List' else [item]); remaining = remaining[end:].lstrip()
        assert len(created) == 4
        for item in planned:
            name = item['metadata']['name']; previous = next(v for v in created if v['kind'] == item['kind'] and v['metadata']['name'] == name)
            current = get('resumed-' + name, item['kind'], name, '-n', 'iot-agents')
            assert current['metadata']['uid'] == previous['metadata']['uid'] and current['spec'] == item['spec']
            assert current['metadata']['labels'] == item['metadata']['labels']
        save('resume-origin.json', {'output': str(prior), 'source_commit': original_source['commit'], 'same_four_uids_verified': True, 'resources_recreated_or_patched': False})
    observed = {}
    for attempt in range(180):
        ready = True
        for variant, settings in VARIANTS.items():
            resource = get('agent-' + variant + '-poll-' + str(attempt), 'sandboxagent', settings['agent'], '-n', 'iot-agents')
            generation = resource['metadata']['generation']; status = resource.get('status', {})
            accepted = status.get('observedGeneration') == generation and all(any(c.get('type') == kind and c.get('status') == 'True' and c.get('observedGeneration') == generation for c in status.get('conditions', [])) for kind in ['Accepted', 'Ready'])
            ready = ready and accepted; observed[variant] = resource
        if ready: break
        time.sleep(2)
    else: raise RuntimeError('Both new sandbox variants did not become Ready')
    templates = get('templates', 'actortemplates', '-n', 'iot-agents')['items']; projections = {}
    for variant, settings in VARIANTS.items():
        actual_model = get('model-' + variant, 'modelconfig', settings['model_config'], '-n', 'iot-agents')
        assert actual_model['spec'] == next(v['spec'] for v in planned if v['metadata']['name'] == settings['model_config'])
        assert actual_model['status']['observedGeneration'] == actual_model['metadata']['generation']
        assert any(c['type'] == 'Accepted' and c['status'] == 'True' for c in actual_model['status']['conditions'])
        matches = [t for t in templates if t['metadata'].get('labels', {}).get('kagent.dev/sandbox-agent') == settings['agent'] and any(o.get('uid') == observed[variant]['metadata']['uid'] for o in t['metadata'].get('ownerReferences', []))]
        assert len(matches) == 1; template = matches[0]
        assert template['status']['phase'] == 'Ready' and template['status']['goldenSnapshot'] and template['spec']['sandboxClass'] == 'gvisor'
        env = {v['name']: v for v in template['spec']['containers'][0]['env']}
        reference = env['KAGENT_CONFIG_JSON']['valueFrom']['secretKeyRef']; assert reference == {'name': settings['agent'], 'key': 'config.json'}
        # The full generated config may contain tool authorization. Inspect it only in memory;
        # publish the temperature projection and Secret metadata, never its data or encoded form.
        secret = json.loads(subprocess.check_output(k + ['get', 'secret', reference['name'], '-n', 'iot-agents', '-o', 'json'], timeout=30, stderr=subprocess.PIPE))
        compiled = json.loads(base64.b64decode(secret['data'][reference['key']])); temperatures = []
        def visit(value, path=''):
            """Project only finite scalar temperature fields from the actual generated runtime config."""
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.lower() == 'temperature' and type(child) in {int, float, str}: temperatures.append({'path': path + '/' + key, 'value': child})
                    else: visit(child, path + '/' + key)
            elif isinstance(value, list):
                for index, child in enumerate(value): visit(child, path + '/' + str(index))
        visit(compiled); assert len(temperatures) == 1 and float(temperatures[0]['value']) == float(settings['temperature'])
        projections[variant] = {'template_uid': template['metadata']['uid'], 'template_name': template['metadata']['name'],
            'secret_uid': secret['metadata']['uid'], 'secret_resource_version': secret['metadata']['resourceVersion'], 'temperature_fields': temperatures,
            'agent_uid': observed[variant]['metadata']['uid'], 'model_config_uid': actual_model['metadata']['uid'], 'full_secret_archived': False}
        compiled = None; secret = None
    save('compiled-projections.json', projections)
    current = get('model-after', 'modelconfig', 'default-model-config', '-n', 'iot-agents')
    after = get('agents-after', 'sandboxagents', '-n', 'iot-agents'); pool_after = get('pools-after', 'workerpools', '-n', 'iot-agents')
    assert current['metadata']['uid'] == model['metadata']['uid'] and current['spec'] == model['spec']
    for before in original['items']:
        retained = next(v for v in after['items'] if v['metadata']['uid'] == before['metadata']['uid']); assert retained['spec'] == before['spec']
    assert {v['metadata']['uid']: v['spec'] for v in pools['items']} == {v['metadata']['uid']: v['spec'] for v in pool_after['items']}
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_commit': seal['commit'], 'variants': projections,
        'resumed_existing_variants': args.resume_from is not None,
        'production_specs_preserved': True, 'pool_specs_preserved': True, 'scope': 'Actual accepted ModelConfigs and current-generation Ready sandbox templates with compiled temperature projections. Conversations and A/B outcomes require a separate native run.'}
    save('verification.json', report); print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root', 'output']: parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--resume-from', type=Path, help='Verify and resume the same four UIDs from a retained partial creation; never overwrite unknown resources')
    run(parser.parse_args())
