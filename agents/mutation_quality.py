"""Produce and independently check source-sealed Cosmic Ray evidence for the split MCP and RAG Search API.

In the pinned MCP test environment with cosmic-ray==8.7.0 installed, run
python -m agents.mutation_quality --output docs/evidence/mcp-quality-current/receipt.zip
then include the resulting ZIP in the same trusted source packet as the tested code.
Mutation runs modify only a temporary copy. Jenkins verifies the raw ledgers before deployment.
"""
from collections import Counter
from hashlib import sha256
from importlib import import_module, metadata
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from agents.release_ci import validate_api_cases


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = "docs/evidence/mcp-quality-current/receipt.zip"
MODULES = tuple("services/mcp_tools/" + name + ".py" for name in ("app", "auth", "contracts", "runtime")) + (
    "services/rag_search/app.py", "services/rag_search/contracts.py", "services/rag_search/reader.py",
)
PAYLOADS = {"run.json", "profile.toml", "junit.xml", "baseline.jsonl", "inventory.jsonl", "results.jsonl"}

def profile(interpreter):
    """Pin the test interpreter explicitly, including on Windows where child executables ignore the supplied PATH."""
    path = PurePosixPath(interpreter)
    if (not (path.is_absolute() or PureWindowsPath(interpreter).is_absolute())
            or not re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?", path.name)
            or any(char in interpreter for char in '"\r\n\\')):
        raise ValueError("The default-operator run must name an absolute Python executable")
    command = '"' + interpreter + '" -B -m pytest --rootdir=tests/mcp tests/mcp -x -q'
    return ("[cosmic-ray]\nmodule-path = " + json.dumps(MODULES) + "\ntimeout = 60.0\n"
            "excluded-modules = []\ntest-command = " + json.dumps(command)
            + '\n[cosmic-ray.distributor]\nname = "local"\n').encode()


def source_hashes(root):
    """Cover both historical input sets, current test/support modules and every new Web API module."""
    names = {"agents/mutation_quality.py", "agents/release_ci.py", "scripts/summarize-mcp-mutations.py",
             "tests/mcp/test_rag_search_api.py"}
    for archive in ("mcp-quality", "mcp-quality-native"):
        manifest = root / "docs/evidence" / archive / "source/manifest.json"
        names.update(json.loads(manifest.read_bytes())["files"])
    for directory in ("services/mcp_tools", "services/rag_search", "tests/mcp"):
        names.update(path.relative_to(root).as_posix() for path in (root / directory).rglob("*.py"))
    names.update(MODULES)
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def work_ledger(raw, initial=False):
    """Index one native work descriptor and its original result per unfiltered first-order mutation."""
    jobs = {}
    descriptors = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        work, result = json.loads(line)
        mutations = work["mutations"]
        identity = work["job_id"]
        if not isinstance(identity, str) or not identity or identity in jobs or len(mutations) != 1:
            raise ValueError("Invalid or duplicate Cosmic Ray job")
        descriptor = json.dumps(mutations[0], sort_keys=True)
        module = mutations[0]["module_path"].replace("\\", "/")
        if module not in MODULES or descriptor in descriptors or (initial and result is not None):
            raise ValueError("Unexpected mutation scope, duplicate work, or nonempty initial result")
        descriptors.add(descriptor)
        jobs[identity] = (work, result, module)
    if {module for _, _, module in jobs.values()} != set(MODULES):
        raise ValueError("Every MCP and RAG Search API target must contribute real native mutants")
    return jobs


def baseline_count(junit, cosmic_baseline):
    """Require the original 132 named API cases, the split search suite, and a matching successful native baseline."""
    count = validate_api_cases(ET.fromstring(junit), search_api=True)
    work, baseline = json.loads(cosmic_baseline)
    if (work != {"job_id": "baseline", "mutations": []} or baseline["worker_outcome"] != "normal"
            or baseline["test_outcome"] != "survived"
            or not re.search(r"\b" + str(count) + r" passed(?:, \d+ warnings?)? in ", baseline["output"])):
        raise ValueError("The unmutated Cosmic Ray baseline disagrees with the successful test suite")
    return count


