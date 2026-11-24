"""Exercise the feature refresh cutover without contacting the retained environment."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('refresh_agent_features', ROOT / 'scripts/refresh-agent-features.py')
REFRESH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFRESH)


class PredictorRollout(unittest.TestCase):
    """Require the ready Deployment owned by the active KServe InferenceService."""

    def test_standard_kserve_uses_the_child_generation(self):
        """A different parent generation can coexist with a reconciled predictor."""
        isvc = {'metadata': {'name': 'model', 'uid': 'isvc', 'generation': 5},
                'status': {'deploymentMode': 'Standard', 'observedGeneration': 9}}
        deployment = {'metadata': {'name': 'model-predictor', 'generation': 9, 'ownerReferences': [
            {'kind': 'InferenceService', 'name': 'model', 'uid': 'isvc', 'controller': True}]},
            'status': {'observedGeneration': 9, 'readyReplicas': 1},
            'spec': {'template': {'spec': {'containers': [{'name': 'kserve-container', 'image': 'known'}]}}}}
        REFRESH.verify_predictor_rollout(isvc, deployment, 'known')
        for status in ({'observedGeneration': 8, 'readyReplicas': 1},
                       {'observedGeneration': 9, 'readyReplicas': 0}):
            with self.subTest(status=status), self.assertRaises(ValueError):
                REFRESH.verify_predictor_rollout(isvc, dict(deployment, status=status), 'known')
        with self.assertRaises(ValueError):
            REFRESH.verify_predictor_rollout(dict(isvc, status={'deploymentMode': 'Standard', 'observedGeneration': 8}),
                                             deployment, 'known')
        with self.assertRaises(ValueError):
            REFRESH.verify_predictor_rollout(isvc, deployment, 'other-image')
        deployment['metadata']['ownerReferences'][0]['uid'] = 'other-owner'
        with self.assertRaises(ValueError):
            REFRESH.verify_predictor_rollout(isvc, deployment, 'known')


class Lab:
    """Represent the external Helm/Redis effects for the refresh coordinator."""

    def __init__(self):
        self.old = {'revision': 12, 'values': {'revisionMarker': 'observe-jenkins-91',
            'env': [{'name': 'IOT_KSERVE_URL', 'value': 'http://iot-pump-serve-jenkins-97-predictor.iot-serving.svc.cluster.local'},
                    {'name': 'IOT_FEAST_REDIS', 'value': 'online-serve-jenkins-97.iot-features.svc.cluster.local:6379'},
                    {'name': 'IOT_TRACE_ENDPOINT', 'value': 'http://retained-telemetry'}],
            'volumes': [{'name': 'seed', 'configMap': {'name': 'existing-seed'}}],
            'image': {'repository': 'iot-coursework-feature-api', 'tag': 'observe-jenkins-91'}},
            'feature': {'ip': '10.43.10.8', 'uid': 'original-service'},
            'predictor': {'ip': '10.43.10.7', 'uid': 'existing-predictor'}, 'pin': {'version': '22'}}
        self.current = deepcopy(self.old)
        self.redis = {'online-serve-jenkins-97': 200}
        self.staged = None
        self.switches = []
        self.reject_prepare = False
        self.reject_verify = False
        self.concurrent = False

    def active(self):
        """Read the current release independently of the staged Redis."""
        return deepcopy(self.current)

    def prepare(self, baseline, run, resume):
        """Stage new keys, leaving all previous keys available during materialization."""
        if self.reject_prepare:
            raise ValueError('Native preparation failed')
        if resume:
            return self.staged
        online, seed = REFRESH.resource_names('serve-jenkins-97', run)
        self.redis[online] = 200
        self.staged = {'online': online, 'seed': seed}
        if self.concurrent:
            self.current['revision'] += 1
        return self.staged

    def validate(self, baseline, values, staged):
        """Reject any trial rollout until all candidate keys are present."""
        if self.redis[staged['online']] != 200:
            raise ValueError('Missing keys')

    def upgrade(self, values):
        """Replace only the live Helm values."""
        self.switches.append('upgrade')
        self.current['values'] = values
        self.current['revision'] += 1

    def verify(self, baseline, values, staged):
        """Return the application probe only when the live service passes."""
        if self.reject_verify:
            raise ValueError('HTTP verification failed')
        return {'ready': True}

    def rollback(self, baseline, values):
        """Restore the preceding values only if this attempt still owns the rollout."""
        if self.current['values'] == values:
            self.switches.append('rollback')
            self.current = deepcopy(baseline)


class FeatureRefreshTests(unittest.TestCase):
    """Cover release-generic behavior, failure isolation and clock agreement."""

    def test_refresh_while_original_keys_are_live(self):
        """Switch only the feature cache and seed for a later serving model and telemetry build."""
        lab = Lab()
        self.assertEqual(REFRESH.cutover(lab, 'demo-next', False), {'ready': True})
        self.assertEqual(lab.switches, ['upgrade'])
        self.assertEqual(lab.redis['online-serve-jenkins-97'], 200)
        new = lab.current['values']
        self.assertEqual(new['image'], lab.old['values']['image'])
        self.assertEqual(new['env'][0], lab.old['values']['env'][0])
        self.assertEqual(new['env'][2], lab.old['values']['env'][2])
        self.assertTrue(new['env'][1]['value'].startswith('online-serve-jenkins-97-r-'))
        self.assertTrue(new['volumes'][0]['configMap']['name'].startswith('iot-feature-state-serve-jenkins-97-r-'))

    def test_preparation_failure_and_revision_conflict_do_not_switch(self):
        """Keep active values in place when materialization fails or another release intervenes."""
        for failure in ['reject_prepare', 'concurrent']:
            with self.subTest(failure=failure):
                lab = Lab()
                setattr(lab, failure, True)
                with self.assertRaises(ValueError):
                    REFRESH.cutover(lab, 'demo-next', False)
                self.assertEqual(lab.switches, [])
                self.assertEqual(lab.current['values'], lab.old['values'])

    def test_failed_post_switch_probe_restores_previous_values_and_prepared_retry(self):
        """Use the same isolated candidate after restoring the previous routing."""
        lab = Lab()
        lab.reject_verify = True
        with self.assertRaisesRegex(ValueError, 'HTTP verification failed'):
            REFRESH.cutover(lab, 'demo-next', False)
        self.assertEqual(lab.switches, ['upgrade', 'rollback'])
        self.assertEqual(lab.current['values'], lab.old['values'])
        staged = lab.staged
        lab.reject_verify = False
        REFRESH.cutover(lab, 'demo-next', True)
        self.assertIs(lab.staged, staged)

    def test_physical_expiry_must_agree_with_wall_lease(self):
        """Reject partial, expired, future-clock and unexpected renewed physical state."""
        prepared = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
        checked = prepared + timedelta(minutes=15)
        probe = {'keys': 2, 'dbsize': 2, 'pttl_ms': [2_690_000, 2_691_000],
                 'redis_utc': checked.isoformat(), 'run_id': 'a' * 40}
        self.assertEqual(REFRESH.check_lease(probe, 2, prepared, checked), prepared + timedelta(hours=1))
        for changes in [{'keys': 1}, {'dbsize': 3}, {'pttl_ms': [2_690_000, -1]},
                        {'pttl_ms': [3_590_000] * 2}, {'redis_utc': (checked + timedelta(minutes=2)).isoformat()}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                REFRESH.check_lease({**probe, **changes}, 2, prepared, checked)

    def test_selection_binds_later_production_bundle_and_plan(self):
        """Use the selected build and reject changed source even with a correct model version."""
        with TemporaryDirectory() as path:
            root = Path(path)
            bundle, outputs = root / 'bundle', root / 'outputs'
            bundle.mkdir()
            outputs.mkdir()
            release, commit = 'serve-jenkins-97', 'a' * 40
            protocol = 'b' * 64
            portable = {'threshold': .67, 'protocol_sha256': protocol}
            (bundle / 'portable.json').write_text(json.dumps(portable))
            digest = lambda file: sha256(file.read_bytes()).hexdigest()
            pin = {'name': 'iot-pump-failure-v1', 'production': True, 'version': '22', 'run_id': 'c' * 32,
                   'threshold': .67, 'protocol_sha256': protocol, 'training_code_commit': 'd' * 40,
                   'data_version': 'e' * 64, 'portable_sha256': digest(bundle / 'portable.json')}
            lineage = {key: value for key, value in pin.items() if key not in {'name', 'threshold'}}
            lineage['model_name'] = pin['name']
            (bundle / 'lineage.json').write_text(json.dumps(lineage))
            (bundle / 'model-pin.json').write_text(json.dumps(pin))
            values = {'revisionMarker': release}
            hashes = {item.name: digest(item) for item in bundle.iterdir()}
            identity = {'release': release, 'commit': commit}
            plan = {**identity, 'pin': pin, 'feature_values': values}
            files = {'plan': plan, 'selection': {**identity, 'pin': pin, 'bundle_sha256': hashes},
                     'build': {**identity, 'pin': pin}, 'feature-values': values}
            for name, body in files.items():
                (outputs / (name + '.json')).write_text(json.dumps(body))
            stage = {**identity, 'passed': True, 'model_pin': pin, 'plan_sha256': digest(outputs / 'plan.json'),
                     'values_sha256': digest(outputs / 'feature-values.json')}
            (outputs / 'staged.json').write_text(json.dumps(stage))
            self.assertEqual(REFRESH.selected_release(root, release)[0]['pin']['version'], '22')
            (bundle / 'portable.json').write_text(json.dumps({**portable, 'threshold': .68}))
            with self.assertRaises(ValueError):
                REFRESH.selected_release(root, release)

    def test_telemetry_predictor_requires_installed_digest_and_selected_bytes(self):
        """Accept an attested overlay and reject differences in its live image digest."""
        with TemporaryDirectory() as path:
            root = Path(path)
            tag = 'observe-jenkins-91'
            image = 'iot-coursework-predictor:' + tag
            native_digest = 'sha256:' + 'd' * 64
            receipt = root / tag / 'outputs'
            receipt.mkdir(parents=True)
            pin = {'version': '22'}
            files = {'portable.json': 'a' * 64, 'lineage.json': 'b' * 64}
            build = {'release': tag, 'commit': 'c' * 40, 'images': {
                'predictor': {'tag': image, 'config_digest': native_digest, 'packaged_sha256': files}}}
            deployed = {'release': tag, 'commit': build['commit'], 'passed': True, 'model_pin': pin}
            for name, payload in [('build', build), ('deployed', deployed)]:
                (receipt / (name + '.json')).write_text(json.dumps(payload))
            lab = SimpleNamespace(release_path=root, json=lambda args: {'status': {'id': native_digest}})
            plan = {'pin': pin, 'predictor_image': 'iot-coursework-predictor:serve-jenkins-97'}
            baseline_build = {'images': {'predictor': {}}}
            self.assertEqual(REFRESH.Lab.observed_image(lab, image, 'predictor', plan, baseline_build, files), native_digest)
            lab.json = lambda args: {'status': {'id': 'sha256:' + 'e' * 64}}
            with self.assertRaises(ValueError):
                REFRESH.Lab.observed_image(lab, image, 'predictor', plan, baseline_build, files)

    def test_occupied_candidate_never_executes_create(self):
        """Reject a live resource at the deterministic staging name before any create command."""
        lab = SimpleNamespace(get=lambda kind, name, **kwargs: {'metadata': {'uid': 'existing'}})
        with self.assertRaisesRegex(ValueError, 'pre-existing Redis or seed'):
            REFRESH.Lab.stage_redis(lab, {}, 'online-candidate', 'seed-candidate', {}, 'demo-next')

    def test_effective_expiry_is_bounded_by_first_physical_key(self):
        """Keep the effective serving lease within both clocks and reject a substituted Redis key set."""
        prepared = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
        checked = prepared + timedelta(minutes=15)
        redis = {'uid': 'redis-uid'}
        probe = {'keys': 2, 'dbsize': 2, 'pttl_ms': [2_690_000, 2_691_000], 'redis_utc': checked.isoformat(),
                 'run_id': 'a' * 40, 'key_set_sha256': 'b' * 64, 'all_expected_project': True}
        staged = {'online': 'online-candidate', 'redis': redis, 'count': 2, 'prepared_wall_utc': prepared.isoformat(),
                  'seed': 'seed-candidate', 'seed_uid': 'seed-uid', 'hashes': {}, 'run': 'demo-next',
                  'redis_run_id': probe['run_id'], 'key_set_sha256': probe['key_set_sha256']}
        lab = SimpleNamespace(redis_info=lambda name: redis, redis_probe=lambda *args: probe,
                              inspect_seed=lambda *args: 'seed-uid')
        with patch.object(REFRESH, 'now', return_value=checked):
            self.assertEqual(REFRESH.Lab.recheck(lab, staged, 'ttl.json'),
                             prepared + timedelta(minutes=59, seconds=50))
            probe['key_set_sha256'] = 'c' * 64
            with self.assertRaises(ValueError):
                REFRESH.Lab.recheck(lab, staged, 'ttl.json')
