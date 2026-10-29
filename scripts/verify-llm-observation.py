"""Reconcile the completed three-request stream pilot with original SSE, native clocks and replayed SQLite facts."""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path,PurePosixPath
from tempfile import TemporaryDirectory
import json,sys,tarfile
from integration_evidence import ROOT,capsule,read
sys.path.insert(0,str(ROOT))
from llm_observability.contracts import StreamObservation,PromptPolicy
from llm_observability.ledger import TelemetryLedger

MANIFEST='84ae9354e8e0551ad942fcb2f789a9fe34af2b0929015eab1e354a4ca8e33b74'


def verify():
    """Check independent wire usage and exact replay without pretending that offline parsing can reproduce elapsed wall time."""
    base,count=capsule('llm-observation',MANIFEST);meta=read(base,'native-archive.json');raw=(base/'native-receipts.tar.gz').read_bytes()
    assert count==6 and len(raw)==meta['archive_bytes']==27659
    assert sha256(raw).hexdigest()==meta['archive_sha256']=='58c890d25992fb17982c776bf33c0179028b73b78bb07627a8f93af49f0b6216'
    assert not meta['recompressed_or_rewritten_on_pc'] and not meta['inference_rerun_for_capture']
    assert meta['actual_native_credentials_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    bodies={}
    with tarfile.open(fileobj=BytesIO(raw)) as packet:
        for member in packet:
            assert member.isdir() or member.isfile()
            if not member.isfile():continue
            name=PurePosixPath(member.name).relative_to('llm-observation-capture-29c').as_posix()
            assert '..' not in PurePosixPath(name).parts and name not in bodies and member.size<10485760
            bodies[name]=packet.extractfile(member).read()
    inventory=json.loads(bodies['manifest.json'])
    assert sha256(bodies['manifest.json']).hexdigest()==meta['original_inventory_sha256']=='945c8cd58d99c3f44f69b7be6fcaac10e0116d5993a6b4e8c25de00810455bbf'
    assert len(inventory['files'])==meta['native_payloads']==30 and set(bodies)==set(inventory['files'])|{'manifest.json'}
    for name,item in inventory['files'].items():assert len(bodies[name])==item['bytes'] and sha256(bodies[name]).hexdigest()==item['sha256']
    def native(name):
        """Decode one original document only after authenticating the entire native archive."""
        return json.loads(bodies[name])
    for public,n in [('pilot-verification.json','pilot/verification.json'),('pilot-source.json','source/manifest.json'),
                     ('measurements.json','pilot/measurements.json'),('ledger-readback.json','pilot/ledger-readback.json')]:
        assert (base/public).read_bytes()==bodies[n]
    source=native('source/manifest.json');assert source==native('pilot/source.json') and source['format']=='iot-llm-observation-v1'
    assert source['commit']=='fedab2ce21f93cb7beebc04b098adfc0d0e0e43e' and len(source['files'])==6
    for name,digest in source['files'].items():assert sha256(bodies['source/tree/'+name]).hexdigest()==digest
    # Preserve historical helper/ledger sources separately from later observer API improvements.
    for name in ['scripts/probe-llm-observation.py']:
        assert sha256((ROOT/name).read_bytes()).hexdigest()==source['files'][name]
    assert bodies['source/tests.exit']==b'0\n' and b'Ran 18 tests' in bodies['source/tests.stderr'] and b'OK' in bodies['source/tests.stderr']
    journal=[json.loads(line) for line in bodies['journal.stdout'].splitlines()]
    assert len(journal)==2 and all(e['INVOCATION_ID']=='e7e87f00ff6b4fc5a8f9a54ac490f387' and e['UNIT']=='iot-llm-observation-pilot-29a.service' for e in journal)
    assert 'Started ' in journal[0]['MESSAGE'] and 'Deactivated successfully.' in journal[1]['MESSAGE']
    start,stop=[datetime.fromtimestamp(int(e['__REALTIME_TIMESTAMP'])/1e6,timezone.utc) for e in journal]
    cpu=native('pilot/cpu-session.json');gpu=native('pilot/gpu-session.json')
    assert start<stop<datetime.fromisoformat(cpu['auto_stop_utc'])
    assert gpu['status']=='RUNNING' and gpu['termination_action']=='STOP' and gpu['max_duration_seconds']==28800
    assert datetime.fromisoformat(gpu['last_start_utc'])<start<stop<datetime.fromisoformat(gpu['last_start_utc'])+timedelta(seconds=28800)
    pods=native('pilot/gpu-pods-before.json')['items'];live=[p for p in pods if p['status']['phase']=='Running' and not p['metadata'].get('deletionTimestamp')]
    assert len(live)==1 and live[0]['spec']['nodeName']=='iot-cw-gpu' and all(c['ready'] for c in live[0]['status']['containerStatuses'])
    assert live[0]['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu']=='1'
    router=native('pilot/router-service.json');assert router['metadata']['name']=='iot-router-epp' and router['spec']['type']=='ClusterIP'
    measurements=native('pilot/measurements.json');report=native('pilot/verification.json');rows=native('pilot/ledger-readback.json')
    assert report['passed'] and report['source_commit']==source['commit'] and report['native_requests']==report['ledger_rows_after_replay_and_reopen']==len(rows)==len(measurements)==3
    assert report['actual_reported_usage']==[m['fact']['usage'] for m in measurements]
    assert report['observed_ttft_seconds']==[m['fact']['ttft_seconds'] for m in measurements]
    assert report['full_response_seconds']==[m['fact']['duration_seconds'] for m in measurements]
    assert start<datetime.fromisoformat(report['checked_utc'])<=stop
    assert sum(report['full_response_seconds'])<(stop-start).total_seconds()
    expected_usage=[{'prompt_tokens':46,'completion_tokens':31,'total_tokens':77},
                    {'prompt_tokens':45,'completion_tokens':29,'total_tokens':74},
                    {'prompt_tokens':46,'completion_tokens':56,'total_tokens':102}]
    for i,m in enumerate(measurements):
        body=native('pilot/'+str(i)+'-request.json');assert PromptPolicy().inspect(body)==[] and body['model']=='iot-qwen2.5-7b'
        assert body['stream'] and body['stream_options']=={'include_usage':True} and body['temperature']==0 and body['seed']==7 and body['max_tokens']==128
        wire=bodies['pilot/'+str(i)+'-response.sse'];assert len(wire)==m['response_bytes'] and sha256(wire).hexdigest()==m['response_sha256']
        # An independent JSON scan verifies actual provider usage; timings remain original native measurements.
        events=[json.loads(line[6:]) for line in wire.splitlines() if line.startswith(b'data: ') and line!=b'data: [DONE]']
        usages=[{k:e['usage'][k] for k in ['prompt_tokens','completion_tokens','total_tokens']} for e in events if e.get('usage') is not None]
        assert usages[-1]==m['fact']['usage']==expected_usage[i]
        assert usages[-1]['prompt_tokens']+usages[-1]['completion_tokens']==usages[-1]['total_tokens']
        parser=StreamObservation();parser.feed(wire,0);parsed=parser.finish(0)
        assert parsed['complete'] and parsed['usage']==expected_usage[i] and parsed['finish_reason']==m['fact']['finish_reason']
        assert m['question_index']==i and m['http_status']==m['fact']['status']==200
        assert 0<m['headers_seconds']<=m['fact']['ttft_seconds']<=m['fact']['duration_seconds']
        assert m['observation']['usage']==m['fact']['usage'] and m['observation']['duration_seconds']==m['fact']['duration_seconds']
    with TemporaryDirectory() as folder:
        path=Path(folder)/'original.sqlite';path.write_bytes(bodies['pilot/telemetry.sqlite']);ledger=TelemetryLedger(path)
        assert ledger.rows()==rows
        for m in measurements:assert ledger.put('request',m['request_id'],m['fact']) is False
        assert ledger.size()==3 and ledger.rows()==rows;ledger.close();ledger=TelemetryLedger(path)
        assert ledger.rows()==rows and ledger.size()==3;ledger.close()
    return {'passed':True,'public_payloads':count,'native_payloads':30,'native_requests':3,'usage':expected_usage,
        'ttft_seconds':report['observed_ttft_seconds'],'full_response_seconds':report['full_response_seconds'],
        'replay_and_reopen_rows':3,'coursework_complete':False,
        'scope':'Original three-request router pilot, reported usage and durable replay. Native clock values are preserved, not reconstructed from SSE. No sidecar deployment, dashboard, agent trace or latency optimization comparison claim.'}


if __name__=='__main__':print(json.dumps(verify(),indent=2))
