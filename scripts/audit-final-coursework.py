"""Audit all original final-rubric rows, their linked evidence and read-only artifact verifiers without starting services."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import html
import json
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SNAPSHOT_SHA256 = '66b8c73d65bb101b2966e1155898d080901002a6fcbd2c1432063befca871ae3'
LLM_SNAPSHOT_SHA256 = '947df230e73d6960fc28781cbf702a650d14e0142680db9288d1468f59ccb72f'
TRANSLATION_SHA256 = {'ML': 'e08a2544db9073401c595c70f834b30aabedeb8c4b26b1cf7b6a3b7c2dca60c1',
                      'LLM': '9001e6a26fa6e61bae15d5e3cff82bb1bbdcfe2b89f689bfa9ee8ff7435a16be'}
VERIFIERS = (
    'ab-monitoring', 'active-feature-refresh', 'api-observability', 'ci-api-observability',
    'ci-stream-push', 'ci-training', 'datahub-restore', 'deployment-policy', 'drift-eventing',
    'drift-retraining', 'final-handover', 'focused-api', 'host-monitoring', 'iac-final',
    'joint-cohort', 'linux-host', 'linux-logs', 'log-discovery', 'native-serving',
    'observed-api-load', 'offline-drift', 'private-gateway', 'private-mesh', 'vault', 'window-cohort', 'final-review', 'llm-inference',
    'rag-foundation', 'llm-grounding', 'rag-orchestration', 'rag-corpus-drift', 'assistant-data', 'mcp-tools', 'mcp-gateway', 'agent-platform', 'agent-response-baseline', 'llm-model-ab', 'llm-agent-ab',
    'mcp-quality', 'mcp-quality-native', 'mcp-quality-ui', 'gpu-inference', 'gpu-agents', 'llm-iac-ui', 'vault-agent-ui', 'agent-network', 'agent-scaling', 'agent-notebook', 'agent-warmup', 'ci-rag', 'ci-agents', 'agent-gateway', 'llm-observation', 'llm-observer', 'llm-agent-telemetry', 'llm-monitoring', 'llm-policy-window', 'agent-admission',
)
CAPSULES = (
    'drift-api', 'api-autoscale', 'notebook', 'notebook-parity', 'distributed-training',
    'automatic-versioning', 'data-versions', 'ci-materialization', 'ci-data-pipelines',
)


def normalize(value):
    """Compare original text while allowing Markdown line-break representation and insignificant whitespace."""
    return ' '.join(html.unescape(str(value or '')).replace('<br>', '\n').replace('\\|', '|').split())


def original_snapshot(path):
    """Pin the original workbook-cell snapshot so editing both source cells and tracker cannot weaken the audit."""
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != SOURCE_SNAPSHOT_SHA256: raise ValueError('The original final-rubric source snapshot changed')
    return json.loads(raw)


def cell(snapshot, address):
    """Resolve a merged cell to its original anchor without evaluating workbook formulas."""
    column, row = re.fullmatch(r'([A-Z]+)(\d+)', address).groups()
    for merged in snapshot['merged_ranges']:
        left, top, right, bottom = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)', merged).groups()
        if left == right == column and int(top) <= int(row) <= int(bottom):
            address = left + top
            break
    return address, snapshot['cells'].get(address, {}).get('value')


def english_view(path, kind):
    """Pin the reviewed English wording independently of both the tracker and original source snapshot."""
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != TRANSLATION_SHA256[kind]: raise ValueError('The reviewed English rubric translation changed')
    data = json.loads(raw)
    expected_source = SOURCE_SNAPSHOT_SHA256 if kind == 'ML' else LLM_SNAPSHOT_SHA256
    assert data['source_snapshot_sha256'] == expected_source and data['language'] == 'en'
    return {row['id']: row for row in data['rows']}


def rubric_rows(snapshot, text, kind='ML'):
    """Check English wording plus original IDs, merged-cell addresses and weights without altering source-language artifacts."""
    count, prefix = (57, 'F') if kind == 'ML' else (60, 'L')
    assert snapshot['weighted_rows'] == count and snapshot['source_point_total'] == 100
    translation = english_view(ROOT / 'docs/rubric' / ('final-' + kind.lower() + '-english.json'), kind)
    rows = {}
    for line in text.splitlines():
        if not re.match(r'\| ' + prefix + r'\d+ \|', line):
            continue
        columns = [part.strip() for part in re.split(r'(?<!\\)\|', line)[1:-1]]
        key = columns[0]
        assert key not in rows and len(columns) == 8, key
        rows[key] = columns
    assert set(rows) == set(translation) == {f'{prefix}{i:02}' for i in range(3, count + 3)}, 'Missing or unexpected final rows'
    points = 0
    for number in range(3, count + 3):
        key = f'{prefix}{number:02}'; current = rows[key]; translated = translation[key]
        _, subgroup = cell(snapshot, f'B{number}')
        _, detail = cell(snapshot, f'C{number}')
        requirement_column = 'C' if detail else 'B' if subgroup else 'A'
        requirement_address, _ = cell(snapshot, requirement_column + str(number))
        deliverable_address, _ = cell(snapshot, f'D{number}')
        weight = float(cell(snapshot, f'E{number}')[1]); points += weight
        refs = f'{requirement_address}; {deliverable_address}; E{number}'
        assert translated['source_refs'] == refs and translated['points'] == weight, key
        expected = [key, refs, translated['group'], translated['requirement'], translated['deliverable'], f'{weight:g}']
        assert [normalize(v) for v in current[:6]] == [normalize(v) for v in expected], f'Rubric presentation changed: {key}'
    assert points == 100
    return rows


def capsule_manifest(root, name):
    """Hash every original mapped payload in an older native capsule without trusting success flags alone."""
    manifest_path = root / 'docs/evidence' / name / 'capture/manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    files = manifest.get('files', manifest)
    mapping_path = manifest_path.with_name('evidence-map.json')
    mapping = json.loads(mapping_path.read_bytes()) if mapping_path.exists() else {}
    excluded = mapping.get('excluded_native_sources', {})
    mapping = mapping.get('source_to_repository', mapping.get('files', mapping))
    assert files and isinstance(files, dict)
    for native_name, metadata in files.items():
        expected = metadata if isinstance(metadata, str) else metadata['sha256']
        if native_name in excluded:
            assert native_name.endswith('/coverage-html/.gitignore') and excluded[native_name]['sha256'] == expected
            assert excluded[native_name]['reason']
            continue
        target = mapping.get(native_name)
        if isinstance(target, dict):
            assert target['sha256'] == expected
            target = target['path']
        if target:
            path = (root / target).resolve()
        else:
            options = [manifest_path.parent / native_name, manifest_path.parent.parent / native_name]
            path = next((p.resolve() for p in options if p.is_file()), None)
        assert path and path.is_relative_to((root / 'docs/evidence').resolve()) and path.is_file(), (name, native_name)
        raw = path.read_bytes(); assert sha256(raw).hexdigest() == expected, str(path)
        if isinstance(metadata, dict) and 'bytes' in metadata:
            assert len(raw) == metadata['bytes'], str(path)
    return {'verified_payloads': len(files) - len(excluded), 'declared_nonessential_exclusions': sorted(excluded)}


def execute_verifier(name, output):
    """Run one explicitly whitelisted offline checker with bounded time and retain its unmodified console."""
    assert name in VERIFIERS
    started = time.monotonic()
    try:
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'scripts' / f'verify-{name}.py')], cwd=ROOT, capture_output=True, timeout=240)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        code, stdout, stderr = 'timeout', error.stdout or b'', error.stderr or b''
    (output / f'{name}.stdout').write_bytes(stdout)
    (output / f'{name}.stderr').write_bytes(stderr)
    return name, {'exit': code, 'seconds': time.monotonic() - started}


def audit(output, workers):
    """Produce a reproducible structural/evidence audit; explicitly leave live readiness and grading undecided."""
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'outputs'):
        raise ValueError('Choose a new audit directory under outputs/')
    output.mkdir(parents=True, exist_ok=False)
    snapshot_path = ROOT / 'docs/evidence/rubric-final/source.json'
    snapshot = original_snapshot(snapshot_path)
    rows = rubric_rows(snapshot, (ROOT / 'docs/final-rubric-tracker.md').read_text(encoding='utf-8'))
    llm_raw = (ROOT / 'docs/evidence/rubric-llm/source.json').read_bytes()
    if sha256(llm_raw).hexdigest() != LLM_SNAPSHOT_SHA256: raise ValueError('The original LLM source snapshot changed')
    llm_rows = rubric_rows(json.loads(llm_raw), (ROOT / 'docs/final-llm-rubric-tracker.md').read_text(encoding='utf-8'), 'LLM')
    links = 0
    for key, columns in (rows | llm_rows).items():
        for target in re.findall(r'\]\(([^)]+)\)', columns[-1]):
            if '://' in target:
                continue
            path = (ROOT / 'docs' / target.split('#')[0]).resolve()
            assert path.is_relative_to(ROOT) and path.is_file(), (key, target)
            links += 1
    capsules = {name: capsule_manifest(ROOT, name) for name in CAPSULES}
    checkers = [ROOT / 'scripts' / f'verify-{name}.py' for name in VERIFIERS] + [Path(__file__).resolve(), ROOT / 'scripts/evidence_integrity.py', ROOT / 'scripts/git_sources.py', ROOT / 'scripts/integration_evidence.py', ROOT / 'agents/answer_acceptance.py', ROOT / 'agents/delegation_acceptance.py', ROOT / 'scripts/go-mutation-exec.py', ROOT / 'agents/probe.py', ROOT / 'scripts/view-vault-metadata.py', ROOT / 'tests/test_vault_metadata_view.py']
    source_before = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in checkers}
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, result in pool.map(lambda name: execute_verifier(name, output), VERIFIERS):
            results[name] = result
            print(name, result['exit'], flush=True)
    source_after = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest() for p in checkers}
    assert source_before == source_after, 'Audit checker source changed during execution'
    report = {'passed': all(v['exit'] == 0 for v in results.values()), 'checked_utc': datetime.now(timezone.utc).isoformat(),
              'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'working_tree_clean': not bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
              'checker_source_sha256': source_before, 'checker_source_unchanged': True,
              'snapshot_sha256': sha256(snapshot_path.read_bytes()).hexdigest(), 'scored_rows': len(rows), 'original_source_points': 100,
              'source_columns_unchanged': False, 'source_text_preserved_in_snapshots': True, 'presentation_language': 'en',
              'english_translations_pinned': True, 'llm_scored_rows': len(llm_rows), 'llm_source_points': 100,
              'llm_inference_verified': results['llm-inference']['exit'] == 0,
              'llm_grounding_verified': results['llm-grounding']['exit'] == 0,
              'rag_orchestration_verified': results['rag-orchestration']['exit'] == 0,
              'mcp_tools_verified': results['mcp-tools']['exit'] == 0,
              'agent_platform_foundation_verified': results['agent-platform']['exit'] == 0,
              'agent_drift_answer_verified': results['agent-response-baseline']['exit'] == 0,
              'direct_specialist_mcp_egress_verified': results['agent-network']['exit'] == 0,
              'agent_worker_autoscaling_verified': results['agent-scaling']['exit'] == 0,
              'native_agent_notebook_verified': results['agent-notebook']['exit'] == 0,
              'native_agent_startup_comparison_verified': results['agent-warmup']['exit'] == 0,
              'llm_runtime_verified': False, 'coursework_complete': False,
              'local_evidence_links': links, 'additional_capsule_payloads': capsules, 'verifiers': results,
              'live_readiness_checked': False, 'scope': 'Recorded ML, inference/RAG/MCP, GPU routing, strict mutation audits and real specialist/coordinator conversations plus both rubric presentations. The full agent track remains incomplete. This is not a grade, current live readiness, full100GB ML or laptop verification.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    sys.exit(audit(args.output, args.workers))
