"""Authenticate actual Jupyter execution, exact source, fresh Feast preparation and native specialist/MCP answers offline."""
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json
import sys
import tarfile
from integration_evidence import ROOT, capsule, read

sys.path.insert(0, str(ROOT))
from agents.answer_acceptance import final_answer, native_tools, verify_specialist
from agents.probe import QUESTIONS

MANIFEST_SHA256 = 'b692eb047a39859ced8338f567fbadf34fe4d042b56491c3f7df30030f42697a'
NOTEBOOK = 'notebooks/03_agent_feature_drift_rag.ipynb'


def text(value):
    """Compare source/output fields stored as either a JSON string or an array of lines."""
    return value if isinstance(value, str) else ''.join(value)


def output_objects(cell, prefix=''):
    """Parse complete original stdout objects while rejecting extra prose, error outputs or a fabricated summary suffix."""
    assert all(o['output_type'] == 'stream' and o['name'] == 'stdout' for o in cell['outputs'])
    stream = ''.join(text(o['text']) for o in cell['outputs'])
    if prefix:
        assert stream.startswith(prefix + '\n'); stream = stream[len(prefix) + 1:]
    result = []; decoder = json.JSONDecoder()
    while stream.strip():
        stream = stream.lstrip(); value, end = decoder.raw_decode(stream)
        result.append(value); stream = stream[end:]
    return result


