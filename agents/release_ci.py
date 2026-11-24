"""Validate agent release readiness, immutable MCP packages and source-bound reuse of the existing quality baseline."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import json
import re
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from agents.ci_contract import validate_agents

JOBS={"iot-llm-retrieval":"retrieval","iot-llm-drift":"drift","iot-llm-coordinator":"coordinator"}
MCP_RUNTIME="sha256:939c4201356b974bd7754cd274f42dd9b928049820e6f58086ca0a8c2a1c6db2"
MCP_TESTS="sha256:b8b7c3549378130382de4bbbb39aefeddc81a1b4dce522cee698105522719022"


def capture_json_command(command,directory,name,timeout=240,*,input_bytes=None):
    """Keep original JSON stdout separate from diagnostic stderr and require the actual process to succeed."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,60}",name):raise ValueError("Use a bounded CI artifact name")
    directory=Path(directory)
    with (directory/(name+".json")).open("xb") as output,(directory/(name+".stderr.txt")).open("xb") as errors:
        result=subprocess.run(command,input=input_bytes,stdout=output,stderr=errors,timeout=timeout)
    (directory/(name+".exit")).write_text(str(result.returncode)+"\n")
    if result.returncode:raise RuntimeError("Native JSON command failed; preserve both original streams")
    return json.loads((directory/(name+".json")).read_bytes())


def role_for_job(name):
    """Reject undeclared Jenkins jobs before selecting any deployment or release output."""
    if name not in JOBS:raise ValueError("Use one of the three declared private agent jobs")
    return JOBS[name]


def required_inputs(root,seal):
    """Refuse partial CI input sets, including transitive test imports and the complete cached quality source."""
    root=Path(root);name="infra/ci/agents-source.json";configuration=json.loads((root/name).read_bytes())
    if configuration.get("schema_version")!=1:raise ValueError("Unknown agent CI source declaration")
    required={name,*configuration["required"],*configuration["quality_manifests"]}
    if not required.issubset(seal["files"]):raise ValueError("The source packet omits a required CI or test input")
    for relative in configuration["quality_manifests"]:
        baseline=json.loads((root/relative).read_bytes())
        required.update(baseline["files"])
    if not required.issubset(seal["files"]):raise ValueError("The source packet omits frozen API/test inputs")
    return len(required)


def packaged_paths(seal,tests=False,*,search_api=False):
    """Name every application file copied into the runtime overlay and, optionally, its test overlay."""
    paths={"services/mcp_tools/"+name+".py" for name in ["__init__","app","auth","contracts","probe","runtime","snapshot"]}
    paths|={"services/__init__.py","services/predictor/__init__.py","services/predictor/contracts.py",
           "services/drift_api/__init__.py","services/drift_api/contracts.py","rag/__init__.py","rag/store.py",
           "rag/embeddings.py","rag/retrieval.py","infra/rag/embedding.lock.json","knowledge/catalog.json"}
    if search_api:
        paths.update("services/rag_search/"+name+".py" for name in ("__init__", "app", "contracts", "reader"))
    if tests:
        paths|={"services/drift_api/detector.py","services/drift_api/runtime.py"}
        paths|={name for name in seal["files"] if name.startswith("tests/mcp/") and name.endswith(".py")}
        if search_api:
            paths.add("tests/mcp/test_rag_search_api.py")
    if not paths.issubset(seal["files"]):raise ValueError("The CI source does not cover the entire MCP package")
    return sorted(paths)


def ready_agents(documents,policy,*,gateway=False):
    """Require the accepted current policy and Ready conditions belonging to the actual resource generation."""
    validate_agents(documents,policy,gateway=gateway)
    for agent in documents:
        if not agent or agent.get("kind")!="SandboxAgent":continue
        generation=agent["metadata"]["generation"];status=agent.get("status",{})
        if status.get("observedGeneration")!=generation:raise ValueError("Agent status belongs to an older generation")
        for condition in ["Ready","Accepted"]:
            if not any(c.get("type")==condition and c.get("status")=="True" and c.get("observedGeneration")==generation
                       for c in status.get("conditions",[])):
                raise ValueError("The actual agent generation is not ready and accepted")
    return True


def feature_lease(context,now,minimum_seconds=600):
    """Use the real prepared feature clock and fixed one-hour lease without changing timestamps or cache TTLs."""
    prepared=datetime.fromisoformat(context["prepared_wall_utc"])
    if prepared.tzinfo is None or now.tzinfo is None:raise ValueError("Use aware native clocks")
    remaining=(prepared+timedelta(hours=1)-now).total_seconds()
    if prepared>now or remaining<minimum_seconds:raise ValueError("The real feature lease is too short for this release")
    return {"prepared_wall_utc":prepared.isoformat(),"lease_expires_utc":(prepared+timedelta(hours=1)).isoformat(),
            "remaining_seconds":remaining,"checked_utc":now.astimezone(timezone.utc).isoformat()}


