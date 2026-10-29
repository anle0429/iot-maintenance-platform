"""Independently verify the complete local MCP mutation ledger, original baseline and exact current source snapshot."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import tomllib
import xml.etree.ElementTree as ET
from integration_evidence import ROOT, capsule, read, sources

MANIFEST_SHA256 = '565e689dc4e0d52af9ef1ab89c6b4466e98622ff76dc4a0a4f6dfbd4d7b23b4e'


def verify():
    """Recompute the strict result while retaining prior tool failures and separating local quality from deployment."""
    base, count = capsule('mcp-quality', MANIFEST_SHA256)
    assert count == 84
    seal = sources(base, '.', current=True)
    assert len(seal['files']) == 50 and seal['base_commit'] == '5d6fdb4bab810b84339f4b696b563a444f05f0ba'
    baseline = json.loads((base / 'run/baseline.jsonl').read_text().strip())
    assert baseline[0] == {'job_id': 'baseline', 'mutations': []}
    assert baseline[1]['worker_outcome'] == 'normal' and baseline[1]['test_outcome'] == 'survived'
    assert '132 passed' in baseline[1]['output'] and 'failed' not in baseline[1]['output'].lower()
    for name in ['baseline.exit', 'init.exit', 'candidate-tests-2.exit']:
        assert (base / 'run' / name).read_text().strip() == '0'
    for name in ['candidate-tests-2.xml', 'promoted-root-tests.xml']:
        document = ET.parse(base / 'run' / name).getroot()
        assert len(list(document.iter('testcase'))) == 132
        assert not any(list(document.iter(tag)) for tag in ['failure', 'error', 'skipped'])
    terminal = read(base, 'run/terminal.json')
    assert terminal['process_exit'] == terminal['export_exit'] == 0 and terminal['source_restored']
    assert 3000 < terminal['elapsed_seconds'] < 3600
    profile = read(base, 'run/profile-decision.json')
    assert profile['tool'] == 'cosmic-ray==8.7.0' and profile['default_operators']
    assert not profile['operator_filters'] and not profile['line_filters']
    assert profile['parallel_test_processes'] == 1 and profile['timeout_seconds'] == 60
    config = tomllib.loads((base / 'run/cosmic-ray.toml').read_text())['cosmic-ray']
    assert config['excluded-modules'] == [] and config['timeout'] == 60
    assert config['distributor']['name'] == 'local'
    module = importlib.util.spec_from_file_location('mcp_mutation_scoreboard', ROOT / 'scripts/summarize-mcp-mutations.py')
    scoreboard = importlib.util.module_from_spec(module); module.loader.exec_module(scoreboard)
    assert set(config['module-path']) == scoreboard.MODULES
    actual = scoreboard.summarize(base / 'run/inventory.jsonl', base / 'run/results.jsonl')
    assert actual == read(base, 'run/strict-score.json') and actual['mutation_gate_passed']
    assert actual['total'] == 850
    assert actual['counts'] == {'killed': 681, 'survived': 167, 'timeout': 1, 'incompetent': 0,
        'worker_error': 1, 'no_test': 0, 'skipped': 0, 'pending': 0}
    inventory = scoreboard.ledger(base / 'run/inventory.jsonl', initial=True)
    validators = Counter(work['mutations'][0].get('definition_name') for work, _, name in inventory.values()
        if name == 'services/mcp_tools/contracts.py')
    assert sum(validators.values()) == 305
    assert all(validators[name] > 0 for name in ['valid_simulated_times', 'complete_scores', 'valid_decision', 'unique_cohort'])
    preflight = read(base, 'run/pre-execution.json')
    assert preflight['inventory_sha256'] == actual['inventory_sha256']
    earlier = read(base, 'diagnostics/earlier-validator-pilot.json')
    later = read(base, 'run/decorated-validator-pilot.json')
    assert earlier['source_restored'] and later['source_restored']
    assert earlier['result']['test_outcome'] == 'survived' and later['result']['test_outcome'] == 'killed'
    assert earlier['result']['diff'] == later['result']['diff']
    assert '1 failed' in (base / 'diagnostics/timestamp-before.log').read_text()
    assert 'assert 500 == 503' in (base / 'diagnostics/timestamp-before.log').read_text()
    assert (base / 'diagnostics/timestamp-before.exit').read_text().strip() != '0'
    assert (base / 'diagnostics/timestamp-after.exit').read_text().strip() == '0'
    assert (base / 'diagnostics/baseline.exit').read_text().strip() != '0'
    assert 'pytest' in (base / 'diagnostics/baseline.log').read_text()
    auth = read(base, 'authentication.json')
    assert auth['cosmic_ray_version'] == '8.7.0' and auth['current_and_restored_worker_match_seal']
    assert auth['native_deployment_verified'] is False
    return {'passed': True, 'payloads': count, 'local_tests': 132, 'source_files': len(seal['files']),
        'mutation_gate_passed': True, 'strict_score_percent': actual['strict_score_percent'],
        'counts': actual['counts'], 'current_source_matches_tested_candidate': True,
        'native_candidate_deployment_verified': False, 'coursework_complete': False,
        'scope': 'Recorded single-worker Windows quality run, original baseline and complete default-operator inventory on four API modules. The two VM mutmut failures remain historical; their scores are not directly comparable to this engine. Native deployment, agent acceptance and remaining rubric work require separate proof.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
