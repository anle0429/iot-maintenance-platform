"""Authenticate the retained A/B gateway migration and paired native wire evidence without contacting services."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.ab_configuration import VARIANTS
from agents.ab_gateway import migration_plan
from agents.ab_gateway_acceptance import CASE_FILES, ORDER, aware, decode, require, verify_ab_comparison
from agents.answer_acceptance import verify_specialist
from infra.agentgateway import candidate
from scripts.git_sources import git_blobs

CAPSULE = 'agent-ab-gateway-20261108'
CAPSULE_MANIFEST_SHA256 = 'b49e871aef76691b7b209e5c1584820124a6447f146172396281b123e814423a'
SOURCE_COMMIT = '1e883f4c68ea27f973f9191f4e187e0438a9437c'
STUDY_COMMIT = 'f70eacbe553682b0f5412fa48ceae94f8d158860'
POST_CAPTURE_ACCEPTANCE_SHA256 = '9b40258d36a3cd21a94e57fe41425ef732ce71afa4274e43cd484094b56a3dba'
SOURCE_FILES = {
    'agents/README.md', 'agents/__init__.py', 'agents/ab_configuration.py', 'agents/ab_gateway.py',
    'agents/ab_gateway_acceptance.py', 'agents/admission.py', 'agents/admission_cases.py',
    'agents/admission_ci.py', 'agents/answer_acceptance.py', 'agents/ci_contract.py',
    'agents/delegation_acceptance.py', 'agents/gateway_acceptance.py', 'agents/policy_execution.py',
    'agents/probe.py', 'agents/prompt_registry.py', 'agents/release_ci.py', 'agents/response-policy.json',
    'agents/tracing.py', 'infra/agentgateway/agents-default.yaml', 'infra/agentgateway/candidate.py',
    'infra/agentgateway/delegation.py', 'infra/agentgateway/kagent-default.yaml',
    'infra/agentgateway/probe.py', 'infra/llm_observer_routing.py', 'knowledge/assistant-policy.md',
    'knowledge/catalog.json', 'knowledge/drift.md', 'knowledge/monitoring.md', 'knowledge/prediction.md',
    'scripts/demo-feature-api.py', 'scripts/migrate-agent-ab-gateway.py', 'scripts/run-agent-ab-gateway.py',
}


def member_name(value):
    """Reject ambiguous or escaped archive and manifest paths on every reviewing operating system."""
    require(isinstance(value, str) and value and '\\' not in value and '\x00' not in value,
            'Invalid native member path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and path.as_posix() == value and value != '.',
            'Native member path escaped its archive')
    return value


def payload(files, name):
    """Require the exact original byte payload instead of accepting a replacement result summary."""
    member_name(name)
    require(name in files and isinstance(files[name], bytes), 'Missing original native payload: ' + name)
    return files[name]


def native(files, name):
    """Decode one already retained original JSON payload with duplicate/non-finite rejection."""
    return decode(payload(files, name))


def verify_archive(raw, expectedsha, expectedmanifest):
    """Check independent archive/inventory pins, bounded regular members and the complete original inventory."""
    require(isinstance(raw, bytes) and sha256(raw).hexdigest() == expectedsha, 'Native archive hash changed')
    files = {}; total = 0
    try:
        with tarfile.open(fileobj=BytesIO(raw)) as archive:
            for entry in archive:
                name = member_name(entry.name.rstrip('/') if entry.isdir() else entry.name)
                if entry.isdir(): continue
                require(entry.isfile() and 0 <= entry.size <= 32 * 1024 ** 2
                        and name not in files and len(files) < 10000, 'Unsafe or duplicate native archive member')
                total += entry.size
                require(total <= 256 * 1024 ** 2, 'Native archive exceeds the review memory budget')
                files[name] = archive.extractfile(entry).read()
    except tarfile.TarError as error:
        raise ValueError('Unreadable native archive') from error
    manifest_raw = payload(files, 'manifest.json')
    require(sha256(manifest_raw).hexdigest() == expectedmanifest, 'Original native inventory hash changed')
    manifest = decode(manifest_raw)
    require(isinstance(manifest, dict) and isinstance(manifest.get('files'), dict), 'Missing native inventory')
    files.pop('manifest.json')
    require(set(files) == set(manifest['files']), 'Missing or unrecorded native payloads')
    for name, pin in manifest['files'].items():
        body = payload(files, name)
        require(isinstance(pin, dict) and type(pin.get('bytes')) is int
                and len(body) == pin['bytes'] and sha256(body).hexdigest() == pin.get('sha256'),
                'Native payload differs from the original member inventory: ' + name)
    return files


def load_operator():
    """Use the independently tested exact current-generation template and credential-free projector checks."""
    spec = importlib.util.spec_from_file_location('ab_gateway_migration_operator', ROOT / 'scripts/migrate-agent-ab-gateway.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def verify_source(files):
    """Authenticate both thirty-two-file Git packets and the separately corrected measured-wire recorder."""
    seals = {}; packets = {}; immutable = {}
    for packet, commit in [('source', SOURCE_COMMIT), ('source-study', STUDY_COMMIT)]:
        prefix = packet + '/'; seal = native(files, prefix + 'manifest.json')
        require(seal.get('format') == 'iot-agent-ab-gateway-source-v1' and seal.get('commit') == commit
                and isinstance(seal.get('files'), dict) and set(seal['files']) == SOURCE_FILES,
                'Unexpected complete A/B runtime source packet: ' + packet)
        immutable = git_blobs(ROOT, commit, sorted(SOURCE_FILES))
        require({name for name in files if name.startswith(prefix + 'tree/')} == {
            prefix + 'tree/' + name for name in SOURCE_FILES}, 'Missing or unrecorded runtime source member')
        for name, pin in seal['files'].items():
            raw = payload(files, prefix + 'tree/' + member_name(name))
            require(sha256(raw).hexdigest() == pin and raw == immutable[name],
                    'As-run source differs from its immutable Git blob: ' + name)
        seals[packet] = seal; packets[packet] = {'commit': commit, 'immutable_git_files': len(SOURCE_FILES)}
    runtime_interpreters = {'agents/ab_gateway.py', 'agents/ab_configuration.py', 'agents/answer_acceptance.py',
        'agents/probe.py', 'agents/response-policy.json', 'infra/agentgateway/candidate.py',
        'scripts/demo-feature-api.py', 'scripts/migrate-agent-ab-gateway.py', 'scripts/run-agent-ab-gateway.py'}
    for name in runtime_interpreters:
        require((ROOT / name).read_bytes().replace(b'\r\n', b'\n') == immutable[name],
                'Current runtime interpreter differs from the accepted source: ' + name)
    for prefix, packet in [('native/study/', 'source'), ('native/migration-b/', 'source'),
                           ('native/gateway/', 'source'), ('native/study-b/', 'source-study')]:
        require(native(files, prefix + 'source.json') == seals[packet], 'Native run used a different source packet')
    reader = (ROOT / 'agents/ab_gateway_acceptance.py').read_bytes().replace(b'\r\n', b'\n')
    require(sha256(reader).hexdigest() == POST_CAPTURE_ACCEPTANCE_SHA256,
            'The exact reviewed post-capture native-format reader changed')
    return {'passed': True, 'packets': packets,
            'runtime_interpreters_checked': len(runtime_interpreters),
            'post_capture_reader_sha256': POST_CAPTURE_ACCEPTANCE_SHA256}


def identities(value, kind=None):
    """Retain complete names, UIDs and specs while allowing normal Kubernetes status/version writes."""
    require(isinstance(value.get('items'), list), 'Missing native resource cohort')
    result = {}
    for item in value['items']:
        metadata = item['metadata']; name = metadata['name']
        require(metadata.get('namespace') == 'iot-agents' and isinstance(name, str) and name
                and isinstance(metadata.get('uid'), str) and metadata['uid'] and name not in result
                and (kind is None or item.get('kind') == kind), 'Ambiguous or wrongly typed native resource cohort')
        result[name] = {'uid': metadata['uid'], 'spec': item['spec']}
    return result


def require_ready(resource, *, agent=False):
    """A Ready predecessor cannot stand in for the actual accepted current generation."""
    generation, status = resource['metadata']['generation'], resource['status']
    require(status.get('observedGeneration') == generation, 'Native resource generation has not reconciled')
    for condition in ['Accepted', 'Ready'] if agent else ['Accepted']:
        require(any(row.get('type') == condition and row.get('status') == 'True'
                    and (not agent or row.get('observedGeneration') == generation)
                    for row in status.get('conditions', [])), 'Native resource lacks its current accepted/Ready condition')


def verify_migration(files, policy):
    """Recompute every guarded in-place apply and validate the current compiled template projections."""
    prefix = 'native/migration-b/'
    before = {name: native(files, prefix + 'before/' + name + '.json')
              for name in ['agents', 'models', 'tool', 'pools']}
    after = {name: native(files, prefix + 'after/' + name + '.json')
             for name in ['agents', 'models', 'tool', 'pools']}
    plan = migration_plan(before['agents']['items'], before['models']['items'], before['tool'], policy)
    require(plan == native(files, prefix + 'planned.json') and len(plan['patches']) == 4,
            'Initial retained migration is not the four reviewed guarded spec transitions')
    desired = {target['name']: target for target in plan['targets']}; successful = []
    attempts = sorted({int(name[len(prefix + 'apply-'):].split('/', 1)[0]) for name in files
        if name.startswith(prefix + 'apply-') and name.endswith('/plan.json')})
    require(attempts and attempts == list(range(len(attempts))), 'Native apply attempts are missing or reordered')
    for attempt in attempts:
        stage = prefix + 'apply-' + str(attempt) + '/'
        readback = {name: native(files, stage + 'readback/' + name + '.json')
                    for name in ['agents', 'models', 'tool', 'pools']}
        current = migration_plan(readback['agents']['items'], readback['models']['items'], readback['tool'], policy)
        require(current == native(files, stage + 'plan.json'), 'Native apply plan differs from its actual fresh readbacks')
        if not current['patches']:
            require(attempt == attempts[-1], 'Native apply continued after the accepted no-op plan'); continue
        patch = current['patches'][0]; command = native(files, stage + 'patch.command.json')
        argv = command['arguments']
        require(argv == ['patch', patch['kind'], patch['name'], '-n', 'iot-agents', '--type=json',
                         '-p', json.dumps(patch['operations'], separators=(',', ':'))]
                and command.get('timed_out') is not True, 'Native patch dispatch differs from the full UID/version/spec guard')
        if command.get('exit') == 0:
            acknowledgement = payload(files, stage + 'patch.stdout')
            require(acknowledgement == (patch['kind'].lower() + '.kagent.dev/' + patch['name'] + ' patched\n').encode()
                    and patch['name'] not in successful, 'Unexpected native kubectl patch acknowledgement')
            next_prefix = prefix + 'apply-' + str(attempt + 1) + '/readback/' if attempt + 1 in attempts else prefix + 'after/'
            cohort = native(files, next_prefix + ('models' if patch['kind'] == 'ModelConfig' else 'agents') + '.json')
            targets = [item for item in cohort['items'] if item['metadata']['name'] == patch['name']]
            require(len(targets) == 1 and targets[0]['metadata']['uid'] == patch['uid']
                    and targets[0]['spec'] == patch['operations'][-1]['value'],
                    'The next native readback does not confirm the guarded retained-resource transition')
            successful.append(patch['name'])
        else:
            failed = native(files, stage + 'failed-patch-readback.json')
            require(failed['metadata']['uid'] == patch['uid'] and failed['spec'] == patch['operations'][2]['value']
                    and failed['metadata']['resourceVersion'] != patch['resource_version'],
                    'Native patch failed beyond a retained-spec resourceVersion conflict')
    require(set(successful) == set(desired), 'Not all four retained transitions have original successful patch responses')
    require(not migration_plan(after['agents']['items'], after['models']['items'], after['tool'], policy)['patches'],
            'Final A/B profile is not selected')
    for name, kind in [('agents', 'SandboxAgent'), ('models', 'ModelConfig')]:
        old, new = identities(before[name], kind), identities(after[name], kind)
        require(old.keys() == new.keys() and all(old[n]['uid'] == new[n]['uid'] for n in old),
                'A retained experiment or production resource identity changed')
        for resource_name in old:
            expected = desired[resource_name]['desired_spec'] if resource_name in desired else old[resource_name]['spec']
            require(new[resource_name]['spec'] == expected, 'An unrelated production field or sampling value changed')
    require(identities(before['pools']) == identities(after['pools'])
            and before['tool']['metadata']['uid'] == after['tool']['metadata']['uid']
            and before['tool']['spec'] == after['tool']['spec'], 'Existing pools or shared tool identity/spec changed')
    operator = load_operator(); templates = native(files, prefix + 'templates.json')['items']
    projections = native(files, prefix + 'compiled-projections.json'); accepted = native(files, prefix + 'verification.json')
    require(set(projections) == {'a', 'b'} and accepted.get('passed') is True
            and accepted.get('source_commit') == SOURCE_COMMIT and accepted.get('variants') == projections,
            'Native compiled acceptance does not match the retained projections')
    for variant, settings in VARIANTS.items():
        agent = next(item for item in after['agents']['items'] if item['metadata']['name'] == settings['agent'])
        model = next(item for item in after['models']['items'] if item['metadata']['name'] == settings['model_config'])
        require_ready(agent, agent=True); require_ready(model)
        template = operator.current_template(templates, agent); projection = projections[variant]
        require(projection['agent_uid'] == agent['metadata']['uid']
                and projection['agent_generation'] == agent['metadata']['generation']
                and projection['model_config_uid'] == model['metadata']['uid']
                and projection['model_config_generation'] == model['metadata']['generation']
                and projection['template_uid'] == template['metadata']['uid']
                and projection['template_name'] == template['metadata']['name']
                and projection['golden_snapshot'] == template['status']['goldenSnapshot']
                and projection.get('full_secret_archived') is False
                and re.fullmatch('[0-9a-f]{64}', projection.get('config_sha256', ''))
                and isinstance(projection.get('secret_uid'), str) and projection['secret_uid'],
                'Compiled projection is not the current owned template/agent/model generation')
        public = projection['selected_public_settings']
        reconstructed = {'model': public['model'], 'http_tools': [{'tools': public['mcp']['tools'],
            'params': {'url': public['mcp']['url'], 'headers': {'x-kagent-host': public['mcp']['proxy_host']}}}]}
        require(operator.compiled_projection(reconstructed, settings['temperature']) == public,
                'Credential-free native compiled projection violates the selected gateway contract')
        env = {row['name']: row for row in template['spec']['containers'][0]['env']}
        require(env['KAGENT_CONFIG_JSON']['valueFrom']['secretKeyRef'] == {
            'name': settings['agent'], 'key': 'config.json'}, 'Current template uses a different generated runtime Secret')
    for name in files:
        if name.startswith(prefix) and name.endswith('.command.json'):
            command = native(files, name)
            require(command['arguments'][0] in {'get', 'patch'}, 'Unexpected mutating operator dispatch')
    return {'passed': True, 'guarded_spec_patches': len(successful), 'retained_agent_uids': {
        variant: projections[variant]['agent_uid'] for variant in VARIANTS},
        'compiled_temperatures': {variant: projections[variant]['selected_public_settings']['model']['temperature']
                                  for variant in VARIANTS}, 'production_and_pools_preserved': True}


def verify_gateway(files):
    """Authenticate the exact optional A/B rendering while preserving every original route, port and peer rule."""
    prefix = 'native/gateway/'
    before = {kind: native(files, prefix + kind + '-before.json')
              for kind in ['configmap', 'deployment', 'service', 'networkpolicy']}
    after = {kind: native(files, prefix + kind + '-after.json') for kind in before}
    configs = {name: decode(value['configmap']['data']['config.yaml'].encode())
               for name, value in [('before', before), ('after', after)]}
    routes = {bind['port']: {route['name']: route for listener in bind['listeners']
                              for route in listener['routes']} for bind in configs['before']['binds']}
    hashes = {role: routes[port][role]['policies']['apiKey']['keys'][0]['keyHash']
              for role, port in candidate.PORTS.items() if role != 'a2a'}
    hashes['a2a'] = routes[18083]['a2a-coordinator']['policies']['apiKey']['keys'][0]['keyHash']
    delegation = routes[18083]['delegate-retrieval']['policies']['apiKey']['keys'][0]['keyHash']
    require(configs['before'] == candidate.config(hashes, tracing=True, delegation_hash=delegation)
            and configs['after'] == candidate.config(hashes, tracing=True, delegation_hash=delegation,
                                                     ab_operator_routes=True),
            'Native gateway differs from either exact reviewed fixed-backend renderer')
    for kind in before:
        require(before[kind]['metadata']['uid'] == after[kind]['metadata']['uid'], 'Retained gateway resource was replaced')
    for kind in ['service', 'networkpolicy']:
        require(before[kind]['spec'] == after[kind]['spec'], 'Existing private gateway service or network peers changed')
    require(after['service']['spec']['type'] == 'ClusterIP'
            and {row['port'] for row in after['service']['spec']['ports']} == set(candidate.PORTS.values()),
            'Gateway exposure or retained listener ports changed')
    restored = deepcopy(after['deployment']['spec'])
    annotations = restored['template']['metadata']['annotations']
    require(annotations.pop('iot.coursework/ab-gateway-source') == SOURCE_COMMIT, 'Gateway rollout source annotation differs')
    if not annotations and 'annotations' not in before['deployment']['spec']['template']['metadata']:
        restored['template']['metadata'].pop('annotations')
    require(restored == before['deployment']['spec'], 'Gateway rollout changed unrelated workload fields')
    deployment = after['deployment']; status = deployment['status']
    require(status.get('observedGeneration') == deployment['metadata']['generation']
            and status.get('availableReplicas') == status.get('updatedReplicas') == deployment['spec']['replicas'] == 1,
            'Selected gateway rollout did not become available')
    return {'passed': True, 'listener_ports': sorted(routes), 'new_operator_variants': ['a', 'b'],
            'original_routes_and_boundaries_preserved': True}


def verify_final(files, study):
    """Bind the final readback to the accepted cohort and preserve the three production-role configurations."""
    for name, cohort in [('agents', 'sandboxagents'), ('models', 'modelconfigs'),
                          ('tools', 'remotemcpservers'), ('pools', 'workerpools')]:
        require(identities(native(files, 'final/' + name + '.json')) ==
                identities(native(files, study + cohort + '-after.json')),
                'Final retained resource UID/spec differs from the accepted study: ' + name)
    agents = native(files, 'final/agents.json')['items']
    require({row['metadata']['name'] for row in agents} == {
        'iot-retrieval', 'iot-drift', 'iot-coordinator', 'iot-ab-retrieval-a', 'iot-ab-retrieval-b'},
        'Final five-agent cohort is missing or contains unreviewed additions')
    for agent in agents:
        require_ready(agent, agent=True)
    for model in native(files, 'final/models.json')['items']:
        require_ready(model)
    controller = native(files, 'final/controller.json'); previous = native(files, study + 'controller.json')
    require(controller['metadata']['uid'] == previous['metadata']['uid'] and controller['spec'] == previous['spec'],
            'The controller execution profile changed during the captured A/B workload')
    status = controller['status']
    require(status.get('observedGeneration') == controller['metadata']['generation']
            and status.get('availableReplicas') == status.get('updatedReplicas') == controller['spec']['replicas'] == 1,
            'The captured controller rollout was not available at final inspection')
    configmap = native(files, 'final/kagent-controller-configmap.json')
    expected_url = 'http://' + candidate.DNS_NAME + ':18083'
    require(configmap['metadata']['name'] == 'kagent-controller'
            and configmap['metadata']['namespace'] == 'iot-agents'
            and configmap['data'].get('A2A_BASE_URL') == configmap['data'].get('PROXY_URL') == expected_url
            and configmap['data'].get('TOOL_REFRESH_INTERVAL') == '60s',
            'Final controller discovery/advertised URLs or observed periodic tool-refresh configuration differs')
    require(any(row.get('configMapRef', {}).get('name') == 'kagent-controller'
                for container in controller['spec']['template']['spec']['containers'] for row in container.get('envFrom', [])),
            'Actual controller does not consume the captured selected routing ConfigMap')
    for kind in ['configmap', 'deployment', 'service', 'networkpolicy']:
        final = native(files, 'final/gateway-' + kind + '.json')
        accepted = native(files, 'native/gateway/' + kind + '-after.json')
        require(final['metadata']['uid'] == accepted['metadata']['uid']
                and final.get('spec') == accepted.get('spec') and final.get('data') == accepted.get('data'),
                'Final selected gateway UID/configuration differs from its accepted rollout')
    return {'passed': True, 'current_ready_agents': len(agents), 'production_role_specs_preserved': 3,
            'selected_gateway_persisted': True}


def verify_predecessors(files, policy):
    """Keep the actual unaccepted attempts distinct from the accepted measured-wire replay."""
    initial = 'native/migration/'
    require(native(files, initial + 'source.json') == native(files, 'source/manifest.json'),
            'Failed initial migration used an unrecorded source packet')
    try:
        migration_plan(native(files, initial + 'before/agents.json')['items'],
                       native(files, initial + 'before/models.json')['items'],
                       native(files, initial + 'before/tool.json'), policy)
    except ValueError as error:
        require(str(error) == 'Require the accepted selected gateway MCP alias and exactly its three readonly tools',
                'Failed initial migration no longer has its original unaccepted-tool boundary')
    else:
        raise ValueError('Initial unaccepted migration was relabelled as accepted')
    require(not any(name.startswith(initial + 'apply-') for name in files),
            'Initial migration contains an unexpected patch dispatch')
    for index, (variant, machine) in enumerate(ORDER):
        prefix = 'native/study/' + str(index // 2) + '-' + variant + '/'
        response = native(files, prefix + 'response.json')
        accepted = verify_specialist(response, 'retrieval', machine_id=machine)
        require(accepted == native(files, prefix + 'answer-acceptance.json'),
                'Original predecessor answer content or MCP history changed')
    for name, commit, error in [
            ('first', SOURCE_COMMIT, 'Require the original worker PodList'),
            ('b', SOURCE_COMMIT, 'MCP traffic is not on the selected path from the actual actor worker'),
            ('c', STUDY_COMMIT, 'MCP traffic is not on the selected path from the actual actor worker')]:
        report = native(files, 'native/wire-verification-' + name + '.json')
        require(report.get('passed') is False and report.get('error_type') == 'ValueError'
                and report.get('source_commit') == commit and report.get('error') == error,
                'An original failed wire-verification receipt was removed or relabelled')
    return {'passed': True, 'first_migration_patch_dispatches': 0,
            'original_tool_grounded_answers_retained': 6, 'failed_wire_checks_retained': 3,
            'original_six_transport_acceptance_claimed': False}


def verify_native(files):
    """Recompute source, guarded migration, gateway boundaries, genuine feature lease and the complete native cohort."""
    try:
        return _verify_native(files)
    except (KeyError, TypeError, AttributeError, IndexError, UnicodeError) as error:
        raise ValueError('Malformed or incomplete native A/B capsule evidence') from error


def _verify_native(files):
    """Validate one bounded retained-lab run without inferring live readiness after the captured session."""
    source = verify_source(files); policy = native(files, 'source/tree/agents/response-policy.json')
    migration = verify_migration(files, policy); gateway = verify_gateway(files)
    predecessors = verify_predecessors(files, policy)
    study = 'native/study-b/'
    cases = {f'{index // 2}-{variant}/{name}': payload(files, study + f'{index // 2}-{variant}/{name}')
             for index, (variant, _machine) in enumerate(ORDER) for name in CASE_FILES}
    pin = native(files, 'models/model-pin.json'); portable = payload(files, 'models/portable.json')
    expected_pin = decode((ROOT / 'docs/evidence/ci-serving-linux/selected-model6/model-pin.json').read_bytes())
    require(pin == expected_pin, 'The native feature API model6 pin differs from its original accepted release')
    comparison = verify_ab_comparison(cases, portable_bytes=portable, model_pin=pin)
    report = native(files, study + 'verification.json')
    require(report.get('passed') is True and report.get('requests') == 6 and report.get('source_commit') == STUDY_COMMIT
            and report.get('summary') == comparison['summary'], 'Native study result disagrees with independent recomputation')
    outcomes = [decode(cases[f'{index // 2}-{variant}/outcome.json']) for index, (variant, _) in enumerate(ORDER)]
    require(outcomes == native(files, study + 'outcomes.json')
            and comparison['summary'] == native(files, study + 'summary.json')
            and native(files, study + 'machines.json') == ['P-001', 'P-002', 'P-003'],
            'Paired study inventory was reordered or selectively replaced')
    for kind in ['sandboxagents', 'modelconfigs', 'remotemcpservers', 'workerpools']:
        require(identities(native(files, study + kind + '-before.json')) ==
                identities(native(files, study + kind + '-after.json')), 'Resource specs changed during the measured workload')
    for variant in VARIANTS:
        card = native(files, study + 'card-' + variant + '.json')
        transport = native(files, study + 'card-' + variant + '-transport.json')
        expected = 'http://' + candidate.DNS_NAME + ':18083' + candidate.ab_a2a_path(variant) + '/'
        advertised = {card.get('url')} | {row.get('url') for row in card.get('additionalInterfaces', [])}
        require(card.get('name') == VARIANTS[variant]['agent'].replace('-', '_')
                and expected in advertised and transport.get('http_status') == 200
                and transport.get('route') == 'a2a-ab-retrieval-' + variant
                and transport.get('expected_url') == expected, 'A/B advertised interface is not the actual authenticated gateway path')
    features = native(files, 'native/features/verification.json')
    require(features.get('passed') is True and features.get('machines') == 200 and features.get('model_version') == '6'
            and features.get('resume_deploy') is False and features['ready'].get('ready') is True
            and aware(features['lease_expires_utc']) - aware(features['prepared_wall_utc']) == timedelta(hours=1)
            and native(files, study + 'feature-lease.json') == features, 'Fresh native feature preparation is not a genuine one-hour lease')
    before = native(files, 'native/features/physical-ttl-before.json')
    after = native(files, 'native/features/physical-ttl-after.json')
    container = native(files, 'native/features/materialization-container.json')
    require(before == {'keys': 0, 'pttl_ms': []} and after['keys'] == len(after['pttl_ms']) == 200
            and min(after['pttl_ms']) > 600000 and container['state']['ExitCode'] == 0
            and container['state']['OOMKilled'] is False and container['image'] == features['sdk_image_id'],
            'Native materialization, physical key expiry or actual container outcome differs')
    starts = [aware(row['started_utc']) for row in outcomes]; ends = [aware(row['finished_utc']) for row in outcomes]
    require(aware(features['prepared_wall_utc']) <= min(starts) and max(ends) < aware(features['lease_expires_utc']),
            'The accepted cohort is outside its actual fresh feature lease')
    sessions = {}
    for role, name, machine, disks in [('cpu', 'iot-cw-vm', 'e2-highmem-8', 3), ('gpu', 'iot-cw-gpu', 'n1-standard-4', 1)]:
        session = native(files, 'session/' + role + '-descriptor.json'); scheduling = session['scheduling']
        start = aware(session['lastStartTimestamp']); deadline = start + timedelta(hours=8)
        require(session.get('name') == name and session.get('status') == 'RUNNING'
                and session.get('machineType', '').endswith('/' + machine) and session.get('disk_count') == disks
                and scheduling.get('automaticRestart') is False and scheduling.get('instanceTerminationAction') == 'STOP'
                and str(scheduling['maxRunDuration']['seconds']) == '28800'
                and start <= aware(features['prepared_wall_utc']) <= min(starts) and max(ends) < deadline,
                'Captured workload is outside the approved retained VM configuration/session')
        sessions[role] = {'last_start_utc': start.isoformat(), 'auto_stop_utc': deadline.isoformat()}
    final = verify_final(files, study)
    return {'passed': True, 'native_payloads': len(files), 'source': source, 'migration': migration,
        'gateway': gateway, 'comparison': comparison, 'feature_machines': 200, 'sessions': sessions, 'final': final,
        'predecessors': predecessors,
        'scope': 'Captured A/B gateway migration and six paired native tasks on the same retained two-VM lab. '
                 'Original failures and the original direct-profile experiment remain separate. '
                 'No general sampling-quality ranking, automatic promotion or always-on readiness claim.'}


def verify_stops(stopped, files):
    """Bind actual terminated Compute projections to both original instances and the captured eight-hour grant."""
    require(isinstance(stopped, list) and len(stopped) == 2, 'Require both actual stopped Compute descriptors')
    names = {value.get('name') for value in stopped}
    require(names == {'iot-cw-vm', 'iot-cw-gpu'}, 'Stopped instance cohort differs from the approved two VMs')
    last_request = max(aware(native(files, 'native/study-b/' + str(index // 2) + '-' + variant + '/outcome.json')['finished_utc'])
                       for index, (variant, _) in enumerate(ORDER))
    times = {}
    for role, name in [('cpu', 'iot-cw-vm'), ('gpu', 'iot-cw-gpu')]:
        initial = native(files, 'session/' + role + '-descriptor.json')
        actual = next(value for value in stopped if value['name'] == name)
        start, stop = aware(actual['lastStartTimestamp']), aware(actual['lastStopTimestamp'])
        require(actual.get('status') == 'TERMINATED' and actual.get('id') == initial['id']
                and actual.get('machineType') == initial['machineType']
                and actual.get('scheduling') == initial['scheduling']
                and start == aware(initial['lastStartTimestamp'])
                and last_request <= stop < start + timedelta(hours=8),
                'Actual VM termination changed the instance/configuration or exceeded the approved captured session')
        times[role] = stop.isoformat()
    return {'passed': True, 'terminated_instances': len(stopped), 'last_stop_utc': times}


def verify():
    """Read the exact published capsule, authenticate all public/native bytes and compare its deterministic result."""
    require(isinstance(CAPSULE_MANIFEST_SHA256, str), 'A/B native capsule publication pins are not finalized')
    base = ROOT / 'docs/evidence' / CAPSULE
    manifest_raw = (base / 'manifest.json').read_bytes()
    require(sha256(manifest_raw).hexdigest() == CAPSULE_MANIFEST_SHA256, 'Published A/B capsule inventory changed')
    manifest = decode(manifest_raw)
    require({path.relative_to(base).as_posix() for path in base.rglob('*') if path.is_file()} ==
            set(manifest['files']) | {'manifest.json'}, 'Unrecorded public A/B capsule files')
    for name, pin in manifest['files'].items():
        body = (base / member_name(name)).read_bytes()
        require(len(body) == pin['bytes'] and sha256(body).hexdigest() == pin['sha256'], 'Published payload changed: ' + name)
    metadata = decode((base / 'native-archive.json').read_bytes()); raw = (base / 'native-receipts.tar.gz').read_bytes()
    require(metadata.get('passed') is True and metadata.get('credential_scan_passed') is True
            and len(raw) == metadata['archive_bytes'], 'Native archive provenance/credential scan is incomplete')
    files = verify_archive(raw, metadata['archive_sha256'], metadata['manifest_sha256'])
    require(len(files) == metadata['native_payloads'], 'Native member count differs')
    report = verify_native(files)
    require(report == decode((base / 'verification.json').read_bytes()), 'Published verification differs from original native evidence')
    stopped = verify_stops(decode((base / 'stopped.json').read_bytes()), files)
    return {**report, 'vm_stops': stopped}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2, allow_nan=False))
