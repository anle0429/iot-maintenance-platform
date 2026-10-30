"""Add document-embedding distance admission before the existing tested RAG Airflow publication pipeline."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import importlib.util
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from rag.drift_ci import require_distance_receipt
SPEC = importlib.util.spec_from_file_location('retained_rag_release', ROOT/'scripts/ci-rag.py')
BASE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(BASE)


class DriftContext(BASE.Context):
    """Retain source-bound CI behavior while requiring a version-distance decision before deployment."""
    driver = '/src/scripts/ci-rag-drift.py'

    def __init__(self):
        """Permit only production and an explicitly non-deployable foreign-corpus control."""
        super().__init__(); self.scenario = os.environ.get('IOT_RAG_DRIFT_SCENARIO', 'production')
        if self.scenario not in {'production', 'out-of-domain'}: raise ValueError('Unknown corpus admission scenario')

    def source_check(self):
        """Require all new policy, corpus and test dependencies in the authenticated source packet."""
        super().source_check(); declaration = 'infra/ci/rag-drift-source.json'
        if declaration not in self.seal['files']: raise ValueError('Missing corpus admission source declaration')
        names = json.loads((ROOT/declaration).read_bytes())['required']
        if not names or not set(names) <= set(self.seal['files']): raise ValueError('Incomplete distance gate dependencies')

    def distance_receipt(self):
        """Authenticate the actual gate artifact again immediately before launching deployment."""
        return require_distance_receipt(json.loads((self.out/'corpus-distance.json').read_bytes()), self.identity,
                                        self.source_digest, self.prior('build'), (self.out/'distance-report.json').read_bytes())

    def native(self, phase, extra=None):
        """Use the original bounded controller, routing deployment through the additional exact-image check."""
        if phase == 'deploy-host': self.distance_receipt(); phase = 'admitted-deploy-host'
        super().native(phase, ['--env', 'IOT_RAG_DRIFT_SCENARIO']+(extra or []))


def distance_host(c):
    """Run the built image offline with its own corpus, fixed reference and genuine cached CPU embeddings."""
    build = c.prior('build'); image = build['image_id']; name = c.release+'-corpus-distance'
    if BASE.image_files(image) != build['packaged_sha256']: raise ValueError('The built corpus image changed')
    command = ['docker', 'run', '--name', name, '--init', '--read-only', '--network', 'none', '--user', '10001:10001',
               '--memory', '4g', '--memory-swap', '4g', '--cpus', '2', '--pids-limit', '256', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,size=512m',
               '--env', 'HF_HOME=/models', '--env', 'HF_HUB_OFFLINE=1', '--env', 'TRANSFORMERS_OFFLINE=1',
               '--mount', 'type=bind,src=/srv/iot-fast/models/iot-rag-embeddings,dst=/models,readonly',
               '--mount', 'type=bind,src='+str(c.host_root/'infra/rag')+',dst=/policy,readonly',
               '--mount', 'type=bind,src='+str(c.out)+',dst=/result',
               '--entrypoint', '/usr/bin/timeout', image, '--signal=TERM', '--kill-after=10s', '600',
               'python', '-B', '-m', 'rag.corpus_drift', '--source', '/opt/iot-rag-release',
               '--reference', '/policy/corpus-reference.json', '--protocol', '/policy/corpus-drift-protocol.json',
               '--output', '/result/distance-report.json', '--scenario', c.scenario]
    try:
        c.log('corpus-distance', command, 660)
    finally:
        result = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
        if result.returncode == 0:
            value = json.loads(result.stdout)[0]
            BASE.SHARED.save(c.out/'corpus-distance-container.json', {'id': value['Id'], 'image': value['Image'],
                'state': value['State'], 'user': value['Config']['User'], 'mounts': value['Mounts'],
                'network_mode': value['HostConfig']['NetworkMode'], 'memory_bytes': value['HostConfig']['Memory'],
                'nano_cpus': value['HostConfig']['NanoCpus']})
            if not value['State']['Running']: subprocess.run(['docker', 'rm', name], check=True, stdout=subprocess.DEVNULL)
    if c.scenario != 'production': raise ValueError('A synthetic counterfactual may never authorize deployment')
    report = (c.out/'distance-report.json').read_bytes()
    receipt = {'schema_version': 1, 'phase': 'corpus-distance', 'passed': True, **c.identity,
               'source_digest': c.source_digest, 'image_id': image, 'scenario': c.scenario,
               'report_sha256': sha256(report).hexdigest(), 'checked_utc': datetime.now(timezone.utc).isoformat()}
    require_distance_receipt(receipt, c.identity, c.source_digest, build, report)
    BASE.SHARED.save(c.out/'corpus-distance.json', receipt)


def run(phase):
    """Keep existing test/build/deploy/verify phases, adding a gate and its immediate pre-publication recheck."""
    BASE.Context = DriftContext
    if phase not in {'corpus-distance', 'admitted-deploy-host'}: return BASE.run(phase)
    c = DriftContext(); BASE.SHARED.real_admission(c, 900)
    with (c.out/(phase+'.attempt')).open('x') as stream: stream.write(datetime.now(timezone.utc).isoformat())
    if phase == 'corpus-distance':
        c.log('distance-contracts', [sys.executable, '-B', '-m', 'unittest', 'tests.test_rag_corpus_drift', 'tests.test_rag_drift_ci', '-q'])
        distance_host(c)
    else:
        receipt = c.distance_receipt(); build = c.prior('build')
        if BASE.image_files(build['image_id']) != build['packaged_sha256']: raise ValueError('Admitted image bytes changed')
        BASE.SHARED.save(c.out/'corpus-distance-recheck.json', {'passed': True, **c.identity,
            'image_id': build['image_id'], 'report_sha256': receipt['report_sha256'], 'source_digest': c.source_digest})
        BASE.deploy_host(c)
    c.source_check()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['admission', 'test', 'test-host', 'build', 'corpus-distance', 'deploy',
                                         'deploy-host', 'admitted-deploy-host', 'verify', 'verify-host', 'archive'])
    run(parser.parse_args().phase)