def verify_receipt(root, bundle, seal):
    """Authenticate the entire bundle against Jenkins' source seal and recompute its strict result."""
    digest = sha256(bundle.read_bytes()).hexdigest()
    hashes = source_hashes(root)
    if seal.get(RECEIPT) != digest or not hashes.items() <= seal.items():
        raise ValueError("The Jenkins source packet must seal the mutation receipt and all tested inputs")
    with ZipFile(bundle) as archive:
        if sorted(archive.namelist()) != sorted(PAYLOADS) or any(row.file_size > 128_000_000 for row in archive.infolist()):
            raise ValueError("Unexpected or duplicate mutation evidence payload")
        run = json.loads(archive.read("run.json"))
        inventory = archive.read("inventory.jsonl")
        if (run != {"engine": "cosmic-ray==8.7.0", "restored_source": hashes, "source": hashes, "python": run["python"],
                    "initial_inventory_sha256": sha256(inventory).hexdigest()}
                or archive.read("profile.toml") != profile(run["python"])):
            raise ValueError("Mutation source, restored worker, inventory or full default-operator profile changed")
        tested = baseline_count(archive.read("junit.xml"), archive.read("baseline.jsonl"))
        initial = work_ledger(inventory, initial=True)
        results = work_ledger(archive.read("results.jsonl"))
    if initial.keys() != results.keys():
        raise ValueError("The complete initialized mutant inventory must be scored")
    category = import_module("scripts.summarize-mcp-mutations").category
    counts = Counter()
    for identifier, (work, _, _) in initial.items():
        actual, result, _ = results[identifier]
        if actual != work:
            raise ValueError("A mutation descriptor changed after initialization")
        counts[category(result)] += 1
    if counts["pending"] or counts["skipped"] or counts["killed"] * 5 <= len(initial) * 4:
        raise ValueError("Current sources require a complete mutation run strictly above 80%")
    return {"receipt_sha256": digest, "engine": run["engine"], "tests": tested, "targets": list(MODULES),
            "strict_mutation_score_percent": counts["killed"] * 100 / len(initial), "total": len(initial),
            "counts": dict(counts), "tested_source_files": len(hashes), "fresh_mutation_run_in_jenkins": False}


def produce(root, output):
    """Run the pinned local distributor in isolated scratch and publish only independently passing evidence."""
    if metadata.version("cosmic-ray") != "8.7.0":
        raise ValueError("Install the pinned infra/mcp/requirements-cosmic-ray.txt in the MCP test environment")
    if output.exists():
        raise FileExistsError(output)
    hashes = source_hashes(root)
    with TemporaryDirectory() as directory:
        scratch = Path(directory)
        worker = scratch / "worker"
        for name in hashes:
            target = worker / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
        config = scratch / "profile.toml"
        interpreter = Path(sys.executable).absolute().as_posix()
        config.write_bytes(profile(interpreter))
        env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
               "PYTHONPATH": str(worker), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        def run(args, capture=None):
            """Require native process success before recording its output as evidence."""
            with (scratch / capture).open("wb") if capture else open(os.devnull, "wb") as stream:
                subprocess.run([sys.executable, "-B", "-m", *map(str, args)], cwd=worker, env=env, stdout=stream, check=True)
        cosmic = ["cosmic_ray.cli"]
        baseline_db, session = scratch / "baseline.sqlite", scratch / "mutations.sqlite"
        run(["pytest", "--rootdir=tests/mcp", "tests/mcp", "-q", "--junitxml=" + str(scratch / "junit.xml")])
        run([*cosmic, "baseline", config, "--session-file", baseline_db])
        run([*cosmic, "dump", baseline_db], "baseline.jsonl")
        baseline_count((scratch / "junit.xml").read_bytes(), (scratch / "baseline.jsonl").read_bytes())
        run([*cosmic, "init", config, session])
        run([*cosmic, "dump", session], "inventory.jsonl")
        inventory = (scratch / "inventory.jsonl").read_bytes()
        work_ledger(inventory, initial=True)
        run([*cosmic, "exec", config, session])
        run([*cosmic, "dump", session], "results.jsonl")
        restored = {name: sha256((worker / name).read_bytes()).hexdigest() for name in hashes}
        (scratch / "run.json").write_text(json.dumps({"engine": "cosmic-ray==8.7.0", "source": hashes, "python": interpreter,
                    "restored_source": restored, "initial_inventory_sha256": sha256(inventory).hexdigest()},
                    sort_keys=True), encoding="utf-8")
        bundle = scratch / "receipt.zip"
        with ZipFile(bundle, "x", ZIP_DEFLATED) as archive:
            for name in sorted(PAYLOADS):
                archive.write(scratch / name, name)
        report = verify_receipt(root, bundle, {**hashes, RECEIPT: sha256(bundle.read_bytes()).hexdigest()})
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as destination, bundle.open("rb") as source:
            shutil.copyfileobj(source, destination)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(produce(ROOT, parser.parse_args().output), indent=2))