def original_api_cases():
    """Read the exact 132 test identities from the accepted retrieval build."""
    archive=Path(__file__).resolve().parents[1]/'docs/evidence/ci-agents/native-receipts.tar.gz'
    with tarfile.open(archive, 'r:gz') as capture:
        raw=capture.extractfile('agent-ci-capture-29a/release/retrieval/4/api-tests/junit.xml').read()
    if sha256(raw).hexdigest()!='555db9233ff7773bb6a6106d9fbc54169d42c0db96aecd0e73e647f721ee1b15':
        raise ValueError('The original native MCP test receipt changed')
    return {(case.get('classname'),case.get('name')) for case in ET.fromstring(raw).iter('testcase')}


def validate_api_cases(junit,*,search_api=False):
    """Require all named native cases for the recorded or split deployment before accepting an API baseline."""
    cases=list(junit.iter("testcase"))
    identities={(case.get("classname"),case.get("name")) for case in cases}
    if search_api:
        # The image collects /tests/mcp; a checkout collects the same modules under tests.mcp.
        identities={((module or "").removeprefix("tests.mcp."),name) for module,name in identities}
    required=original_api_cases()
    if (len(cases)!=len(identities) or not required.issubset(identities)
            or any(list(junit.iter(tag)) for tag in ["failure","error","skipped"])):
        raise ValueError("The complete original MCP suite must run successfully, without duplicate cases")
    if search_api:
        search_cases={name for module,name in identities if module=="test_rag_search_api"}
        expected={"test_mcp_and_rest_use_the_private_search_api","test_snapshot_must_open_before_serving",
                  "test_reversed_passages_are_not_a_valid_ranking","test_uninitialized_replica_and_weak_service_credentials",
                  "test_retrieval_readiness_survives_either_dependency_outage[feature]",
                  "test_retrieval_readiness_survives_either_dependency_outage[search]",
                  "test_search_bounds_concurrent_admission_and_counts_offered_work",
                  "test_saturated_search_cannot_queue_unbounded_work_or_starve_machine_reads"}
        for failure,status in (("limit",422),("backend",503),("incomplete",503),("duplicate",503)):
            expected.add(f"test_private_api_hides_diagnostics_and_rejects_partial_results[{failure}-{status}]")
        if not expected.issubset(search_cases):
            raise ValueError("The private search routes, isolated readiness and bounded admission cases must run")
        drift_cases={name for module,name in identities if module=="test_drift_serving_scope"}
        expected_drift={"test_serving_drift_uses_only_operating_rows_and_matching_training_reference"+f"[{case}]"
                        for case in ("None-200","wrong_reference-409","mixed_rollout-409","too_few-422")}
        if not expected_drift.issubset(drift_cases):
            raise ValueError("The serving-cohort eligibility and reference matching cases must run")
    elif identities!=required:
        raise ValueError("The recorded build must contain exactly the original 132 cases")
    return len(cases)


def api_quality(coverage,junit,*,search_api=False):
    """Require real statement/branch coverage and every one of the existing native API cases without exclusions."""
    totals=coverage["totals"]
    if (totals["num_statements"]<=0 or totals["num_branches"]<=0 or totals["excluded_lines"]!=0
            or totals["covered_lines"]/totals["num_statements"]<=.9
            or totals["covered_branches"]/totals["num_branches"]<=.9):
        raise ValueError("MCP statement and branch coverage must both exceed90%")
    count=validate_api_cases(junit,search_api=search_api)
    return {"tests":count,"line_percent":100*totals["covered_lines"]/totals["num_statements"],
            "branch_percent":100*totals["covered_branches"]/totals["num_branches"]}


def unchanged_quality_source(root):
    """Keep every mutation input exact; accept only the pinned deployment-only autoscaling revision separately."""
    root=Path(root);counts={};revision=None
    declaration=root/'infra/mcp/autoscaling-source.json'
    if declaration.exists():
        raw=declaration.read_bytes()
        if sha256(raw).hexdigest()!='8d2417c2177fa04cad60cde059fb087cb6595bceb3952748b4c0e95fa6870436':
            raise ValueError('The reviewed deployment-only revision changed')
        revision=json.loads(raw)
        for relative,digest in revision['files'].items():
            if sha256((root/relative).read_bytes()).hexdigest()!=digest:
                raise ValueError('Unreviewed MCP deployment change: '+relative)
    deployment_only={'infra/charts/iot-mcp/templates/workload.yaml','infra/charts/iot-mcp/values.yaml'}
    for name in ["mcp-quality","mcp-quality-native"]:
        path=root/"docs/evidence"/name/"source/manifest.json";seal=json.loads(path.read_bytes())
        for relative,digest in seal["files"].items():
            target=(root/relative).resolve()
            if not target.is_relative_to(root.resolve()):raise ValueError('Escaped MCP quality input')
            if sha256(target.read_bytes()).hexdigest()!=digest and not (name=='mcp-quality-native' and revision and relative in deployment_only):
                raise ValueError("Changed MCP source/tests require a fresh mutation gate: "+relative)
        counts[name]={"files":len(seal["files"]),"manifest_sha256":sha256(path.read_bytes()).hexdigest()}
        if name=='mcp-quality-native' and revision:
            counts[name]['deployment_only_revision']=revision['source_commit']
    return counts
