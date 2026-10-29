"""Exercise independent release clocks, readiness generations and strict quality boundaries before native deployment."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import importlib.util,json,sys,tempfile,unittest,xml.etree.ElementTree as ET
from agents.release_ci import api_quality,feature_lease,packaged_paths,ready_agents,role_for_job,required_inputs,capture_json_command

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("agent_release_renderer",ROOT/"scripts/render-agent-responses.py")
renderer=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(renderer)


class AgentReleaseCiTests(unittest.TestCase):
    """Use fixed known clocks and recorded object shapes, leaving actual deployment proof to Jenkins."""
    def test_probe_json_and_observed_call_diagnostics_remain_separate(self):
        """Real processes can emit JSON on both streams; only successful stdout is the command result."""
        code="import json,sys;print(json.dumps({'passed':True}));print(json.dumps({'observed_call':'diagnostic'}),file=sys.stderr)"
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.assertEqual(capture_json_command([sys.executable,"-B","-c",code],root,"probe"),{"passed":True})
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
        junit=ET.fromstring("<testsuite>"+"".join('<testcase classname="native" name="case-'+str(i)+'"/>' for i in range(132))+"</testsuite>")
        self.assertEqual(api_quality(value,junit)["tests"],132)
        for key,new in [("covered_lines",90),("covered_branches",90),("excluded_lines",1)]:
            changed=deepcopy(value);changed["totals"][key]=new
            with self.subTest(key=key),self.assertRaises(ValueError):api_quality(changed,junit)
        with self.assertRaises(ValueError):api_quality(value,ET.fromstring("<testsuite><testcase/></testsuite>"))
        ET.SubElement(junit[0],"skipped")
        with self.assertRaises(ValueError):api_quality(value,junit)

    def test_package_covers_native_runtime_and_tests(self):
        """Every copied application/test path must be accounted for by the trusted source seal."""
        seal=json.loads((ROOT/"docs/evidence/mcp-quality-native/source/manifest.json").read_bytes())
        runtime=packaged_paths(seal);tests=packaged_paths(seal,True)
        self.assertEqual(len(runtime),18);self.assertTrue(set(runtime)<set(tests))
        del seal["files"]["services/mcp_tools/app.py"]
        with self.assertRaises(ValueError):packaged_paths(seal)


if __name__=="__main__":unittest.main()
