"""Retain six real paired A/B tasks and their actor-attributed private gateway traffic."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
from uuid import uuid4
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents.ab_configuration import VARIANTS, comparison_summary
from agents.answer_acceptance import verify_specialist
from agents.probe import QUESTIONS
from infra.agentgateway.candidate import DNS_NAME, PORTS, ab_a2a_path


def run(args):
    """Keep every attempt; a failed task is never silently replaced by another answer."""
    root, out = args.runtime_root.resolve(), args.output.resolve()
    if os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data') or not out.is_relative_to(root/'outputs'):
        raise ValueError('Require sudo and a fresh retained-workspace output')
    seal = json.loads(args.source_manifest.read_bytes())
    required = {'scripts/run-agent-ab-gateway.py','agents/ab_configuration.py','agents/answer_acceptance.py',
        'agents/probe.py','infra/agentgateway/candidate.py'}
    if (seal['format'] != 'iot-agent-ab-gateway-source-v1'
            or not re.fullmatch('[a-f0-9]{40}', str(seal.get('commit','')))
            or not isinstance(seal.get('files'),dict) or not required <= seal['files'].keys()):
        raise ValueError('Require the reviewed source packet')
    for name, digest in seal['files'].items():
        member = (ROOT/name).resolve()
        if ('\\' in name or name.startswith('/') or '..' in Path(name).parts or not member.is_relative_to(ROOT)
                or sha256(member.read_bytes()).hexdigest() != digest):
            raise ValueError('Source packet changed: '+name)
    now = datetime.now(timezone.utc)
    session = json.loads((root/'data/runtime/ci/session.json').read_bytes())
    lease = json.loads((args.feature_lease/'verification.json').read_bytes())
    migration = json.loads((args.migration/'verification.json').read_bytes())
    if (not migration['passed'] or not lease['passed'] or lease['machines'] != 200
            or (datetime.fromisoformat(session['auto_stop_utc'])-now).total_seconds() < 1800
            or (datetime.fromisoformat(lease['lease_expires_utc'])-now).total_seconds() < 1200):
        raise ValueError('Require accepted migration, a genuine feature lease and sufficient session time')
    os.umask(0o077)
    out.mkdir(exist_ok=False)
    k = ['/usr/local/bin/k3s', 'kubectl']
    private = []
    for name in ['agent-platform.json', 'mcp-tools.json']:
        for value in json.loads((root/'data/runtime/secrets'/name).read_bytes()).values():
            if isinstance(value, str) and len(value) >= 16 and value != str(root):
                private.extend([value.encode(), base64.b64encode(value.encode())])
    token = (root/'outputs/agw-smoke-20261102b/.a2a-bearer').read_text().strip()
    private.extend([token.encode(), base64.b64encode(token.encode())])

    def keep(name, raw):
        """Scan actual credentials before retaining original bytes, including error replies."""
        if any(value in raw for value in private):
            raise ValueError('Refuse credential-bearing artifact: '+name)
        path = out/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def save(name, value):
        """Write finite measurements without exposing authentication inputs."""
        keep(name, (json.dumps(value, indent=2, allow_nan=False)+'\n').encode())

    def get(label, *query):
        """Capture native public resource readbacks."""
        raw = subprocess.check_output(k+['get', *query, '-n', 'iot-agents', '-o', 'json'], timeout=45, stderr=subprocess.PIPE)
        keep(label+'.json', raw)
        return json.loads(raw)

    def logs(label):
        """Capture the complete retained gateway Pod stream instead of selecting successful lines."""
        pod = get(label+'-gateway-pod', 'pods', '-l', 'app.kubernetes.io/instance=iot-agentgateway-candidate')
        active = [p for p in pod['items'] if not p['metadata'].get('deletionTimestamp')]
        if len(active) != 1 or active[0]['status']['phase'] != 'Running':
            raise ValueError('Require exactly one running selected gateway Pod')
        raw = subprocess.check_output(k+['logs', active[0]['metadata']['name'], '-n', 'iot-agents'], timeout=45, stderr=subprocess.PIPE)
        keep(label+'.log', raw)
        return active[0]['metadata']['uid'], raw

    opener = build_opener(ProxyHandler({}))
    service = get('gateway-service', 'service', 'iot-agentgateway-candidate')
    if service['spec']['type'] != 'ClusterIP':
        raise ValueError('Keep gateway private')
    base = 'http://'+service['spec']['clusterIP']+':'+str(PORTS['a2a'])

    def http(path, payload=None):
        """Authenticate the bounded operator request and retain its actual status and route marker."""
        headers = {'Accept': 'application/json', 'A2A-Version': '0.3', 'Authorization': 'Bearer '+token}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers['Content-Type'] = 'application/json'
        started = time.monotonic()
        try:
            with opener.open(Request(base+path, data=data, headers=headers), timeout=240) as response:
                return response.status, response.read(), time.monotonic()-started, response.headers.get('x-iot-agentgateway-route')
        except HTTPError as error:
            return error.code, error.read(), time.monotonic()-started, error.headers.get('x-iot-agentgateway-route')

    controller = get('controller', 'deployment', 'kagent-controller')
    auth = subprocess.check_output(k+['create', 'token', controller['spec']['template']['spec']['serviceAccountName'],
        '-n', 'iot-agents', '--audience=api.ate-system.svc', '--duration=30m'], timeout=30, stderr=subprocess.PIPE).strip()
    private.extend([auth, base64.b64encode(auth)])
    api_raw = subprocess.check_output(k+['get', 'service', 'api', '-n', 'ate-system', '-o', 'json'], timeout=30)
    keep('ate-api-service.json', api_raw)
    api = json.loads(api_raw)
    assets = root/'outputs/agent-isolation-assets-29a'
    if sha256((assets/'grpcurl').read_bytes()).hexdigest() != '32a5cc701c4d2c84980ed348c6383926a8b397d96a4cd9303769ee868cac2251':
        raise ValueError('Require pinned grpcurl')
    env = {**os.environ, 'IOT_ATE_AUTH': auth.decode()}
    grpc = [str(assets/'grpcurl'), '-cacert', str(assets/'ateapi-ca.crt'), '-authority', 'api.ate-system.svc',
        '-expand-headers', '-H', 'authorization: Bearer ${IOT_ATE_AUTH}', '-max-time', '45', '-d', '@', api['spec']['clusterIP']+':443', 'ateapi.Control/GetActor']
    save('source.json', seal)
    save('feature-lease.json', lease)
    save('migration-acceptance.json', migration)
    originals = {kind: get(kind+'-before', kind) for kind in ['sandboxagents', 'modelconfigs', 'remotemcpservers', 'workerpools']}
    machines = ['P-001', 'P-002', 'P-003']
    save('machines.json', machines)
    rows = []
    for variant in VARIANTS:
        status, body, elapsed, marker = http(ab_a2a_path(variant)+'/.well-known/agent-card.json')
        keep('card-'+variant+'.json', body)
        card = json.loads(body)
        expected = 'http://'+DNS_NAME+':18083'+ab_a2a_path(variant)+'/'
        advertised = {card.get('url')} | {v.get('url') for v in card.get('additionalInterfaces', [])}
        save('card-'+variant+'-transport.json', {'http_status':status, 'route':marker, 'elapsed_seconds':elapsed, 'expected_url':expected})
        if status != 200 or expected not in advertised or marker != 'a2a-ab-retrieval-'+variant:
            raise ValueError('Advertised A/B interface is not the selected authenticated gateway route')
    for index, machine in enumerate(machines):
        for variant in (['a', 'b'] if index % 2 == 0 else ['b', 'a']):
            label = str(index)+'-'+variant
            context, identity = str(uuid4()), str(uuid4())
            payload = {'jsonrpc':'2.0', 'id':identity, 'method':'message/send', 'params':{
                'message':{'role':'user', 'messageId':str(uuid4()), 'contextId':context,
                    'parts':[{'kind':'text', 'text':QUESTIONS['retrieval'].replace('P-001', machine)}]},
                'configuration':{'blocking':True}}}
            save(label+'/request.json', payload)
            pod_uid, before = logs(label+'/gateway-before')
            row = {'variant':variant,'machine_id':machine,'context_id':context,'request_id':identity,
                'passed':False,'response_complete':False,'started_utc':datetime.now(timezone.utc).isoformat()}
            started = time.monotonic()
            actor = 'asr-'+sha256(('iot-agents/'+VARIANTS[variant]['agent']+'/'+context).encode()).hexdigest()[:24]
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(http, ab_a2a_path(variant)+'/', payload)
                    captured = False
                    attempt = 0
                    while not future.done() and time.monotonic()-started < 245:
                        result = subprocess.run(grpc, input=json.dumps({'actor':{'atespace':'iot-agents','name':actor}}).encode(), env=env, capture_output=True, timeout=60)
                        keep(label+'/actor-poll-'+str(attempt)+'.stdout', result.stdout)
                        keep(label+'/actor-poll-'+str(attempt)+'.stderr', result.stderr)
                        save(label+'/actor-poll-'+str(attempt)+'-exit.json', {'exit':result.returncode})
                        if result.returncode == 0 and json.loads(result.stdout).get('status') == 'STATUS_RUNNING':
                            keep(label+'/actor.json', result.stdout)
                            save(label+'/actor-exit.json', {'exit':0,'expected_actor':actor,'captured_during_request':True})
                            get(label+'/worker-pods', 'pods')
                            captured = True
                            break
                        attempt += 1
                        time.sleep(1)
                    status, body, elapsed, marker = future.result(timeout=250)
                keep(label+'/response.json', body)
                row.update(http_status=status, elapsed_seconds=elapsed, response_complete=True, route=marker)
                if status != 200 or marker != 'a2a-ab-retrieval-'+variant:
                    raise ValueError('A/B operator gateway transport failed')
                reply = json.loads(body)
                if reply.get('result', {}).get('contextId') != context or reply.get('id') != identity:
                    raise ValueError('Task identity changed')
                accepted = verify_specialist(reply, 'retrieval', machine_id=machine, knowledge_root=ROOT/'knowledge')
                save(label+'/answer-acceptance.json', accepted)
                if not captured:
                    raise ValueError('No actual Running actor/Pod was captured during the request')
                row.update(passed=True, task_id=accepted['task_id'], successful_tool_events=accepted['successful_tool_events'], tool_error_events=accepted['tool_error_events'])
            except (OSError, ValueError, AssertionError) as error:
                row.update(failure_type=type(error).__name__, elapsed_seconds=time.monotonic()-started)
            row['finished_utc'] = datetime.now(timezone.utc).isoformat()
            save(label+'/outcome.json', row)
            after_uid, after = logs(label+'/gateway-after')
            if pod_uid != after_uid or not after.startswith(before):
                raise ValueError('Gateway restarted or log prefix changed during the recorded request')
            rows.append(row)
            save('outcomes.json', rows)
            print(json.dumps(row), flush=True)
    summary = comparison_summary(rows, machines)
    save('summary.json', summary)
    for kind, before in originals.items():
        after = get(kind+'-after', kind)
        if {v['metadata']['uid']:v['spec'] for v in before['items']} != {v['metadata']['uid']:v['spec'] for v in after['items']}:
            raise ValueError('Resource specs changed during A/B requests')
    report = {'passed':all(r['passed'] for r in rows),'checked_utc':datetime.now(timezone.utc).isoformat(),
        'source_commit':seal['commit'],'summary':summary,'requests':6,'scope':'Actual A/B tasks through authenticated operator ingress; complete native logs and actor/Pod identities retained for independent model/MCP route verification. No automatic variant promotion.'}
    save('verification.json', report)
    print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root','output','source-manifest','feature-lease','migration']:
        parser.add_argument('--'+name, type=Path, required=True)
    raise SystemExit(run(parser.parse_args()))
