"""Exercise independent release clocks, readiness generations and strict quality boundaries before native deployment."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from pathlib import Path
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock
import importlib.util,json,sys,tempfile,unittest,xml.etree.ElementTree as ET
from agents.release_ci import api_quality,feature_lease,original_api_cases,packaged_paths,ready_agents,role_for_job,required_inputs,capture_json_command,unchanged_quality_source
import shutil
import tarfile

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("agent_release_renderer",ROOT/"scripts/render-agent-responses.py")
renderer=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(renderer)


class AgentReleaseCiTests(unittest.TestCase):
    """Use fixed known clocks and recorded object shapes, leaving actual deployment proof to Jenkins."""
    def test_deployment_only_revision_cannot_waive_runtime_or_unknown_chart_changes(self):
        """Reuse unchanged mutation targets only with the exact independently measured deployment revision."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['mcp-quality', 'mcp-quality-native']:
                snapshot = Path('docs/evidence') / name / 'source'
                manifest = root / snapshot / 'manifest.json'
                manifest.parent.mkdir(parents=True); shutil.copyfile(ROOT / snapshot / 'manifest.json', manifest)
                for relative in json.loads(manifest.read_bytes())['files']:
                    target = root / relative; target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / snapshot / 'tree' / relative, target)
            declaration = Path('infra/mcp/autoscaling-source.json')
            shutil.copyfile(ROOT / declaration, root / declaration)
            with tarfile.open(ROOT / 'docs/evidence/mentor-runtime-20261107/mcp-source-packet.tar.gz') as packet:
                for relative in json.loads((root / declaration).read_bytes())['files']:
                    (root / relative).write_bytes(packet.extractfile('tree/' + relative).read())
            self.assertEqual(set(unchanged_quality_source(root)), {'mcp-quality', 'mcp-quality-native'})
            for name in ['services/mcp_tools/app.py', 'infra/charts/iot-mcp/templates/workload.yaml',
                         'infra/charts/iot-mcp/templates/request-scaling.yaml', 'infra/mcp/autoscaling-source.json']:
                path = root / name; original = path.read_bytes(); path.write_bytes(original + b'\n')
                with self.subTest(name=name), self.assertRaises(ValueError): unchanged_quality_source(root)
                path.write_bytes(original)

    def test_probe_json_and_observed_call_diagnostics_remain_separate(self):
        """Real processes can emit JSON on both streams; only successful stdout is the command result."""
        code="import json,sys;print(json.dumps({'passed':True}));print(json.dumps({'observed_call':'diagnostic'}),file=sys.stderr)"
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.assertEqual(capture_json_command([sys.executable,"-B","-c",code],root,"probe"),{"passed":True})
            echo=[sys.executable,"-B","-c","import sys;sys.stdout.write(sys.stdin.read())"]
            self.assertEqual(capture_json_command(echo,root,"corpus",input_bytes=b'["chunk"]'),["chunk"])
            self.assertEqual(json.loads((root/"probe.stderr.txt").read_bytes()),{"observed_call":"diagnostic"})
            with self.assertRaises(RuntimeError):capture_json_command([sys.executable,"-B","-c",code+";sys.exit(1)"],root,"failed")

    def test_transitive_test_import_must_be_in_the_sealed_packet(self):
        """An otherwise valid declaration cannot omit a module required by its imported tests."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/"infra/ci/agents-source.json";path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schema_version":1,"required":["rag/ci_metadata.py"],"quality_manifests":[]}))
            seal={"files":{"infra/ci/agents-source.json":"configuration"}}
            with self.assertRaises(ValueError):required_inputs(root,seal)
            seal["files"]["rag/ci_metadata.py"]="module"
            self.assertEqual(required_inputs(root,seal),2)

    def test_retrieval_probe_only_feeds_the_sealed_corpus(self):
        """Read back the independent host corpus and refuse mutable or mismatched source data."""
        driver_spec=importlib.util.spec_from_file_location("agent_ci_driver",ROOT/"scripts/ci-agents.py")
        driver=importlib.util.module_from_spec(driver_spec);driver_spec.loader.exec_module(driver)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);snapshot=root/'data/rag/snapshot';snapshot.mkdir(parents=True)
            chunks=snapshot/'chunks.json';chunks.write_bytes(b'[{"chunk_id":"test"}]')
            (root/'mcp-values.json').write_text(json.dumps({'snapshot':{'directory':str(snapshot),'sealConfigMap':'test-seal'}}))
            expected={'files':{'chunks.json':sha256(chunks.read_bytes()).hexdigest()}}
            seal={'immutable':True,'data':{'seal.json':json.dumps(expected)}}
            context=SimpleNamespace(out=root,deploy=root,get=Mock(return_value=seal))
            self.assertEqual(driver.retrieval_probe_chunks(context),chunks.read_bytes())
            context.get.assert_called_once_with('get','configmap','test-seal','-n','iot-agents')
            seal['immutable']=False
            with self.assertRaises(ValueError):driver.retrieval_probe_chunks(context)
            seal['immutable']=True;chunks.write_bytes(b'[]')
            with self.assertRaises(ValueError):driver.retrieval_probe_chunks(context)

    def test_only_three_owned_jobs_are_allowed(self):
        """A job name cannot select an arbitrary workload or share another component's release."""
        for role in ["retrieval","drift","coordinator"]:self.assertEqual(role_for_job("iot-llm-"+role),role)
        for name in ["iot-llm-rag","../retrieval","iot-llm-RETRIEVAL",None]:
            with self.subTest(name=name),self.assertRaises(ValueError):role_for_job(name)

    def test_real_lease_boundary_and_future_clock(self):
        """Exactly ten remaining minutes is valid; an older snapshot or future preparation is refused."""
        now=datetime(2026,10,29,18,tzinfo=timezone.utc)
        context={"prepared_wall_utc":(now-timedelta(minutes=50)).isoformat()}
        self.assertEqual(feature_lease(context,now)["remaining_seconds"],600)
        for prepared in [now-timedelta(minutes=50,microseconds=1),now+timedelta(seconds=1)]:
            with self.subTest(prepared=prepared),self.assertRaises(ValueError):feature_lease({"prepared_wall_utc":prepared.isoformat()},now)

    def test_stale_ready_flags_do_not_pass(self):
        """A green condition from an old generation cannot establish acceptance for a new release."""
        original=json.loads((ROOT/"docs/evidence/agent-platform/readback/agents.stdout").read_bytes())["items"]
        policy=(ROOT/"agents/response-policy.json").read_bytes();documents=renderer.render(original,policy)
        for doc in documents:
            doc["metadata"]["generation"]=5
            doc["status"]={"observedGeneration":5,"conditions":[{"type":name,"status":"True","observedGeneration":5} for name in ["Ready","Accepted"]]}
        self.assertTrue(ready_agents(documents,json.loads(policy)))
        for case in ["status","condition","false"]:
            changed=deepcopy(documents)
            if case=="status":changed[0]["status"]["observedGeneration"]=4
            elif case=="condition":changed[0]["status"]["conditions"][0]["observedGeneration"]=4
            else:changed[0]["status"]["conditions"][0]["status"]="False"
            with self.subTest(case=case),self.assertRaises(ValueError):ready_agents(changed,json.loads(policy))

    def test_quality_thresholds_are_strict_and_all_cases_must_run(self):
        """Coverage equal to90%, exclusions, missing cases and skipped cases must fail."""
        value={"totals":{"num_statements":100,"covered_lines":91,"num_branches":100,"covered_branches":91,"excluded_lines":0}}
        junit=ET.Element("testsuite")
        for module,name in sorted(original_api_cases()):
            ET.SubElement(junit,"testcase",classname=module,name=name)
        self.assertEqual(api_quality(value,junit)["tests"],132)
        with self.assertRaises(ValueError):api_quality(value,junit,search_api=True)
        current=deepcopy(junit)
        for name in ("test_mcp_and_rest_use_the_private_search_api","test_snapshot_must_open_before_serving",
                     "test_reversed_passages_are_not_a_valid_ranking","test_uninitialized_replica_and_weak_service_credentials",
                     "test_retrieval_readiness_survives_either_dependency_outage[feature]",
                     "test_retrieval_readiness_survives_either_dependency_outage[search]",
                     "test_search_bounds_concurrent_admission_and_counts_offered_work",
                     "test_saturated_search_cannot_queue_unbounded_work_or_starve_machine_reads",
                     *("test_private_api_hides_diagnostics_and_rejects_partial_results["+case+"]"
                       for case in ("limit-422","backend-503","incomplete-503","duplicate-503"))):
            ET.SubElement(current,"testcase",classname="test_rag_search_api",name=name)
        for case in ("None-200","wrong_reference-409","mixed_rollout-409","too_few-422"):
            ET.SubElement(current,"testcase",classname="test_drift_serving_scope",
                          name=f"test_serving_drift_uses_only_operating_rows_and_matching_training_reference[{case}]")
        self.assertEqual(api_quality(value,current,search_api=True)["tests"],148)
        for case in current:
            case.set("classname","tests.mcp."+case.get("classname"))
        self.assertEqual(api_quality(value,current,search_api=True)["tests"],148)
        incomplete=deepcopy(current);incomplete.remove(incomplete[-1])
        with self.assertRaisesRegex(ValueError,"serving-cohort"):
            api_quality(value,incomplete,search_api=True)
        current[0].set("name","a_replacement_test")
        with self.assertRaises(ValueError):api_quality(value,current,search_api=True)
        for key,new in [("covered_lines",90),("covered_branches",90),("excluded_lines",1)]:
            changed=deepcopy(value);changed["totals"][key]=new
            with self.subTest(key=key),self.assertRaises(ValueError):api_quality(changed,junit)
        with self.assertRaises(ValueError):api_quality(value,ET.fromstring("<testsuite><testcase/></testsuite>"))
        ET.SubElement(junit[0],"skipped")
        with self.assertRaises(ValueError):api_quality(value,junit)

    def test_package_covers_native_runtime_and_tests(self):
        """Every copied application/test path must be accounted for by the trusted source seal."""
        seal=json.loads((ROOT/"docs/evidence/mcp-quality-native/source/manifest.json").read_bytes())
        original=packaged_paths(seal)
        self.assertEqual(len(original),18)
        with self.assertRaises(ValueError):packaged_paths(seal,search_api=True)
        for name in ("__init__","app","contracts","reader"):
            seal["files"]["services/rag_search/"+name+".py"]="source"
        with self.assertRaises(ValueError):packaged_paths(seal,True,search_api=True)
        seal["files"]["tests/mcp/test_rag_search_api.py"]="tests"
        runtime=packaged_paths(seal,search_api=True);tests=packaged_paths(seal,True,search_api=True)
        self.assertEqual(len(runtime),22);self.assertTrue(set(runtime)<set(tests))
        del seal["files"]["services/mcp_tools/app.py"]
        with self.assertRaises(ValueError):packaged_paths(seal,search_api=True)


if __name__=="__main__":unittest.main()
