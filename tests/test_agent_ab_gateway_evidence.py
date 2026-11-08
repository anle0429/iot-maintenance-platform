"""Reject unsafe or rehashed contradictory A/B migration and gateway evidence."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import tarfile
import unittest

from infra.agentgateway.candidate import NAME, PORTS, bearer_hash, config, manifests
from tests.test_agentgateway_candidate import TOKENS
from scripts.git_sources import git_blobs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/verify-agent-ab-gateway.py'


def encode(value):
    """Keep deterministic fixture bytes without confusing them with captured native evidence."""
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def archive_fixture(files, extra=None):
    """Make a small explicitly synthetic complete tar inventory for archive boundary tests."""
    manifest = encode({'files': {name: {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
                                for name, raw in files.items()}})
    result = BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as archive:
        for name, raw in [*files.items(), ('manifest.json', manifest)]:
            entry = tarfile.TarInfo(name); entry.size = len(raw)
            archive.addfile(entry, BytesIO(raw))
        if extra is not None:
            entry, raw = extra
            archive.addfile(entry, BytesIO(raw))
    raw = result.getvalue()
    return raw, sha256(raw).hexdigest(), sha256(manifest).hexdigest()


def gateway_fixture():
    """Create two synthetic gateway readbacks with only the explicit opt-in route additions."""
    hashes = {role: bearer_hash(token) for role, token in TOKENS.items()}
    delegate = bearer_hash('fixture-delegate-' + 'd' * 32)
    objects = manifests(hashes, 'agw-fixture-evidence', delegation_hash=delegate)
    before = {}
    for index, resource in enumerate(objects):
        resource['metadata'].update(uid='fixture-uid-' + str(index), resourceVersion=str(10 + index), generation=1)
        before[resource['kind'].lower()] = resource
    after = deepcopy(before)
    after['configmap']['data']['config.yaml'] = json.dumps(config(hashes, tracing=True,
        delegation_hash=delegate, ab_operator_routes=True), indent=2)
    after['deployment']['spec']['template']['metadata']['annotations'] = {
        'iot.coursework/ab-gateway-source': '1e883f4c68ea27f973f9191f4e187e0438a9437c'}
    after['deployment']['metadata']['generation'] = 2
    after['deployment']['status'] = {'observedGeneration': 2, 'availableReplicas': 1, 'updatedReplicas': 1}
    files = {}
    for kind in before:
        files['native/gateway/' + kind + '-before.json'] = encode(before[kind])
        files['native/gateway/' + kind + '-after.json'] = encode(after[kind])
    return files


class ABGatewayEvidenceTests(unittest.TestCase):
    """Outer evidence checking must remain effective after an attacker recalculates a local summary hash."""

    def module(self):
        """Require the independent archive verifier before loading it without executing its CLI."""
        self.assertTrue(SCRIPT.is_file(), 'Independent A/B native capsule verifier is missing')
        spec = importlib.util.spec_from_file_location('verify_ab_gateway_evidence', SCRIPT)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_complete_archive_and_wrong_inventory_pins(self):
        """Every member must match both independent archive and original member-inventory hashes."""
        module = self.module(); files = {'native/a.json': encode({'value': 1})}
        raw, digest, inventory = archive_fixture(files)
        self.assertEqual(module.verify_archive(raw, digest, inventory), files)
        for archive_pin, manifest_pin in [('0' * 64, inventory), (digest, '0' * 64)]:
            with self.subTest(archive_pin=archive_pin), self.assertRaises(ValueError):
                module.verify_archive(raw, archive_pin, manifest_pin)

    def test_link_traversal_duplicates_and_unrecorded_members_fail(self):
        """A freshly rehashed archive cannot add links, escaped paths, duplicate names or unrecorded payloads."""
        module = self.module(); files = {'native/a.json': encode({'value': 1})}
        for name, kind, link in [('../escape', tarfile.REGTYPE, ''), ('native/a.json', tarfile.REGTYPE, ''),
                ('native/link', tarfile.SYMTYPE, 'native/a.json'), ('native/unrecorded', tarfile.REGTYPE, '')]:
            entry = tarfile.TarInfo(name); entry.type = kind; entry.linkname = link
            raw, digest, inventory = archive_fixture(files, (entry, b''))
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.verify_archive(raw, digest, inventory)

    def test_gateway_opt_in_preserves_old_routes_backend_and_boundary(self):
        """The native before/after pair must match both exact reviewed renderings on the existing four ports."""
        module = self.module(); files = gateway_fixture()
        result = module.verify_gateway(files)
        self.assertTrue(result['passed'])
        self.assertEqual(result['listener_ports'], sorted(PORTS.values()))
        for mode in ['old-route', 'wrong-credential', 'public-service', 'policy-change', 'replaced-uid']:
            changed = dict(files)
            if mode in ['old-route', 'wrong-credential']:
                name = 'native/gateway/configmap-after.json'; value = json.loads(changed[name])
                cfg = json.loads(value['data']['config.yaml'])
                routes = next(b['listeners'][0]['routes'] for b in cfg['binds'] if b['port'] == 18083)
                if mode == 'old-route':
                    next(r for r in routes if r['name'] == 'delegate-retrieval')['backends'] = [{'host': 'wrong:8083'}]
                else:
                    next(r for r in routes if r['name'] == 'a2a-ab-retrieval-a')['policies']['apiKey']['keys'] = [
                        {'keyHash': 'sha256:' + '0' * 64}]
                value['data']['config.yaml'] = json.dumps(cfg)
            else:
                name = 'native/gateway/service-after.json' if mode == 'public-service' else 'native/gateway/networkpolicy-after.json'
                value = json.loads(changed[name])
                if mode == 'public-service': value['spec']['type'] = 'LoadBalancer'
                elif mode == 'policy-change': value['spec']['ingress'].append({'from': [{}]})
                else: value['metadata']['uid'] = 'replacement'
            changed[name] = encode(value)
            with self.subTest(mode=mode), self.assertRaises(ValueError): module.verify_gateway(changed)

    def test_resealed_as_run_sources_must_still_match_both_immutable_git_commits(self):
        """Recomputing captured source hashes cannot substitute different code for either thirty-two-file packet."""
        module = self.module(); files = {}; seals = {}
        for packet, commit in [('source', module.SOURCE_COMMIT), ('source-study', module.STUDY_COMMIT)]:
            originals = git_blobs(ROOT, commit, sorted(module.SOURCE_FILES))
            seal = {'format': 'iot-agent-ab-gateway-source-v1', 'commit': commit,
                    'files': {name: sha256(raw).hexdigest() for name, raw in originals.items()}}
            files[packet + '/manifest.json'] = encode(seal); seals[packet] = seal
            files.update({packet + '/tree/' + name: raw for name, raw in originals.items()})
        for prefix, packet in [('native/study/', 'source'), ('native/migration-b/', 'source'),
                ('native/gateway/', 'source'), ('native/study-b/', 'source-study')]:
            files[prefix + 'source.json'] = encode(seals[packet])
        self.assertEqual(module.verify_source(files)['packets']['source-study']['immutable_git_files'], 32)
        changed = dict(files); name = 'agents/ab_gateway_acceptance.py'
        changed['source-study/tree/' + name] += b'\n# Unrecorded replacement\n'
        seal = deepcopy(seals['source-study'])
        seal['files'][name] = sha256(changed['source-study/tree/' + name]).hexdigest()
        changed['source-study/manifest.json'] = changed['native/study-b/source.json'] = encode(seal)
        with self.assertRaises(ValueError): module.verify_source(changed)
        changed = dict(files); changed['source/tree/unrecorded.py'] = b'not part of the packet'
        with self.assertRaises(ValueError): module.verify_source(changed)

    def test_real_stops_must_match_both_original_instances_and_eight_hour_sessions(self):
        """A stopped different VM, a renewed session or a still-running descriptor cannot close this captured grant."""
        module = self.module(); files = {}; stopped = []
        for role, name in [('cpu', 'iot-cw-vm'), ('gpu', 'iot-cw-gpu')]:
            value = {'name': name, 'id': 'fixture-' + role, 'status': 'RUNNING',
                'lastStartTimestamp': '2026-11-08T09:00:00Z', 'machineType': 'private/' + role,
                'scheduling': {'automaticRestart': False, 'instanceTerminationAction': 'STOP',
                               'maxRunDuration': {'seconds': '28800'}}}
            files['session/' + role + '-descriptor.json'] = encode(value)
            stopped.append({**value, 'status': 'TERMINATED', 'lastStopTimestamp': '2026-11-08T09:31:00Z'})
        for label in ['0-a', '0-b', '1-b', '1-a', '2-a', '2-b']:
            files['native/study-b/' + label + '/outcome.json'] = encode({'finished_utc': '2026-11-08T09:30:00Z'})
        self.assertEqual(module.verify_stops(stopped, files)['terminated_instances'], 2)
        for key, value in [('status', 'RUNNING'), ('id', 'another-instance'),
                ('lastStartTimestamp', '2026-11-08T10:00:00Z'),
                ('lastStopTimestamp', '2026-11-08T17:01:00Z'), ('lastStopTimestamp', '2026-11-08T09:29:00Z')]:
            changed = deepcopy(stopped); changed[0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): module.verify_stops(changed, files)


if __name__ == '__main__':
    unittest.main()
