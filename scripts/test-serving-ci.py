"""Run actual Linux API coverage/mutation gates in a fresh bounded CI container and retain every result."""
import argparse,json,shutil,subprocess,sys
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.serving_ci import MUTATION_FILES,test_gate
from ml.serving_gate_reuse import completed_mutation


def checkpoint_mutation_metadata(output):
    """Preserve raw native statuses for diagnosis even if a later CLI export fails; never use a partial cache as a gate."""
    inventory={}
    for name in sorted(MUTATION_FILES):
        source=ROOT/'mutants'/(name+'.meta')
        if source.is_file():
            target=output/'mutation-metadata'/(name+'.meta');target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target);raw=target.read_bytes()
            inventory[name]={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
    (output/'mutation-metadata-manifest.json').write_text(json.dumps({'files':inventory,
        'scope':'Raw diagnostic checkpoint only; incomplete/interrupted statuses never satisfy the native gate.'},indent=2)+'\n')


def run(output,parent_outputs=None,parent_source=None,current_source=None,expected_pin=None):
    """Never reuse mutation caches or count timed-out/skipped mutants as killed."""
    if (ROOT/'mutants').exists():raise ValueError('CI requires a fresh mutation workspace')
    if any(x is not None for x in [parent_outputs,parent_source,current_source,expected_pin]) and not all(x is not None for x in [parent_outputs,parent_source,current_source,expected_pin]):
        raise ValueError('A complete explicit native mutation parent binding is required')
    output.mkdir(parents=True,exist_ok=True)
    (output/'requirements-installed.lock').write_text(subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True))
    def call(command,name,timeout):
        with (output/name).open('w') as stream:
            subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    call([sys.executable,'-m','pytest','tests/test_serving_ci.py','tests/test_serving_release.py','tests/test_ab_ci.py','tests/test_serving_gate_reuse.py','-q'],'contracts.txt',120)
    call([sys.executable,'-m','pytest','tests/api/test_predictor.py','tests/api/test_feature_api.py','tests/api/test_model_binding.py','tests/api/test_ab_api.py',
        '-q','--cov=services.predictor','--cov=services.feature_api','--cov=services.ab_api','--cov-branch','--cov-report=json:'+str(output/'coverage.json'),
        '--cov-report=html:'+str(output/'coverage-html'),
        '--junitxml='+str(output/'junit.xml')],'api-tests.txt',180)
    suites=list(ElementTree.parse(output/'junit.xml').iter('testsuite'))
    if not suites or sum(int(s.attrib.get('tests',0)) for s in suites)<1 or any(
            int(s.attrib.get(k,0)) for s in suites for k in ['errors','failures','skipped']):
        raise ValueError('Every native serving API test must actually execute and pass')
    provenance=None
    if parent_outputs is not None:
        provenance=completed_mutation(parent_source,current_source,parent_outputs,output/'requirements-installed.lock',json.loads(expected_pin.read_bytes()))
        parent=parent_outputs/'tests';retained=output/'mutation-parent';retained.mkdir()
        for name in ['mutmut-all-results.txt','mutmut-cicd-stats.json']:
            shutil.copyfile(parent/name,output/name)
        for name in ['verification.json','coverage.json','junit.xml','requirements-installed.lock','mutmut-console.txt','mutmut-export.txt']:
            shutil.copyfile(parent/name,retained/name)
        for source,label in [(parent_source,'source-parent.json'),(current_source,'source-current.json')]:shutil.copyfile(source,retained/label)
        (output/'mutation-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    else:
        try:call(['mutmut','run','--max-children','2'],'mutmut-console.txt',3000)
        finally:checkpoint_mutation_metadata(output)
        call(['mutmut','results','--all','true'],'mutmut-all-results.txt',60)
        call(['mutmut','export-cicd-stats'],'mutmut-export.txt',60)
        shutil.copyfile(ROOT/'mutants/mutmut-cicd-stats.json',output/'mutmut-cicd-stats.json')
    stats=output/'mutmut-cicd-stats.json'
    proof=test_gate(json.loads((output/'coverage.json').read_text()),json.loads(stats.read_text()),(output/'mutmut-all-results.txt').read_text())
    proof['result_sha256']={name:sha256((output/name).read_bytes()).hexdigest() for name in ['coverage.json','mutmut-cicd-stats.json','mutmut-all-results.txt','junit.xml']}
    if provenance is not None:
        proof['mutation_provenance']=provenance
    (output/'verification.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps({'passed':True,'mutation_score_percent':proof['mutation_score_percent'],
        'mutation_origin':provenance['parent_release'] if provenance else 'fresh-native-run'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    for name in ['parent-outputs','parent-source','current-source','expected-pin']:parser.add_argument('--'+name,type=Path)
    args=parser.parse_args();run(args.output,args.parent_outputs,args.parent_source,args.current_source,args.expected_pin)
