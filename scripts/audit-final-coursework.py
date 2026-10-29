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
SOURCE_SNAPSHOT_SHA256 = '37ae3f47b05035d159bdb7ea2987ac9e83b60577e4103428b68e80bd77ff0914'
VERIFIERS = (
    'ab-monitoring', 'active-feature-refresh', 'api-observability', 'ci-api-observability',
    'ci-stream-push', 'ci-training', 'datahub-restore', 'deployment-policy', 'drift-eventing',
    'drift-retraining', 'final-handover', 'focused-api', 'host-monitoring', 'iac-final',
    'joint-cohort', 'linux-host', 'linux-logs', 'log-discovery', 'native-serving',
    'observed-api-load', 'offline-drift', 'private-gateway', 'private-mesh', 'vault', 'window-cohort', 'final-review',
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
    return address, snapshot['cells'][address]['value']


def rubric_rows(snapshot, text):
    """Reject missing, duplicated or altered scored rows against the original workbook-cell snapshot."""
    assert snapshot['weighted_rows'] == 57 and snapshot['source_point_total'] == 100
    rows = {}
    for line in text.splitlines():
        if not re.match(r'\| F\d+ \|', line):
            continue
        columns = [part.strip() for part in re.split(r'(?<!\\)\|', line)[1:-1]]
        key = columns[0]
        assert key not in rows and len(columns) == 8, key
        rows[key] = columns
    assert set(rows) == {f'F{i:02}' for i in range(3, 60)}, 'Missing or unexpected final rows'
    points = 0
    for number in range(3, 60):
        key = f'F{number:02}'; current = rows[key]
        _, category = cell(snapshot, f'A{number}')
        _, subgroup = cell(snapshot, f'B{number}')
        _, detail = cell(snapshot, f'C{number}')
        requirement_column = 'C' if detail else 'B'
        requirement_address, requirement = cell(snapshot, requirement_column + str(number))
        deliverable_address, deliverable = cell(snapshot, f'D{number}')
        weight = float(cell(snapshot, f'E{number}')[1]); points += weight
        group = ' / '.join(str(value) for value in [category, subgroup if detail and normalize(subgroup) != normalize(detail) else None] if value)
        expected = [key, f'{requirement_address}; {deliverable_address}; E{number}', group, requirement, deliverable, f'{weight:g}']
        assert [normalize(v) for v in current[:6]] == [normalize(v) for v in expected], f'Original rubric changed: {key}'
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
    links = 0
    for key, columns in rows.items():
        for target in re.findall(r'\]\(([^)]+)\)', columns[-1]):
            if '://' in target:
                continue
            path = (ROOT / 'docs' / target.split('#')[0]).resolve()
            assert path.is_relative_to(ROOT) and path.is_file(), (key, target)
            links += 1
    capsules = {name: capsule_manifest(ROOT, name) for name in CAPSULES}
    checkers = [ROOT / 'scripts' / f'verify-{name}.py' for name in VERIFIERS] + [Path(__file__).resolve(), ROOT / 'scripts/evidence_integrity.py']
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
              'source_columns_unchanged': True, 'local_evidence_links': links, 'additional_capsule_payloads': capsules, 'verifiers': results,
              'live_readiness_checked': False, 'scope': 'Historical artifact integrity and independent numerical checks on PC. Not a grade, VM deployment, full100GB ML or laptop runtime verification.'}
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    sys.exit(audit(args.output, args.workers))
