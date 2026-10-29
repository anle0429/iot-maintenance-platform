"""Validate agent release readiness, immutable MCP packages and source-bound reuse of the existing quality baseline."""
from datetime import datetime,timezone,timedelta
from hashlib import sha256
from pathlib import Path
import json
from agents.ci_contract import validate_agents

JOBS={"iot-llm-retrieval":"retrieval","iot-llm-drift":"drift","iot-llm-coordinator":"coordinator"}
MCP_RUNTIME="sha256:939c4201356b974bd7754cd274f42dd9b928049820e6f58086ca0a8c2a1c6db2"
MCP_TESTS="sha256:b8b7c3549378130382de4bbbb39aefeddc81a1b4dce522cee698105522719022"


def role_for_job(name):
    """Reject undeclared Jenkins jobs before selecting any deployment or release output."""
    if name not in JOBS:raise ValueError("Use one of the three declared private agent jobs")
    return JOBS[name]


def packaged_paths(seal,tests=False):
    """Name every application file copied into the runtime overlay and, optionally, its test overlay."""
    paths={"services/mcp_tools/"+name+".py" for name in ["__init__","app","auth","contracts","probe","runtime","snapshot"]}
    paths|={"services/__init__.py","services/predictor/__init__.py","services/predictor/contracts.py",
           "services/drift_api/__init__.py","services/drift_api/contracts.py","rag/__init__.py","rag/store.py",
           "rag/embeddings.py","rag/retrieval.py","infra/rag/embedding.lock.json","knowledge/catalog.json"}
    if tests:
        paths|={"services/drift_api/detector.py","services/drift_api/runtime.py"}
        paths|={name for name in seal["files"] if name.startswith("tests/mcp/") and name.endswith(".py")}
    if not paths.issubset(seal["files"]):raise ValueError("The CI source does not cover the entire MCP package")
    return sorted(paths)


def ready_agents(documents,policy):
    """Require the accepted current policy and Ready conditions belonging to the actual resource generation."""
    validate_agents(documents,policy)
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


def api_quality(coverage,junit):
    """Require real statement/branch coverage and every one of the existing native API cases without exclusions."""
    totals=coverage["totals"]
    if (totals["num_statements"]<=0 or totals["num_branches"]<=0 or totals["excluded_lines"]!=0
            or totals["covered_lines"]/totals["num_statements"]<=.9
            or totals["covered_branches"]/totals["num_branches"]<=.9):
        raise ValueError("MCP statement and branch coverage must both exceed90%")
    cases=list(junit.iter("testcase"))
    if (len(cases)!=132 or len({(case.get("classname"),case.get("name")) for case in cases})!=132
            or any(list(junit.iter(tag)) for tag in ["failure","error","skipped"])):
        raise ValueError("All132nativeMCPAPIcases must execute successfully")
    return {"tests":len(cases),"line_percent":100*totals["covered_lines"]/totals["num_statements"],
            "branch_percent":100*totals["covered_branches"]/totals["num_branches"]}


def unchanged_quality_source(root):
    """Permit cached mutations only when every native/candidate source and test byte still matches its frozen baseline."""
    root=Path(root);counts={}
    for name in ["mcp-quality","mcp-quality-native"]:
        path=root/"docs/evidence"/name/"source/manifest.json";seal=json.loads(path.read_bytes())
        for relative,digest in seal["files"].items():
            target=(root/relative).resolve()
            if not target.is_relative_to(root.resolve()) or sha256(target.read_bytes()).hexdigest()!=digest:
                raise ValueError("Changed MCP source/tests require a fresh mutation gate: "+relative)
        counts[name]={"files":len(seal["files"]),"manifest_sha256":sha256(path.read_bytes()).hexdigest()}
    return counts
