"""Run all default mutations against the sealed adapter in a disposable copy, retaining every outcome."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import json,os,shutil,subprocess,re
root=Path('/srv/iot-data/workspace/iot-maintenance-platform');tooling=root/'outputs/model-control-quality-29a';out=root/'outputs/model-control-quality-29d';out.mkdir(exist_ok=False)
source=root/'data/llm-source/model-control-29e/tree/tools/model-control'
seal=json.loads((root/'data/llm-source/model-control-29e/manifest.json').read_bytes())
# source.parents[2] is the sealed packet root, above tree/.
for name in ['go.mod','main.go','main_test.go']:
 assert sha256((source/name).read_bytes()).hexdigest()==seal['files']['tools/model-control/'+name]
work=out/'work';shutil.copytree(source,work)
(out/'tested-source.json').write_text(json.dumps({n:sha256((work/n).read_bytes()).hexdigest() for n in ['go.mod','main.go','main_test.go']},indent=2)+'\n')
env={**os.environ,'PATH':str(tooling/'go/bin')+':'+os.environ['PATH'],'GOTELEMETRY':'off','GOTOOLCHAIN':'local','GOPATH':str(tooling/'gopath'),'GOBIN':str(tooling/'bin'),'GOCACHE':str(tooling/'cache'),'GOFLAGS':'-p=2','GOMAXPROCS':'2','CGO_ENABLED':'0','IOT_GO':str(tooling/'go/bin/go'),'IOT_MUTATION_WORKDIR':str(work),'IOT_MUTATION_RESULTS':str(out/'mutant-results'),'IOT_MUTATION_SOURCE_SHA256':seal['files']['tools/model-control/main.go'],'IOT_MUTATION_MIN_TESTS':'14'}
def command(name,args,timeout):
 with (out/(name+'.stdout')).open('wb') as stdout,(out/(name+'.stderr')).open('wb') as stderr:
  r=subprocess.run(args,cwd=work,env=env,stdout=stdout,stderr=stderr,timeout=timeout)
 (out/(name+'.exit')).write_text(str(r.returncode)+'\n');return r.returncode
assert command('tests',[str(tooling/'go/bin/go'),'test','-json','-coverprofile='+str(out/'coverage.out'),'.'],300)==0
assert command('coverage',[str(tooling/'go/bin/go'),'tool','cover','-func',str(out/'coverage.out')],30)==0
config=out/'mutation.yaml';config.write_text('skip_without_test: false\nskip_with_build_tags: false\njson_output: true\nhtml_output: true\nsilent_mode: false\n')
code=command('mutation',[str(tooling/'bin/go-mutesting'),'--config='+str(config),'--exec=/tmp/iot-go-mutation-exec.py','--exec-timeout=12','--do-not-remove-tmp-folder','main.go'],3200)
for name in ['report.json','go-mutesting-report.html']:
 if (work/name).exists():shutil.copyfile(work/name,out/name)
for name in ['go.mod','main.go','main_test.go']:
 assert sha256((source/name).read_bytes()).hexdigest()==seal['files']['tools/model-control/'+name]
report={'terminal':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'mutation_exit':code,'all_default_mutators':True,'excluded_functions':[],'mutated_file':'main.go','deployed_source_unchanged':True,'strict_test_receipts':True,'exec_source_sha256':sha256(Path('/tmp/iot-go-mutation-exec.py').read_bytes()).hexdigest(),'coursework_complete':False}
(out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
