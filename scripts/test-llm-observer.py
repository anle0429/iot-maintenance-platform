"""Run fresh native regression, full-package coverage and strict mutation checks for the new observation API."""
from hashlib import sha256
from pathlib import Path,PurePosixPath
import argparse,json,os,re,shutil,subprocess,sys

MODULES={'llm_observability/'+n+'.py' for n in ['contracts','ledger','metrics','private_traces','app']}
TESTS=['tests/test_llm_observation_contracts.py','tests/test_llm_telemetry_ledger.py','tests/test_llm_observation_metrics.py',
       'tests/test_llm_private_traces.py','tests/test_llm_observer_properties.py','tests/api/test_llm_observer_api.py']


def mutation_gate(stats,text,raw_codes):
    """Keep all mutant outcomes in the denominator and require a complete normal test inventory above eighty percent."""
    categories=['killed','survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']
    if any(type(stats.get(k)) is not int or stats[k]<0 for k in categories+['total']):raise ValueError('Incomplete mutation outcome counts')
    if stats['total']<=0 or sum(stats[k] for k in categories)!=stats['total']:raise ValueError('Mutation outcome total differs')
    if any(stats[k] for k in categories[2:]):raise ValueError('Retain unresolved mutation outcomes; do not count them as failing-test kills')
    rows=re.findall(r'^\s*(\S+): (killed|survived)\s*$',text,re.M)
    if len(rows)!=stats['total'] or len({n for n,_ in rows})!=stats['total']:raise ValueError('Incomplete original mutation inventory')
    if set(raw_codes)!={name for name,_ in rows}:raise ValueError('Raw mutation exits do not cover the complete inventory')
    for name,status in rows:
        code=raw_codes[name]
        if type(code) is not int or code not in {0,1} or status!=('killed' if code==1 else 'survived'):
            raise ValueError('Only normal failing-test exit one counts as a kill; retain engine or harness errors separately')
    wanted={n.removesuffix('.py').replace('/','.') for n in MODULES};actual=set()
    for name,status in rows:
        prefix=next((module for module in wanted if name.startswith(module+'.')),None)
        if prefix is None:raise ValueError('Mutation outside the declared new five-module scope')
        actual.add(prefix)
    if actual!=wanted or sum(status=='killed' for _,status in rows)!=stats['killed']:raise ValueError('Mutation scope/count mismatch')
    score=100*stats['killed']/stats['total']
    if score<=80:raise ValueError('Fresh observer mutation score must exceed eighty percent')
    return score


def run(args):
    """Copy authenticated source into new writable scratch; never reuse completed or partial mutation caches."""
    source=args.source.resolve();seal=json.loads(args.manifest.read_bytes());workspace=args.workspace.resolve();out=args.output.resolve()
    assert seal['format']=='iot-llm-observer-source-v1'
    workspace.mkdir(parents=True,exist_ok=False);out.mkdir(parents=True,exist_ok=False)
    for name,digest in seal['files'].items():
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe source identity')
        raw=(source/name).read_bytes()
        if sha256(raw).hexdigest()!=digest:raise ValueError('Changed sealed observer input')
        target=workspace/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    shutil.copyfile(workspace/'infra/llm-observability/mutmut.toml',workspace/'pyproject.toml')
    env={**os.environ,'PYTHONPATH':str(workspace),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1','NO_COLOR':'1',
         'COVERAGE_FILE':str(out/'.coverage'),'HYPOTHESIS_STORAGE_DIRECTORY':'/tmp/hypothesis'}
    def call(command,name,timeout):
        """Keep the original bounded console and exact exit status for each independently reviewable quality phase."""
        with (out/name).open('x') as stream:
            result=subprocess.run(command,cwd=workspace,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        (out/(name+'.exit')).write_text(str(result.returncode)+'\n')
        if result.returncode:raise RuntimeError('Observer quality phase failed: '+name)
    (out/'requirements-installed.lock').write_bytes(subprocess.check_output([sys.executable,'-m','pip','freeze']))
    call([sys.executable,'-B','-m','pytest','tests/test_llm_observer_quality.py','-q'],'quality-contracts.txt',30)
    call([sys.executable,'-B','-m','coverage','run','--branch','--source=llm_observability','-m','pytest',*TESTS,'-q',
          '--hypothesis-show-statistics','--junitxml='+str(out/'junit.xml')],'tests.txt',180)
    call([sys.executable,'-B','-m','coverage','json','-o',str(out/'coverage.json')],'coverage-json.txt',30)
    call([sys.executable,'-B','-m','coverage','html','-d',str(out/'coverage-html')],'coverage-html.txt',30)
    coverage=json.loads((out/'coverage.json').read_bytes());totals=coverage['totals']
    if set(coverage['files'])!=MODULES|{'llm_observability/__init__.py'} or totals['excluded_lines']!=0:raise ValueError('Full observer coverage scope changed')
    if totals['num_statements']<=0 or totals['num_branches']<=0 or totals['covered_lines']/totals['num_statements']<=.9 or totals['covered_branches']/totals['num_branches']<=.9:
        raise ValueError('Observer statement and branch coverage must both exceed ninety percent')
    (out/'coverage-gate.json').write_text(json.dumps({'passed':True,'source_commit':seal['commit'],'totals':totals,'modules':sorted(MODULES)},indent=2)+'\n')
    try:call(['mutmut','run','--max-children','4'],'mutmut-console.txt',6900)
    finally:
        folder=workspace/'mutants/llm_observability'
        if folder.exists():
            for path in folder.rglob('*.meta'):
                target=out/'mutation-meta'/path.relative_to(folder);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    call(['mutmut','results','--all','true'],'mutmut-all-results.txt',90)
    call(['mutmut','export-cicd-stats'],'mutmut-export.txt',60)
    stats_path=workspace/'mutants/mutmut-cicd-stats.json';stats=json.loads(stats_path.read_bytes());shutil.copyfile(stats_path,out/'mutmut-cicd-stats.json')
    raw_codes={}
    for path in (out/'mutation-meta').rglob('*.meta'):
        native=json.loads(path.read_bytes())
        if native.get('type_check_error_by_key'):raise ValueError('Type-check outcomes are not failing-test kills')
        for name,code in native['exit_code_by_key'].items():
            if name in raw_codes:raise ValueError('Repeated raw mutant identity')
            raw_codes[name]=code
    (out/'mutation-raw-exits.json').write_text(json.dumps(raw_codes,indent=2,ensure_ascii=True)+'\n')
    score=mutation_gate(stats,(out/'mutmut-all-results.txt').read_text(),raw_codes)
    report={'passed':True,'commit':seal['commit'],'source_files':len(seal['files']),'coverage':totals,'mutation':stats,
            'strict_mutation_score_percent':score,'fresh_mutation_run':True,'mutation_modules':sorted(MODULES),
            'scope':'All five new runtime observer modules. Original tests, complete native engine inventory and non-test outcomes are preserved. No cached mutation reuse or deployment claim.'}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['source','manifest','workspace','output']:parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
