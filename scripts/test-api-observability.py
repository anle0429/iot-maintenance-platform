"""Test the new telemetry wrapper in fresh scratch: regress all three business APIs and mutate only the changed module."""
from pathlib import Path,PurePosixPath
from hashlib import sha256
from xml.etree import ElementTree
import argparse,json,os,re,shutil,subprocess,sys


def validate_mutation(stats,text):
    """Require a complete strict one-module inventory, including Unicode class identities and a score above80%."""
    keys=['killed','survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']
    if any(type(stats.get(k)) is not int or stats[k]<0 for k in keys+['total']):raise ValueError('Incomplete mutation status counts')
    if stats['total']<=0 or sum(stats[k] for k in keys)!=stats['total'] or any(stats[k] for k in keys[2:]):raise ValueError('Every mutant must finish as killed or survived')
    rows=re.findall(r'^\s*(\S+): (killed|survived)\s*$',text,re.M)
    identity=r'services\.observed\.x(?:_[A-Za-z_]\w*|ǁ[A-Za-z_]\w*ǁ[A-Za-z_]\w*)__mutmut_[1-9][0-9]*'
    if (len(rows)!=stats['total'] or len({n for n,_ in rows})!=stats['total']
            or any(re.fullmatch(identity,n) is None for n,_ in rows) or sum(s=='killed' for _,s in rows)!=stats['killed']):raise ValueError('Full one-module mutation inventory differs')
    if stats['killed']/stats['total']<=.8:raise ValueError('Changed wrapper mutation score must exceed80%')
    return 100*stats['killed']/stats['total']


def regression_counts(path):
    """Validate a flat native JUnit report; distinguish physical cases from the suite count including subtests."""
    root=ElementTree.parse(path).getroot()
    suites=list(root.iter('testsuite'));cases=list(root.iter('testcase'))
    if not suites or any(list(s.iter('testsuite'))!=[s] for s in suites):raise ValueError('Use flat native JUnit suites')
    counts=[int(s.attrib.get('tests',0)) for s in suites]
    if (not cases or any(n<=0 for n in counts) or sum(counts)<len(cases)
            or any(int(s.attrib.get(k,0)) for s in suites for k in ['errors','failures','skipped'])
            or any(c.find(k) is not None for c in cases for k in ['error','failure','skipped'])):
        raise ValueError('Every native regression test must execute and pass')
    return {'regression_main_testcase_elements':len(cases),'regression_suite_reported_tests':sum(counts)}


def run(source,manifest,workspace,output):
    """Preserve sealed sources and previous results; never reuse a partial or completed mutation cache."""
    seal=json.loads(manifest.read_bytes());workspace.mkdir(parents=True,exist_ok=False);output.mkdir(parents=True,exist_ok=False)
    for name,wanted in seal['files'].items():
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts:raise ValueError('Invalid source path')
        raw=(source/name).read_bytes()
        if sha256(raw).hexdigest()!=wanted:raise ValueError('Changed sealed source: '+name)
        target=workspace/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    shutil.copyfile(workspace/'infra/observability/mutmut.toml',workspace/'pyproject.toml')
    env={**os.environ,'PYTHONPATH':str(workspace),'PYTHONUNBUFFERED':'1','HOME':'/tmp'}
    def call(args,name,timeout):
        with (output/name).open('x') as stream:subprocess.run(args,cwd=workspace,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    (output/'requirements-installed.lock').write_text(subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True))
    call([sys.executable,'-m','pytest','tests/test_api_observability_gate.py',*[str(Path('tests/api')/('test_'+n+'.py')) for n in ['observed','feature_api','predictor','model_binding','ab_api','drift_api']],
        '-q','--cov=services.observed','--cov-branch','--cov-report=json:'+str(output/'coverage.json'),
        '--cov-report=html:'+str(output/'coverage-html'),'--junitxml='+str(output/'junit.xml')],'api-tests.txt',180)
    counts=regression_counts(output/'junit.xml')
    coverage=json.loads((output/'coverage.json').read_bytes());c=coverage['totals']
    if (set(coverage['files'])!={'services/observed.py'} or c['num_statements']<=0 or c['num_branches']<=0 or c['excluded_lines']!=0
            or c['covered_lines']/c['num_statements']<=.9 or c['covered_branches']/c['num_branches']<=.9):raise ValueError('Wrapper statement and branch coverage must exceed90% with no exclusions')
    try:call(['mutmut','run','--max-children','2'],'mutmut-console.txt',3000)
    finally:
        meta=workspace/'mutants/services/observed.py.meta'
        if meta.exists():shutil.copyfile(meta,output/'observed.py.meta')
    call(['mutmut','results','--all','true'],'mutmut-all-results.txt',60)
    call(['mutmut','export-cicd-stats'],'mutmut-export.txt',60)
    stats=json.loads((workspace/'mutants/mutmut-cicd-stats.json').read_bytes());(output/'mutmut-cicd-stats.json').write_text(json.dumps(stats,indent=2)+'\n')
    score=validate_mutation(stats,(output/'mutmut-all-results.txt').read_text())
    proof={'passed':True,'commit':seal['commit'],'coverage':c,'mutation':stats,'mutation_score_percent':score,
        'mutation_files':['services/observed.py'],**counts,
        'scope':'New telemetry wrapper only. Unchanged feature/predictor/AB/drift modules run regression tests; earlier serving mutation proof is separate.'}
    (output/'verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['source','manifest','workspace','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run(args.source,args.manifest,args.workspace,args.output)
