"""Exercise the actual CI A/B probe over three loopback servers, including concurrent readiness counter noise."""
from contextlib import ExitStack
from pathlib import Path
from hashlib import sha256
import importlib.util,json,os,threading
from ml import ab_ci
from ml.ab_native import ledger_state
from services.ab_api.app import create_app
from services.feature_api.runtime import FeastKServeBackend
from services.predictor.app import create_app as predictor_app
from tests.api.test_ab_api import fixture_runtime,ASOF
from tests.integration.test_ab_routing_http import listening

ROOT=Path(__file__).resolve().parents[2]


def test_ci_probe_with_actual_http_and_background_readiness(tmp_path):
    """Run800distinct trials plus40concurrent replays; no Kubernetes, native Feast or actual registry is involved."""
    spec=importlib.util.spec_from_file_location('ab_ci_http_driver',ROOT/'scripts/ci-serving.py');driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    runtime=fixture_runtime(tmp_path)
    offline={m:{'features':runtime.runtimes['baseline'].backend.fetch(m)['values'],'feature_event_ts':ASOF.isoformat()} for m in runtime.context['machines']}
    with ExitStack() as stack:
        urls={'baseline':stack.enter_context(listening(predictor_app(ROOT/'models/pump-failure-v1'))),
            'candidate':stack.enter_context(listening(predictor_app(tmp_path/'candidate')))}
        for variant,feature in runtime.runtimes.items():
            feature.backend.endpoint=urls[variant];feature.backend.predict=FeastKServeBackend.predict.__get__(feature.backend)
        url=stack.enter_context(listening(create_app(runtime)));stop=threading.Event();background=[]
        def readiness():
            """Represent ordinary Kubernetes dependency probes without touching the experiment ledger."""
            while not stop.is_set():
                code,_=driver.http(url,'/health/ready');background.append(code)
                if stop.wait(.05):break
        thread=threading.Thread(target=readiness,daemon=True);thread.start()
        try:
            result=ab_ci.probe(lambda path,body=None,headers=None:driver.http(url,path,body,headers),
                lambda path:driver.http_text(url,path),lambda variant:driver.http_text(urls[variant],'/metrics'),
                runtime.context,runtime.experiment,runtime.pins,runtime.detector.reference_id,offline)
        finally:stop.set();thread.join(10);assert not thread.is_alive()
        assert result['cohort_counts']=={'baseline':372,'candidate':428} and len(result['readback']['observations'])==800
        assert result['replay_checks']==40 and sum(result['predictor_counter_deltas'].values())>800
        assert background and all(code==200 for code in background)
        assert result['readback']['drift']['baseline']['unique_rows']==93 and result['readback']['drift']['candidate']['unique_rows']==107
        database=Path(runtime.database);before=database.read_bytes();retained=ledger_state(database,result['readback'],runtime.context)
        assert retained['rows']==800 and database.read_bytes()==before
        destination=os.environ.get('IOT_AB_CI_PROOF_DIR')
        if destination:
            out=Path(destination);out.mkdir(parents=True,exist_ok=False)
            (out/'probe-result.json').write_bytes(ab_ci.canonical(result)+b'\n')
            summary={'passed':True,'http_predictions':800,'concurrent_replays':40,'unique_snapshots':200,'cohort_counts':result['cohort_counts'],
                'predictor_counter_deltas':result['predictor_counter_deltas'],'background_readiness_calls':len(background),
                'registry_is_fixture':True,'online_features_are_fixture':True,'offline_oracle_is_fixture':True,
                'same_legacy_weights':True,'kubernetes_contacted':False,'native_deployed':False,
                'retained_local_sqlite':retained,'readonly_ledger_check_preserved_bytes':True,
                'scope':'Actual CI probe/driver HTTP transport across three PC loopback ASGI servers with background readiness activity. No K8s deploy, native Feast, registry promotion, two trained models or Grafana claim.'}
            (out/'verification.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
            files={p.name:{'sha256':sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in out.iterdir()}
            (out/'manifest.json').write_text(json.dumps({'files':files,'scope':summary['scope']},indent=2)+'\n',encoding='utf-8')
