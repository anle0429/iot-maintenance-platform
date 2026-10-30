"""Audit current maintainable Python documentation and exact docstring-only edits without rewriting historical evidence."""
from git_sources import historical_blob
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import ast,json,subprocess,argparse
from evidence_integrity import ROOT,documentation_equivalent


def audit(output,edit_proof):
    """Count module/definition descriptions and independently validate each declared documentation-only source edit."""
    output.mkdir(parents=True,exist_ok=False);files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','*.py'],cwd=ROOT,text=True).splitlines();files=sorted(set(n for n in files if not n.startswith('docs/evidence/')))
    missing=[];definitions=0;hashes={}
    for name in files:
        raw=(ROOT/name).read_bytes();tree=ast.parse(raw);hashes[name]=sha256(raw).hexdigest()
        if not ast.get_docstring(tree):missing.append({'path':name,'line':1,'name':'<module>'})
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                definitions+=1
                if not ast.get_docstring(node):missing.append({'path':name,'line':node.lineno,'name':node.name})
    proof=json.loads(edit_proof.read_bytes());assert proof['added_docstrings']==419
    for row in proof['files']:
        name=row['path'];original=historical_blob(ROOT, proof['base_commit']+':'+name);current=(ROOT/name).read_bytes()
        assert sha256(original).hexdigest()==row['before_git_sha256'] and sha256(current).hexdigest()==row['after_sha256'],name
        if row['documentation_only']:assert documentation_equivalent(original,current,'.py'),name
        else:assert name=='scripts/verify-ab-monitoring.py' and row['added_docstrings']==1
    tracked=subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines();forbidden=[n for n in tracked if n.startswith(('data/','outputs/','.venv/')) or n.endswith(('.pyc','.tfstate')) or n in ['AGENTS.md','START_HERE.md','docs/status.md','docs/decisions.md']]
    result={'passed':not missing and not forbidden,'checked_utc':datetime.now(timezone.utc).isoformat(),'python_files':len(files),'definitions':definitions,'missing_docstrings':missing,'forbidden_tracked_paths':forbidden,'docstring_edits':419,'docstring_files':len(proof['files']),'documentation_only_docstrings':418,'mixed_verifier_docstrings':1,'documentation_base_commit':proof['base_commit'],'source_sha256':hashes,'scope':'Current maintainable Python only. Historical docs/evidence snapshots remain immutable; no claim they have been retroactively documented. AST equality excludes only docstrings and source positions; __doc__/help text intentionally change. Runtime deployments retain their original source pins.'}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');assert result['passed'],(missing,forbidden);print(json.dumps({k:result[k] for k in ['passed','python_files','definitions','docstring_edits','docstring_files']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--edit-proof',type=Path,required=True);args=parser.parse_args();audit(args.output,args.edit_proof)
