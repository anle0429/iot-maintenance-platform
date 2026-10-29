"""Compare fresh native actors booted from the same template with and without its golden snapshot."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from statistics import median
from urllib.request import ProxyHandler, build_opener
import argparse
import base64
import json
import math
import os
import re
import shutil
import subprocess
import time

ROLES = ('retrieval', 'drift', 'coordinator')


def validate_card(card, role):
    """Bind the native underscore-normalized card name to its role URL and deployed version."""
    return (role in ROLES and card.get('name') == 'iot_' + role and card.get('version') == '0.2.2'
            and card.get('url') == 'http://iot-' + role + '.iot-agents:8080')


def summarize(rows):
    """Report matched readiness measurements, without requiring a favorable optimization result."""
    result = {}
    for role in sorted({row['role'] for row in rows}):
        selected = [row for row in rows if row['role'] == role]
        identities = {(row['template_uid'], row['template_spec_sha256']) for row in selected}
        if len(identities) != 1 or len({row['actor_uid'] for row in selected}) != len(selected):
            raise ValueError('Compare the same template using distinct fresh actors')
        modes = {mode:[r for r in selected if r['mode'] == mode] for mode in ['cold', 'warm']}
        if (not modes['cold'] or {r['mode'] for r in selected} != {'cold','warm'}
                or len(modes['cold']) != len(modes['warm'])
                or len({r['pair'] for r in modes['cold']}) != len(modes['cold'])
                or {r['pair'] for r in modes['cold']} != {r['pair'] for r in modes['warm']}):
            raise ValueError('Both modes need the same actual pairs')
        if any(type(r['resume_to_http_card_seconds']) not in {int,float} or not math.isfinite(r['resume_to_http_card_seconds'])
               or r['resume_to_http_card_seconds'] <= 0 for r in selected):
            raise ValueError('Native readiness times must be positive finite measurements')
        medians = {mode:median(row['resume_to_http_card_seconds'] for row in values) for mode,values in modes.items()}
        result[role] = {'pairs':len(modes['cold']), 'cold_median_seconds':medians['cold'], 'warm_median_seconds':medians['warm'],
                        'cold_over_warm_ratio':medians['cold'] / medians['warm'],
                        'warm_median_lower':medians['warm'] < medians['cold']}
    return result


def run(root, source, output, run_name, roles, pairs):
    """Preserve all lifecycle/readiness responses and suspend only the new actors owned by this experiment."""
    if os.name != 'posix' or os.geteuid() != 0: raise ValueError('Run with sudo on the retained Linux VM')
    root, source, output = root.resolve(), source.resolve(), output.resolve()
    if (not source.is_relative_to(root / 'data/llm-source') or not output.is_relative_to(root / 'outputs')
            or output.exists() or not re.fullmatch(r'warmup-[a-z0-9-]{1,24}', run_name)
            or not 1 <= pairs <= 4 or not roles or len(set(roles)) != len(roles) or not set(roles).issubset(ROLES)):
        raise ValueError('Use sealed lab source, a fresh output/name and bounded distinct roles/pairs')
    seal = json.loads((source.parent / 'manifest.json').read_bytes())
    for name, digest in seal['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Warm-up source seal changed')
    if sha256(Path(__file__).read_bytes()).hexdigest() != seal['files']['scripts/benchmark-agent-warmup.py']:
        raise ValueError('Execute the sealed benchmark source')
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    ram = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if ((datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 1800
            or ram < 8 * 1024**3 or shutil.disk_usage('/srv/iot-fast').free < 20 * 1024**3):
        raise ValueError('Require 30 minutes of the approved session and adequate RAM/SSD')
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'; k = ['/usr/local/bin/k3s', 'kubectl']
    private = []
    for name in ['agent-platform.json','mcp-tools.json']:
        for value in json.loads((root / 'data/runtime/secrets' / name).read_bytes()).values():
            private.extend([value.encode(), base64.b64encode(value.encode())])
    output.mkdir(parents=True, exist_ok=False)
    def keep(name, raw):
        """Store original benchmark bytes only after checking real credential values."""
        if any(value in raw for value in private): raise ValueError('Credential-bearing benchmark output refused')
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    def save(name, value):
        """Write strict local measurements without replacing any native response."""
        keep(name, (json.dumps(value, indent=2, allow_nan=False) + '\n').encode())
    def kube(name, args):
        """Retain the original specifically scoped Kubernetes JSON without changing resources."""
        raw = subprocess.check_output(k + args + ['-o','json'], timeout=20); keep(name, raw); return json.loads(raw)
    save('source.json', seal); save('session.json', session)
    templates = kube('templates-before.json', ['get','actortemplates','-n','iot-agents'])['items']
    pools = kube('pools-before.json', ['get','workerpools','-n','iot-agents'])['items']
    current = {}
    for role in roles:
        matches = [t for t in templates if t['metadata']['name'].startswith('iot-' + role + '-')]
        if len(matches) != 1: raise ValueError('Require exactly one current role template')
        template = matches[0]; containers = template['spec']['containers']
        if (template['status']['phase'] != 'Ready' or not template['status'].get('goldenSnapshot')
                or template['spec']['sandboxClass'] != 'gvisor' or not containers
                or any(c.get('readyz',{}).get('httpGet') != {'path':'/.well-known/agent-card.json','port':80} for c in containers)):
            raise ValueError('Require a Ready golden template with an explicit native HTTP readiness probe')
        pool = next(p for p in pools if p['metadata']['name'] == 'iot-' + role + '-pool')
        if pool['spec']['replicas'] != 2 or pool['status']['replicas'] != 2:
            raise ValueError('Keep the retained two-worker baseline')
        current[role] = template
    controller = kube('controller.json', ['get','deployment','kagent-controller','-n','iot-agents'])
    token = subprocess.check_output(k + ['create','token',controller['spec']['template']['spec']['serviceAccountName'],
                    '-n','iot-agents','--audience=api.ate-system.svc','--duration=10m']).strip()
    private.extend([token, base64.b64encode(token)]); env = {**os.environ,'IOT_ATE_AUTH':token.decode()}
    assets = root / 'outputs/agent-isolation-assets-29a'
    if sha256((assets / 'grpcurl').read_bytes()).hexdigest() != '32a5cc701c4d2c84980ed348c6383926a8b397d96a4cd9303769ee868cac2251':
        raise ValueError('Require the inspected grpcurl 1.9.4 binary')
    api = kube('api-service.json', ['get','service','api','-n','ate-system'])
    base = [str(assets / 'grpcurl'),'-cacert',str(assets / 'ateapi-ca.crt'),'-authority','api.ate-system.svc',
            '-expand-headers','-H','authorization: Bearer ${IOT_ATE_AUTH}','-max-time','90','-d','@',api['spec']['clusterIP']+':443']
    def grpc(prefix, method, payload):
        """Measure the native authenticated RPC including CLI/TLS overhead; never retry a failed lifecycle call."""
        save(prefix + '.request.json', payload)
        started = time.perf_counter(); result = subprocess.run(base + ['ateapi.Control/' + method],
                    input=json.dumps(payload).encode(), env=env, capture_output=True, timeout=100)
        seconds = time.perf_counter() - started
        for suffix, raw in [('stdout',result.stdout),('stderr',result.stderr),('exit',str(result.returncode).encode()+b'\n')]: keep(prefix + '.' + suffix, raw)
        save(prefix + '.timing.json', {'elapsed_seconds':seconds,'method':method,'checked_utc':datetime.now(timezone.utc).isoformat()})
        if result.returncode: raise RuntimeError('Native lifecycle call failed: ' + method)
        return json.loads(result.stdout), seconds
    all_before, _ = grpc('actors-before', 'ListActors', {})
    if any(a['metadata']['atespace'] == 'iot-agents' and a['status'] == 'STATUS_RUNNING' for a in all_before.get('actors',[])):
        raise ValueError('Wait for existing agent work to become idle')
    opener = build_opener(ProxyHandler({})); rows = []
    for role in roles:
        template = current[role]
        for pair in range(pairs):
            for mode in (['cold','warm'] if pair % 2 == 0 else ['warm','cold']):
                name = run_name + '-' + role + '-' + mode + '-' + str(pair); prefix = role + '/' + str(pair) + '-' + mode
                reference = {'atespace':'iot-agents','name':name}; created = None
                try:
                    created, _ = grpc(prefix + '/create', 'CreateActor', {'actor':{'metadata':reference,
                        'actorTemplateNamespace':'iot-agents','actorTemplateName':template['metadata']['name']}})
                    if created['status'] != 'STATUS_SUSPENDED' or created.get('latestSnapshotInfo'):
                        raise ValueError('Require a genuinely new actor with no private snapshot')
                    begin = time.perf_counter()
                    resumed, seconds = grpc(prefix + '/resume', 'ResumeActor', {'actor':reference,'boot':mode=='cold'})
                    actor = resumed['actor']
                    if actor['status'] != 'STATUS_RUNNING' or actor['metadata']['uid'] != created['metadata']['uid']:
                        raise ValueError('Native RPC did not return the same Ready actor')
                    with opener.open('http://' + actor['ateomPodIp'] + ':80/.well-known/agent-card.json', timeout=15) as response:
                        body = response.read(); status = response.status
                    ready_seconds = time.perf_counter() - begin; keep(prefix + '/http-card.json', body)
                    card = json.loads(body)
                    if status != 200 or not validate_card(card, role):
                        raise ValueError('The actual actor HTTP card differs from the measured role')
                    save(prefix + '/http-transport.json', {'http_status':status,'resume_to_http_card_seconds':ready_seconds})
                    worker = kube(prefix + '/worker.json', ['get','pod',actor['ateomPodName'],'-n','iot-agents'])
                    if (worker['metadata']['uid'] != actor['ateomPodUid']
                            or worker['metadata']['labels'].get('ate.dev/worker-pool') != 'iot-' + role + '-pool'):
                        raise ValueError('The new actor ran on an unexpected worker')
                    rows.append({'role':role,'pair':pair,'mode':mode,'actor_name':name,'actor_uid':actor['metadata']['uid'],
                        'template_name':template['metadata']['name'],'template_uid':template['metadata']['uid'],
                        'template_spec_sha256':sha256(json.dumps(template['spec'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                        'golden_snapshot':template['status']['goldenSnapshot'], 'worker_uid':worker['metadata']['uid'],
                        'resume_rpc_seconds':seconds,'resume_to_http_card_seconds':ready_seconds,'receipt_prefix':prefix})
                    save('measurements.json', rows)
                finally:
                    if created is not None:
                        actual, _ = grpc(prefix + '/cleanup-state', 'GetActor', {'actor':reference})
                        if actual['metadata']['uid'] != created['metadata']['uid']: raise ValueError('Never change a different actor')
                        if actual['status'] == 'STATUS_RUNNING': grpc(prefix + '/suspend', 'SuspendActor', {'actor':reference})
                        after, _ = grpc(prefix + '/after', 'GetActor', {'actor':reference})
                        if after['status'] != 'STATUS_SUSPENDED': raise ValueError('The owned actor was not restored to idle')
    after = kube('templates-after.json', ['get','actortemplates','-n','iot-agents'])['items']
    for role, template in current.items():
        observed = next(t for t in after if t['metadata']['uid'] == template['metadata']['uid'])
        if observed['spec'] != template['spec'] or observed['status']['goldenSnapshot'] != template['status']['goldenSnapshot']:
            raise ValueError('The measured template or golden snapshot changed')
    kube('pools-after.json', ['get','workerpools','-n','iot-agents'])
    report = {'passed':len(rows)==len(roles)*pairs*2,'checked_utc':datetime.now(timezone.utc).isoformat(),
              'measurements':len(rows),'roles':summarize(rows),'manual_replica_changes':False,'coursework_complete':False,
              'scope':'Matched fresh actors: boot from the same current template versus its golden snapshot, with native readiness and actual HTTP card. Includes CLI/TLS/HTTP overhead. Cached images and warm worker Pods retained; excludes model download, LLM generation/TTFT, first-ever host start and cross-node HA. Owned test actors retained suspended.'}
    save('verification.json', report); print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root',type=Path,required=True); parser.add_argument('--source-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True); parser.add_argument('--run-name',required=True)
    parser.add_argument('--roles',nargs='+',choices=ROLES,default=list(ROLES)); parser.add_argument('--pairs',type=int,default=4)
    args=parser.parse_args(); raise SystemExit(run(args.runtime_root,args.source_root,args.output,args.run_name,args.roles,args.pairs))
