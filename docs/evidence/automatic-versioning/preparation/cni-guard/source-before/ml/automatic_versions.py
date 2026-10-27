"""Attach incremental dataset publication and candidate registration to the existing five ML stages."""
import argparse,json,os,runpy,shutil,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
from ml.training_contract import digest,save
from ml.model_data_link import verify_source_snapshot

ROOT=Path(__file__).resolve().parents[1]


def context(path):
    """Refuse unbound execution bytes before a fresh pull or registry side effect."""
    value=json.loads(Path(path).read_text());verify_source_snapshot(value,ROOT,value['commit'])
    if value.get('tracking_uri')!='http://tracking:5000':raise ValueError('Use the prepared private registry service')
    return value


def load_and_version(args):
    """Publish the exact fresh pull before allowing the downstream split to consume its descriptor."""
    binding=context(args.source_binding)
    from ml.notebook_load import run as native_load
    from ml.canonical_dataset import run as canonicalize
    from ml.mlflow_data_versions import MlflowDatasetPublisher
    base=args.workspace;lineage=base/'lineage';lineage.mkdir(parents=True,exist_ok=False);lineage.chmod(0o2775)
    native_load(SimpleNamespace(**{**vars(args),'workspace':base/'input'}))
    cohort=lineage/'cohort.json';shutil.copyfile(args.output,cohort)
    canonical=SimpleNamespace(cohort=cohort,protocol=args.protocol,destination=lineage/'canonical-dataset',output=lineage/'canonical.json')
    canonicalize(canonical)
    versioner=runpy.run_path(str(ROOT/'scripts/version-feast-dataset.py'))['run']
    versioner(SimpleNamespace(canonical_root=canonical.destination,canonical_report=canonical.output,store=Path('/data-cas'),
        output=lineage/'local-version.json',parent_version=binding.get('parent_data_version'),through_day=None,restore_to=None))
    local=json.loads((lineage/'local-version.json').read_text())
    remote=MlflowDatasetPublisher('/data-cas',binding['tracking_uri']).publish(local['version'],lineage/'remote-restore')
    save(lineage/'remote-version.json',remote)
    save(lineage/'automatic-data.json',{'passed':True,'data_version':local['version'],'data_run_id':remote['data_run_id'],
        'source_snapshot_commit':binding['commit'],'native_descriptor_sha256':digest(cohort),
        'native_report_sha256':json.loads(cohort.read_text())['report_sha256'],
        'new_local_blob_bytes':local['new_blob_bytes'],'new_remote_blob_payload_bytes':remote['new_blob_payload_bytes'],
        'remote_restore':remote['remote_restore'],'scope':'Automatically completed inside the fresh Feast load stage, before typed output publication.'})


def validation_gate(selection,verification):
    """Block evaluation and registration when validation belongs to a different or already-open selection."""
    value=json.loads(Path(verification).read_text())
    if not value['passed'] or value['holdout_rows_opened']!=0 or value['selection_sha256']!=digest(selection):
        raise ValueError('Saved-model validation must seal this exact selection before holdout and registration')


def evaluate_and_register(args):
    """Run the unchanged Spark evaluation, then register model-only artifacts linked to this pull's data version."""
    binding=context(args.source_binding);lineage=args.workspace/'lineage'
    validation_gate(args.selection,args.verification)
    shutil.copyfile(args.verification,lineage/'validation-reload.json')
    if not json.loads((lineage/'automatic-data.json').read_text())['passed']:raise ValueError('Dataset publication is incomplete')
    native=lineage/'evaluation.json'
    command=['/opt/spark/bin/spark-submit','--master','local[2]','--driver-memory','6g',
        '--conf','spark.driver.maxResultSize=128m','--conf','spark.local.dir=/ml-scratch',
        str(ROOT/'ml/cluster_training_job.py'),'--step','evaluate','--workspace',str(args.workspace),
        '--input',str(args.selection),'--output',str(native),'--protocol',str(args.protocol)]
    subprocess.run(command,check=True)
    # The optional registry SDK directory is isolated from the Spark process's Python path.
    environment={**os.environ,'PYTHONPATH':'/opt/registry-sdk:'+str(ROOT),'MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING':'false'}
    command=[sys.executable,'-m','ml.register_incremental_model','--evaluation',str(native),
        '--canonical-report',str(lineage/'canonical.json'),'--local-version',str(lineage/'local-version.json'),
        '--remote-version',str(lineage/'remote-version.json'),'--protocol',str(args.protocol),
        '--output',str(lineage/'registration.json'),'--source-binding',str(args.source_binding),
        '--source-commit',binding['commit'],'--tracking-uri',binding['tracking_uri'],'--run',args.workspace.name]
    subprocess.run(command,env=environment,check=True)
    # Keep kernel evaluation bytes immutable; registration has a separate lineage receipt.
    if args.output!=native:shutil.copyfile(native,args.output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--step',choices=['load','evaluate'],required=True)
    for n in ['workspace','output','protocol','source-binding']:parser.add_argument('--'+n,type=Path,required=True)
    for n in ['state','artifacts','batch-report','feature-report','selection','verification']:parser.add_argument('--'+n,type=Path)
    parser.add_argument('--executor-input');args=parser.parse_args()
    (load_and_version if args.step=='load' else evaluate_and_register)(args)
    if args.executor_input:
        from ml.kfp_artifacts import publish
        publish(args.executor_input,'cohort' if args.step=='load' else 'evaluation',args.output)
