"""Restore retained private lab services after an approved VM start without recreating data, cloud resources or Vault storage."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler, HTTPRedirectHandler
import argparse
import base64
import http.cookiejar
import json
import ipaddress
import os
import re
import ssl
import subprocess
import time
import uuid


class NoRedirect(HTTPRedirectHandler):
    """Keep private health and unseal requests on their explicitly verified endpoint."""
    def redirect_request(self, *args):
        """Never forward the unseal request or its body to a redirect target."""
        return None


def command(args):
    """Capture subprocess output privately; only callers' explicitly selected non-secret fields are reported."""
    return subprocess.check_output(args, stderr=subprocess.PIPE)


def token_files(value):
    """Locate authorization file paths without reading or displaying bearer token values."""
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ('credentials_file', 'bearer_token_file') and isinstance(child, str): found.add(child)
            else: found.update(token_files(child))
    elif isinstance(value, list):
        for child in value: found.update(token_files(child))
    return found


def session_deadline(descriptor, now):
    """Require the existing8CPU/three-disk VM and an unchanged8hourSTOP grant with at least40minutes remaining."""
    if descriptor['name'] != 'iot-cw-vm' or descriptor['status'] != 'RUNNING' or not descriptor['machineType'].endswith('/e2-highmem-8') or len(descriptor['disks']) != 3:
        raise ValueError('Unexpected lab VM descriptor')
    scheduling = descriptor['scheduling']
    if scheduling['maxRunDuration']['seconds'] != '28800' or scheduling['instanceTerminationAction'] != 'STOP':
        raise ValueError('Keep the approved8hourSTOP scheduling')
    started = datetime.fromisoformat(descriptor['lastStartTimestamp'])
    remaining = 28800 - (now - started).total_seconds()
    if not 2400 < remaining <= 28800: raise ValueError('Use a current explicitly approved VM session with40minutes remaining')
    return int(remaining) - 120


def bind_session(path, output, descriptor):
    """Back up an older CI guard and derive its new deadline only from the validated actual Compute start."""
    previous = json.loads(path.read_bytes()); started = datetime.fromisoformat(descriptor['lastStartTimestamp']).astimezone(timezone.utc)
    if datetime.fromisoformat(previous['last_start_utc']) > started: raise ValueError('Do not replace a newer recorded session')
    expected = (started + timedelta(seconds=28800)).isoformat()
    if datetime.fromisoformat(previous['last_start_utc']) == started:
        if datetime.fromisoformat(previous['auto_stop_utc']) != datetime.fromisoformat(expected): raise ValueError('Existing session deadline differs from Compute scheduling')
        return previous
    (output / 'previous-session.json').write_bytes(path.read_bytes())
    current = {'last_start_utc': started.isoformat(), 'auto_stop_utc': expected, 'max_run_seconds': 28800,
               'source': 'Actual operator-supplied Compute descriptor, unchanged8hourSTOP; explicitly approved VM start.'}
    pending = path.with_suffix('.next'); pending.write_text(json.dumps(current, indent=2) + '\n'); pending.replace(path)
    return current