def verify():
    """Recompute every notebook result from original sealed tool histories rather than its green status text."""
    base, public_count = capsule('agent-notebook', MANIFEST_SHA256)
    provenance = read(base, 'native-archive.json'); archive = (base / 'native-receipts.tar.gz').read_bytes()
    assert len(archive) == provenance['archive_bytes'] == 188307
    assert sha256(archive).hexdigest() == provenance['archive_sha256'] == '27c778d6c85f11ba2bbfe30b1b2753308af21bc4ceb2a89c089daad1e082a395'
    bodies = {}
    with tarfile.open(fileobj=BytesIO(archive), mode='r:gz') as package:
        for member in package:
            assert member.isfile() or member.isdir()
            if not member.isfile(): continue
            name = Path(member.name).relative_to('agent-notebook-capture-29a').as_posix()
            assert '..' not in Path(name).parts and name not in bodies
            bodies[name] = package.extractfile(member).read()
    inventory = json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest() == provenance['original_inventory_sha256']
    assert len(inventory['files']) == provenance['native_payloads'] == 58
    assert set(bodies) == set(inventory['files']) | {'manifest.json'}
    for name, metadata in inventory['files'].items():
        assert len(bodies[name]) == metadata['bytes'] and sha256(bodies[name]).hexdigest() == metadata['sha256'], name
    def original(name):
        """Decode an inventory-authenticated original JSON file."""
        return json.loads(bodies[name])
    seal = original('source/manifest.json'); assert len(seal['files']) == 10 and not seal['newline_normalization']
    for name, digest in seal['files'].items():
        raw = bodies['source/tree/' + name]
        assert sha256(raw).hexdigest() == digest and raw == (ROOT / name).read_bytes(), name
    prefix = 'native/agent-notebook-29a/'
    for name in ['execution.json', 'executed.ipynb', 'executed.html']:
        assert (base / name).read_bytes() == bodies[prefix + name]
    assert (base / 'answer-checks.json').read_bytes() == bodies[prefix + 'requests/answer-checks.json']
    execution = original(prefix + 'execution.json')
    assert execution['passed'] and execution['code_cells'] == execution['executed_code_cells'] == 7 and execution['error_cells'] == 0
    assert execution['source_notebook_sha256'] == seal['files'][NOTEBOOK]
    assert execution['runner_sha256'] == seal['files']['scripts/run-agent-notebook.py']
    assert execution['executed_notebook_sha256'] == sha256(bodies[prefix + 'executed.ipynb']).hexdigest()
    assert execution['source_files_unchanged'] and execution['coursework_complete'] is False
    assert execution['kernel_argv'][0].endswith('/data/runtime/notebook-venv/bin/python')
    assert execution['packages'] == {'nbclient':'0.10.4', 'nbformat':'5.10.4', 'nbconvert':'7.16.6', 'ipykernel':'6.30.1', 'jupyter-client':'8.10.0'}
    for unit, invocation in [('iot-agent-notebook-29a', '001856d4187c41ae88251e0205536fc5'),
                             ('iot-agent-feature-29h', '8666d5434f1041098a25b81299dd8979')]:
        state = bodies['process/' + unit + '.unit.txt'].decode()
        assert all(value in state for value in ['ActiveState=inactive', 'Result=success', 'ExecMainStatus=0'])
        journal = [json.loads(line) for line in bodies['process/' + unit + '.journal.jsonl'].decode().splitlines()]
        assert any(row.get('_SYSTEMD_INVOCATION_ID') == invocation for row in journal)
    lease = original(prefix + 'feature-lease.json')
    assert lease == original('native/agent-feature-29h/verification.json') and lease['passed'] and lease['machines'] == 200
    prepared = datetime.fromisoformat(lease['prepared_wall_utc']); expiry = datetime.fromisoformat(lease['lease_expires_utc'])
    assert expiry - prepared == timedelta(hours=1)
    assert prepared < datetime.fromisoformat(execution['started_utc']) < datetime.fromisoformat(execution['completed_utc']) < expiry
    sdk = original('prepared-feature/verification.json'); ready = original('prepared-feature/_READY.json')
    assert ready['verification_sha256'] == sha256(bodies['prepared-feature/verification.json']).hexdigest()
    assert ready['context_sha256'] == sdk['context_sha256'] == sha256(bodies['prepared-feature/context.json']).hexdigest()
    assert sdk['passed'] and sdk['online_comparison']['passed'] and sdk['online_comparison']['machines'] == 200
    assert original('native/agent-feature-29h/physical-ttl-before.json')['keys'] == 0
    assert original('native/agent-feature-29h/physical-ttl-after.json')['keys'] == 200
    source = original('source/tree/' + NOTEBOOK); executed = original(prefix + 'executed.ipynb')
    assert len(source['cells']) == len(executed['cells'])
    code = []
    for before, after in zip(source['cells'], executed['cells']):
        assert before['cell_type'] == after['cell_type'] and before['id'] == after['id'] and text(before['source']) == text(after['source'])
        if before['cell_type'] == 'code':
            assert before['execution_count'] is None and before['outputs'] == []
            code.append(after); assert after['execution_count'] == len(code)
    assert len(code) == 7
    checks = original(prefix + 'requests/answer-checks.json'); actual = {}; tools = {}; counts = {}
    for role, index in [('retrieval',1), ('drift',4)]:
        folder = prefix + 'requests/' + role + '/'
        request = original(folder + role + '-request.json'); reply = original(folder + role + '-response.json')
        assert request['params']['message']['parts'] == [{'kind':'text','text':QUESTIONS[role]}]
        assert request['params']['message']['contextId'] == reply['result']['contextId']
        assert request['id'] == reply['id']
        transport = original(folder + role + '-transport.json')
        assert transport['response_complete'] and transport['http_status'] == 200
        actual[role] = verify_specialist(reply, role); assert actual[role] == checks[role]
        tools[role], counts[role] = native_tools(reply['result'], role)
        values = output_objects(code[index], QUESTIONS[role])
        assert len(values) == 3 and values[1] == final_answer(reply['result']) and values[2] == actual[role]
        assert values[0] == original(folder + 'verification.json')
    assert actual['retrieval']['successful_tool_events'] == 3 and actual['drift']['successful_tool_events'] == 1
    assert actual['retrieval']['tool_error_events'] == actual['drift']['tool_error_events'] == 0
    feature = tools['retrieval']['get_machine_features']['output']; prediction = tools['retrieval']['predict_failure']['output']
    assert datetime.fromisoformat(feature['prepared_wall_utc']) == prepared
    assert feature['operating_context'] == original('prepared-feature/context.json')['machines']['P-001']
    sample = next(row for row in sdk['online_comparison']['sample'] if row['machine_id'] == 'P-001')
    assert feature['features'] == {k:v for k,v in sample.items() if k != 'machine_id'}
    assert output_objects(code[0]) == [{'source_files':10, 'model_version':lease['model_version'],
                                      'prepared_wall_utc':lease['prepared_wall_utc'], 'lease_expires_utc':lease['lease_expires_utc'], 'simulated':True}]
    assert output_objects(code[2]) == [{'native_tool_names':sorted(tools['retrieval']), 'feature':feature,
                                      'model':prediction['model'], 'prediction':prediction['prediction'], 'actual_event_counts':counts['retrieval']}]
    knowledge = tools['retrieval']['search_knowledge']['output']
    assert output_objects(code[3]) == [{'query':tools['retrieval']['search_knowledge']['arguments'],
        'snapshot_sha256':knowledge['snapshot_sha256'], 'embedding_revision':knowledge['embedding_revision'],
        'source_type':knowledge['source_type'], 'returned_passages':knowledge['matches'],
        'answer_document_ids':final_answer(original(prefix + 'requests/retrieval/retrieval-response.json')['result'])['document_ids']}]
    drift = tools['drift']['assess_feature_drift']
    assert output_objects(code[5]) == [{'native_arguments':drift['arguments'],'native_assessment':drift['output'],'actual_event_counts':counts['drift']}]
    expected = {'passed':True, 'retrieval':actual['retrieval'], 'drift':actual['drift'], 'native_successful_tool_events':4,
                'feature_and_prediction_same_snapshot':prediction['feature_trace']==feature, 'rag_source_citations_verified':True,
                'unique_drift_machines':drift['output']['unique_rows'], 'coursework_complete':False}
    assert checks == expected and output_objects(code[6]) == [expected]
    assert checks['feature_and_prediction_same_snapshot'] and checks['unique_drift_machines'] == 32
    ui = read(base / 'ui', 'index.json')
    assert ui['original_screenshots'] and len(ui['entries']) == 4 and ui['html_sha256'] == sha256(bodies[prefix+'executed.html']).hexdigest()
    for entry in ui['entries']:
        assert entry['format'] == 'JPEG' and entry['pixels_edited'] is False
        assert (base / 'ui' / entry['image']).read_bytes().startswith(b'\xff\xd8')
        ax = (base / 'ui' / entry['accessibility']).read_text(encoding='utf-8')
        assert 'http://127.0.0.1:19341/executed.html' in ax
        assert actual['retrieval']['task_id'] in ax and actual['drift']['task_id'] in ax
    return {'passed':True, 'public_payloads':public_count, 'original_native_payloads':58, 'source_files_exact':10,
            'executed_code_cells':7, 'native_specialist_requests':2, 'successful_mcp_events':4,
            'same_prepared_feast_snapshot':True, 'rag_citations_verified':True, 'unique_drift_machines':32,
            'original_notebook_export_screenshots':4, 'notebook_outputs_edited':False, 'coursework_complete':False}


if __name__ == '__main__': print(json.dumps(verify(), indent=2))
