"""Record real MCP scale cycles under bounded, validated business-tool traffic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from infra.mcp_scaling import evaluate, quantity

ROLES = ['retrieval', 'drift']
TOOLS = {'retrieval': 'search_knowledge', 'drift': 'assess_feature_drift'}


def run(args):
    """Issue no replica mutations; retain original controller samples and every business response."""
    root, out = args.runtime_root.resolve(), args.output.resolve()
    source = Path(__file__).resolve().parents[1]
    if os.geteuid() != 0 or not out.is_relative_to(root/'outputs') or not 180 <= args.duration <= 600:
        raise ValueError('Use sudo, a fresh project output and 180..600 seconds of load')
    if not re.fullmatch(r'mcp-scale-[a-z0-9-]{1,30}', out.name):
        raise ValueError('Require a unique mcp-scale-* measurement name')
    seal = json.loads((source.parent/'manifest.json').read_bytes())
    for name, pin in seal['files'].items():
        if sha256((source/name).read_bytes()).hexdigest() != pin:
            raise ValueError('Sealed source changed: '+name)
    session = json.loads((root/'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds() < 2400:
        raise ValueError('Require forty approved session minutes')
    if int(next(v.split()[1] for v in Path('/proc/meminfo').read_text().splitlines() if v.startswith('MemAvailable:'))) < 12*1024**2:
        raise ValueError('Require twelve GiB available memory')
    tokens = json.loads((root/'data/runtime/secrets/mcp-tools.json').read_bytes())
    private = [tokens[role].encode() for role in ROLES]
    out.mkdir(mode=0o700, exist_ok=False)
    k = ['/usr/local/bin/k3s', 'kubectl']
    opener = build_opener(ProxyHandler({}))
    lock, stop = Lock(), Event()
    counts = {role: {'success': 0, 'failure': 0} for role in ROLES}
    history = []
    report = {'passed': False}

    def keep(name, raw):
        """Keep original bounded receipts after checking the actual credentials used for this run."""
        if any(value in raw for value in private):
            raise ValueError('Credential-bearing receipt refused')
        path = out/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)

    def save(name, value):
        """Persist strict JSON without inventing successful missing observations."""
        keep(name, (json.dumps(value, indent=2, allow_nan=False)+'\n').encode())

    def kube(name, *query):
        """Capture read-only Kubernetes observations and their original process status."""
        p = subprocess.run(k+['get', *query, '-n', 'iot-agents', '-o', 'json'], capture_output=True, timeout=25)
        keep(name+'.stdout', p.stdout); keep(name+'.stderr', p.stderr)
        keep(name+'.exit', (str(p.returncode)+'\n').encode())
        if p.returncode:
            raise RuntimeError('Native observation failed: '+name)
        return json.loads(p.stdout)

    def http(url, payload=None, token=None):
        """Bound private HTTP reads and never store request authentication headers."""
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer '+token
        req = Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            with opener.open(req, timeout=45) as response:
                return response.status, response.read(2*1024**2+1)
        except HTTPError as error:
            return error.code, error.read(2*1024**2+1)

    save('source.json', seal); save('session.json', session)
    services = kube('services', 'services')['items']
    urls = {role: 'http://'+next(v['spec']['clusterIP'] for v in services if v['metadata']['name']=='iot-'+role+'-mcp')+':8080' for role in ROLES}
    deployments = kube('deployment-binding', 'deployments')['items']
    retrieval = next(v for v in deployments if v['metadata']['name']=='iot-retrieval-mcp')
    snapshot = Path(next(v['hostPath']['path'] for v in retrieval['spec']['template']['spec']['volumes'] if v['name']=='snapshot'))
    chunks_raw = (snapshot/'chunks.json').read_bytes()
    keep('chunks.json', chunks_raw)
    chunks = {row['chunk_id']: row for row in json.loads(chunks_raw)}
    drift = next(v for v in deployments if v['metadata']['name']=='iot-drift-mcp')
    reference = next(v['value'] for v in drift['spec']['template']['spec']['containers'][0]['env'] if v['name']=='IOT_DRIFT_REFERENCE_ID')
    begin = time.monotonic()

    def sample(phase):
        """Bind native HPA demand to actual Pod UIDs, Ready state and per-Pod business counters."""
        prefix = 'observations/'+str(len(history)).zfill(4)
        data = kube(prefix+'/resources', 'deployment,pod,hpa,scaledobject,endpointslice,event')['items']
        row = {'phase': phase, 'elapsed_seconds': time.monotonic()-begin,
               'utc': datetime.now(timezone.utc).isoformat(), 'prefix': prefix, 'roles': {}}
        for role in ROLES:
            name = 'iot-'+role+'-mcp'
            deployment = next(v for v in data if v['kind']=='Deployment' and v['metadata']['name']==name)
            targets = [v for v in data if v['kind']=='HorizontalPodAutoscaler' and v['spec']['scaleTargetRef'].get('kind')=='Deployment' and v['spec']['scaleTargetRef']['name']==name]
            if len(targets) != 1: raise ValueError('Require exactly one autoscaler owner per MCP Deployment')
            hpa = targets[0]
            if (hpa['spec']['minReplicas'], hpa['spec']['maxReplicas']) != (2, 4):
                raise ValueError('Require the reviewed two-to-four HPA bounds')
            active = any(v['type']=='ScalingActive' and v['status']=='True' for v in hpa['status'].get('conditions', []))
            configured = hpa['spec']['metrics'][0]; actual = hpa['status'].get('currentMetrics', [])
            if configured['type']=='Resource' and configured['resource']['name']=='cpu':
                demand = next((v['resource']['current'].get('averageUtilization') for v in actual if v['type']=='Resource' and v['resource']['name']=='cpu'), None)
                target = configured['resource']['target']['averageUtilization']; metric_source = 'Resource:cpu'
            elif configured['type']=='External':
                metric_name = configured['external']['metric']['name']; metric_source = 'External:'+metric_name
                value = next((v['external']['current'].get('averageValue') for v in actual if v['type']=='External' and v['external']['metric']['name']==metric_name), None)
                demand = quantity(value) if value is not None else None
                target = quantity(configured['external']['target']['averageValue'])
                scaler = next(v for v in data if v['kind']=='ScaledObject' and v['metadata']['name']==name)
                conditions = {v['type']: v['status'] for v in scaler.get('status', {}).get('conditions', [])}
                active = active and conditions.get('Ready')=='True' and conditions.get('Fallback')!='True'
            else: raise ValueError('Unreviewed HPA metric type')
            pods = []
            for pod in data:
                if pod['kind']!='Pod' or pod['metadata'].get('labels', {}).get('app.kubernetes.io/instance')!=name or pod['metadata'].get('deletionTimestamp'):
                    continue
                ready = any(v['type']=='Ready' and v['status']=='True' for v in pod['status'].get('conditions', []))
                identity = pod['metadata']['uid']; metrics = {}
                if ready:
                    status, raw = http('http://'+pod['status']['podIP']+':8080/metrics')
                    keep(prefix+'/'+identity+'.prom', raw)
                    if status != 200: raise ValueError('Ready Pod metrics unavailable')
                    for line in raw.decode().splitlines():
                        if line.startswith('iot_mcp_tool_calls_total{') and 'tool="'+TOOLS[role]+'"' in line:
                            result = re.search(r'result="(success|failure)"', line).group(1)
                            metrics[result] = metrics.get(result, 0)+float(line.rsplit(' ', 1)[1])
                pods.append({'uid': identity, 'name': pod['metadata']['name'], 'ip': pod['status'].get('podIP'),
                             'ready': ready, 'successful_calls': metrics.get('success', 0), 'failed_calls': metrics.get('failure', 0),
                             'containers': [{'name': v['name'], 'image_id': v.get('imageID'), 'restarts': v['restartCount']} for v in pod['status'].get('containerStatuses', [])]})
            row['roles'][role] = {'deployment_uid': deployment['metadata']['uid'],
                'template_sha256': sha256(json.dumps(deployment['spec']['template'], sort_keys=True).encode()).hexdigest(),
                'desired': deployment['spec']['replicas'], 'hpa_uid': hpa['metadata']['uid'], 'hpa_active': active,
                'demand_value': demand, 'demand_target': target, 'metric_source': metric_source, 'pods': pods}
        history.append(row); save('observations.json', history); return row

    def client(role, number):
        """Call the existing REST transport's shared MCP dispatcher and validate every native result."""
        index = 0
        while not stop.is_set() and time.monotonic() < load_deadline:
            identity = out.name+'-'+role+'-'+str(number)+'-'+str(index); index += 1
            window = identity
            payload = ({'question': 'How do vibration, temperature and event timestamps indicate pump maintenance needs?', 'top_k': 3}
                       if role=='retrieval' else {'window_id': window, 'machine_ids': ['P-'+str(i).zfill(3) for i in range(1, 33)]})
            path = '/v1/knowledge:search' if role=='retrieval' else '/v1/drift:assess'
            started = time.monotonic(); status = 0
            try:
                status, raw = http(urls[role]+path, payload, tokens[role]); keep('requests/'+identity+'.json', raw)
                if status != 200: raise ValueError('Business HTTP '+str(status))
                value = json.loads(raw)
                if role=='retrieval':
                    if value['snapshot_sha256'] != sha256(chunks_raw).hexdigest() or len(value['matches']) != 3:
                        raise ValueError('Knowledge snapshot/result count differs')
                    for match in value['matches']:
                        if any(match.get(key) != expected for key, expected in chunks[match['chunk_id']].items()):
                            raise ValueError('Knowledge passage differs from actual snapshot')
                elif value['reference_id'] != reference or value['window_id'] != window or value['unique_rows'] != 32:
                    raise ValueError('Drift reference/window/cohort differs')
                outcome = 'success'; error = None
            except Exception as failure:
                outcome = 'failure'; error = type(failure).__name__+': '+str(failure); stop.set()
            with lock:
                counts[role][outcome] += 1
                save('requests/'+identity+'-transport.json', {'role': role, 'http': status,
                     'seconds': time.monotonic()-started, 'validated': outcome=='success', 'error': error})
        return index

    try:
        initial = sample('baseline')
        if any(v['desired']!=2 or sum(p['ready'] for p in v['pods'])!=2 or not v['hpa_active'] for v in initial['roles'].values()):
            raise ValueError('Require two ready baseline Pods per role and valid HPA metrics')
        load_deadline = time.monotonic()+args.duration
        clients = {'retrieval': args.clients, 'drift': args.drift_clients or args.clients}
        with ThreadPoolExecutor(max_workers=sum(clients.values())) as pool:
            futures = [pool.submit(client, role, i) for role in ROLES for i in range(clients[role])]
            while time.monotonic()<load_deadline and not stop.is_set():
                sample('load'); time.sleep(5)
            stop.set()
            for future in futures: future.result()
        sample('drain'); save('request-counts.json', counts)
        deadline = time.monotonic()+540
        while True:
            last = sample('cooldown')
            if all(v['desired']==2 and sum(p['ready'] for p in v['pods'])==2 and v['hpa_active'] for v in last['roles'].values()): break
            if time.monotonic()>deadline: raise TimeoutError('Automatic scale-in was not observed')
            time.sleep(10)
        report = evaluate(history, counts)
        report.update(checked_utc=datetime.now(timezone.utc).isoformat(), load_seconds=args.duration,
                      clients_per_role=clients, elapsed_seconds=time.monotonic()-begin,
                      transport='REST endpoints sharing the native MCP tool dispatcher', manual_replica_mutations_by_recorder=False)
    except Exception as error:
        stop.set(); report.update(error=type(error).__name__+': '+str(error), request_counts=counts)
    finally:
        save('verification.json', report)
    print(json.dumps(report, indent=2)); return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration', type=int, default=240); parser.add_argument('--clients', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--drift-clients', type=int, choices=range(1, 9), help='Optional lower concurrency for the 32-read upstream cohort')
    raise SystemExit(run(parser.parse_args()))
