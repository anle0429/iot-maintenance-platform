"""Verify native agent control, catalog and gVisor snapshot evidence while preserving incomplete chat and sandbox checks."""
from hashlib import sha256
import json
from integration_evidence import ROOT, capsule, read, sources

MANIFEST_SHA256 = '920fad574ff1022ac9a414634e9e5909746e4100198720ec33803f7e2d1656f3'


def tool_results(result):
    """Read actual MCP business results from A2A task history, excluding model text and requested-but-unexecuted calls."""
    observed = {}
    for message in result.get('history', []):
        for part in message.get('parts', []):
            if part.get('metadata', {}).get('adk_type') == 'function_response':
                data = part['data']; observed[data['name']] = json.loads(data['response']['output'])
    return observed


def verify():
    """Reconcile exact source/object identities and reject claims of completed agent acceptance from partial native results."""
    base, count = capsule('agent-platform', MANIFEST_SHA256)
    assert count == 528
    curation = read(base, 'curation.json')
    assert curation['native_payload_files'] == 523 and curation['original_ui_captures'] == 4
    assert curation['native_archive_sha256'] == '0268cbb7c179ee519ff08af8373c77f2047623d855bab5ee5468ba7577c70167'
    assert curation['excluded_native_files'] == []
    for prefix in ['core', 'substrate', 'invalid-schema', 'selector-failure', 'selector-repair',
                   'catalog-and-wire-failure', 'session-id-failure', 'router-failure', 'generation-timeout', 'truncated-answer']:
        sources(base, prefix)
    for prefix in ['deployment', 'model-budget', 'model-context']:
        sources(base, prefix, current=True)
    for prefix in ['core', 'substrate', 'deployment', 'model-budget', 'model-context']:
        assert read(base, prefix + '/native/verification.json')['passed']
        state = (base / 'readback' / (prefix + '-unit.stdout')).read_text()
        assert 'ActiveState=inactive\n' in state and 'ExecMainStatus=0\n' in state
    http = read(base, 'core/native/http-readback.json')
    assert set(http) == {'kagent-controller', 'kagent-ui', 'iot-agent-registry'}
    assert all(row['status'] == 200 and row['bytes'] > 0 for row in http.values())
    core = read(base, 'core/native/verification.json')
    assert core['postgres_image'] == 'postgres@sha256:1a0c23d53c1ad77468c8653fa6eed4f4105cfbef9e6f027bc1dfeb16949a8c29'
    assert 'changed=0' in (base / 'substrate/native/ssd-second.stdout').read_text()
    assert read(base, 'substrate/native/verification.json')['controller_tls_verification']
    objects = read(base, 'readback/agents.stdout')['items']
    agents = {r['metadata']['name']: r for r in objects if r['kind'] == 'SandboxAgent'}
    assert set(agents) == {'iot-retrieval', 'iot-drift', 'iot-coordinator'}
    for row in agents.values():
        assert row['spec']['declarative']['modelConfig'] == 'default-model-config'
        assert row['spec']['declarative']['runtime'] == 'go' and not row['spec']['declarative']['executeCodeBlocks']
        assert any(c['type'] == 'Ready' and c['status'] == 'True' for c in row['status']['conditions'])
    pools = [r for r in objects if r['kind'] == 'WorkerPool']
    assert len(pools) == 3
    for row in pools:
        assert row['spec']['replicas'] == row['status']['replicas'] == 2
        assert row['metadata']['labels']['kagent.dev/worker-pool'] == row['metadata']['name']
        assert row['spec']['sandboxClass'] == 'gvisor'
    tools = {r['metadata']['name']: r for r in objects if r['kind'] == 'RemoteMCPServer'}
    assert {t['name'] for t in tools['iot-retrieval-tools']['status']['discoveredTools']} == {'get_machine_features', 'predict_failure', 'search_knowledge'}
    assert {t['name'] for t in tools['iot-drift-tools']['status']['discoveredTools']} == {'assess_feature_drift'}
    for row in tools.values():
        assert row['spec']['headersFrom'][0]['valueFrom']['type'] == 'Secret'
    model = next(r for r in objects if r['kind'] == 'ModelConfig')
    assert model['spec']['model'] == 'iot-qwen2.5-7b'
    assert model['spec']['openAI']['timeout'] == 600 and model['spec']['openAI']['maxTokens'] == 384
    for row in read(base, 'readback/compiled-model-budget.json').values():
        assert row['selected_public_settings']['/model/timeout'] == 600
        assert row['selected_public_settings']['/model/max_tokens'] == 384
    props = read(base, 'model-context/native/model-props.json')
    assert props['default_generation_settings']['n_ctx'] == 8192
    before = read(base, 'model-context/native/before-values.stdout')
    after = read(base, 'model-context/native/values.json')
    assert before['contextTokens'] == 4096 and after == dict(before, contextTokens=8192)
    deployed = read(base, 'model-context/native/after-deployment.stdout')
    assert deployed['spec']['strategy'] == {'type': 'Recreate'} and deployed['status']['availableReplicas'] == 1
    templates = read(base, 'readback/templates.stdout')['items']
    assert len(templates) == 3 and all(t['status']['phase'] == 'Ready' and t['spec']['sandboxClass'] == 'gvisor' for t in templates)
    catalog_base = 'catalog-and-wire-failure/native/catalog/'
    assert read(base, 'catalog-and-wire-failure/native/phase-exits.json') == {'catalog': 0, 'chat': 1}
    catalog = read(base, catalog_base + 'verification.json'); assert catalog['passed'] and len(catalog['entries']) == 3
    for entry in catalog['entries']:
        native = agents[entry['name']]
        digest = sha256(json.dumps(native['spec'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        actual = read(base, catalog_base + entry['name'] + '-readback.json')
        assert actual['metadata']['annotations']['iot.coursework/resource-uid'] == native['metadata']['uid'] == entry['resource_uid']
        assert actual['metadata']['annotations']['iot.coursework/spec-sha256'] == digest == entry['spec_sha256']
        assert actual['spec']['source']['image'] == entry['observed_image']
    calls = 0
    for prefix, expected_state in [('generation-timeout', 'failed'), ('truncated-answer', 'completed')]:
        path = prefix + '/native/' + ('chat/' if prefix == 'truncated-answer' else '') + 'retrieval-response.json'
        result = read(base, path)['result']; assert result['status']['state'] == expected_state
        observed = tool_results(result); calls += len(observed)
        assert set(observed) == {'get_machine_features', 'predict_failure', 'search_knowledge'}
        feature = observed['get_machine_features']; prediction = observed['predict_failure']
        assert feature['machine_id'] == 'P-001' and len(feature['features']) == 8
        assert prediction['feature_trace'] == feature and prediction['model']['version'] == '6'
        for chunk in observed['search_knowledge']['matches']:
            source = ROOT / 'knowledge' / chunk['source_file']
            assert sha256(source.read_bytes()).hexdigest() == chunk['source_sha256']
            assert chunk['text'] in source.read_text()
    truncated = read(base, 'truncated-answer/native/chat/retrieval-response.json')['result']
    usage = truncated['metadata']['adk_usage_metadata']
    assert usage['candidatesTokenCount'] + usage['promptTokenCount'] == 4096
    assert truncated['metadata']['adk_partial'] is True
    text = ''.join(p.get('text', '') for a in truncated['artifacts'] for p in a['parts'])
    assert '0.103' in text and '2026-11-29T23:50:00Z' in text and text.endswith('universal vibration alarm threshold')
    for suffix in ['29a', '29b', '29c']:
        assert not read(base, 'agent-sandbox-probe-' + suffix + '/native/verification.json')['passed']
    return {'passed': True, 'payload_files': count, 'native_tool_results': calls, 'registry_entries': 3,
        'agent_roles_ready': 3, 'workers_per_role': 2, 'context_tokens': 8192,
        'agent_platform_foundation_verified': True, 'complete_agent_chat_verified': False,
        'recorded_probe_matches_current': sha256((ROOT / 'agents/probe.py').read_bytes()).hexdigest() == read(base, 'truncated-answer/source/manifest.json')['files']['agents/probe.py'],
        'sandbox_boundary_probe_verified': False, 'autoscaling_verified': False, 'warmup_benchmark_verified': False,
        'coursework_complete': False,
        'scope': 'Source-bound controller/catalog, Ready gVisor templates and actual tool history. Full conversations, boundary probes, scale and warmup remain separate acceptance work.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
