"""Refresh the active serving release with isolated native Feast state and an atomic Helm cutover."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from urllib.request import ProxyHandler, Request, build_opener
import argparse
import base64
import ipaddress
import json
import os
import re
import shutil
import subprocess


FEATURE = 'iot-feature-api'
NAMESPACE = 'iot-features'
RELEASE = r'serve-jenkins-[1-9][0-9]{0,9}'
PREDICTOR = rf'http://(iot-pump-({RELEASE}))-predictor\.iot-serving\.svc\.cluster\.local'
PROTOCOL = 'ml/protocols/medium-60d-v1.json'
CHART = 'infra/charts/iot-api'
SEED_FILES = {'context.json': 'context.json', 'verification.json': 'verification.json',
              '_READY.json': '_READY.json', 'registry.db': 'registry/registry.db'}
SOURCE_FILES = {PROTOCOL, 'ml/__init__.py', 'ml/protocol.py',
                *(f'feature_store/{name}.py' for name in ['__init__', 'common', 'batch', 'repository', 'serving']),
                *(f'{CHART}/{name}' for name in ['Chart.yaml', 'values.yaml', 'templates/deployment.yaml',
                                                  'templates/service.yaml', 'templates/hpa.yaml'])}
REDIS_PROBE = """
from datetime import datetime, timezone
from hashlib import sha256
import json, redis, sys
client = redis.Redis(host=sys.argv[1], port=6379, socket_timeout=2, socket_connect_timeout=2)
keys = set(client.scan_iter(count=256))
with client.pipeline(transaction=False) as query:
    query.time().dbsize().info('server')
    for key in keys:
        query.pttl(key)
    clock, size, server, *ttl = query.execute()
stamp = datetime.fromtimestamp(clock[0] + clock[1] / 1000000, timezone.utc)
key_set = sha256()
for key in sorted(keys):
    key_set.update(len(key).to_bytes(4, 'big'))
    key_set.update(key)
print(json.dumps({'keys': len(keys), 'dbsize': size, 'pttl_ms': sorted(ttl), 'redis_utc': stamp.isoformat(),
                  'run_id': server['run_id'], 'key_set_sha256': key_set.hexdigest(),
                  'all_expected_project': all(k.endswith(b'iot_serving_demo') for k in keys)}))
