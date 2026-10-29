"""Verify native demand-driven scaling, context-preserving capacity retries and original application screenshots offline."""
from datetime import datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import importlib.util
import json
import re
import sys
import tarfile
from integration_evidence import ROOT, capsule, read

sys.path.insert(0, str(ROOT))
from agents.answer_acceptance import final_answer
from agents.delegation_acceptance import verify_coordinator

MANIFEST_SHA256 = '514be14b9cf079e4c33466f4f7093dc510b7617a4fb7e5a0c94a789c7e7c17af'


def source_matches(name, snapshot, current):
    """Permit only the packet's documented CRLF-to-LF catalog normalization; require exact bytes for all code/config."""
    if snapshot == current: return True
    return (name == 'knowledge/catalog.json' and snapshot == current.replace(b'\r\n', b'\n')
            and json.loads(snapshot) == json.loads(current))


def jpeg_dimensions(raw):
    """Read the original JPEG frame header without re-encoding or changing screenshot pixels."""
    assert raw[:2] == b'\xff\xd8'; offset = 2
    while offset + 9 < len(raw):
        assert raw[offset] == 255
        while raw[offset] == 255: offset += 1
        marker = raw[offset]; offset += 1
        if marker in [0xD8, 0xD9, 0x01] or 0xD0 <= marker <= 0xD7: continue
        length = int.from_bytes(raw[offset:offset + 2], 'big'); assert length >= 2
        if marker in [0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF]:
            return int.from_bytes(raw[offset + 5:offset + 7], 'big'), int.from_bytes(raw[offset + 3:offset + 5], 'big')
        offset += length
    raise AssertionError('No original JPEG frame dimensions')


