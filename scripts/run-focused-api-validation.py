"""Run the declared boundary/partition/property subset and retain original pytest statistics plus JUnit."""
from pathlib import Path
from datetime import datetime,timezone
from hashlib import sha256
import argparse,json,os,re,subprocess,sys,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]


def run(output):
    """Preserve all failures and source identity; this focused receipt never substitutes for a full quality gate."""
    output=output.resolve();output.mkdir(parents=True,exist_ok=False);profile_path=ROOT/'tests/api/focused-validation.json';profile=json.loads(profile_path.read_bytes())
    assert profile['format']=='iot-api-focused-validation-v1' and profile['hypothesis_seed']==20261028
    assert len(profile['selectors'])==14 and all(re.fullmatch(r'tests/api/test_[a-z_]+\.py::test_[a-z_]+',v) for v in profile['selectors'])
    env=dict(os.environ);env['HYPOTHESIS_STORAGE_DIRECTORY']=str(output/'hypothesis');env['PYTHONDONTWRITEBYTECODE']='1'
    args=[sys.executable,'-m','pytest','-c','/dev/null' if os.name=='posix' else 'NUL','--rootdir='+str(ROOT),'-vv','--hypothesis-show-statistics','--hypothesis-seed='+str(profile['hypothesis_seed']),'--junitxml='+str(output/'junit.xml'),'-o','cache_dir='+str(output/'pytest-cache'),*profile['selectors']]
    result=subprocess.run(args,cwd=ROOT,env=env,capture_output=True);(output/'console.stdout').write_bytes(result.stdout);(output/'console.stderr').write_bytes(result.stderr)
    if result.returncode:raise RuntimeError('Focused API checks failed; inspect original console/JUnit')
    suite=ET.parse(output/'junit.xml');cases=suite.findall('.//testcase');assert cases and all(c.find('failure') is None and c.find('error') is None and c.find('skipped') is None for c in cases)
    text=result.stdout.decode();stats={}
    for section in re.split(r'\n(?=tests/api/)',text):
        match=re.search(r'^(tests/api/[^\n]+):\s*\n',section)
        values=re.search(r'(\d+) passing, (\d+) failing, and (\d+) invalid test cases',section)
        if match and values:stats[match.group(1)]={'passing':int(values[1]),'failing':int(values[2]),'invalid':int(values[3])}
    assert len(stats)==4 and sum(v['passing'] for v in stats.values())==220 and all(v['failing']==0 for v in stats.values())
    proof={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'profile_sha256':sha256(profile_path.read_bytes()).hexdigest(),'junit_testcases':len(cases),'hypothesis_seed':profile['hypothesis_seed'],'hypothesis_statistics':stats,'hypothesis_passing_examples':220,'scope':profile['scope']};(output/'verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);run(parser.parse_args().output)
