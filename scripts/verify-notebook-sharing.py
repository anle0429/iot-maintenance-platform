"""Exercise real Feast/Spark user permissions through NotebookRuntime's bounded Docker launcher on tiny fixtures."""
import argparse,json,os,re,subprocess,sys
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml import notebook_runtime
from ml.training_contract import digest,save


def run(args):
    """Check either the historical rejection or shared-group writes without loading training data or opening holdout."""
    if os.name!='posix' or os.geteuid()!=0 or not re.fullmatch('[a-z][a-z0-9-]{2,38}',args.run):raise ValueError('Use root on the prepared Linux VM and a fresh safe name')
    base=ROOT/'data/notebook-sharing-oracles'/args.run;base.mkdir(parents=True,exist_ok=False)
    runtime=object.__new__(notebook_runtime.NotebookRuntime);runtime.run=args.run
    runtime.root=base/'source';runtime.workspace=base/'dataset';runtime.output=ROOT/'outputs/notebook-sharing'/args.run
    runtime.scratch=Path('/srv/iot-fast/notebook-sharing')/args.run;runtime.deadline=datetime.fromisoformat(args.deadline_utc.replace('Z','+00:00'))
    for p in [runtime.root/'data/feast',runtime.root/'data/data-versions/native-feast-27/cas',runtime.workspace,runtime.output,runtime.scratch]:p.mkdir(parents=True,exist_ok=True);p.chmod(0o1777)
    lineage=runtime.workspace/'lineage';lineage.mkdir();os.chown(lineage,10001,10001);lineage.chmod(0o2775)
    runtime.images={name:subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip() for name,tag in
        [('feast','iot-coursework-feast-versioned:0.67.0-3.17.2'),('spark','iot-coursework-spark-registry:3.5.9-3.17.2')]}
    facts={}
    for name in (['spark'] if args.expect_rejection else ['feast','spark']):
        code='import os,json;from pathlib import Path;p=Path("/ml-data/lineage/'+name+'.json");p.write_text(json.dumps({"uid":os.getuid(),"gid":os.getgid(),"groups":os.getgroups()}))'
        rejected=False
        try:runtime.container(name,name,['python3','-c',code])
        except RuntimeError:
            if not args.expect_rejection:raise
            rejected=True;assert 'Permission denied' in (runtime.output/(name+'-console.txt')).read_text()
        state=json.loads(subprocess.check_output(['docker','inspect',runtime.run+'-'+name],text=True))[0]
        facts[name]={'image':state['Image'],'configured_user':state['Config']['User'],'group_add':state['HostConfig'].get('GroupAdd'),
            'exit_code':state['State']['ExitCode'],'expected_rejection':args.expect_rejection,'rejected':rejected}
        if args.expect_rejection:assert rejected and state['State']['ExitCode']!=0
        else:
            value=json.loads((lineage/(name+'.json')).read_text());facts[name]['native_identity']=value
            assert 10001 in value['groups'] and (lineage/(name+'.json')).stat().st_gid==10001
    save(runtime.output/'verification.json',{'passed':True,'expected_rejection':args.expect_rejection,'facts':facts,
        'script_sha256':digest(Path(__file__)),'runtime_sha256':digest(Path(notebook_runtime.__file__)),
        'scope':'Tiny native filesystem oracle only. Script and runtime hashes are separate; no Feast pull, model training, holdout access or production deployment.'})
    print(json.dumps({'passed':True,'expected_rejection':args.expect_rejection,'users':list(facts)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--deadline-utc',required=True)
    parser.add_argument('--expect-rejection',action='store_true');run(parser.parse_args())