def verify():
    """Recompute the accepted tasks, original rejections and every observed role capacity from native receipts."""
    base, public_count = capsule('agent-scaling', MANIFEST_SHA256)
    provenance = read(base, 'native-archive.json'); archive = (base / 'native-receipts.tar.gz').read_bytes()
    assert len(archive) == provenance['archive_bytes'] and sha256(archive).hexdigest() == provenance['archive_sha256']
    bodies = {}
    with tarfile.open(fileobj=BytesIO(archive), mode='r:gz') as package:
        for member in package:
            assert member.isfile() or member.isdir()
            if not member.isfile(): continue
            name = Path(member.name).relative_to('agent-scaling-capture-29a').as_posix()
            assert '..' not in Path(name).parts and name not in bodies
            bodies[name] = package.extractfile(member).read()
    inventory = json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest() == provenance['original_inventory_sha256']
    assert len(inventory['files']) == provenance['native_payloads'] == 1300
    assert set(bodies) == set(inventory['files']) | {'manifest.json'}
    for name, row in inventory['files'].items():
        assert len(bodies[name]) == row['bytes'] and sha256(bodies[name]).hexdigest() == row['sha256'], name

    def original(name):
        """Decode one inventory-authenticated original JSON document."""
        return json.loads(bodies[name])

    def process(unit, invocation, expected, identifier='python'):
        """Bind a printed result to the actual terminal native process rather than only a report file."""
        rows = [json.loads(line) for line in bodies['process/' + unit + '.journal.jsonl'].splitlines()]
        text = '\n'.join(r['MESSAGE'] for r in rows if r.get('_SYSTEMD_INVOCATION_ID') == invocation and r.get('SYSLOG_IDENTIFIER') == identifier)
        assert json.loads(text) == expected, unit
        assert any('Deactivated successfully' in r.get('MESSAGE', '') for r in rows), unit

    for source_name in ['agent-scaling-29c', 'agent-burst-29b']:
        seal = original('source/' + source_name + '/manifest.json')
        for name, digest in seal['files'].items():
            assert sha256(bodies['source/' + source_name + '/tree/' + name]).hexdigest() == digest
            assert source_matches(name, bodies['source/' + source_name + '/tree/' + name], (ROOT / name).read_bytes()), name
    assert bodies['operators/park-inactive-ab-candidates.py'] == (ROOT / 'scripts/park-inactive-ab-candidates.py').read_bytes()
    install = original('native/agent-scaling-29c/verification.json'); assert install['passed'] and install['keda_version'] == '2.21.0'
    process('iot-agent-scaling-29c', '34da5aab69664a7f88a004f1aa5dcdd5', install)
    pins = json.loads((ROOT / 'infra/agents/keda.lock.json').read_bytes())
    assert sha256(bodies['native/agent-scaling-29c/keda-2.21.0.tgz']).hexdigest() == pins['chart']['sha256']
    actual_images = {c['image'] for p in original('current/keda-pods.json')['items'] for c in p['spec']['containers']}
    assert actual_images == {p['image'] for p in pins['images'].values()}
    assert all(c['ready'] for p in original('current/keda-pods.json')['items'] for c in p['status']['containerStatuses'])
    crd = original('current/workerpool-crd.json')['spec']['versions'][0]
    assert crd['subresources']['scale']['labelSelectorPath'] == '.spec.selector'
    assert crd['schema']['openAPIV3Schema']['properties']['spec']['properties']['selector'] == {'type': 'string'}
    spec = importlib.util.spec_from_file_location('native_burst', ROOT / 'scripts/benchmark-agent-scaling.py')
    burst = importlib.util.module_from_spec(spec); spec.loader.exec_module(burst)
    prefix = 'native/agent-burst-29c/'; result = original(prefix + 'verification.json')
    assert result['passed'] and result['automatic_scale_in'] and not result['manual_replica_changes'] and not result['coursework_complete']
    process('iot-agent-burst-29c', '9b030594914148cd84ef3d5c6b545dc6', result)
    tasks = original(prefix + 'requests.json'); assert len(tasks) == result['verified_coordinator_requests'] == 18
    capacity_errors = 0; contexts = set(); child_sessions = set(); completed_times = []
    for row in tasks:
        folder = prefix + row['path'] + '/'; request = original(folder + 'coordinator-request.json')
        context = request['params']['message']['contextId']; assert context not in contexts; contexts.add(context)
        replies = [('coordinator-response.json', original(folder + 'coordinator-response.json'))]
        retry_names = sorted(n for n in bodies if n.startswith(folder + 'capacity-retries/') and n.endswith('/response.json'))
        for name in retry_names:
            retry_folder = name.removesuffix('response.json')
            assert original(retry_folder + 'request.json') == request
            transport = original(retry_folder + 'transport.json'); assert transport['http_status'] == 200 and transport['response_complete']
            replies.append((name.removeprefix(folder), original(name)))
        assert row['http_attempts'] == len(replies)
        for _, rejected in replies[:-1]: assert burst.retryable_capacity_error(rejected)
        assert row['capacity_rejections'] == len(replies) - 1; capacity_errors += len(replies) - 1
        final_name, reply = replies[-1]; assert prefix + row['final_response_path'] == folder + final_name
        assert reply['result']['contextId'] == context and reply['id'] == request['id']
        children = {role: {kind: original(folder + role + '-' + kind + '.json') for kind in ['session', 'tasks']} for role in ['retrieval', 'drift']}
        accepted = verify_coordinator(reply, children)
        assert accepted == original(folder + 'acceptance.json') and accepted['passed'] and row['passed']
        for role in children:
            session = children[role]['session']['data']['session']['id']; assert session not in child_sessions; child_sessions.add(session)
        completed_times.append(datetime.fromisoformat(reply['result']['status']['timestamp']))
    assert len(child_sessions) == 36 and capacity_errors == result['retryable_capacity_rejections'] == 14
    assert result['unhandled_request_failures'] == 0
    lease = original(prefix + 'feature-lease.json'); assert lease == original('native/agent-feature-29g-resume/verification.json')
    assert lease['passed'] and lease['resume_deploy'] and lease['machines'] == 200
    for finished in completed_times: assert datetime.fromisoformat(lease['prepared_wall_utc']) < finished < datetime.fromisoformat(lease['lease_expires_utc'])
    observations = original(prefix + 'observations.json'); maxima = {role: {'desired': 0, 'ready': 0, 'running': 0} for role in burst.ROLES}
    for observation in observations:
        location = prefix + observation['prefix'] + '/'
        pools = original(location + 'pools.json')['items']; pods = original(location + 'pods.json')['items']; actors = original(location + 'actors.json')['data']['actors']
        for role in burst.ROLES:
            pool_name = 'iot-' + role + '-pool'; pool = next(p for p in pools if p['metadata']['name'] == pool_name)
            assert pool['spec']['selector'] == 'ate.dev/worker-pool=' + pool_name
            ready = {p['metadata']['uid'] for p in pods if p['metadata']['labels'].get('ate.dev/worker-pool') == pool_name
                     and not p['metadata'].get('deletionTimestamp') and any(c['type'] == 'Ready' and c['status'] == 'True' for c in p['status'].get('conditions', []))}
            running = [a for a in actors if a['atespace'] == 'iot-agents' and a['status'] == 'Running' and a['actorTemplateName'].startswith('iot-' + role + '-')]
            recorded = observation['roles'][role]
            assert recorded['desired'] == pool['spec']['replicas'] and set(recorded['ready_worker_uids']) == ready
            assert recorded['ready'] == len(ready) and recorded['running'] == len(running)
            assert recorded['active_worker_names'] == sorted({a['ateomPodName'] for a in running})
            for field in maxima[role]: maxima[role][field] = max(maxima[role][field], recorded[field])
        for scaler in original(location + 'scalers.json')['items']:
            conditions = {c['type']: c['status'] for c in scaler['status']['conditions']}
            assert conditions['Fallback'] == 'False' and conditions['Ready'] == 'True' and conditions['HPAActive'] == 'True'
    assert maxima == result['maxima']
    for role in burst.ROLES:
        assert maxima[role]['desired'] == maxima[role]['ready'] == 4 and maxima[role]['running'] >= 2
        for row in [observations[0], observations[-1]]: assert all(row['roles'][role][key] == value for key, value in [('desired', 2), ('ready', 2), ('running', 0)])
    assert not original('native/agent-scaling-29a/verification.json')['passed']
    failed = [original(n) for n in bodies if n.startswith('native/agent-burst-29b/requests/') and n.endswith('/coordinator-response.json')]
    assert any(burst.retryable_capacity_error(r) for r in failed)
    parked = original('native/park-inactive-ab-29a/verification.json'); assert parked['passed'] and len(parked['changed_to_zero']) == 3
    assert not parked['serving_endpoints_affected']
    binding = original('native/feature-rollout-recovery-29a/candidate-binding.json'); assert binding['candidate_revision'] == 36 and not binding['regenerated_data'] and not binding['edited_helm_storage']
    # UI captures show the earlier independently verified N conversation and this run's actual capacity cycle.
    with __import__('zipfile').ZipFile(ROOT / 'docs/evidence/agent-network/native-receipts.zip') as package:
        original_n = {role: json.loads(package.read('native/agent-chat-29n/' + role + '-response.json')) for role in burst.ROLES}
    index = read(base / 'ui', 'index.json'); assert len(index['entries']) == 10 and index['original_screenshots']
    ui_texts = {}
    for entry in index['entries']:
        assert entry['pixels_edited'] is False and entry['image_format'] == 'JPEG'
        assert jpeg_dimensions((base / 'ui' / entry['image']).read_bytes()) == (entry['width'], entry['height']) == (1280, 720)
        text = (base / 'ui' / entry['accessibility']).read_text(); ui_texts[entry['subject']] = text
        if entry['subject'] in ['retrieval-N', 'drift-N', 'coordinator-N'] and 'answer' in entry['image']:
            role = entry['subject'].removesuffix('-N'); expected = final_answer(original_n[role]['result'])
            candidates = [json.loads(m[1]) for m in re.finditer(r'^\s*\d+ text (\{.*\})$', text, flags=re.MULTILINE)]
            assert expected in candidates, role
    assert all('iot-' + role in ui_texts['catalog'] for role in burst.ROLES) and ui_texts['catalog'].count('0.2.2') == 3
    assert 'maxTokens' in ui_texts['models'] and '768' in ui_texts['models']
    return {'passed': True, 'public_payloads': public_count, 'original_native_payloads': 1300, 'original_screenshots': 10,
            'verified_coordinator_requests': len(tasks), 'independent_child_sessions': len(child_sessions),
            'retryable_capacity_rejections_retained': capacity_errors, 'all_three_pools_2_to_4_to_2': True,
            'catalog_newline_normalization_only': True,
            'unhandled_request_failures': 0, 'cross_node_ha_verified': False, 'warmup_benchmark_verified': False, 'coursework_complete': False}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
