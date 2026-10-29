"""Measure native MCP API coverage and all mutants in the changed API modules without touching deployed images or state."""
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re
import shutil
import subprocess
import sys


def run(source, manifest, workspace, output, configuration):
    """Copy sealed code into fresh scratch, run real test/mutation processes and retain unsuccessful outcomes too."""
    seal=json.loads(manifest.read_bytes());workspace.mkdir(parents=True,exist_ok=False);output.mkdir(parents=True,exist_ok=False)
    for name,digest in seal['files'].items():
        path=(source/name).resolve()
        if not path.is_relative_to(source.resolve()) or sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Source seal differs')
        target=workspace/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    shutil.copyfile(configuration,workspace/'pyproject.toml')
    env={**os.environ,'PYTHONPATH':str(workspace),'PYTHONDONTWRITEBYTECODE':'1','HOME':'/tmp','HYPOTHESIS_STORAGE_DIRECTORY':'/tmp/hypothesis-mutation'}
    (output/'source.json').write_text(json.dumps(seal,indent=2)+'\n')
    (output/'mutmut.toml').write_bytes(configuration.read_bytes())
    (output/'requirements-installed.lock').write_bytes(subprocess.check_output([sys.executable,'-m','pip','freeze']))
    def command(label, values, timeout=300):
        """Preserve stdout/stderr and exit status, including a timed-out process rather than marking it successful."""
        result=subprocess.run(values,cwd=workspace,env=env,capture_output=True,timeout=timeout)
        (output/(label+'.stdout')).write_bytes(result.stdout);(output/(label+'.stderr')).write_bytes(result.stderr)
        (output/(label+'.exit')).write_text(str(result.returncode)+'\n')
        return result.returncode
    code=command('tests',[sys.executable,'-m','coverage','run','--branch','--source=services.mcp_tools','--omit=*/probe.py',
        '-m','pytest','tests/mcp','-q','--hypothesis-seed=20261029','--hypothesis-show-statistics','--junitxml='+str(output/'junit.xml')])
    if code:raise RuntimeError('Native regression tests failed')
    for kind, arguments in [('json',['json','-o',str(output/'coverage.json')]),('html',['html','-d',str(output/'coverage-html')]),('xml',['xml','-o',str(output/'coverage.xml')])]:
        if command('coverage-'+kind,[sys.executable,'-m','coverage']+arguments):raise RuntimeError('Native coverage export failed')
    if json.loads((output/'coverage.json').read_bytes())['totals']['percent_covered']<=90:raise ValueError('Combined API coverage must exceed90%')
    code=command('mutmut',['mutmut','run','--max-children','2'],timeout=3000)
    for path in (workspace/'mutants/services/mcp_tools').glob('*.meta'):
        shutil.copyfile(path,output/path.name)
    command('mutation-inventory',['mutmut','results','--all','true'])
    command('mutation-export',['mutmut','export-cicd-stats'])
    stats=json.loads((workspace/'mutants/mutmut-cicd-stats.json').read_bytes())
    (output/'mutation-stats.json').write_text(json.dumps(stats,indent=2)+'\n')
    categories=['killed','survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']
    if any(type(stats.get(name)) is not int or stats[name]<0 for name in categories+['total']):raise ValueError('Incomplete mutation inventory')
    rows=re.findall(r'^\s*(\S+): (\w+)\s*$',(output/'mutation-inventory.stdout').read_text(),re.M)
    declared=[row for row in rows if row[0].startswith('services.mcp_tools.')]
    complete=stats['total']>0 and sum(stats[name] for name in categories)==stats['total'] and len(declared)==stats['total']
    terminal=not any(stats[name] for name in categories[2:])
    score=100*stats['killed']/stats['total'] if stats['total'] else 0
    result={'passed':complete and terminal and score>80,'mutmut_exit':code,'complete_inventory':complete,'terminal_killed_or_survived_only':terminal,
        'mutation_score_percent':score,'stats':stats,'mutated_files':['auth.py','contracts.py','runtime.py','app.py'],
        'scope':'All generated mutants in the four changed MCP Web API modules. Acceptance probe and deployment init are not Web API mutation targets.'}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if not result['passed']:raise RuntimeError('Inspect surviving or incomplete native mutants; do not weaken the denominator')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['source','manifest','workspace','output','configuration']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run(args.source,args.manifest,args.workspace,args.output,args.configuration)
