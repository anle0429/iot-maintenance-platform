"""Run fresh native regression, coverage and mutation for the CloudEvent HTTP change from sealed source."""
from pathlib import Path,PurePosixPath
from hashlib import sha256
from xml.etree import ElementTree
import argparse,json,os,re,shutil,subprocess,sys

FILES={'services/drift_api/app.py','services/drift_api/events.py'}


def validate(coverage,stats,text):
    """Account for every mutant and require strict statement/branch and mutation thresholds."""
    totals=coverage['totals']
    if (set(coverage['files'])!=FILES or totals['num_statements']<=0 or totals['num_branches']<=0 or totals['excluded_lines']!=0
            or totals['covered_lines']/totals['num_statements']<=.9 or totals['covered_branches']/totals['num_branches']<=.9):raise ValueError('Changed HTTP modules need coverage>90% without exclusions')
    keys=['killed','survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']
    if (any(type(stats.get(k)) is not int or stats[k]<0 for k in keys+['total']) or stats['total']<=0
            or sum(stats[k] for k in keys)!=stats['total'] or any(stats[k] for k in keys[2:])):raise ValueError('Every mutant must finish')
    rows=re.findall(r'^\s*(\S+): (killed|survived)\s*$',text,re.M)
    found=set()
    for name,_ in rows:
        match=re.fullmatch(r'(services\.drift_api\.(?:app|events))\.x(?:_[A-Za-z_]\w*|ǁ[A-Za-z_]\w*ǁ[A-Za-z_]\w*)__mutmut_[1-9][0-9]*',name)
        if match is None:raise ValueError('Unexpected mutation identity')
        found.add(match[1].replace('.','/')+'.py')
    if (found!=FILES or len(rows)!=stats['total'] or len({n for n,_ in rows})!=stats['total']
            or sum(s=='killed' for _,s in rows)!=stats['killed'] or stats['killed']/stats['total']<=.8):raise ValueError('Mutation inventory/score differs')
    return 100*stats['killed']/stats['total']


def run(source,manifest,workspace,output):
    """Preserve original results; use an empty scratch workspace and no inherited mutation cache."""
    seal=json.loads(manifest.read_bytes());workspace.mkdir(parents=True,exist_ok=False);output.mkdir(parents=True,exist_ok=False)
    for name,wanted in seal['files'].items():
        p=PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts:raise ValueError('Invalid source path')
        raw=(source/name).read_bytes()
        if sha256(raw).hexdigest()!=wanted:raise ValueError('Changed sealed file')
        target=workspace/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    shutil.copyfile(workspace/'infra/eventing/mutmut.toml',workspace/'pyproject.toml')
    env={**os.environ,'PYTHONPATH':str(workspace),'HOME':'/tmp'}
    def call(args,name,timeout):
        with (output/name).open('x') as stream:subprocess.run(args,cwd=workspace,env=env,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    (output/'requirements-installed.lock').write_text(subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True))
    call([sys.executable,'-m','pytest','tests/api/test_drift_api.py','tests/api/test_drift_events.py','tests/api/test_observed.py','tests/test_drift_eventing.py','tests/test_drift_eventing_gate.py',
        '-q','--cov=services.drift_api.app','--cov=services.drift_api.events','--cov-branch','--cov-report=json:'+str(output/'coverage.json'),
        '--cov-report=html:'+str(output/'coverage-html'),'--junitxml='+str(output/'junit.xml')],'api-tests.txt',180)
    suites=list(ElementTree.parse(output/'junit.xml').getroot().iter('testsuite'))
    if not suites or any(int(s.attrib.get(k,0)) for s in suites for k in ['errors','failures','skipped']):raise ValueError('Native regressions must all execute and pass')
    call(['mutmut','run','--max-children','2'],'mutmut-console.txt',3000)
    call(['mutmut','results','--all','true'],'mutmut-all-results.txt',60);call(['mutmut','export-cicd-stats'],'mutmut-export.txt',60)
    stats=json.loads((workspace/'mutants/mutmut-cicd-stats.json').read_bytes());(output/'mutmut-cicd-stats.json').write_text(json.dumps(stats,indent=2)+'\n')
    coverage=json.loads((output/'coverage.json').read_bytes());score=validate(coverage,stats,(output/'mutmut-all-results.txt').read_text())
    proof={'passed':True,'commit':seal['commit'],'coverage':coverage['totals'],'mutation':stats,'mutation_files':sorted(FILES),
        'mutation_score_percent':score,'regression_main_testcase_elements':len(list(ElementTree.parse(output/'junit.xml').getroot().iter('testcase'))),
        'regression_suite_reported_tests':sum(int(s.attrib['tests']) for s in suites)}
    (output/'verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['source','manifest','workspace','output']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();run(a.source,a.manifest,a.workspace,a.output)
