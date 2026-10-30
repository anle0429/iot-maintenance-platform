"""Reconcile original MCP CI measurements, official report exports and unedited technique/design captures."""
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO
from pathlib import PurePosixPath
import ast
import json
import sqlite3
import tarfile
import xml.etree.ElementTree as ET
from git_sources import git_blobs
from integration_evidence import ROOT, capsule, read


class SourceLines(HTMLParser):
    """Recover displayed source text from official coverage HTML, including nested highlighting spans."""

    def __init__(self):
        """Keep source-span nesting separate from the surrounding report markup."""
        super().__init__(); self.depth = 0; self.parts = []; self.lines = []

    def handle_starttag(self, tag, attrs):
        """Begin one coverage source line or descend into syntax highlighting."""
        if tag == 'span' and dict(attrs).get('class') == 't':
            assert not self.depth; self.depth = 1; self.parts = []
        elif self.depth: self.depth += 1

    def handle_endtag(self, tag):
        """Finish only the outer source span, retaining original code indentation."""
        if self.depth:
            self.depth -= 1
            if not self.depth: self.lines.append(''.join(self.parts).replace('\xa0', ' ').rstrip())

    def handle_data(self, data):
        """Collect actual visible source characters without adding report labels."""
        if self.depth: self.parts.append(data)


def archive_files(base):
    """Authenticate the untouched native archive and every safe inventoried member in memory."""
    raw = (base/'native-receipts.tar.gz').read_bytes()
    assert len(raw) == 159797 and sha256(raw).hexdigest() == '0f9feddfbd88b15f9ca15682415b43f2f097eb084ca9d5cfb9d1372c75bac2b6'
    files = {}
    with tarfile.open(fileobj=BytesIO(raw)) as archive:
        for member in archive:
            if member.isdir(): continue
            assert member.isfile() and member.size < 10000000
            name = PurePosixPath(member.name).relative_to('mcp-quality-view-capture-30c')
            assert not name.is_absolute() and '..' not in name.parts and name.as_posix() not in files
            files[name.as_posix()] = archive.extractfile(member).read()
    assert sha256(files['manifest.json']).hexdigest() == '8bc9ece63208bc5a43ff9321059ea5ca5f1afb5ed7fbce37d0adc90dcea3608c'
    inventory = json.loads(files['manifest.json'])
    assert len(inventory['files']) == 89 and set(files) == set(inventory['files']) | {'manifest.json'}
    for name, pin in inventory['files'].items():
        assert len(files[name]) == pin['bytes'] and sha256(files[name]).hexdigest() == pin['sha256']
        assert b'-----BEGIN PRIVATE KEY-----' not in files[name]
    meta = read(base, 'native-archive.json')
    assert meta['current_native_credentials_scanned'] and meta['vault_root_and_unseal_scanned'] and meta['pc_actual_gpu_join_credential_scanned']
    assert not any(meta[k] for k in ['recompressed_or_rewritten_on_pc', 'tests_rerun_for_capture', 'mutations_rerun_for_capture'])
    return files