def renew_consumers(root, remaining, context, jenkins):
    """Renew existing narrow AppRole secret-ids and update one Jenkins SecretText without logging any credential value."""
    stop = datetime.fromisoformat(json.loads((root / 'data/runtime/ci/session.json').read_bytes())['auto_stop_utc'])
    remaining = min(remaining, int((stop - datetime.now(timezone.utc)).total_seconds()) - 120)
    if remaining < 300: raise ValueError('Insufficient approved time for consumer credential renewal')
    private = root / 'data/runtime/vault-28-private'
    bootstrap = json.loads((private / 'bootstrap.json').read_bytes())
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
    token = bootstrap['root_token']; bootstrap = None
    def vault(path, body=None):
        """Call only the fixed existing Vault endpoint and suppress secret-bearing response/error bodies."""
        req = Request('https://10.52.0.1:18200/v1/' + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json', 'X-Vault-Token': token})
        try:
            with opener.open(req, timeout=10) as response: return json.load(response)
        except HTTPError as error: raise RuntimeError('Vault operator request failed with HTTP ' + str(error.code)) from None
    roles = {}
    for name in ['ci', 'gateway']:
        observed = vault('auth/approle/role/iot-' + name)['data']
        if observed['token_policies'] != ['iot-' + name] or not observed['token_no_default_policy']: raise ValueError('Preserve different AppRole permissions')
        config = {'token_policies': ['iot-' + name], 'token_no_default_policy': True, 'token_ttl': 300, 'token_max_ttl': 900,
                  'secret_id_ttl': remaining, 'secret_id_num_uses': 20, 'bind_secret_id': True}
        # Role updates may return an empty204 body; use an explicit request for this write.
        req = Request('https://10.52.0.1:18200/v1/auth/approle/role/iot-' + name, data=json.dumps(config).encode(), headers={'Content-Type': 'application/json', 'X-Vault-Token': token})
        with opener.open(req, timeout=10) as response: response.read()
        role = vault('auth/approle/role/iot-' + name + '/role-id')['data']['role_id']
        secret = vault('auth/approle/role/iot-' + name + '/secret-id', {})['data']['secret_id']
        target = private / (name + '-approle.json'); pending = target.with_suffix('.next')
        pending.write_text(json.dumps({'role_id': role, 'secret_id': secret}) + '\n'); pending.chmod(0o600); pending.replace(target)
        roles[name] = {'policy': 'iot-' + name, 'token_ttl_seconds': 300, 'secret_id_ttl_seconds': remaining, 'secret_id_uses': 20}
    token = None
    # The controller reads a one-use file owned by its existing UID; the Groovy request contains only its pathname.
    home_mount = next(m for m in jenkins['Mounts'] if m['Destination'] == '/var/jenkins_home')
    home = Path(home_mount['Source']); identity = jenkins['Config']['User'].split(':')
    uid = int(identity[0]) if identity[0].isdigit() else 0
    gid = int(identity[1]) if len(identity) > 1 and identity[1].isdigit() else uid
    directory = home / 'iot-demo-operator'; directory.mkdir(mode=0o700, exist_ok=True); os.chown(directory, uid, gid); directory.chmod(0o700)
    for leftover in directory.glob('*.json'):
        if re.fullmatch(r'[a-f0-9]{32}\.json', leftover.name) and leftover.stat().st_uid == uid and leftover.stat().st_size < 1024:
            if set(json.loads(leftover.read_bytes())) == {'role_id', 'secret_id'}: leftover.unlink()
    name = uuid.uuid4().hex + '.json'; staged = directory / name
    staged.write_bytes((private / 'ci-approle.json').read_bytes()); staged.chmod(0o600); os.chown(staged, uid, gid)
    credentials = json.loads((root / 'data/runtime/ci/jenkins-private/bootstrap.json').read_bytes())
    authorization = 'Basic ' + base64.b64encode(('learner:' + credentials['admin_password']).encode()).decode(); credentials = None
    jar = http.cookiejar.CookieJar()
    from urllib.request import HTTPCookieProcessor
    client = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar), NoRedirect())
    def jenkins_http(path, data=None, headers=None):
        """Use the existing private administrative session with CSRF enabled and never archive request bodies."""
        return client.open(Request('http://127.0.0.1:18787' + path, data=data, headers={'Authorization': authorization, **(headers or {})}), timeout=30)
    try:
        with jenkins_http('/crumbIssuer/api/json') as response: crumb = json.load(response)
        script = """import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.domains.Domain
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl
import hudson.util.Secret
def store = SystemCredentialsProvider.getInstance().getStore()
def domain = Domain.global()
def old = store.getCredentials(domain).find { it.id == 'iot-vault-approle' }
if (old == null) throw new IllegalStateException('Existing credential missing')
def source = new File('/var/jenkins_home/iot-demo-operator/FILE')
def fresh = new StringCredentialsImpl(CredentialsScope.GLOBAL, old.id, old.description, Secret.fromString(source.text))
if (!store.updateCredentials(domain, old, fresh)) throw new IllegalStateException('Credential update refused')
store.save()
println('UPDATED_EXISTING_CREDENTIAL')
""".replace('FILE', name)
        headers = {crumb['crumbRequestField']: crumb['crumb'], 'Content-Type': 'application/x-www-form-urlencoded'}
        with jenkins_http('/scriptText', urlencode({'script': script}).encode(), headers) as response:
            result = response.read(65536)
        if b'UPDATED_EXISTING_CREDENTIAL' not in result: raise RuntimeError('Jenkins credential update was not acknowledged; private values were not logged')
    finally:
        staged.unlink(missing_ok=True)
    return {'roles': roles, 'jenkins_credential_id': 'iot-vault-approle', 'jenkins_updated': True, 'private_values_archived': False,
            'scope': 'Existing permissions unchanged; fresh secret-ids expire before this approved session ends.'}


