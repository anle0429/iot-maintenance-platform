"""Migrate the retained retrieval A/B pair in place and capture guarded native configuration acceptance."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.ab_configuration import VARIANTS
from agents.ab_gateway import GATEWAY, MODEL_URL, TOOLS_URL, TOOL_NAMES, migration_plan


REQUIRED_SOURCE = {'agents/__init__.py', 'agents/ab_configuration.py', 'agents/ab_gateway.py',
                   'agents/response-policy.json', 'scripts/migrate-agent-ab-gateway.py'}


def source_manifest(tree, path):
    """Authenticate every packet member and reject missing operator dependencies or escaping paths."""
    tree = tree.resolve()
    value = json.loads(path.read_bytes())
    if (value.get('format') != 'iot-agent-ab-gateway-source-v1'
            or not re.fullmatch('[a-f0-9]{40}', str(value.get('commit', '')))
            or not isinstance(value.get('files'), dict) or not REQUIRED_SOURCE <= value['files'].keys()):
        raise ValueError('Require the complete committed A/B gateway source packet')
    for name, pin in value['files'].items():
        if (not isinstance(name, str) or '\\' in name or name.startswith('/')
                or '..' in Path(name).parts or not re.fullmatch('[a-f0-9]{64}', str(pin))):
            raise ValueError('Invalid sealed source member')
        member = (tree / name).resolve()
        if not member.is_relative_to(tree) or not member.is_file() or sha256(member.read_bytes()).hexdigest() != pin:
            raise ValueError('Missing or modified sealed source member: ' + name)
    return value


def require_session(session, now):
    """Reserve thirty minutes for completion and refuse a session deadline beyond the authorized eight hours."""
    try:
        deadline = datetime.fromisoformat(session['auto_stop_utc'])
        if now.tzinfo is None or deadline.tzinfo is None:
            raise ValueError('Require timezone-aware session clocks')
        remaining = (deadline - now).total_seconds()
    except (KeyError, TypeError) as error:
        raise ValueError('Missing or malformed session deadline') from error
    if not 1800 < remaining <= 8 * 3600:
        raise ValueError('Require an authorized session with more than thirty minutes remaining')
    return remaining


def compiled_projection(config, expected_temperature):
    """Project only verified model/MCP settings from a generated Secret; never return credentials or arbitrary headers."""
    try:
        model = config['model']
        tools = config['http_tools']
        if (model['type'] != 'openai' or model['model'] != 'iot-qwen2.5-7b'
                or model['base_url'] != MODEL_URL or model['seed'] != 7 or model['max_tokens'] != 768
                or type(model['temperature']) not in (int, float)
                or model['temperature'] != float(expected_temperature)
                or len(tools) != 1 or sorted(tools[0]['tools']) != sorted(TOOL_NAMES)
                or tools[0]['params']['url'] != TOOLS_URL
                or tools[0]['params']['headers']['x-kagent-host'] != GATEWAY
                or config.get('sse_tools') or config.get('remote_agents') or config.get('execute_code', False)):
            raise ValueError('Generated A/B runtime does not match the selected model/MCP gateway contract')
        return {'model': {key: model[key] for key in
                    ['type', 'model', 'base_url', 'temperature', 'seed', 'max_tokens']},
                'mcp': {'url': tools[0]['params']['url'], 'tools': sorted(tools[0]['tools']),
                        'proxy_host': GATEWAY}}
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError('Incomplete generated A/B runtime configuration') from error


def current_template(templates, agent):
    """Reject old Ready blue/green templates and select the owned template for this exact desired generation."""
    try:
        name, uid, generation = (agent['metadata'][key] for key in ['name', 'uid', 'generation'])
        matches = [v for v in templates if not v['metadata'].get('deletionTimestamp')
            and v['metadata'].get('namespace') == 'iot-agents'
            and v['metadata'].get('labels', {}).get('kagent.dev/sandbox-agent') == name
            and v['metadata'].get('annotations', {}).get('kagent.dev/desired-generation') == str(generation)
            and any(o.get('uid') == uid and o.get('name') == name and o.get('kind') == 'SandboxAgent'
                    and o.get('controller') is True for o in v['metadata'].get('ownerReferences', []))]
        if (len(matches) != 1 or not matches[0]['metadata'].get('uid')
                or matches[0]['status'].get('phase') != 'Ready' or not matches[0]['status'].get('goldenSnapshot')
                or matches[0]['spec'].get('sandboxClass') != 'gvisor'
                or matches[0]['spec'].get('workerSelector') != {
                    'matchLabels': {'kagent.dev/worker-pool': 'iot-retrieval-pool'}}):
            raise ValueError('Require the exact current-generation owned Ready gVisor template and actual snapshot')
        return matches[0]
    except (KeyError, TypeError) as error:
        raise ValueError('Incomplete owned ActorTemplate readback') from error


def run(args):
    """Apply only fresh UID/version/spec-guarded patches and retain all native attempts before checking readiness."""
    root, out = args.runtime_root.resolve(), args.output.resolve()
    if (os.name != 'posix' or os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data')
            or out == root / 'outputs' or not out.is_relative_to(root / 'outputs')):
        raise ValueError('Run as root in the retained Linux workspace with a new output below its outputs directory')
    seal = source_manifest(ROOT, args.source_manifest or ROOT.parent / 'manifest.json')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    require_session(session, datetime.now(timezone.utc))
    available = int(next(v.split()[1] for v in Path('/proc/meminfo').read_text().splitlines()
                         if v.startswith('MemAvailable:'))) * 1024
    if available < 8 * 1024 ** 3 or shutil.disk_usage('/srv/iot-fast').free < 20 * 1024 ** 3:
        raise ValueError('Require eight GiB available RAM and twenty GiB free scratch space')
    os.umask(0o077)
    out.mkdir(exist_ok=False)
    kubectl = ['/usr/local/bin/k3s', 'kubectl']
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            if isinstance(value, str) and len(value) >= 16 and value != str(root):
                private.extend([value.encode(), base64.b64encode(value.encode()),
                                base64.b64encode(('Bearer ' + value).encode())])

    def keep(name, body):
        """Retain original bytes only once, rejecting actual known private service values."""
        if any(value in body for value in private):
            raise ValueError('Private service value detected in native receipt: ' + name)
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(body)

    def save(name, value):
        """Preserve finite structured projections and exact plan identities."""
        keep(name, (json.dumps(value, indent=2, allow_nan=False) + '\n').encode())

    def command(label, arguments, *, timeout=30):
        """Capture stdout/stderr/exit for one non-Secret read or guarded patch, including every failed attempt."""
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = subprocess.run(kubectl + arguments, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            keep(label + '.stdout', error.stdout or b'')
            keep(label + '.stderr', error.stderr or b'')
            save(label + '.command.json', {'arguments': arguments, 'started_utc': started, 'timed_out': True})
            raise
        keep(label + '.stdout', result.stdout)
        keep(label + '.stderr', result.stderr)
        save(label + '.command.json', {'arguments': arguments, 'started_utc': started,
             'completed_utc': datetime.now(timezone.utc).isoformat(), 'exit': result.returncode})
        return result

    def get(label, *query):
        """Capture original non-Secret JSON resources and refuse an unsuccessful native query."""
        result = command(label, ['get', *query, '-o', 'json'])
        if result.returncode:
            raise RuntimeError('Native read failed: ' + label)
        keep(label + '.json', result.stdout)
        return json.loads(result.stdout)

    def snapshot(label):
        """Bind both variants to the complete known agent/model cohort, selected tool and retained pools."""
        return {'agents': get(label + '/agents', 'sandboxagents', '-n', 'iot-agents'),
                'models': get(label + '/models', 'modelconfigs', '-n', 'iot-agents'),
                'tool': get(label + '/tool', 'remotemcpserver', 'iot-gateway-retrieval-tools', '-n', 'iot-agents'),
                'pools': get(label + '/pools', 'workerpools', '-n', 'iot-agents')}

    def uid_specs(value):
        """Compare actual retained identities/specs without treating normal controller status writes as changes."""
        return {v['metadata']['name']: {'uid': v['metadata']['uid'], 'spec': v['spec']}
                for v in value['items']}

    policy = json.loads((ROOT / 'agents/response-policy.json').read_bytes())
    save('source.json', seal)
    save('session.json', {'auto_stop_utc': session['auto_stop_utc'],
                         'checked_utc': datetime.now(timezone.utc).isoformat()})
    before = snapshot('before')
    initial = migration_plan(before['agents']['items'], before['models']['items'], before['tool'], policy)
    save('planned.json', initial)
    original_agents, original_models = uid_specs(before['agents']), uid_specs(before['models'])
    original_pools = uid_specs(before['pools'])

    def preserved(current):
        """Refuse unrelated production/pool changes or replacement of any retained experiment identity."""
        actual_agents, actual_models = uid_specs(current['agents']), uid_specs(current['models'])
        if (actual_agents.keys() != original_agents.keys() or actual_models.keys() != original_models.keys()
                or any(actual_agents[n]['uid'] != v['uid'] for n, v in original_agents.items())
                or any(actual_models[n]['uid'] != v['uid'] for n, v in original_models.items())
                or any(actual_agents[n] != original_agents[n] for n in ['iot-retrieval', 'iot-drift', 'iot-coordinator'])
                or actual_models['default-model-config'] != original_models['default-model-config']
                or current['tool']['metadata']['uid'] != before['tool']['metadata']['uid']
                or current['tool']['spec'] != before['tool']['spec']
                or uid_specs(current['pools']) != original_pools):
            raise ValueError('A retained identity or unrelated production/pool spec changed')

    for attempt in range(12):
        require_session(session, datetime.now(timezone.utc))
        current = snapshot('apply-' + str(attempt) + '/readback')
        preserved(current)
        plan = migration_plan(current['agents']['items'], current['models']['items'], current['tool'], policy)
        save('apply-' + str(attempt) + '/plan.json', plan)
        if not plan['patches']:
            break
        patch = plan['patches'][0]
        result = command('apply-' + str(attempt) + '/patch', ['patch', patch['kind'], patch['name'],
            '-n', 'iot-agents', '--type=json', '-p', json.dumps(patch['operations'], separators=(',', ':'))], timeout=60)
        if result.returncode:
            fresh = get('apply-' + str(attempt) + '/failed-patch-readback', patch['kind'], patch['name'], '-n', 'iot-agents')
            original_spec = patch['operations'][2]['value']
            if (fresh['metadata']['uid'] != patch['uid'] or fresh['spec'] != original_spec
                    or fresh['metadata']['resourceVersion'] == patch['resource_version']):
                raise RuntimeError('Patch failed beyond an unchanged-spec concurrent resourceVersion update')
            # Only a fresh full-cohort plan can retry a status-only resourceVersion conflict.
    else:
        raise RuntimeError('Guarded migration did not converge within twelve native attempts')

    last = None
    for attempt in range(600):
        require_session(session, datetime.now(timezone.utc))
        last = snapshot('ready-' + str(attempt))
        preserved(last)
        plan = migration_plan(last['agents']['items'], last['models']['items'], last['tool'], policy)
        if plan['patches']:
            raise ValueError('A migrated variant reverted during readiness checks')
        ready = True
        for kind, items, field in [('SandboxAgent', last['agents']['items'], 'agent'),
                                   ('ModelConfig', last['models']['items'], 'model_config')]:
            for settings in VARIANTS.values():
                resource = next(v for v in items if v['metadata']['name'] == settings[field])
                generation, status = resource['metadata']['generation'], resource.get('status', {})
                conditions = ['Accepted', 'Ready'] if kind == 'SandboxAgent' else ['Accepted']
                ready = ready and status.get('observedGeneration') == generation and all(
                    any(c.get('type') == required and c.get('status') == 'True'
                        and (kind != 'SandboxAgent' or c.get('observedGeneration') == generation)
                        for c in status.get('conditions', [])) for required in conditions)
        if ready:
            break
        time.sleep(2)
    else:
        raise RuntimeError('Migrated resources did not become current-generation Accepted/Ready')

    templates = get('templates', 'actortemplates', '-n', 'iot-agents')['items']
    projections = {}
    for variant, settings in VARIANTS.items():
        agent = next(v for v in last['agents']['items'] if v['metadata']['name'] == settings['agent'])
        model = next(v for v in last['models']['items'] if v['metadata']['name'] == settings['model_config'])
        template = current_template(templates, agent)
        containers = template['spec']['containers']
        env = {v['name']: v for v in containers[0]['env']}
        reference = env['KAGENT_CONFIG_JSON']['valueFrom']['secretKeyRef']
        if reference != {'name': settings['agent'], 'key': 'config.json'}:
            raise ValueError('Unexpected generated configuration Secret reference')
        # Secret bytes and their decoded JSON stay in memory; command() is deliberately not used.
        secret = json.loads(subprocess.check_output(kubectl + ['get', 'secret', reference['name'],
            '-n', 'iot-agents', '-o', 'json'], stderr=subprocess.PIPE, timeout=30))
        raw = base64.b64decode(secret['data'][reference['key']], validate=True)
        selected = compiled_projection(json.loads(raw), settings['temperature'])
        projections[variant] = {'agent_uid': agent['metadata']['uid'], 'agent_generation': agent['metadata']['generation'],
            'model_config_uid': model['metadata']['uid'], 'model_config_generation': model['metadata']['generation'],
            'template_name': template['metadata']['name'], 'template_uid': template['metadata']['uid'],
            'golden_snapshot': template['status']['goldenSnapshot'], 'secret_uid': secret['metadata']['uid'],
            'secret_resource_version': secret['metadata']['resourceVersion'], 'config_sha256': sha256(raw).hexdigest(),
            'selected_public_settings': selected, 'full_secret_archived': False}
        raw = None
        secret = None
    save('compiled-projections.json', projections)
    after = snapshot('after')
    preserved(after)
    if migration_plan(after['agents']['items'], after['models']['items'], after['tool'], policy)['patches']:
        raise ValueError('Final selected profile did not persist')
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_commit': seal['commit'],
        'variants': projections, 'original_resource_uids_retained': True, 'production_specs_preserved': True,
        'pool_specs_preserved': True, 'gateway_tool_spec_preserved': True, 'operator_create_calls': 0,
        'scope': 'Native guarded in-place configuration migration and current-generation Ready compiled gateway '
                 'model/MCP settings. Real conversations, tool correctness and wire attribution require a separate run.'}
    save('verification.json', report)
    print(json.dumps(report, allow_nan=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path)
    run(parser.parse_args())
