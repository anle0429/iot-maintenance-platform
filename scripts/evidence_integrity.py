"""Check exact native capsule payload bytes and historical Git source bindings without executing evidence."""
from pathlib import Path
from hashlib import sha256
import json,subprocess,ast,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.git_sources import git_blobs


def verified(base,count,native_count):
    """Require exactly the manifest-listed safe paths and compare all original byte lengths and digests."""
    read=lambda n:json.loads((base/n).read_bytes())
    manifest=read('manifest.json');files={p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file() and p!=base/'manifest.json'};assert files==set(manifest['files']) and len(files)==count
    for n,v in manifest['files'].items():
        assert not Path(n).is_absolute() and '..' not in Path(n).parts
        raw=(base/n).read_bytes();assert len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'] and b'PRIVATE KEY-----' not in raw,n
    native=read('native-manifest.json');assert len(native['files'])==native_count and all(manifest['files'][n]==v for n,v in native['files'].items())
    return read


def source_bindings(seal):
    """Verify every declared source/input against its immutable Git blob; return the checked count."""
    blobs=git_blobs(ROOT,seal['commit'],seal['files'])
    for n,h in seal['files'].items():assert sha256(blobs[n]).hexdigest()==h,n
    return len(seal['files'])


def executable_python_tree(source):
    """Parse Python and remove only recognized docstrings for documentation-aware historical comparisons."""
    class StripDocumentation(ast.NodeTransformer):
        """Remove only the leading string expression that Python recognizes as a docstring."""
        def visit_Module(self,node):
            """Visit nested definitions and retain every non-docstring statement and expression."""
            self.generic_visit(node)
            if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):node.body=node.body[1:]
            return node
        visit_FunctionDef=visit_Module;visit_AsyncFunctionDef=visit_Module;visit_ClassDef=visit_Module
    return StripDocumentation().visit(ast.parse(source))


def documentation_equivalent(original,current,suffix):
    """Accept exact bytes, Python docstrings or notebook Markdown-only edits; preserve executable state."""
    if original==current:return True
    if suffix=='.ipynb':
        try:
            left=json.loads(original);right=json.loads(current)
            for document in [left,right]:
                for cell in document['cells']:
                    if cell['cell_type']=='markdown':cell['source']=[]
            return left==right
        except (ValueError,KeyError,TypeError):return False
    if suffix!='.py':return False
    try:return ast.dump(executable_python_tree(original),include_attributes=False)==ast.dump(executable_python_tree(current),include_attributes=False)
    except (SyntaxError,ValueError):return False


def dashboard_labels_equivalent(original,current,name):
    """Allow only six declared display-title translations in one known dashboard, never metric/config changes."""
    if name!='infra/observability/grafana-dashboard.json':return False
    labels={
        'T\u1ed5ng y\u00eau c\u1ea7u theo API':'Requests by API',
        'Y\u00eau c\u1ea7u m\u1ed7i gi\u00e2y':'Requests per second',
        'Ph\u1ea3n h\u1ed3i 4xx v\u00e0 5xx':'HTTP 4xx and 5xx responses',
        'P95 th\u1eddi gian x\u1eed l\u00fd':'P95 request duration',
        'Trung b\u00ecnh th\u1eddi gian x\u1eed l\u00fd':'Mean request duration',
        'S\u1ed1 y\u00eau c\u1ea7u theo route v\u00e0 status':'Requests by route and status'}
    try:
        left=json.loads(original);right=json.loads(current)
        for document in [left,right]:
            for panel in document['panels']:
                if panel.get('title') in labels:panel['title']=labels[panel['title']]
        return left==right
    except (ValueError,KeyError,TypeError):return False


def current_source_bindings(seal):
    """First verify immutable as-run Git bytes, then allow only explicitly documented docstring changes in current Python."""
    source_bindings(seal)
    for name in seal['files']:
        original=subprocess.check_output(['git','show',seal['commit']+':'+name],cwd=ROOT);path=ROOT/name
        current=path.read_bytes()
        assert documentation_equivalent(original,current,path.suffix) or dashboard_labels_equivalent(original,current,name),name
    return len(seal['files'])