def run(args):
    """Renew scoped tokens, restore five saved monitors, dynamic log discovery, existing Jenkins and sealed Vault."""
    if os.name != 'posix' or os.geteuid() != 0: raise ValueError('Run inside the existing Linux VM with sudo')
    root = args.runtime_root.resolve(); output = args.output.resolve()
    if not root.is_relative_to('/srv/iot-data') or not all(Path(p).is_mount() for p in ['/srv/iot-data', '/srv/iot-fast']):
        raise ValueError('Existing mounted lab data/scratch disks are required')
    if not output.is_relative_to(root / 'outputs'): raise ValueError('Choose a fresh receipt under this project outputs/')
    descriptor = json.loads(args.descriptor.read_bytes()); remaining = session_deadline(descriptor, datetime.now(timezone.utc))
    output.mkdir(parents=True, exist_ok=False)
    current = bind_session(root / 'data/runtime/ci/session.json', output, descriptor)
    k = ['/usr/local/bin/k3s', 'kubectl']; report = {'passed': False, 'steps': {}, 'session': current, 'source_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
    def save():
        """Retain completed phases if a later service fails, without saving token or unseal-key values."""
        (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    def active(name):
        """Check only a named coursework unit before attempting an idempotent start."""
        return subprocess.run(['systemctl', 'is-active', '--quiet', name], capture_output=True).returncode == 0
    def health(url, context=None):
        """Read a bounded public health response through the selected private endpoint."""
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
        try: response = opener.open(url, timeout=5)
        except HTTPError as error: response = error
        with response: return response.status, response.read(1_048_576)
    try:
        command(k + ['wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=180s'])
        # One existing narrowly authorized identity; never broaden RBAC or print the minted value.
        token = json.loads(command(k + ['create', 'token', 'iot-api-metrics', '-n', 'iot-observability', '--duration=' + str(remaining) + 's', '-o', 'json']))
        expiry = token['status']['expirationTimestamp']
        if datetime.fromisoformat(expiry.replace('Z', '+00:00')) >= datetime.fromisoformat(current['auto_stop_utc']): raise ValueError('Token exceeds approved VM session')
        config = json.loads((root / 'data/runtime/monitoring-28/prometheus.json').read_bytes())
        paths = token_files(config) | {str(root / 'data/runtime/log-discovery-28/auth/token')}
        for name in paths:
            path = Path(name).resolve()
            if not path.is_relative_to(root / 'data/runtime') or not path.is_file(): raise ValueError('Unexpected authorization file')
            pending = path.with_suffix('.next'); pending.write_text(token['status']['token']); pending.chmod(0o400); pending.replace(path)
        token = None; report['steps']['scoped_token'] = {'expires_utc': expiry, 'files': len(paths), 'value_saved_in_evidence': False}; save()
        # Use the exact recipes that ran before STOP, excluding the superseded static-file Alloy collector.
        recipes = root / 'data/runtime/vm-session-28b/units'
        checkpoint = json.loads((recipes.parent / 'checkpoint.json').read_bytes())
        units = ['iot-node-exporter-28.service', 'iot-loki-28.service', 'iot-pushgateway-28.service', 'iot-prometheus-28.service', 'iot-grafana-28.service']
        for name in units:
            raw = (recipes / name).read_bytes()
            if sha256(raw).hexdigest() != checkpoint['units'][name]: raise ValueError('Retained unit recipe changed: ' + name)
            if not active(name):
                target = Path('/run/systemd/system') / name
                if target.exists() and target.read_bytes() != raw: raise ValueError('Inspect different existing unit: ' + name)
                target.write_bytes(raw)
        command(['systemctl', 'daemon-reload'])
        for name in units:
            if not active(name): command(['systemctl', 'start', name])
        report['steps']['monitors'] = {'units': units, 'all_active': all(active(name) for name in units)}; save()
        # The gateway reaches loopback-only Grafana through this existing CNI-private byte relay.
        # It is a separate transient unit and must be restored alongside Grafana after STOP.
        proxy = next(p for p in ['/usr/lib/systemd/systemd-socket-proxyd', '/lib/systemd/systemd-socket-proxyd'] if Path(p).is_file())
        relay = {'socket': '[Unit]\nDescription=IoT gateway private CNI Grafana socket\n[Socket]\nListenStream=10.52.0.1:13002\nNoDelay=true\n[Install]\nWantedBy=sockets.target\n',
                 'service': '[Unit]\nDescription=IoT gateway loopback Grafana relay\n[Service]\nExecStart=' + proxy + ' 127.0.0.1:13001\nUser=nobody\nNoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nMemoryMax=32M\nCPUQuota=10%\n'}
        for suffix, text in relay.items():
            path = Path('/run/systemd/system/iot-gateway-grafana-28.' + suffix)
            if path.exists() and path.read_text() != text: raise ValueError('Inspect the existing private gateway relay')
            path.write_text(text)
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'start', 'iot-gateway-grafana-28.socket'])
        report['steps']['private_gateway_relay'] = {'socket_active': active('iot-gateway-grafana-28.socket'), 'listener': '10.52.0.1:13002', 'upstream': '127.0.0.1:13001'}; save()
        alloy = 'iot-alloy-discovery-28'
        storage = root / 'data/runtime/log-discovery-28/alloy'
        hardened = ['--property=ProtectSystem=strict', '--property=ProtectHome=yes', '--property=PrivateTmp=yes', '--property=CapabilityBoundingSet=', '--property=ReadWritePaths=' + str(storage)]
        if not active(alloy):
            command(['systemd-run', '--unit=' + alloy, '--property=RuntimeMaxSec=' + str(remaining), '--property=MemoryMax=768M', '--property=MemorySwapMax=0', '--property=CPUQuota=50%', '--property=NoNewPrivileges=yes'] + hardened + [
                str(root / 'data/runtime/logging-assets-28/alloy-linux-amd64'), 'run', '--disable-reporting', '--server.http.listen-addr=127.0.0.1:12345',
                '--storage.path=' + str(storage), str(root / 'data/runtime/log-discovery-28/config.alloy')])
        properties = command(['systemctl', 'show', alloy, '-p', 'ProtectSystem', '-p', 'ProtectHome', '-p', 'PrivateTmp', '-p', 'ReadWritePaths', '-p', 'CapabilityBoundingSet']).decode()
        if not all(value in properties.splitlines() for value in ['ProtectSystem=strict', 'ProtectHome=yes', 'PrivateTmp=yes', 'CapabilityBoundingSet=', 'ReadWritePaths=' + str(storage)]):
            dropin = Path('/run/systemd/system/iot-alloy-discovery-28.service.d/review-hardening.conf')
            dropin.parent.mkdir(parents=True, exist_ok=True)
            content = '[Service]\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\nCapabilityBoundingSet=\nReadWritePaths=' + str(storage) + '\n'
            previous = content.replace('CapabilityBoundingSet=\n', '')
            if dropin.exists() and dropin.read_text() not in (content, previous): raise ValueError('Inspect the existing Alloy hardening override')
            dropin.write_text(content); command(['systemctl', 'daemon-reload']); command(['systemctl', 'restart', alloy])
        report['steps']['collector_hardening'] = command(['systemctl', 'show', alloy, '-p', 'ProtectSystem', '-p', 'ProtectHome', '-p', 'PrivateTmp', '-p', 'ReadWritePaths', '-p', 'NoNewPrivileges', '-p', 'CapabilityBoundingSet']).decode().splitlines()
        report['steps']['dynamic_logs'] = {'active': active(alloy), 'old_positions_preserved': True}; save()
        jenkins = json.loads(command(['docker', 'inspect', 'iot-jenkins-ci-27']))[0]
        if not any(m['Destination'] == '/var/jenkins_home' and Path(m['Source']).is_relative_to(root / 'data/runtime') for m in jenkins['Mounts']): raise ValueError('Unexpected Jenkins home')
        if not jenkins['State']['Running']: command(['docker', 'start', 'iot-jenkins-ci-27'])
        report['steps']['jenkins'] = {'container_id': jenkins['Id'], 'image_id': jenkins['Image'], 'existing_home_preserved': True}; save()
        # The retained restored MLflow container deliberately has no Docker published port.
        # Restore only a loopback byte relay; keep its SQLite/artifact mount and container unchanged.
        tracking = json.loads(command(['docker', 'inspect', 'iot-mlflow-tracking-1']))[0]
        state_mount = next(m for m in tracking['Mounts'] if m['Destination'] == '/state')
        if Path(state_mount['Source']).resolve() != (root / 'data/mlflow').resolve() or not tracking['State']['Running']:
            raise ValueError('Inspect the retained MLflow state/container before restoring UI access')
        address = tracking['NetworkSettings']['Networks'][tracking['HostConfig']['NetworkMode']]['IPAddress']
        if not ipaddress.ip_address(address).is_private: raise ValueError('Require the existing private MLflow network')
        if not tracking['NetworkSettings']['Ports'].get('5000/tcp'):
            mlflow_units = {'socket': '[Unit]\nDescription=IoT demo MLflow private socket\n[Socket]\nListenStream=127.0.0.1:15000\nNoDelay=true\n',
                'service': '[Unit]\nDescription=IoT demo MLflow private relay\n[Service]\nExecStart=' + proxy + ' ' + address + ':5000\nUser=nobody\nNoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\nMemoryMax=32M\nCPUQuota=10%\n'}
            for suffix, text in mlflow_units.items():
                target = Path('/run/systemd/system/iot-demo-mlflow.' + suffix)
                if target.exists() and target.read_text() != text:
                    old = target.read_text()
                    if suffix != 'service' or not old.startswith('[Unit]\nDescription=IoT demo MLflow private relay\n[Service]\nExecStart=' + proxy + ' '):
                        raise ValueError('Preserve an independently configured MLflow relay')
                    command(['systemctl', 'stop', 'iot-demo-mlflow.service'])
                target.write_text(text)
            command(['systemctl', 'daemon-reload']); command(['systemctl', 'start', 'iot-demo-mlflow.socket'])
        model_url = 'http://127.0.0.1:15000/api/2.0/mlflow/model-versions/get?name=iot-pump-failure-v1&version=6'
        model_http, body = health(model_url); model = json.loads(body)['model_version']
        if model_http != 200 or model['version'] != '6' or model['run_id'] != 'be3b27fa0960493d92b45be69b20690e':
            raise ValueError('The retained registry model6 changed')
        report['steps']['mlflow_ui'] = {'container_id': tracking['Id'], 'image_id': tracking['Image'], 'state_mount_preserved': True,
            'listener': '127.0.0.1:15000', 'model_http': model_http, 'model_version': model['version'], 'run_id': model['run_id']}; save()
        # Vault retains the same Raft directory and server key; sys/init is deliberately absent.
        recipe = root / 'outputs/vault-28/resume-active/vault.service'; raw = recipe.read_bytes()
        binary = root / 'data/runtime/vault-assets-28/vault'; pins = json.loads(args.vault_assets.read_bytes())
        if sha256(binary.read_bytes()).hexdigest() != pins['binary_sha256']: raise ValueError('Vault binary checksum differs')
        if not active('iot-vault-28'):
            target = Path('/run/systemd/system/iot-vault-28.service')
            if target.exists() and target.read_bytes() != raw: raise ValueError('Inspect the existing Vault service first')
            target.write_bytes(raw); command(['systemctl', 'daemon-reload']); command(['systemctl', 'start', 'iot-vault-28'])
        context = ssl.create_default_context(cafile=str(root / 'outputs/vault-28/resume-active/ca.crt'))
        vault_url = 'https://10.52.0.1:18200/v1/'
        for attempt in range(30):
            try: code, body = health(vault_url + 'sys/health', context); break
            except OSError:
                if attempt == 29: raise
                time.sleep(1)
        state = json.loads(body)
        if not state.get('initialized'): raise ValueError('Unexpected uninitialized Vault; preserve it and inspect')
        if state['sealed']:
            bootstrap = json.loads((root / 'data/runtime/vault-28-private/bootstrap.json').read_bytes())
            opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
            req = Request(vault_url + 'sys/unseal', data=json.dumps({'key': bootstrap['keys_base64'][0]}).encode(), headers={'Content-Type': 'application/json'})
            with opener.open(req, timeout=10) as response: response.read(1_048_576)
            bootstrap = None
        for attempt in range(30):
            code, body = health(vault_url + 'sys/health', context); state = json.loads(body)
            if code == 200 and not state['sealed'] and not state.get('standby'): break
            time.sleep(1)
        else:
            report['steps']['vault_after_unseal'] = {'http': code, 'initialized': state.get('initialized'), 'sealed': state.get('sealed'), 'standby': state.get('standby')}
            raise ValueError('Vault has not completed Raft activation after private unseal')
        report['steps']['vault'] = {'http': code, 'cluster_id': state['cluster_id'], 'initialized': True, 'sealed': False, 'reinitialized': False}; save()
        urls = {'prometheus': 'http://127.0.0.1:19091/-/ready', 'grafana': 'http://127.0.0.1:13001/api/health', 'loki': 'http://127.0.0.1:13100/ready', 'alloy': 'http://127.0.0.1:12345/-/ready', 'jenkins': 'http://127.0.0.1:18787/login', 'mlflow': 'http://127.0.0.1:15000/health'}
        statuses = {}
        for attempt in range(90):
            for name, url in urls.items():
                try: statuses[name] = health(url)[0]
                except OSError: statuses[name] = 0
            if all(code == 200 for code in statuses.values()): break
            time.sleep(2)
        if not all(code == 200 for code in statuses.values()): raise ValueError('Service health has not recovered: ' + str(statuses))
        if args.renew_consumers:
            report['steps']['consumer_credentials'] = renew_consumers(root, remaining, context, jenkins)
        report.update(passed=True, health_http=statuses, checked_utc=datetime.now(timezone.utc).isoformat(),
            scope='Same retained lab state and private listeners; no new cloud resources, data generation or training. Consumer AppRole secret-id renewal remains separate from unseal.')
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    save(); print(json.dumps(report, indent=2)); return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--descriptor', type=Path, required=True)
    parser.add_argument('--vault-assets', type=Path, default=Path(__file__).resolve().parents[1] / 'infra/security/vault-assets.json')
    parser.add_argument('--renew-consumers', action='store_true', help='Renew existing AppRole secret-ids and the existing Jenkins SecretText after unseal')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); raise SystemExit(run(args))
