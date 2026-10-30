"""Apply a measured observer monitoring budget and verify stable native readiness without changing application code or data."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
import argparse, json, os, sqlite3, subprocess, sys, time
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from infra.llm_steady_state import monitoring_configuration, observer_deployment


def run(args):
    """Preserve immutable images, ledger, credentials and other jobs; retain every original failure and actual observation."""
    root = args.runtime_root.resolve(); out = args.output.resolve()
    assert os.geteuid() == 0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    session = json.loads((root/'data/runtime/ci/session.json').read_bytes())
    assert (datetime.fromisoformat(session['auto_stop_utc'])-datetime.now(timezone.utc)).total_seconds() > 900
    seal = json.loads((ROOT.parent/'manifest.json').read_bytes()); assert seal['format'] == 'iot-llm-steady-state-v1'
    for name, pin in seal['files'].items(): assert sha256((ROOT/name).read_bytes()).hexdigest() == pin
    out.mkdir(exist_ok=False); os.umask(0o077); client = build_opener(ProxyHandler({})); k = ['/usr/local/bin/k3s', 'kubectl']

    def keep(name, value):
        """Persist safe original configuration and observation JSON with no runtime credential values."""
        (out/name).write_text(json.dumps(value, indent=2)+'\n')

    def command(name, argv, timeout=60):
        """Retain command streams and exits before accepting a native configuration operation."""
        result = subprocess.run(argv, capture_output=True, timeout=timeout)
        (out/(name+'.stdout')).write_bytes(result.stdout); (out/(name+'.stderr')).write_bytes(result.stderr)
        (out/(name+'.exit')).write_text(str(result.returncode)+'\n'); assert result.returncode == 0, name
        return result.stdout

    def get(name, *query):
        """Capture only approved non-secret Kubernetes resources."""
        return json.loads(command(name, k+['get', *query, '-o', 'json']))

    def http(name, url):
        """Keep original public health/Prometheus responses and their observed transport durations."""
        started = time.monotonic()
        with client.open(url, timeout=15) as response:
            raw = response.read(8*1024**2+1); assert len(raw) <= 8*1024**2 and response.status == 200
        (out/(name+'.json')).write_bytes(raw)
        return json.loads(raw), time.monotonic()-started

    before = get('deployment-before', 'deployment', 'iot-llm-observer', '-n', 'iot-llm')
    assert before['metadata']['uid'] == '208a0ec5-2e41-49ce-90a3-c2e5f0d6d2a9'
    baseline = json.loads((root/'outputs/llm-ab-models-deploy-30b/iot-llm-observer-before.stdout').read_bytes())
    desired = observer_deployment(before)
    assert before['spec'] in [baseline['spec'], observer_deployment(baseline)['spec']]
    keep('deployment-planned.json', desired); keep('source.json', seal)
    config = root/'data/runtime/monitoring-28/prometheus.json'; raw = config.read_bytes(); stat = config.stat()
    existing = json.loads(raw); planned = monitoring_configuration(existing)
    (out/'prometheus-before.json').write_bytes(raw); keep('prometheus-planned.json', planned)
    tool = root/'data/runtime/monitoring-assets-28/prometheus/prometheus-3.13.5.linux-amd64/promtool'
    command('promtool', [str(tool), 'check', 'config', str(out/'prometheus-planned.json')])
    pid = int(command('prometheus-pid-before', ['systemctl', 'show', 'iot-prometheus-28', '-p', 'MainPID', '--value']))
    assert pid > 0 and config.read_bytes() == raw
    ledger_dir = next(v['hostPath']['path'] for v in before['spec']['template']['spec']['volumes'] if v['name'] == 'ledger')
    database = Path(ledger_dir)/'telemetry.sqlite'
    with sqlite3.connect('file:'+str(database)+'?mode=ro', uri=True) as ledger:
        old_rows = dict(ledger.execute('select kind,count(*) from telemetry_events group by kind'))
    keep('ledger-before.json', old_rows)
    if planned != existing:
        pending = config.with_name('llm-steady-state.pending'); pending.write_bytes((out/'prometheus-planned.json').read_bytes())
        os.chown(pending, stat.st_uid, stat.st_gid); pending.chmod(stat.st_mode & 0o777); os.replace(pending, config)
        command('prometheus-reload', ['systemctl', 'kill', '--kill-who=main', '--signal=HUP', 'iot-prometheus-28'])
    if desired['spec'] != before['spec']:
        index = next(i for i, v in enumerate(before['spec']['template']['spec']['containers']) if v['name'] == 'observer')
        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': before['metadata']['uid']},
                 {'op': 'test', 'path': '/metadata/resourceVersion', 'value': before['metadata']['resourceVersion']}]
        for name in ['livenessProbe', 'readinessProbe']:
            patch.append({'op': 'replace', 'path': '/spec/template/spec/containers/'+str(index)+'/'+name+'/timeoutSeconds', 'value': 5})
        command('patch', k+['patch', 'deployment', 'iot-llm-observer', '-n', 'iot-llm', '--type=json', '-p', json.dumps(patch), '-o', 'json'])
    command('rollout', k+['rollout', 'status', 'deployment/iot-llm-observer', '-n', 'iot-llm', '--timeout=150s'], timeout=160)
    actual = get('deployment-after', 'deployment', 'iot-llm-observer', '-n', 'iot-llm')
    assert actual['metadata']['uid'] == before['metadata']['uid'] and actual['spec'] == desired['spec']
    service = get('service', 'service', 'iot-llm-observer', '-n', 'iot-llm')['spec']['clusterIP']
    rows = []; started = time.monotonic(); pod_identity = None
    for sample in range(13):
        if sample: time.sleep(max(0, started+15*sample-time.monotonic()))
        pods = get('pods-'+str(sample), 'pods', '-n', 'iot-llm', '-l', 'app.kubernetes.io/instance=iot-llm-observer')['items']
        pods = [v for v in pods if not v['metadata'].get('deletionTimestamp')]; assert len(pods) == 1
        pod = pods[0]; identity = (pod['metadata']['uid'], tuple((v['name'], v['restartCount']) for v in pod['status']['containerStatuses']))
        if pod_identity is None: pod_identity = identity
        assert identity == pod_identity and all(v['ready'] for v in pod['status']['containerStatuses'])
        live, live_seconds = http('live-'+str(sample), 'http://'+service+':8081/health/live')
        ready, ready_seconds = http('ready-'+str(sample), 'http://'+service+':8081/health/ready')
        assert live['live'] and ready['ready']
        rows.append({'sample': sample, 'utc': datetime.now(timezone.utc).isoformat(), 'elapsed': time.monotonic()-started,
                     'pod_uid': pod['metadata']['uid'], 'live_seconds': live_seconds, 'ready_seconds': ready_seconds})
        keep('observations.json', rows)
    targets, _ = http('targets-after', 'http://127.0.0.1:19091/api/v1/targets')
    observed = [v for v in targets['data']['activeTargets'] if v['labels'].get('job') == 'iot-llm-observer']
    assert len(observed) == 1 and observed[0]['health'] == 'up'
    assert observed[0]['scrapeInterval'] == '15s' and observed[0]['scrapeTimeout'] == '10s'
    command('top-after', k+['top', 'pod', '-n', 'iot-llm', '-l', 'app.kubernetes.io/instance=iot-llm-observer', '--containers'])
    assert pid == int(command('prometheus-pid-after', ['systemctl', 'show', 'iot-prometheus-28', '-p', 'MainPID', '--value']))
    assert json.loads(config.read_bytes()) == planned
    assert (config.stat().st_uid, config.stat().st_gid, config.stat().st_mode & 0o777) == (stat.st_uid, stat.st_gid, stat.st_mode & 0o777)
    with sqlite3.connect('file:'+str(database)+'?mode=ro', uri=True) as ledger:
        new_rows = dict(ledger.execute('select kind,count(*) from telemetry_events group by kind'))
    assert all(new_rows.get(kind, 0) >= count for kind, count in old_rows.items()); keep('ledger-after.json', new_rows)
    keep('verification.json', {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_commit': seal['commit'],
        'stable_samples': len(rows), 'stable_seconds': rows[-1]['elapsed'], 'same_pod_and_restart_counts': True, 'prometheus_pid': pid,
        'application_images_and_code_unchanged': True, 'ledger_preserved': True, 'other_scrape_jobs_preserved': True,
        'scope': 'Measured steady-state configuration repair and bounded readiness stability; not a new inference-speed or quality benchmark.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