def verify():
    """Check historical source identities, independent coverage arithmetic, real test cases and seven native views."""
    base, count = capsule('mcp-quality-ui', 'dbe8a6cf10a47ddbf40f2029781583a2dd090c0ee163f78365e3025fd89a8c31')
    assert count == 42
    files = archive_files(base)

    def native(name):
        """Decode only authenticated original JSON bytes."""
        return json.loads(files[name])

    seal = native('source/manifest.json'); assert len(seal['files']) == 375
    commit = 'beb7f5c7dbec14e8429a0877c87eb17e6c901b7b'; assert seal['commit'] == commit
    source_digest = sha256(json.dumps(seal, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert source_digest == 'ac070f3fe76baca92a82b50a4aa1e7675771221879d89c6df921bdec3f9b2cc9'
    committed = git_blobs(ROOT, commit, seal['files'])
    for name, pin in seal['files'].items(): assert sha256(committed[name]).hexdigest() == pin, name
    image = native('export/test-image.json'); assert len(image['packaged_sha256']) == 24 and image['revision_label'] == commit
    for name, pin in image['packaged_sha256'].items():
        original = files['source/tree/'+name]
        assert original == committed[name] and sha256(original).hexdigest() == pin
        # The retained catalog has CRLF; compare canonical text on both sides, never rewrite original evidence.
        assert (ROOT/name).read_bytes().replace(b'\r\n', b'\n') == original.replace(b'\r\n', b'\n'), name
    index = read(base, 'source-index.json'); assert index['source_commit'] == commit and index['image_id'] == image['image_id']
    for alias, pin in index['files'].items():
        name = alias.removesuffix('.txt'); original = files['export/source/'+name]
        assert (base/'source'/alias).read_bytes() == original == files['export/source/'+alias]
        assert sha256(original).hexdigest() == pin
        path = 'services/mcp_tools/app.py' if name == 'app.py' else 'tests/mcp/'+name
        assert committed[path] == original

    export = native('export/verification.json'); assert export == read(base, 'report-export.json')
    assert export['passed'] and export['report_totals_and_files_unchanged'] and not export['tests_rerun'] and not export['mutations_rerun']
    assert export['image_id'] == image['image_id'] == 'sha256:66754a484ca4ab76d907b0f9200975bd0a3047b60599867c489afe63844cb875'
    for name in ['.coverage', 'coverage.json', 'junit.xml']:
        assert files['export/'+name] == files['original-ci/api-tests/'+name]
        if name != '.coverage': assert (base/name).read_bytes() == files['export/'+name]
    assert sha256(files['export/.coverage']).hexdigest() == export['source_database_sha256'] == '40c85987ed77b250f640b6a5ab12d882810eebda079e5f9f4812fe3373d4ed4f'
    measured = native('export/coverage.json'); reexport = native('export/coverage-export.json')
    assert measured['totals'] == reexport['totals'] == export['original_totals'] and measured['files'] == reexport['files']
    assert measured['meta']['timestamp'] == '2026-10-30T02:46:07.486813' and measured['meta']['version'] == '7.17.0'
    totals = measured['totals']
    assert [totals[k] for k in ['covered_lines', 'num_statements', 'covered_branches', 'num_branches', 'excluded_lines']] == [349, 356, 98, 108, 0]
    for key, numerator, denominator in [('percent_statements_covered', 349, 356), ('percent_branches_covered', 98, 108), ('percent_covered', 447, 464)]:
        assert abs(totals[key]-100*numerator/denominator) < 1e-12
    database = sqlite3.connect(':memory:')
    try:
        database.deserialize(files['export/.coverage']); assert database.execute('pragma integrity_check').fetchone() == ('ok',)
        rows = list(database.execute('select id,path from file')); assert len(rows) == len(measured['files']) == 6
        assert database.execute('select count(*) from arc').fetchone()[0] == 546
        for file_id, path in rows:
            name = path.removeprefix('/workspace/'); result = measured['files'][name]
            arc_lines = {line for row in database.execute('select fromno,tono from arc where file_id=?', (file_id,)) for line in row if line > 0}
            # Raw arcs include non-statements such as module docstrings; JSON filters these with coverage.py's source analysis.
            assert set(result['executed_lines']) <= arc_lines
            assert not set(result['executed_lines']) & set(result['missing_lines'])
            assert len(result['executed_lines']) == result['summary']['covered_lines']
            assert len(result['missing_lines']) == result['summary']['missing_lines']
    finally: database.close()
    for name, original in files.items():
        if not name.startswith('export/html/') or name.endswith('/.gitignore'): continue
        assert (base/'report'/name.removeprefix('export/html/')).read_bytes() == original
        if name.endswith('_py.html'):
            parser = SourceLines(); parser.feed(original.decode())
            module = name.split('/')[-1].removeprefix('z_66e154b85a9f1067_').removesuffix('_py.html')+'.py'
            assert '\n'.join(parser.lines).strip() == committed['services/mcp_tools/'+module].decode().strip()
    container = native('export/container.json')
    assert not container['state']['Running'] and not container['state']['OOMKilled'] and container['state']['ExitCode'] == 0
    assert container['image'] == image['image_id'] and container['network'] == 'none' and container['memory'] == 536870912 and container['user'] == '10001:10001'

    cases = list(ET.fromstring(files['export/junit.xml']).iter('testcase'))
    assert len(cases) == 132 and all(not any(c.tag in ['failure', 'error', 'skipped'] for c in case) for case in cases)
    names = {case.attrib['name'] for case in cases}
    expected = {'test_cohort_replay_is_independent_of_input_order'}
    expected |= {f'test_question_length_boundaries[{n}-{status}]' for n, status in [(4, 422), (5, 200), (1000, 200), (1001, 422)]}
    assert expected <= names
    app = ast.parse(files['export/source/app.py']); factory = next(node for node in app.body if isinstance(node, ast.FunctionDef) and node.name == 'create_app')
    assert [v.arg for v in factory.args.args] == ['runtime', 'role', 'token']
    assert {24, 28, 48, 130} <= set(measured['files']['services/mcp_tools/app.py']['executed_lines'])
    tests = files['export/source/test_tools.py'].decode()
    for fragment in ['@pytest.fixture', 'httpx2.MockTransport(handle)', "create_app(runtime,'retrieval',TOKEN)", '@settings(max_examples=12,deadline=None)', "@given(st.permutations", "['stored_windows']==1"]: assert fragment in tests, fragment

    ci = native('original-ci/test.json'); contracts = native('original-ci/contracts.json')
    assert read(base, 'ci-test.json') == ci and read(base, 'ci-mutation-cache.json') == contracts == ci['contracts']
    assert ci['passed'] and ci['source_commit'] == commit and contracts['source_digest'] == source_digest and ci['fresh_api']['tests'] == 132
    cache = contracts['unchanged_mcp_quality_cache']
    assert not cache['fresh_mutation_run'] and cache['counts'] == {'killed': 681, 'survived': 167, 'timeout': 1, 'incompetent': 0, 'worker_error': 1, 'no_test': 0, 'skipped': 0, 'pending': 0}
    assert sum(cache['counts'].values()) == 850 and cache['strict_mutation_score_percent'] == 100*681/850 > 80
    for name, pin in cache['source_bindings'].items():
        raw = (ROOT/'docs/evidence'/name/'source/manifest.json').read_bytes()
        assert sha256(raw).hexdigest() == pin['manifest_sha256'] and len(json.loads(raw)['files']) == pin['files']
    assert native('jenkins/build.json')['result'] == 'SUCCESS' and not native('jenkins/build.json')['building']
    assert files['jenkins/test.json'] == files['original-ci/test.json'] and files['jenkins/contracts.json'] == files['original-ci/contracts.json']
    assert b'217/USER' in files['export/view-failed-journal.txt']
    for fragment in ['User=nobody', 'Group=nogroup', 'ProtectSystem=strict', 'NoNewPrivileges=yes', 'RuntimeMaxUSec=1h', 'ActiveState=active']:
        assert fragment.encode() in files['current-view-unit.txt']

    ui = read(base, 'ui/index.json'); assert len(ui['captures']) == 7 and not ui['screenshots_edited']
    for row in ui['captures']:
        assert row['unedited'] and row['format'] == 'JPEG'
        raw = (base/'ui'/ (row['name']+'.jpg')).read_bytes(); assert raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9')
        snapshot = (base/'ui'/(row['name']+'.ax.txt')).read_text(encoding='utf-8')
        if row['name'] in ['ci-quality', 'mutation-cache']:
            actual = json.JSONDecoder().raw_decode(snapshot[snapshot.index('{'):])[0]
            assert actual == (ci if row['name'] == 'ci-quality' else contracts)
        elif row['name'] in ['fixtures-mocks', 'partitions-boundaries', 'property-replay']:
            assert tests.strip() in snapshot
        elif row['name'] == 'application-factory': assert 'def create_app' in snapshot and 'runtime' in snapshot
        else: assert '96%' in snapshot and '356' in snapshot
    return {'passed': True, 'native_payloads': 89, 'sealed_sources': 375, 'current_packaged_texts': 24, 'tests': 132,
            'statements': '349/356', 'branches': '98/108', 'strict_cached_mutations': '681/850', 'original_captures': 7,
            'scope': 'Historical successful CI and later official report/source views; no new tests, mutations or business workloads.'}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
