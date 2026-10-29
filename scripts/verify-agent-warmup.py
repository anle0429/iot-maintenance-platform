"""Recompute matched cold/golden actor readiness measurements and retain unfavorable native results offline."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import importlib.util
import json
import sys
import tarfile
from integration_evidence import ROOT, capsule, read

MANIFEST_SHA256 = '08cd75ed505bf5548d8282238e1354fc80018a1f50df1bc3f4d2fab802d161e4'
SPEC = importlib.util.spec_from_file_location('warmup_benchmark', ROOT / 'scripts/benchmark-agent-warmup.py')
benchmark = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(benchmark)


def verify():
    """Require fresh distinct actors, identical role templates, true boot flags, HTTP readiness and cleanup for every pair."""
    base, public_count = capsule('agent-warmup', MANIFEST_SHA256)
    provenance = read(base, 'native-archive.json'); raw = (base / 'native-receipts.tar.gz').read_bytes()
    assert len(raw) == provenance['archive_bytes'] == 203950
    assert sha256(raw).hexdigest() == provenance['archive_sha256'] == '5f3729159f31af4a5dd9faa2d48e83894bcad916b561d1089bc6e750be315daf'
    bodies = {}
    with tarfile.open(fileobj=BytesIO(raw), mode='r:gz') as package:
        for member in package:
            assert member.isfile() or member.isdir()
            if not member.isfile(): continue
            name = Path(member.name).relative_to('agent-warmup-capture-29a').as_posix()
            assert '..' not in Path(name).parts and name not in bodies
            bodies[name] = package.extractfile(member).read()
    inventory = json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest() == provenance['original_inventory_sha256']
    assert len(inventory['files']) == provenance['native_payloads'] == 740
    assert set(bodies) == set(inventory['files']) | {'manifest.json'}
    for name, metadata in inventory['files'].items():
        assert len(bodies[name]) == metadata['bytes'] and sha256(bodies[name]).hexdigest() == metadata['sha256'], name
    def original(name):
        """Decode one authenticated original JSON file."""
        return json.loads(bodies[name])
    for run in ['agent-warmup-29a','agent-warmup-29b']:
        seal = original('source/' + run + '/manifest.json'); assert len(seal['files']) == 2 and not seal['newline_normalization']
        for name, digest in seal['files'].items():
            source = bodies['source/' + run + '/tree/' + name]
            assert sha256(source).hexdigest() == digest
            if run == 'agent-warmup-29b': assert source == (ROOT / name).read_bytes(), name
    prefix = 'native/agent-warmup-29b/'
    result = original(prefix + 'verification.json'); rows = original(prefix + 'measurements.json')
    assert (base / 'verification.json').read_bytes() == bodies[prefix + 'verification.json']
    assert (base / 'measurements.json').read_bytes() == bodies[prefix + 'measurements.json']
    assert result['passed'] and result['measurements'] == len(rows) == 24
    assert result['roles'] == benchmark.summarize(rows)
    assert result['manual_replica_changes'] is False and result['coursework_complete'] is False
    before = original(prefix + 'templates-before.json')['items']; after = original(prefix + 'templates-after.json')['items']
    seen = set()
    for role in benchmark.ROLES:
        selected = [r for r in rows if r['role'] == role]
        assert len(selected) == 8 and result['roles'][role]['pairs'] == 4
        for pair in range(4):
            assert [r['mode'] for r in selected if r['pair'] == pair] == (['cold','warm'] if pair % 2 == 0 else ['warm','cold'])
        template = next(t for t in before if t['metadata']['name'] == selected[0]['template_name'])
        end = next(t for t in after if t['metadata']['uid'] == template['metadata']['uid'])
        assert end['spec'] == template['spec'] and end['status']['goldenSnapshot'] == template['status']['goldenSnapshot']
        assert template['spec']['sandboxClass'] == 'gvisor' and template['status']['phase'] == 'Ready'
        for container in template['spec']['containers']:
            assert container['readyz']['httpGet'] == {'path':'/.well-known/agent-card.json','port':80}
        for row in selected:
            assert row['actor_uid'] not in seen; seen.add(row['actor_uid'])
            assert row['template_uid'] == template['metadata']['uid']
            assert row['template_spec_sha256'] == sha256(json.dumps(template['spec'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            assert row['golden_snapshot'] == template['status']['goldenSnapshot']
            location = prefix + row['receipt_prefix'] + '/'
            create = original(location + 'create.request.json'); created = original(location + 'create.stdout')
            reference = {'atespace':'iot-agents','name':row['actor_name']}
            assert create == {'actor':{'metadata':reference,'actorTemplateNamespace':'iot-agents','actorTemplateName':row['template_name']}}
            assert created['metadata']['name'] == row['actor_name'] and created['metadata']['uid'] == row['actor_uid']
            assert created['status'] == 'STATUS_SUSPENDED' and not created.get('latestSnapshotInfo')
            assert original(location + 'resume.request.json') == {'actor':reference,'boot':row['mode']=='cold'}
            running = original(location + 'resume.stdout')['actor']
            assert running['status'] == 'STATUS_RUNNING' and running['metadata']['uid'] == row['actor_uid']
            worker = original(location + 'worker.json')
            assert worker['metadata']['uid'] == row['worker_uid'] == running['ateomPodUid']
            assert worker['spec']['nodeName'] == 'iot-cw-vm'
            assert worker['metadata']['labels']['ate.dev/worker-pool'] == 'iot-' + role + '-pool'
            assert benchmark.validate_card(original(location + 'http-card.json'), role)
            transport = original(location + 'http-transport.json')
            assert transport['http_status'] == 200 and transport['resume_to_http_card_seconds'] == row['resume_to_http_card_seconds']
            assert original(location + 'resume.timing.json')['elapsed_seconds'] == row['resume_rpc_seconds']
            assert 0 < row['resume_rpc_seconds'] <= row['resume_to_http_card_seconds']
            assert original(location + 'suspend.request.json') == {'actor':reference}
            suspended = original(location + 'after.stdout')
            assert suspended['status'] == 'STATUS_SUSPENDED' and suspended['metadata']['uid'] == row['actor_uid']
            for name in ['create','resume','cleanup-state','suspend','after']:
                assert bodies[location + name + '.exit'] == b'0\n'
    assert len(seen) == 24
    for name in ['pools-before.json','pools-after.json']:
        for pool in original(prefix + name)['items']:
            assert pool['spec']['replicas'] == pool['status']['replicas'] == 2
    for unit, invocation, status in [('iot-agent-warmup-29a','c02835d2dd3d431082809e329b36ad7c',1),
                                      ('iot-agent-warmup-29b','67624a538e834eeea6d02c28425623d9',0)]:
        state = bodies['process/' + unit + '.unit.txt'].decode()
        assert 'ExecMainStatus=' + str(status) in state
        assert ('Result=success' if status == 0 else 'Result=exit-code') in state
        journal = [json.loads(line) for line in bodies['process/' + unit + '.journal.jsonl'].decode().splitlines()]
        assert any(row.get('_SYSTEMD_INVOCATION_ID') == invocation for row in journal)
    failed = 'native/agent-warmup-29a/retrieval/0-cold/'
    assert benchmark.validate_card(original(failed + 'http-card.json'), 'retrieval')
    assert original(failed + 'after.stdout')['status'] == 'STATUS_SUSPENDED'
    assert 'native/agent-warmup-29a/verification.json' not in bodies
    assert not result['roles']['retrieval']['warm_median_lower'] and not result['roles']['drift']['warm_median_lower']
    assert result['roles']['coordinator']['warm_median_lower']
    return {'passed':True,'public_payloads':public_count,'original_native_payloads':740,'matched_fresh_actors':24,
            'pairs_per_role':4,'all_http_cards_ready':True,'owned_actors_suspended':True,'mixed_results_retained':True,
            'general_startup_speedup_established':False,'llm_generation_measured':False,'cross_node_ha_verified':False,
            'coursework_complete':False}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
