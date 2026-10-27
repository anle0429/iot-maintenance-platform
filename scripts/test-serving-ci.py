"""Run actual Linux API coverage/mutation gates in a fresh bounded CI container and retain every result."""
import argparse,json,shutil,subprocess,sys
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.serving_ci import test_gate


def run(output):
    """Never reuse mutation caches or count timed-out/skipped mutants as killed."""
    if (ROOT/'mutants').exists():raise ValueError('CI requires a fresh mutation workspace')
    output.mkdir(parents=True,exist_ok=True)
    (output/'requirements-installed.lock').write_text(subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True))
    def call(command,name,timeout):
        with (output/name).open('w') as stream:
            subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    call([sys.executable,'-m','pytest','tests/test_serving_ci.py','tests/test_serving_release.py','tests/test_ab_ci.py','-q'],'contracts.txt',120)
    call([sys.executable,'-m','pytest','tests/api/test_predictor.py','tests/api/test_feature_api.py','tests/api/test_model_binding.py','tests/api/test_ab_api.py',
        '-q','--cov=services.predictor','--cov=services.feature_api','--cov=services.ab_api','--cov-branch','--cov-report=json:'+str(output/'coverage.json'),
        '--cov-report=html:'+str(output/'coverage-html'),
        '--junitxml='+str(output/'junit.xml')],'api-tests.txt',180)
    suites=list(ElementTree.parse(output/'junit.xml').iter('testsuite'))
    if not suites or sum(int(s.attrib.get('tests',0)) for s in suites)<1 or any(
            int(s.attrib.get(k,0)) for s in suites for k in ['errors','failures','skipped']):
        raise ValueError('Every native serving API test must actually execute and pass')
    call(['mutmut','run','--max-children','2'],'mutmut-console.txt',2400)
    call(['mutmut','results','--all'],'mutmut-all-results.txt',60)
    call(['mutmut','export-cicd-stats'],'mutmut-export.txt',60)
    stats=ROOT/'mutants/mutmut-cicd-stats.json';shutil.copyfile(stats,output/'mutmut-cicd-stats.json')
    proof=test_gate(json.loads((output/'coverage.json').read_text()),json.loads(stats.read_text()),(output/'mutmut-all-results.txt').read_text())
    proof['result_sha256']={name:sha256((output/name).read_bytes()).hexdigest() for name in ['coverage.json','mutmut-cicd-stats.json','mutmut-all-results.txt','junit.xml']}
    (output/'verification.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps({'passed':True,'mutation_score_percent':proof['mutation_score_percent']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);run(parser.parse_args().output)
