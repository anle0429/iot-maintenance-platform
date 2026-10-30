"""Exercise the real retrieval HTTPS route, both authentication layers, REST data and the native MCP SDK."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse, asyncio, base64, http.client, json, socket, ssl, sys, time
import httpx2
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client

HOST = 'retrieval.iot.test'


async def run(args):
    """Read credentials only from stdin and retain original response bytes without request authorization values."""
    credentials = json.load(sys.stdin); out = args.output; out.mkdir(exist_ok=False)
    basic = 'Basic '+base64.b64encode((credentials['gateway']['username']+':'+credentials['gateway']['password']).encode()).decode()
    bearer = 'Bearer '+credentials['mcp']; machine = credentials['machine_id']
    assert len(credentials['mcp']) >= 32 and credentials['gateway']['username'] == 'coursework'
    context = ssl.create_default_context(cafile=str(args.ca)); rows = []

    def save(name, value):
        """Keep response metadata and full bodies in immutable phase files, never incoming request headers."""
        (out/name).write_text(json.dumps(value, indent=2)+'\n')

    def request(case, method, path, edge=basic, tool=bearer, body=None, tls=True, extra=None):
        """Bind the actual private gateway address to the fixed verified DNS/SNI name without redirects or TLS bypass."""
        started = time.monotonic(); conn = http.client.HTTPConnection(args.gateway_ip, 80, timeout=30)
        peer = None
        if tls:
            conn = http.client.HTTPSConnection(args.gateway_ip, 443, context=context, timeout=60)
            conn.sock = context.wrap_socket(socket.create_connection((args.gateway_ip, 443), timeout=15), server_hostname=HOST)
            peer = {'ca_verified': True, 'hostname_verified': True, 'tls_version': conn.sock.version(),
                    'leaf_sha256': sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()}
        headers = {'Host': HOST, 'Accept': 'application/json, text/event-stream', **(extra or {})}
        if edge: headers['Authorization'] = edge
        if tool: headers['X-MCP-Authorization'] = tool
        raw = json.dumps(body).encode() if body is not None else None
        if raw is not None: headers['Content-Type'] = 'application/json'
        conn.request(method, path, body=raw, headers=headers)
        response = conn.getresponse(); content = response.read(4194305); assert len(content) <= 4194304
        row = {'case': case, 'method': method, 'path': path, 'status': response.status,
               'headers': {n: v for n, v in response.getheaders() if n.lower() in {'www-authenticate', 'content-type', 'location', 'date', 'server'}},
               'tls': peer, 'body': content.decode(), 'body_sha256': sha256(content).hexdigest(),
               'elapsed_seconds': time.monotonic()-started}
        conn.close(); rows.append(row); save('http.json', rows); return row

    negative = [('missing-edge', None, bearer), ('incorrect-edge', 'Basic Y291cnNld29yazp3cm9uZw==', bearer),
                ('bearer-is-not-edge-basic', bearer, bearer), ('missing-tool-token', basic, None),
                ('incorrect-tool-token', basic, 'Bearer incorrect-native-test')]
    for name, edge, tool in negative:
        value = request(name, 'POST', '/v1/knowledge:search', edge, tool, {'question': 'Does high vibration alone prove failure?', 'top_k': 3})
        assert value['status'] == 401, (name, value['status'])
    redirect = request('plain-http-redirect', 'GET', '/docs', None, None, tls=False)
    assert redirect['status'] == 308 and redirect['headers']['Location'] == 'https://'+HOST+':443/docs'
    docs = request('native-docs', 'GET', '/docs'); assert docs['status'] == 200 and 'swagger-ui' in docs['body']
    schema = request('native-openapi', 'GET', '/openapi.json'); assert schema['status'] == 200
    assert '/v1/machines/{machine_id}/features' in json.loads(schema['body'])['paths']
    first = request('machine-features', 'GET', '/v1/machines/'+machine+'/features')
    assert first['status'] == 200
    features = json.loads(first['body']); assert features['machine_id'] == machine
    prediction = request('machine-prediction', 'POST', '/v1/machines/'+machine+':predict')
    assert prediction['status'] == 200
    predicted = json.loads(prediction['body']); assert predicted['feature_trace'] == features and predicted['model']['version'] == '6'
    question = {'question': 'Does high vibration alone prove pump failure?', 'top_k': 3}
    found = request('knowledge-rest', 'POST', '/v1/knowledge:search', body=question)
    assert found['status'] == 200; retrieved = json.loads(found['body']); assert len(retrieved['matches']) == 3
    assert request('schema-rejection', 'POST', '/v1/knowledge:search', body=dict(question, top_k=0))['status'] == 422
    init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'iot-gateway-negative', 'version': '1'}}}
    invalid_origin = request('foreign-origin-rejected', 'POST', '/mcp', body=init, extra={'Origin': 'https://foreign.invalid'})
    assert invalid_origin['status'] == 403, invalid_origin['status']
    tls_negative = []
    for name, ctx, host in [('untrusted-ca', ssl.create_default_context(), HOST), ('wrong-hostname', context, 'incorrect.iot.test')]:
        try:
            with ctx.wrap_socket(socket.create_connection((args.gateway_ip, 443), timeout=10), server_hostname=host): pass
        except ssl.SSLCertVerificationError as error:
            tls_negative.append({'case': name, 'rejected': True, 'verify_code': error.verify_code, 'verify_message': error.verify_message})
        except ssl.SSLError as error:
            if name != 'wrong-hostname' or error.reason != 'TLSV1_UNRECOGNIZED_NAME': raise
            tls_negative.append({'case': name, 'rejected': True, 'reason': error.reason})
        else: raise AssertionError('An invalid TLS identity was accepted')
    save('tls-negative.json', tls_negative)
    capture = {'calls': []}
    async with httpx2.AsyncClient(headers={'Authorization': basic, 'X-MCP-Authorization': bearer}, verify=context, timeout=90, trust_env=False) as client:
        async with streamable_http_client('https://'+HOST+'/mcp', http_client=client) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as session:
                capture['initialized'] = (await session.initialize()).model_dump(mode='json', by_alias=True)
                tools = await session.list_tools(); capture['tools'] = tools.model_dump(mode='json', by_alias=True)
                assert {v.name for v in tools.tools} == {'get_machine_features', 'predict_failure', 'search_knowledge'}
                for name, arguments, expected in [('get_machine_features', {'machine_id': machine}, features),
                                                  ('predict_failure', {'machine_id': machine}, predicted),
                                                  ('search_knowledge', question, retrieved)]:
                    result = await session.call_tool(name, {'request': arguments})
                    capture['calls'].append({'tool': name, 'arguments': arguments, 'response': result.model_dump(mode='json', by_alias=True)})
                    save('mcp-sdk.json', capture)
                    assert not result.is_error and json.loads(result.content[0].text) == expected
    report = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'host': HOST,
              'http_requests': len(rows), 'auth_rejections': 5, 'tls_rejections': 2, 'foreign_origin_rejected': True,
              'native_mcp_tools': 3, 'rest_and_mcp_results_equal': True, 'model_version': '6', 'machine_id': machine,
              'source_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': 'Actual CA/SNI-verified private NGINX route, two authentication layers, native REST and MCP SDK. Not public DNS, browser CA trust, a new model or a load benchmark.'}
    save('verification.json', report)
    for path in out.iterdir():
        raw = path.read_bytes()
        assert not any(value.encode() in raw for value in [credentials['gateway']['password'], credentials['mcp'], basic, bearer])
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway-ip', required=True)
    for name in ['ca', 'output']: parser.add_argument('--'+name, type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
