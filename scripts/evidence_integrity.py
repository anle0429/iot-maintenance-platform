"""Check exact native capsule payload bytes and historical Git source bindings without executing evidence."""
from pathlib import Path
from hashlib import sha256
import json,subprocess,ast
ROOT=Path(__file__).resolve().parents[1]


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
    for n,h in seal['files'].items():assert sha256(subprocess.check_output(['git','show',seal['commit']+':'+n],cwd=ROOT)).hexdigest()==h,n
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
    """Accept exact bytes or Python edits confined to docstrings; preserve all other AST nodes."""
    if original==current:return True
    if suffix!='.py':return False
    try:return ast.dump(executable_python_tree(original),include_attributes=False)==ast.dump(executable_python_tree(current),include_attributes=False)
    except (SyntaxError,ValueError):return False


def current_source_bindings(seal):
    """First verify immutable as-run Git bytes, then allow only explicitly documented docstring changes in current Python."""
    source_bindings(seal)
    for name in seal['files']:
        original=subprocess.check_output(['git','show',seal['commit']+':'+name],cwd=ROOT);path=ROOT/name
        assert documentation_equivalent(original,path.read_bytes(),path.suffix),name
    return len(seal['files'])
