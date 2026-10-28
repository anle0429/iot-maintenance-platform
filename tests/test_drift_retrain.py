"""Check admission boundaries, truthful manual probes, durable ambiguity and concurrent replay without a real cluster."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import json, sqlite3, unittest
from ml.drift_retrain import FIELDS, digest, process


class DriftRetrainTests(unittest.TestCase):
    """Use independent decision fixtures and a counted training boundary, never perform cloud work in unit tests."""
    def setUp(self):
        """Create sealed native-shaped inputs with a known no-drift decision."""
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.directory=self.root/'outputs/offline-drift/fixture/result';self.directory.mkdir(parents=True)
        self.state=self.root/'state';self.now=datetime.now(timezone.utc);self.calls=[]
        self.recipe=self.root/'recipe.json';self.recipe.write_text('{}')
        self.policy={'format':'iot-drift-retrain-v1','enabled':True,'allow_manual_probe':True,
            'cooldown_seconds':3600,'max_report_age_seconds':600,'reference_id':'a'*64,
            'reference_sha256':'b'*64,'source_sha256':'c'*64,'training_files':{'recipe.json':digest(self.recipe)}}
        self.policy_path=self.root/'policy.json';self.write(self.policy_path,self.policy)
        self.report={'reference_id':'a'*64,'window_id':'known-window','start':'2026-11-22T00:00:00Z',
            'end':'2026-11-22T01:00:00Z','unique_rows':32,'features':{n:{'psi':.1,'drifted':False} for n in FIELDS},
            'policy':{'bins':10,'psi_threshold':.2,'drifted_feature_fraction':.25,'pseudocount':.5},
            'drifted_feature_fraction':0.,'drift_detected':False}
        self.write(self.directory/'retrieval.json',{'passed':True,'native_feast_retrieval':True,
            'all_eight_values_checked':True,'reference_sha256':'b'*64,'source_sha256':'c'*64,'rows':32})
        (self.directory/'metrics.prom').write_text('known_metric 1\n');self.seal()

    def write(self,path,value):
        """Serialize fixture bytes explicitly before calculating their input seal."""
        path.write_text(json.dumps(value,allow_nan=False),encoding='utf-8')

    def seal(self):
        """Bind the fixture completion to this exact report and metric group."""
        self.write(self.directory/'report.json',self.report)
        self.write(self.directory/'completion.json',{'passed':True,'push_status':200,
            'report_sha256':digest(self.directory/'report.json'),'metrics_sha256':digest(self.directory/'metrics.prom'),
            'reference_id':'a'*64,'completed_wall_utc':self.now.isoformat()})

    def train(self,run,attempt):
        """Count admissions and return an explicitly fake backend boundary for policy tests only."""
        self.calls.append(run)
        return {'passed':True,'production':False,'run_id':'unit-fixture-run','state':'SUCCEEDED','run_name':run}

    def call(self,probe=False,now=None,train=None):
        """Run the real controller policy with an injected cluster boundary."""
        return process(self.root,self.directory,self.policy_path,self.state,probe,train or self.train,now or self.now)

    def drift(self,count):
        """Set exact inclusive threshold scores for the requested number of feature flags."""
        for i,name in enumerate(FIELDS): self.report['features'][name]={'psi':.2 if i<count else .1,'drifted':i<count}
        self.report['drifted_feature_fraction']=count/8;self.report['drift_detected']=count>=2;self.seal()

    def test_no_drift_skips_without_an_intent_or_api_call(self):
        self.assertEqual(self.call()['reason'],'no_detected_drift');self.assertEqual(self.calls,[])
        self.assertFalse(self.state.exists())

    def test_two_of_eight_at_exact_threshold_trigger_and_one_does_not(self):
        self.drift(1);self.assertEqual(self.call()['action'],'skipped')
        self.drift(2);self.assertEqual(self.call()['reason'],'detected_drift');self.assertEqual(len(self.calls),1)

    def test_manual_probe_preserves_false_drift_and_completed_replay_after_expiry(self):
        first=self.call(True);second=self.call(True,now=self.now+timedelta(minutes=30))
        self.assertFalse(first['drift_detected']);self.assertTrue(first['manual_probe'])
        self.assertEqual(first['reason'],'explicit_manual_probe');self.assertEqual(second['action'],'reused_completed_submission')
        self.assertEqual(first['run_id'],second['run_id']);self.assertEqual(len(self.calls),1)

    def test_disabled_or_unapproved_manual_probe_never_calls_training(self):
        self.policy['allow_manual_probe']=False;self.write(self.policy_path,self.policy)
        with self.assertRaisesRegex(ValueError,'Manual probe disabled'):self.call(True)
        self.policy['enabled']=False;self.write(self.policy_path,self.policy);self.drift(8)
        self.assertEqual(self.call()['reason'],'policy_disabled');self.assertEqual(self.calls,[])

    def test_age_boundary_and_future_rejected_before_intent(self):
        for delta in [-1,601]:
            with self.subTest(delta=delta),self.assertRaisesRegex(ValueError,'Stale or future'):
                self.call(True,now=self.now+timedelta(seconds=delta))
        self.assertEqual(self.call(True,now=self.now+timedelta(seconds=600))['action'],'submitted_and_verified')

    def test_changed_recipe_or_report_is_rejected_before_training(self):
        self.recipe.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError,'recipe changed'):self.call(True)
        self.recipe.write_text('{}');self.report['drift_detected']=True;self.seal()
        with self.assertRaisesRegex(ValueError,'aggregate'):self.call(True)
        self.assertEqual(self.calls,[])

    def test_bad_completion_and_non_native_receipt_rejected(self):
        for key,value in [('passed',False),('push_status',500),('report_sha256','0'*64)]:
            self.seal();p=self.directory/'completion.json';v=json.loads(p.read_bytes());v[key]=value;self.write(p,v)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'completion'):self.call(True)
        self.assertEqual(self.calls,[])

    def test_unknown_fields_scores_or_policy_are_not_silent_no_drift(self):
        self.report['features'][FIELDS[0]]={'psi':-.1,'drifted':False};self.seal()
        with self.assertRaisesRegex(ValueError,'drift decision'):self.call(True)
        self.report['features'][FIELDS[0]]={'psi':.1,'drifted':False};self.policy['cooldown_seconds']=0;self.write(self.policy_path,self.policy)
        with self.assertRaisesRegex(ValueError,'cooldown'):self.call(True)

    def test_failed_or_ambiguous_call_blocks_retries_and_other_windows(self):
        def failed(run,attempt):
            self.calls.append(run);raise TimeoutError('Response lost after possible server acceptance')
        with self.assertRaises(TimeoutError):self.call(True,train=failed)
        for window in ['known-window','another-window']:
            self.report['window_id']=window;self.seal()
            with self.assertRaisesRegex(ValueError,'Unresolved training intent'):self.call(True)
        self.assertEqual(len(self.calls),1)

    def test_new_window_cooldown_and_replay_conflict(self):
        self.call(True);self.report['features'][FIELDS[0]]={'psi':.11,'drifted':False};self.seal()
        with self.assertRaisesRegex(ValueError,'Replay conflicts'):self.call(True)
        self.report['window_id']='later-window';self.seal()
        self.assertEqual(self.call(True)['reason'],'cooldown');self.assertEqual(len(self.calls),1)

    def test_two_concurrent_attempts_cannot_submit_twice(self):
        entered,release=Event(),Event()
        def held(run,attempt):
            entered.set();release.wait(5);return self.train(run,attempt)
        with ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.call,True,None,held);self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(sqlite3.OperationalError):self.call(True)
            finally:release.set()
            self.assertEqual(first.result()['action'],'submitted_and_verified')
        self.assertEqual(len(self.calls),1);self.assertEqual(self.call(True)['action'],'reused_completed_submission')


if __name__=='__main__':unittest.main()