"""


def digest(path):
    """Hash the exact bytes of a potentially large native input."""
    result = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def read(path):
    """Read native JSON without changing the bytes used in its pin."""
    return json.loads(path.read_bytes())


def write(path, value):
    """Retain a phase receipt without accepting a partial JSON write."""
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    pending.replace(path)


def clock(value):
    """Require an aware UTC clock for simulated and observed timestamps."""
    normalized = re.sub(r'(\.\d{6})\d+(?=Z|[+-])', r'\1', value)
    at = datetime.fromisoformat(normalized.replace('Z', '+00:00'))
    if at.utcoffset() != timedelta(0):
        raise ValueError('Expected a timezone-aware UTC clock')
    return at


def now():
    """Observe wall time independently of the feature simulation."""
    return datetime.now(timezone.utc)


def named(items, name):
    """Select one named Kubernetes item and reject duplicates."""
    matches = [item for item in items if item['name'] == name]
    if len(matches) != 1:
        raise ValueError('Expected exactly one entry for ' + name)
    return matches[0]


def verify_predictor_rollout(isvc, deployment, image):
    """Verify the reconciled child Deployment that supplies Standard KServe status."""
    parent = isvc['metadata']
    child = deployment['metadata']
    status = deployment['status']
    generation = child.get('generation')
    if (isvc['status'].get('deploymentMode') not in {'Standard', 'RawDeployment'}
            or child['name'] != parent['name'] + '-predictor'
            or not any(ref.get('controller') is True and ref['kind'] == 'InferenceService'
                       and ref['uid'] == parent['uid'] and ref['name'] == parent['name']
                       for ref in child.get('ownerReferences', []))
            or type(generation) is not int or generation < 1
            or isvc['status'].get('observedGeneration') != generation
            or status.get('observedGeneration') != generation
            or status.get('readyReplicas', 0) < 1
            or named(deployment['spec']['template']['spec']['containers'], 'kserve-container')['image'] != image):
        raise ValueError('The KServe-owned predictor Deployment is not reconciled with the live model')


def resource_names(release, run):
    """Generate retry-stable seed and cache names within Kubernetes DNS label limits."""
    suffix = release + '-r-' + sha256(run.encode()).hexdigest()[:12]
    return 'online-' + suffix, 'iot-feature-state-' + suffix


def redis_address(name):
    """Use Kubernetes DNS for feature pods and ClusterIP for host checks."""
    return name + '.iot-features.svc.cluster.local:6379'


def selected_release(directory, release):
    """Authenticate the original plan, selected bundle and CI stage for the active release."""
    folder = directory / 'outputs'
    plan, selection, stage, build = (read(folder / f'{name}.json') for name in ('plan', 'selection', 'staged', 'build'))
    commit = plan['commit']
    pin = read(directory / 'bundle/model-pin.json')
    hashes = selection['bundle_sha256']
    if (not re.fullmatch('[0-9a-f]{40}', commit)
            or any(row.get('release') != release or row.get('commit') != commit for row in (plan, selection, stage, build))
            or any(row.get('pin') != pin for row in (plan, selection, build)) or stage.get('model_pin') != pin
            or stage.get('passed') is not True or stage['plan_sha256'] != digest(folder / 'plan.json')
            or stage['values_sha256'] != digest(folder / 'feature-values.json')
            or plan['feature_values'] != read(folder / 'feature-values.json')
            or set(hashes) != {'portable.json', 'lineage.json', 'model-pin.json'}
            or any(digest(directory / 'bundle' / name) != value for name, value in hashes.items())
            or pin.get('production') is not True or not re.fullmatch(r'[1-9][0-9]*', pin['version'])
            or pin['portable_sha256'] != hashes['portable.json']):
        raise ValueError('The active Jenkins serving plan, stage or model bundle differs')
    lineage = read(directory / 'bundle/lineage.json')
    portable = read(directory / 'bundle/portable.json')
    if (lineage.get('model_name') != pin['name'] or lineage.get('production') is not True
            or any(lineage.get(key) != pin[key] for key in
                   ('version', 'run_id', 'protocol_sha256', 'portable_sha256', 'training_code_commit', 'data_version'))
            or portable.get('threshold') != pin['threshold'] or portable.get('protocol_sha256') != pin['protocol_sha256']):
        raise ValueError('Model bundle does not reproduce its pinned production lineage')
    return plan, stage, build, hashes


def verify_source(source):
    """Authenticate the retained packet and reject unsealed materializer or Helm inputs."""
    seal = read(source.parent / 'manifest.json')
    files = seal['files']
    if (seal.get('format') not in {'iot-api-observability-v1', 'iot-sealed-jenkins-serving-v1'}
            or not re.fullmatch('[0-9a-f]{40}', seal['commit']) or not SOURCE_FILES <= files.keys()):
        raise ValueError('The runtime packet lacks required materialization or chart inputs')
    for name, pin in files.items():
        relative = PurePosixPath(name)
        path = source / name
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                or not path.resolve().is_relative_to(source) or digest(path) != pin):
            raise ValueError('The sealed runtime source differs: ' + name)
    for path in source.rglob('*'):
        if (path.is_file() and (path.is_relative_to(source / CHART) or path.suffix in {'.py', '.pyc', '.so'})
                and path.relative_to(source).as_posix() not in files):
            raise ValueError('Unsealed runtime input: ' + str(path.relative_to(source)))
    return seal


def check_lease(probe, count, prepared, checked):
    """Reconcile wall time, Redis time and every individual key expiry."""
    ttl = probe['pttl_ms']
    observed = clock(probe['redis_utc'])
    expiry = prepared + timedelta(hours=1)
    if (count <= 0 or probe['keys'] != count or probe['dbsize'] != count or len(ttl) != count
            or not re.fullmatch('[0-9a-f]{40}', probe['run_id'])
            or any(type(value) is not int for value in ttl)
            or not 0 <= (checked - prepared).total_seconds() < 3000
            or abs((observed - checked).total_seconds()) > 15
            or min(ttl) < 600_000 or observed + timedelta(milliseconds=max(ttl)) > expiry + timedelta(seconds=2)):
        raise ValueError('The physical Redis keys and the genuine feature lease do not agree')
    return expiry


def cutover(ops, run, resume):
    """Change only the cache, seed and revision marker following isolated preparation."""
    baseline = ops.active()
    values = deepcopy(baseline['values'])
    predictor = named(values['env'], 'IOT_KSERVE_URL')['value']
    match = re.fullmatch(PREDICTOR, predictor)
    if not match:
        raise ValueError('The feature release must reference a private Jenkins predictor')
    online, seed = resource_names(match[2], run)
    if (named(values['env'], 'IOT_FEAST_REDIS')['value'] == redis_address(online)
            or named(values['volumes'], 'seed')['configMap']['name'] == seed or values['revisionMarker'] == run):
        raise ValueError('The requested refresh is already routed or its marker is occupied')
    staged = ops.prepare(baseline, run, resume)
    if (staged['online'], staged['seed']) != (online, seed):
        raise ValueError('The prepared state belongs to another refresh')
    values['revisionMarker'] = run
    named(values['env'], 'IOT_FEAST_REDIS')['value'] = redis_address(online)
    named(values['volumes'], 'seed')['configMap']['name'] = seed
    ops.validate(baseline, values, staged)
    if ops.active() != baseline:
        raise ValueError('The active serving revision changed during preparation')
    try:
        ops.upgrade(values)
        return ops.verify(baseline, values, staged)
    except Exception as failure:
        try:
            ops.rollback(baseline, values)
        except Exception as rollback:
            raise RuntimeError(f'Cutover failed ({failure}); rollback could not be verified ({rollback})') from failure
        raise



class Lab:
    """Operate the retained Linux host and private Kubernetes services."""

    def __init__(self, root, source, target, output, seal):
        """Bind all native commands and receipts to this one refresh."""
        self.root, self.source, self.target, self.output, self.seal = root, source, target, output, seal
        self.state = target / 'state'
        self.k = ['/usr/local/bin/k3s', 'kubectl']
        self.helm = [str(root / 'data/runtime/kubernetes-assets-27/helm/helm')]
        self.release_path = root / 'data/ci/releases'
        self.sdk = self.json(['docker', 'image', 'inspect', 'iot-coursework-feast-versioned:0.67.0-3.17.2'])[0]['Id']
        self.opener = build_opener(ProxyHandler({}))

    def command(self, arguments, *, payload=None, log=None, timeout=45):
        """Run a bounded native request and retain logs for mutating phases."""
        kwargs = {'input': payload, 'check': True, 'timeout': timeout}
        if log:
            with (self.output / log).open('wb') as stdout:
                subprocess.run(arguments, stdout=stdout, stderr=subprocess.STDOUT, **kwargs)
            return b''
        return subprocess.run(arguments, capture_output=True, **kwargs).stdout

    def json(self, arguments, **kwargs):
        """Decode a native structured response."""
        return json.loads(self.command(arguments, **kwargs))

    def get(self, kind, name, namespace=NAMESPACE, *, label=None, optional=False):
        """Read an exact private resource or a label-scoped Pod list."""
        args = self.k + ['get', kind] + ([name] if name else []) + ['-n', namespace, '-o', 'json']
        if label:
            args += ['-l', label]
        if optional:
            args += ['--ignore-not-found']
        raw = self.command(args)
        return json.loads(raw) if raw else None

    def service(self, name, namespace=NAMESPACE):
        """Require a private service and retain its exact ClusterIP and object UID."""
        obj = self.get('service', name, namespace)
        spec = obj['spec']
        address = ipaddress.IPv4Address(spec['clusterIP'])
        if (spec.get('type', 'ClusterIP') != 'ClusterIP' or spec.get('externalIPs')
                or not address.is_private or address.is_loopback or address.is_unspecified or address.is_link_local):
            raise ValueError('Refresh requires private IPv4 ClusterIP services')
        return {'uid': obj['metadata']['uid'], 'ip': str(address), 'selector': spec['selector']}

    def http(self, service, path, body=None):
        """Call the observed private ClusterIP without ambient proxy settings."""
        payload = json.dumps(body).encode() if body is not None else None
        request = Request('http://' + service['ip'] + path, data=payload, headers={'content-type': 'application/json'})
        with self.opener.open(request, timeout=10) as response:
            raw = response.read(262_145)
            if response.status != 200 or len(raw) > 262_144:
                raise ValueError('Expected a bounded HTTP 200 from ' + path)
            return json.loads(raw)

    def helm_state(self):
        """Join freshly read Helm values to one native release revision."""
        args = [FEATURE, '-n', NAMESPACE, '-o', 'json']
        status = self.json(self.helm + ['status', *args])
        revision = status['version']
        values = self.json(self.helm + ['get', 'values', *args, '--all', '--revision', str(revision)])
        return revision, values, status['info']['status']

    def observed_image(self, image, role, plan, build, hashes):
        """Verify the installed image against serving CI or a same-model telemetry overlay."""
        family, tag = image.rsplit(':', 1)
        if family != 'iot-coursework-' + ('feature-api' if role == 'feature' else 'predictor'):
            raise ValueError('Unexpected serving image repository')
        proof = build['images'][role]
        if image != plan[role + '_image']:
            if not re.fullmatch(r'observe-jenkins-[1-9][0-9]{0,9}', tag):
                raise ValueError('Serving image is outside the selected release and its telemetry overlays')
            folder = self.release_path / tag / 'outputs'
            overlay, deployed = read(folder / 'build.json'), read(folder / 'deployed.json')
            if (overlay['release'] != tag or deployed['release'] != tag or overlay['commit'] != deployed['commit']
                    or deployed.get('passed') is not True or deployed['model_pin'] != plan['pin']):
                raise ValueError('Telemetry overlay is bound to another model')
            proof = overlay['images'][role]
        files = ['model-pin.json'] if role == 'feature' else ['portable.json', 'lineage.json']
        receipts = proof['packaged_sha256']
        if (proof['tag'] != image or any(receipts.get(key, receipts.get('models/release/' + key)) != hashes[key]
                                         for key in files)):
            raise ValueError('Image build does not contain the selected model bytes')
        endpoint = 'unix:///run/k3s/containerd/containerd.sock'
        cri = self.json(['/usr/local/bin/k3s', 'crictl', '--config=/dev/null', '--runtime-endpoint=' + endpoint,
                         '--image-endpoint=' + endpoint, 'inspecti', image])['status']['id']
        if cri != proof['config_digest'] or not re.fullmatch(r'sha256:[0-9a-f]{64}', cri):
            raise ValueError('Installed serving image differs from the CI digest')
        return cri

    def verify_pods(self, namespace, label, container, image, digest_value, ready, values=None):
        """Check nonterminating Pod image identities even when the old cache is expired."""
        pods = self.get('pods', None, namespace, label=label)['items']
        live = [pod for pod in pods if not pod['metadata'].get('deletionTimestamp')]
        if ready and not live:
            raise ValueError('No live serving Pods were observed')
        for pod in live:
            spec = named(pod['spec']['containers'], container)
            statuses = pod['status'].get('containerStatuses', [])
            status = next((item for item in statuses if item['name'] == container), {})
            actual = status.get('imageID', '').rsplit('://', 1)[-1].rsplit('@', 1)[-1]
            if (spec['image'] != image or (actual and actual != digest_value)
                    or (ready and (actual != digest_value or status.get('ready') is not True or 'running' not in status.get('state', {})))):
                raise ValueError('Observed serving Pod image or readiness differs')
            if values is not None and (spec['env'] != values['env']
                    or pod['metadata']['annotations'].get('iot.coursework/revision-marker') != values['revisionMarker']
                    or named(pod['spec']['volumes'], 'seed')['configMap']['name'] != named(values['volumes'], 'seed')['configMap']['name']):
                raise ValueError('A live feature Pod still uses another seed or Redis route')

    def active(self):
        """Verify the active model, Kubernetes targets and exact serving image configuration."""
        revision, values, helm_status = self.helm_state()
        url = named(values['env'], 'IOT_KSERVE_URL')['value']
        route = re.fullmatch(PREDICTOR, url)
        if helm_status != 'deployed' or not route:
            raise ValueError('Expected one deployed feature release with a private Jenkins predictor')
        isvc_name, release = route.groups()
        plan, stage, build, hashes = selected_release(self.release_path / release, release)
        pin = plan['pin']
        if (pin['protocol_sha256'] != self.seal['files'][PROTOCOL]
                or isvc_name != plan['isvc']['metadata']['name'] or url != plan['predictor_url']
                or any(named(values['env'], key) != named(plan['feature_values']['env'], key)
                       for key in ('IOT_SERVING_STATE', 'IOT_MODEL_PIN'))):
            raise ValueError('The deployed predictor or source protocol differs from the serving plan')
        isvc = self.get('inferenceservice', isvc_name, 'iot-serving')
        meta, status = isvc['metadata'], isvc['status']
        annotations = {'coursework.iot/model-portable-sha256': pin['portable_sha256'],
                       'coursework.iot/model-version': pin['version'], 'coursework.iot/source-commit': plan['commit']}
        if (meta['uid'] != stage['isvc_uid'] or meta.get('labels', {}).get('iot.coursework/release') != release
                or not any(row['type'] == 'Ready' and row['status'] == 'True' for row in status['conditions'])
                or any(meta['annotations'].get(k) != v for k, v in annotations.items())):
            raise ValueError('Live KServe identity differs from the staged CI model')
        predictor_image = named(isvc['spec']['predictor']['containers'], 'kserve-container')['image']
        predictor_deployment = self.get('deployment', isvc_name + '-predictor', 'iot-serving')
        verify_predictor_rollout(isvc, predictor_deployment, predictor_image)
        predictor = self.service(isvc_name + '-predictor', 'iot-serving')
        metadata = self.http(predictor, '/v1/models/' + pin['name'])
        if any(metadata.get(key) != value or type(metadata[key]) is not type(value) for key, value in pin.items()):
            raise ValueError('Live predictor does not identify the selected production model')
        predictor_digest = self.observed_image(predictor_image, 'predictor', plan, build, hashes)
        self.verify_pods('iot-serving', 'serving.kserve.io/inferenceservice=' + isvc_name, 'kserve-container',
                         predictor_image, predictor_digest, ready=True)
        deployment = self.get('deployment', FEATURE)
        template = deployment['spec']['template']
        api = named(template['spec']['containers'], 'api')
        image = values['image']['repository'] + ':' + values['image']['tag']
        if (api['image'] != image or api['env'] != values['env']
                or api['imagePullPolicy'] != 'Never' or values['image']['pullPolicy'] != 'Never'
                or named(template['spec']['initContainers'], 'copy-state')['image'] != image
                or named(values['initContainers'], 'copy-state')['image'] != image
                or named(template['spec']['volumes'], 'seed')['configMap']['name'] != named(values['volumes'], 'seed')['configMap']['name']
                or template['metadata']['annotations'].get('iot.coursework/revision-marker') != values['revisionMarker']):
            raise ValueError('Live feature workload differs from deployed Helm configuration')
        feature_digest = self.observed_image(image, 'feature', plan, build, hashes)
        self.verify_pods(NAMESPACE, 'app.kubernetes.io/instance=' + FEATURE, 'api', image, feature_digest, ready=False)
        return {'release': release, 'revision': revision, 'values': values, 'pin': pin, 'feature': self.service(FEATURE),
                'predictor': predictor, 'inferenceservice': meta['uid'], 'predictor_generation': meta['generation'],
                'deployment': deployment['metadata']['uid'], 'predictor_digest': predictor_digest, 'feature_digest': feature_digest}

    def redis_info(self, name):
        """Read the isolated Redis service and deployment before using its address."""
        service = self.service(name)
        deployment = self.get('deployment', name)
        if (service['selector'] != {'app': name} or deployment['spec']['selector']['matchLabels'] != {'app': name}
                or deployment['spec']['template']['metadata']['labels']['app'] != name
                or deployment['metadata']['annotations'].get('iot.coursework/feature-refresh') != self.target.name):
            raise ValueError('Redis stage does not select this refresh workload')
        return {**service, 'deployment_uid': deployment['metadata']['uid']}

    def redis_probe(self, redis, filename):
        """Observe Redis identity, keys and TTLs without changing values or expiry."""
        value = self.json(['docker', 'run', '--rm', '--network', 'host', '--cpus', '.5', '--memory', '256m',
                           '--entrypoint', 'python', self.sdk, '-c', REDIS_PROBE, redis['ip']])
        write(self.output / filename, value)
        return value


    def materialize(self, redis, run, cutoff):
        """Use the existing SDK and source with only the candidate state writable."""
        name = 'iot-' + run
        command = ['docker', 'run', '--name', name, '--network', 'host', '--memory', '8g', '--memory-swap', '8g',
                   '--cpus', '1', '--entrypoint', 'python', '--env', 'PYTHONPATH=/src',
                   '--env', 'IOT_FEAST_REDIS=' + redis['ip'] + ':6379', '--env', 'FEAST_USAGE=False',
                   '--env', 'OMP_NUM_THREADS=1', '--env', 'OPENBLAS_NUM_THREADS=1', '--env', 'PYTHONDONTWRITEBYTECODE=1',
                   '--mount', f'type=bind,src={self.source},dst=/src,readonly',
                   '--mount', f'type=bind,src={self.root},dst={self.root},readonly',
                   '--mount', f'type=bind,src={self.target},dst={self.target}', self.sdk, '-m', 'feature_store.serving',
                   '--state', str(self.state), '--source-state', str(self.root / 'data/feast/batch-medium-27'),
                   '--batch-report', str(self.root / 'outputs/feast-batch-27/batch/report.json'),
                   '--protocol', '/src/' + PROTOCOL, '--silver', str(self.root / 'data/lakehouse/large-ingest-medium-27-v4'),
                   '--input-binding', str(self.root / 'outputs/feature-medium-27-v2/input-binding.json'), '--cutoff', cutoff.isoformat()]
        write(self.output / 'materialization-command.json', command)
        self.command(command, log='materialization.log', timeout=1200)
        return self.materialization_receipt(name, redis)

    def materialization_receipt(self, container, redis):
        """Bind actual container provenance and wall clocks to the isolated Redis address."""
        obj = self.json(['docker', 'inspect', container])[0]
        state = obj['State']
        mounts = {item['Destination']: (item['Source'], item['RW']) for item in obj['Mounts']}
        if (state['Running'] or state['ExitCode'] != 0 or state['OOMKilled'] or obj['Image'] != self.sdk
                or 'IOT_FEAST_REDIS=' + redis['ip'] + ':6379' not in obj['Config']['Env']
                or obj['Config']['Cmd'][:4] != ['-m', 'feature_store.serving', '--state', str(self.state)]
                or mounts != {'/src': (str(self.source), False), str(self.root): (str(self.root), False),
                              str(self.target): (str(self.target), True)}):
            raise ValueError('Fresh SDK materialization did not complete against candidate Redis')
        return {'id': obj['Id'], 'image': obj['Image'], 'started': state['StartedAt'], 'finished': state['FinishedAt']}

    def seed_files(self):
        """Hash all seed files, including the registry used by runtime reads."""
        files = {name: self.state / path for name, path in SEED_FILES.items()}
        if sum(path.stat().st_size for path in files.values()) > 700_000:
            raise ValueError('Feature seed exceeds the ConfigMap budget')
        return files, {name: digest(path) for name, path in files.items()}

    def inspect_seed(self, seed, hashes, run):
        """Verify the exact immutable Kubernetes bytes and object identity."""
        obj = self.get('configmap', seed, optional=True)
        if obj is None:
            return None
        data, binary = obj.get('data', {}), obj.get('binaryData', {})
        if (obj.get('immutable') is not True or obj['metadata'].get('annotations', {}).get('iot.coursework/feature-refresh') != run
                or set(data) & set(binary) or set(data) | set(binary) != set(hashes)):
            raise ValueError('An incompatible seed ConfigMap occupies this refresh name')
        for name, pin in hashes.items():
            raw = data[name].encode() if name in data else base64.b64decode(binary[name], validate=True)
            if sha256(raw).hexdigest() != pin:
                raise ValueError('Native seed bytes differ: ' + name)
        return obj['metadata']['uid']

    def stage_redis(self, baseline, online, seed, plan, run):
        """Create only new Redis resources with selectors separate from the old cache."""
        if any(self.get(kind, name, optional=True) is not None
               for kind, name in [('deployment', online), ('service', online), ('configmap', seed)]):
            raise ValueError('A pre-existing Redis or seed occupies this refresh name')
        manifests = deepcopy(plan['redis'])
        if (len(manifests) != 2 or {obj['kind'] for obj in manifests} != {'Deployment', 'Service'}
                or any(obj['metadata']['namespace'] != NAMESPACE
                       or obj['metadata']['name'] != 'online-' + baseline['release'] for obj in manifests)):
            raise ValueError('Serving plan must supply the dedicated Redis Deployment and Service')
        spec = next(obj['spec'] for obj in manifests if obj['kind'] == 'Service')
        if spec.get('type') != 'ClusterIP' or spec.get('externalIPs') or spec.get('externalName'):
            raise ValueError('Staging Redis must remain an internal ClusterIP Service')
        old_cache = named(baseline['values']['env'], 'IOT_FEAST_REDIS')['value']
        old_name = old_cache.removesuffix('.iot-features.svc.cluster.local:6379')
        if not re.fullmatch(r'online-' + RELEASE + r'(?:-r-[0-9a-f]{12})?', old_name) or redis_address(old_name) != old_cache:
            raise ValueError('The active cache does not use a release-scoped private service')
        previous = self.service(old_name)
        self.target.mkdir(parents=True, exist_ok=False)
        os.chown(self.target, 10001, 10001)
        for obj in manifests:
            obj['metadata']['name'] = online
            obj['metadata'].setdefault('annotations', {})['iot.coursework/feature-refresh'] = run
            if obj['kind'] == 'Deployment':
                obj['spec']['selector']['matchLabels'] = {'app': online}
                obj['spec']['template']['metadata']['labels']['app'] = online
            else:
                obj['spec']['selector'] = {'app': online}
        payload = {'apiVersion': 'v1', 'kind': 'List', 'items': manifests}
        write(self.output / 'redis-stage.json', payload)
        self.command(self.k + ['create', '-f', '-'], payload=json.dumps(payload).encode(), log='redis-create.log')
        self.command(self.k + ['rollout', 'status', 'deployment/' + online, '-n', NAMESPACE, '--timeout=120s'],
                     timeout=150, log='redis-rollout.log')
        redis = self.redis_info(online)
        if redis['ip'] == previous['ip']:
            raise ValueError('Candidate Redis must have a separate private ClusterIP')
        return redis

    def prepare(self, baseline, run, resume):
        """Stage Redis and materialize once, or reauthenticate that prepared state."""
        release = baseline['release']
        plan, _, _, _ = selected_release(self.release_path / release, release)
        online, seed = resource_names(release, run)
        protocol = read(self.source / PROTOCOL)
        count = protocol['source']['machine_count']
        cutoff = clock(protocol['chronological_split']['observation_end_exclusive']) - timedelta(minutes=10)
        receipt_path = self.target / 'refresh.json'
        if not resume:
            redis = self.stage_redis(baseline, online, seed, plan, run)
            before = self.redis_probe(redis, 'physical-ttl-before.json')
            if before['keys'] != 0 or before['dbsize'] != 0:
                raise ValueError('The new Redis instance already contains keys')
            container = self.materialize(redis, run, cutoff)
            write(self.output / 'materialization-container.json', container)
            os.chown(self.target, 0, 0)
        else:
            original = read(receipt_path)
            redis = self.redis_info(online)
            if redis != original['redis']:
                raise ValueError('The original staging Redis was replaced')
            container = self.materialization_receipt('iot-' + run, redis)

        files, hashes = self.seed_files()
        context, verification, ready = (read(files[name]) for name in ('context.json', 'verification.json', '_READY.json'))
        prepared = clock(context['prepared_wall_utc'])
        binding = self.root / 'outputs/feature-medium-27-v2/input-binding.json'
        compact = self.root / 'data/feast/batch-medium-27/machine_rolling.parquet'
        comparison = verification['online_comparison']
        if (context['clock_mode'] != 'simulation' or clock(context['as_of']) != cutoff
                or len(context['machines']) != count or context['protocol_sha256'] != baseline['pin']['protocol_sha256']
                or context['source_manifest_sha256'] != protocol['source']['manifest_sha256']
                or context['input_binding_sha256'] != digest(binding)
                or verification.get('passed') is not True or verification['project'] != 'iot_serving_demo'
                or verification['compact_sha256'] != digest(compact)
                or clock(verification['cutoff']) != cutoff or clock(comparison['as_of_simulated_utc']) != cutoff
                or comparison.get('passed') is not True or comparison['machines'] != count
                or verification['code_sha256'] != self.seal['files']['feature_store/serving.py']
                or verification['context_sha256'] != hashes['context.json']
                or ready != {'context_sha256': hashes['context.json'], 'verification_sha256': hashes['verification.json']}
                or not clock(container['started']) <= prepared <= clock(verification['completed_utc']) <= clock(container['finished']) <= now()):
            raise ValueError('Prepared seed, sealed source and native materialization clocks do not agree')
        after = self.redis_probe(redis, 'physical-ttl-after.json')
        if not after['all_expected_project']:
            raise ValueError('Unexpected keys in the candidate Redis project')
        expiry = check_lease(after, count, prepared, now())
        staged = {'run': run, 'release': release, 'pin': baseline['pin'], 'online': online, 'seed': seed, 'redis': redis,
                  'redis_run_id': after['run_id'], 'key_set_sha256': after['key_set_sha256'],
                  'container': container, 'count': count, 'hashes': hashes,
                  'runtime_manifest_sha256': digest(self.source.parent / 'manifest.json'),
                  'as_of': cutoff.isoformat(), 'prepared_wall_utc': prepared.isoformat(), 'lease_expires_utc': expiry.isoformat()}
        if resume:
            if {k: v for k, v in original.items() if k != 'seed_uid'} != staged:
                raise ValueError('Replay must preserve the original source, clocks, Redis and seed bytes')
        elif after['run_id'] != before['run_id']:
            raise ValueError('Redis restarted during preparation')
        else:
            write(receipt_path, staged)
        uid = self.inspect_seed(seed, hashes, run)
        expected_uid = original.get('seed_uid') if resume else None
        if (expected_uid and uid != expected_uid) or (not resume and uid is not None):
            raise ValueError('The staged ConfigMap name was concurrently occupied')
        if uid is None:
            args = self.k + ['create', 'configmap', seed, '-n', NAMESPACE, '--dry-run=client', '-o', 'json']
            obj = self.json(args + [f'--from-file={name}={path}' for name, path in files.items()])
            obj['immutable'] = True
            obj['metadata'].setdefault('labels', {})['iot.coursework/release'] = release
            obj['metadata'].setdefault('annotations', {})['iot.coursework/feature-refresh'] = run
            self.command(self.k + ['create', '-f', '-'], payload=json.dumps(obj).encode(), log='seed-create.log')
            uid = self.inspect_seed(seed, hashes, run)
        if not uid:
            raise ValueError('The original prepared seed ConfigMap is absent or replaced')
        staged['seed_uid'] = uid
        if not expected_uid:
            write(receipt_path, staged)
        write(self.output / 'staged.json', staged)
        return staged


    def recheck(self, staged, filename):
        """Recheck the native candidate around Helm without readjusting either clock."""
        redis = self.redis_info(staged['online'])
        if redis != staged['redis']:
            raise ValueError('Candidate Redis Service or Deployment was replaced')
        probe = self.redis_probe(redis, filename)
        wall_expiry = check_lease(probe, staged['count'], clock(staged['prepared_wall_utc']), now())
        seed_uid = self.inspect_seed(staged['seed'], staged['hashes'], staged['run'])
        if (probe['run_id'] != staged['redis_run_id'] or probe['key_set_sha256'] != staged['key_set_sha256']
                or not probe['all_expected_project'] or seed_uid != staged['seed_uid']):
            raise ValueError('Candidate Redis or seed identity changed')
        physical_expiry = clock(probe['redis_utc']) + timedelta(milliseconds=min(probe['pttl_ms']))
        return min(wall_expiry, physical_expiry)

    def validate(self, baseline, values, staged):
        """Reproduce the installed chart and validate the planned Helm upgrade."""
        if verify_source(self.source) != self.seal:
            raise ValueError('Runtime source changed during feature preparation')
        before, after = (self.output / name for name in ('previous-values.json', 'values.json'))
        write(before, baseline['values'])
        write(after, values)
        write(self.output / 'previous.json', baseline)
        chart = str(self.source / CHART)
        self.command(self.helm + ['lint', chart, '-f', str(after)], log='helm-lint.log')
        rendered = self.command(self.helm + ['template', FEATURE, chart, '-n', NAMESPACE, '--is-upgrade', '-f', str(before)])
        installed = self.command(self.helm + ['get', 'manifest', FEATURE, '-n', NAMESPACE, '--revision', str(baseline['revision'])])
        if rendered.strip() != installed.strip():
            raise ValueError('The sealed chart does not reproduce the installed Helm release')
        self.command(self.helm + ['upgrade', FEATURE, chart, '-n', NAMESPACE, '-f', str(after), '--dry-run=server'],
                     log='helm-dry-run.log')
        self.recheck(staged, 'physical-ttl-before-switch.json')

    def upgrade(self, values):
        """Roll the existing API while preserving the Service and predictor route."""
        if read(self.output / 'values.json') != values:
            raise ValueError('The preflight Helm values changed')
        self.command(self.helm + ['upgrade', FEATURE, str(self.source / CHART), '-n', NAMESPACE,
                                  '-f', str(self.output / 'values.json'), '--atomic', '--wait', '--timeout', '240s'],
                     timeout=600, log='helm.stdout')

    def verify(self, baseline, values, staged):
        """Verify the new Helm revision, actual Pods, feature clocks and a pinned prediction."""
        current = self.active()
        if current != {**baseline, 'values': values, 'revision': baseline['revision'] + 1}:
            raise ValueError('The active release identity or rollout revision changed unexpectedly')
        self.verify_pods(NAMESPACE, 'app.kubernetes.io/instance=' + FEATURE, 'api',
                         values['image']['repository'] + ':' + values['image']['tag'], baseline['feature_digest'],
                         ready=True, values=values)
        health = self.http(current['feature'], '/health/ready')
        context = read(self.state / 'context.json')
        machine = next(name for name, fields in context['machines'].items() if fields['status'] == 'operating')
        response = self.http(current['feature'], '/v1/machines/' + machine + ':predict', {})
        trace = response['feature_trace']
        if (health.get('ready') is not True or health.get('clock_mode') != 'simulation'
                or clock(health['as_of']) != clock(staged['as_of']) or trace['machine_id'] != machine
                or trace['clock_mode'] != 'simulation' or clock(trace['as_of']) != clock(staged['as_of'])
                or clock(trace['prepared_wall_utc']) != clock(staged['prepared_wall_utc'])
                or any(response['model'].get(k) != v for k, v in staged['pin'].items())):
            raise ValueError('Feature readiness, clocks or model provenance differ')
        expiry = self.recheck(staged, 'physical-ttl-after-switch.json')
        return {**staged, 'ready': health, 'revision': current['revision'], 'feature_service': baseline['feature'],
                'wall_lease_expires_utc': staged['lease_expires_utc'], 'lease_expires_utc': expiry.isoformat(),
                'feature_image_digest': baseline['feature_digest'], 'predictor_image_digest': baseline['predictor_digest'],
                'predictor_service': baseline['predictor']}

    def rollback(self, baseline, values):
        """Restore only our failed rollout and confirm which exact revision is active."""
        result = {'previous_revision': baseline['revision'], 'requested': False, 'restored': False}
        try:
            revision, live, status = self.helm_state()
            result['revision_before_rollback'] = revision
            restore_values = baseline['values']
            if live == values:
                restore_revision = baseline['revision']
                if revision != baseline['revision'] + 1:
                    history = self.json(self.helm + ['history', FEATURE, '-n', NAMESPACE, '-o', 'json'])
                    restore_revision = max(int(row['revision']) for row in history
                                           if int(row['revision']) < revision and row['status'] in {'deployed', 'superseded'})
                    restore_values = self.json(self.helm + ['get', 'values', FEATURE, '-n', NAMESPACE, '-o', 'json',
                                                            '--all', '--revision', str(restore_revision)])
                if self.helm_state() != (revision, live, status):
                    raise ValueError('A different Helm operation took ownership before rollback')
                result['requested'] = True
                result['restore_revision'] = restore_revision
                self.command(self.helm + ['rollback', FEATURE, str(restore_revision), '-n', NAMESPACE,
                                          '--wait', '--timeout', '240s'], timeout=300, log='helm-rollback.log')
                revision, live, status = self.helm_state()
            result.update(restored=live == restore_values and status == 'deployed',
                          revision_after_rollback=revision)
            if not result['restored'] or self.service(FEATURE) != baseline['feature']:
                raise ValueError('Could not verify the previous feature routing. A concurrent change is left intact')
        finally:
            write(self.output / 'rollback.json', result)


def run(args):
    """Admit the host session and serialize local feature refresh attempts."""
    if os.name != 'posix' or os.geteuid() != 0:
        raise ValueError('Run inside the existing Linux lab VM with sudo')
    import fcntl

    root, source, output = (path.resolve() for path in (args.runtime_root, args.runtime_source, args.output))
    if (not root.is_relative_to('/srv/iot-data') or not source.is_relative_to(root / 'data')
            or not output.is_relative_to(root / 'outputs') or not re.fullmatch(r'demo-[a-z0-9-]{1,40}', args.run)):
        raise ValueError('Use the existing runtime paths and a demo-* run identifier')
    target = (root / 'data/demo' / args.run).resolve()
    if target != root / 'data/demo' / args.run or target.exists() != args.resume_deploy:
        raise ValueError('Use a new run directory or explicitly resume an existing prepared run')
    session = read(root / 'data/runtime/ci/session.json')
    if (clock(session['auto_stop_utc']) - now()).total_seconds() < 2400:
        raise ValueError('At least 40 minutes in the approved session are required')
    if not all(Path(p).is_mount() for p in ['/srv/iot-data', '/srv/iot-fast']):
        raise ValueError('Keep the two existing data mounts')
    ram = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if ram < 16 * 1024**3 or shutil.disk_usage(root).free < 20_000_000_000:
        raise ValueError('Require 16 GiB RAM available and 20 GB free on the data disk')
    seal = verify_source(source)
    os.environ['KUBECONFIG'] = '/etc/rancher/k3s/k3s.yaml'
    with (root / 'data/runtime/ci/feature-refresh.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir(parents=True, exist_ok=False)
        report = {'passed': False, 'source_sha256': digest(Path(__file__)), 'runtime_source': seal,
                  'run': args.run, 'resume_deploy': args.resume_deploy}
        try:
            lab = Lab(root, source, target, output, seal)
            pods = lab.get('pods', None, 'kubeflow')['items']
            if any(pod['status']['phase'] in {'Running', 'Pending'} and 'pipeline/runid' in pod['metadata'].get('labels', {}) for pod in pods):
                raise ValueError('Wait for the active training pipeline')
            result = cutover(lab, args.run, args.resume_deploy)
            report.update(result, passed=True, model_version=result['pin']['version'], checked_utc=now().isoformat(),
                          machines=result['count'], sdk_image_id=lab.sdk)
        except Exception as error:
            report['error'] = f'{type(error).__name__}: {error}'
        write(output / 'verification.json', report)
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime-source', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume-deploy', action='store_true')
    raise SystemExit(run(parser.parse_args()))
