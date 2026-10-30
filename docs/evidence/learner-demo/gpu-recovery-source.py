"""Restore the retained GPU session facts and explicitly load existing aliases without changing cloud resources."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener
import argparse, json, os, subprocess, time


def session_from_descriptor(descriptor, now):
    """Accept only the retained private GPU machine's real eight-hour STOP session with sufficient remaining time."""
    if descriptor.get('name') != 'iot-cw-gpu' or str(descriptor.get('id')) != '11538226507322059' or descriptor.get('status') != 'RUNNING':
        raise ValueError('Require the actual retained running GPU VM descriptor')
    scheduling = descriptor['scheduling']; networks = descriptor['networkInterfaces']
    if str(scheduling['maxRunDuration']['seconds']) != '28800' or scheduling['instanceTerminationAction'] != 'STOP':
        raise ValueError('Preserve the eight-hour auto-STOP policy')
    if len(networks) != 1 or networks[0]['networkIP'] != '10.42.0.7' or networks[0].get('accessConfigs'):
        raise ValueError('Preserve the existing private GPU connection')
    start = datetime.fromisoformat(descriptor['lastStartTimestamp'].replace('Z', '+00:00'))
    if start > now or start+timedelta(hours=8)-now < timedelta(minutes=15):
        raise ValueError('Require a truthful session with at least fifteen minutes remaining')
    return {'last_start_utc': descriptor['lastStartTimestamp'], 'max_duration_seconds': 28800,
            'termination_action': 'STOP', 'status': 'RUNNING', 'private_ip': '10.42.0.7'}


def run(args):
    """Preserve old session bytes, load only unloaded retained aliases and keep native node/model readbacks."""
    root = args.runtime_root.resolve(); out = args.output.resolve()
    assert os.geteuid() == 0 and root.is_relative_to('/srv/iot-data') and out.is_relative_to(root/'outputs')
    raw = args.descriptor.read_bytes(); descriptor = json.loads(raw)
    new = session_from_descriptor(descriptor, datetime.now(timezone.utc))
    path = root/'data/runtime/ci/gpu-session.json'; old = path.read_bytes(); previous = json.loads(old)
    assert previous['private_ip'] == new['private_ip']
    assert datetime.fromisoformat(new['last_start_utc']) >= datetime.fromisoformat(previous['last_start_utc'])
    out.mkdir(parents=True, exist_ok=False); (out/'actual-descriptor.json').write_bytes(raw)
    (out/'previous-session.json').write_bytes(old)
    if previous != new:
        pending = path.with_suffix('.resume-pending'); pending.write_text(json.dumps(new, indent=2)+'\n')
        stat = path.stat(); os.chown(pending, stat.st_uid, stat.st_gid); pending.chmod(stat.st_mode & 0o777); pending.replace(path)
    k = ['/usr/local/bin/k3s', 'kubectl']; client = build_opener(ProxyHandler({})); deadline = time.monotonic()+420

    def get(*query):
        """Read only non-secret native readiness and service routing resources."""
        return json.loads(subprocess.check_output(k+['get', *query, '-o', 'json'], timeout=30, stderr=subprocess.PIPE))

    def models(base):
        """Read actual model-router status without issuing a generation request."""
        with client.open(base+'/models', timeout=15) as response: return json.load(response)

    while True:
        node = get('node', 'iot-cw-gpu'); service = get('service', 'iot-model-gpu', '-n', 'iot-llm')
        base = 'http://'+service['spec']['clusterIP']+':8080'
        try:
            state = models(base)
            if any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions']): break
        except OSError: pass
        assert time.monotonic() < deadline, 'Inspect the retained GPU node; do not recreate it'
        time.sleep(5)
    (out/'models-before.json').write_text(json.dumps(state, indent=2)+'\n')
    aliases = {'iot-qwen2.5-7b', 'iot-qwen2.5-1.5b'}
    assert {v['id'] for v in state['data']} == aliases
    for entry in state['data']:
        status = entry['status']['value']; assert status in {'unloaded', 'loading', 'loaded'}
        if status == 'unloaded':
            request = Request(base+'/models/load', data=json.dumps({'model': entry['id']}).encode(), headers={'Content-Type': 'application/json'})
            with client.open(request, timeout=90) as response: (out/('load-'+entry['id']+'.json')).write_bytes(response.read())
    while True:
        state = models(base); values = {v['id']: v['status']['value'] for v in state['data']}
        if set(values) == aliases and all(v == 'loaded' for v in values.values()): break
        assert time.monotonic() < deadline, 'Observe the original loading outcome before another attempt'
        time.sleep(3)
    (out/'models-after.json').write_text(json.dumps(state, indent=2)+'\n')
    (out/'node.json').write_text(json.dumps(get('node', 'iot-cw-gpu'), indent=2)+'\n')
    (out/'source.py').write_bytes(Path(__file__).read_bytes())
    proof = {'passed': True, 'checked_utc': datetime.now(timezone.utc).isoformat(), 'session': new,
             'auto_stop_utc': (datetime.fromisoformat(new['last_start_utc'])+timedelta(hours=8)).isoformat(),
             'models': values, 'new_generation_requests': 0,
             'scope': 'Retained-node/session recovery only. No cloud allocation, deployment change, weights download or agent-answer claim.'}
    (out/'verification.json').write_text(json.dumps(proof, indent=2)+'\n'); print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['runtime-root', 'descriptor', 'output']: parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
