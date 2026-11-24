"""Reject stale source, incomplete inventories and inflated scores for the split retrieval release."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
import json
import unittest
import xml.etree.ElementTree as ET

from agents import mutation_quality as gate
from agents.release_ci import original_api_cases


class AgentMutationQualityTests(unittest.TestCase):
    """Use protocol fixtures to exercise the gate without reporting a mutation result for the application."""
    def report(self, *, killed=9, drop=False, stale=False, omit_test=False, replace_search_test=False, tamper=False):
        """Seal and verify raw native-shaped payloads with ten distinct jobs across the seven real modules."""
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        digest = {"services/rag_search/app.py": "0" * 64}
        identities = sorted(original_api_cases())
        if omit_test:
            identities.pop(0)
        for name in ("test_mcp_and_rest_use_the_private_search_api", "test_snapshot_must_open_before_serving",
                     "test_reversed_passages_are_not_a_valid_ranking", "test_uninitialized_replica_and_weak_service_credentials"):
            identities.append(("test_rag_search_api",name))
        for failure,status in (("limit",422),("backend",503),("incomplete",503),("duplicate",503)):
            identities.append(("test_rag_search_api",f"test_private_api_hides_diagnostics_and_rejects_partial_results[{failure}-{status}]"))
        if replace_search_test:
            identities[-1] = ("test_rag_search_api", "test_other")
        suite = ET.Element("testsuite")
        for module, name in identities:
            ET.SubElement(suite, "testcase", classname=module, name=name)
        initial, results = [], []
        for index in range(10):
            work = {"job_id": str(index), "mutations": [{"module_path": gate.MODULES[index % 7],
                    "occurrence": index, "operator_name": "core/NumberReplacer"}]}
            initial.append(json.dumps([work, None]))
            result = {"worker_outcome": "normal", "test_outcome": "killed" if index < killed else "survived"}
            results.append(json.dumps([work, result]))
        inventory = "\n".join(initial).encode()
        config = gate.profile("/usr/bin/python3")
        run = {"engine": "cosmic-ray==8.7.0", "source": digest, "restored_source": digest, "python": "/usr/bin/python3",
               "initial_inventory_sha256": sha256(inventory).hexdigest()}
        bundle = root / "receipt.zip"
        with ZipFile(bundle, "x") as archive:
            archive.writestr("profile.toml", config + (b"# changed" if tamper else b""))
            archive.writestr("junit.xml", ET.tostring(suite))
            archive.writestr("run.json", json.dumps(run))
            archive.writestr("baseline.jsonl", json.dumps([{"job_id": "baseline", "mutations": []},
                {"worker_outcome": "normal", "test_outcome": "survived", "output": str(len(identities)) + " passed in 4s"}]))
            archive.writestr("inventory.jsonl", inventory)
            archive.writestr("results.jsonl", "\n".join(results[:-1] if drop else results))
        seal = {gate.RECEIPT: sha256(bundle.read_bytes()).hexdigest(), **digest}
        if stale:
            seal.update({"services/rag_search/app.py": "1" * 64})
        with patch.object(gate, "source_hashes", return_value=digest):
            return gate.verify_receipt(root, bundle, seal)

    def test_strict_threshold_and_exact_source_ledger_and_profile(self):
        """Require more than 80%, every declared mutation, original test identity, source and exact profile."""
        self.assertEqual(self.report()["total"], 10)
        for change in ({"killed": 8}, {"drop": True}, {"stale": True}, {"omit_test": True},
                       {"replace_search_test": True}, {"tamper": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.report(**change)
