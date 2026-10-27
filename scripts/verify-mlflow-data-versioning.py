"""Exercise real MLflow incremental publication/restoration and corruption rejection on a separate tiny JSON fixture."""
import argparse,json,sys,tempfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.data_versions import ContentAddressedDatasetStore,file_digest
from ml.mlflow_data_versions import MlflowDatasetPublisher
from ml.training_contract import save,digest


def run(args):
    """Keep the oracle in its own experiment/store and restore deliberately corrupted test bytes afterwards."""
    args.workspace.mkdir(parents=True,exist_ok=False)
    source=args.workspace/'source';source.mkdir();a=source/'a.json';b=source/'b.json';c=source/'c.json'
    a.write_text('{"reading":1}\n');b.write_text('{"reading":2}\n')
    store=ContentAddressedDatasetStore(args.workspace/'content-store')
    metadata={'protocol_sha256':'known-answer-json-fixture-not-native-feast','purpose':'Incremental storage oracle; no model training or IoT quality claim'}
    publisher=MlflowDatasetPublisher(store.root,args.tracking_uri,'iot-data-version-oracles')
    first=store.publish({'day=1/a.json':a,'day=2/b.json':b},metadata)
    remote_first=publisher.publish(first['version'],args.workspace/'restore-first')
    b.write_text('{"reading":3}\n');c.write_text('{"reading":4}\n')
    second=store.publish({'day=2/b.json':b,'day=3/c.json':c},metadata,first['version'])
    remote_second=publisher.publish(second['version'],args.workspace/'restore-second')
    replay=publisher.publish(second['version'],args.workspace/'restore-replay')
    assert remote_first['new_blob_count']==remote_second['new_blob_count']==2
    assert replay['new_blob_count']==replay['new_blob_payload_bytes']==replay['new_metadata_payload_bytes']==0
    assert replay['data_run_id']==remote_second['data_run_id'] and replay['existing_version_reconciliation']
    for name,expected in [('restore-first',{'day=1/a.json':'{"reading":1}\n','day=2/b.json':'{"reading":2}\n'}),
        ('restore-second',{'day=2/b.json':'{"reading":3}\n','day=3/c.json':'{"reading":4}\n'})]:
        actual={p.relative_to(args.workspace/name/'dataset').as_posix():p.read_text() for p in (args.workspace/name/'dataset').rglob('*') if p.is_file()}
        if actual!=expected:raise ValueError('Known-answer remote snapshot restoration differs')
    # Deliberately corrupt one blob only in this freshly created oracle experiment, then restore it.
    h=next(iter(remote_first['blob_locations']));location=remote_first['blob_locations'][h];client=publisher.client
    owner=client.get_run(location['run_id']);experiment=client.get_experiment(owner.info.experiment_id)
    assert experiment.name=='iot-data-version-oracles' and owner.data.tags['kind']=='dataset_version_not_model_training'
    rejected=False
    try:
        with tempfile.TemporaryDirectory() as directory:
            corrupt=Path(directory)/h;corrupt.write_text('{"deliberately_corrupted_oracle_only":true}\n')
            client.log_artifact(location['run_id'],str(corrupt),str(Path(location['artifact_path']).parent).replace('\\','/'))
        try:publisher.publish(first['version'],args.workspace/'must-not-restore')
        except ValueError as error:
            if 'Remote blob differs byte-for-byte' not in str(error):raise
            rejected=True
    finally:
        client.log_artifact(location['run_id'],str(store.blob(h)),str(Path(location['artifact_path']).parent).replace('\\','/'))
    with tempfile.TemporaryDirectory() as directory:
        recovered=Path(client.download_artifacts(location['run_id'],location['artifact_path'],directory))
        assert file_digest(recovered)==h
    if not rejected or (args.workspace/'must-not-restore').exists():raise ValueError('Remote corruption was not rejected before reconstruction')
    save(args.output,{'passed':True,'checks':{'two_versions_only_store_changed_blobs':True,'unchanged_replay_no_new_run_or_payload':True,
        'full_remote_snapshots_restore_exact_known_answers':True,'remote_corrupt_fixture_rejected_before_restore':True,'original_fixture_blob_restored_exact':True},
        'first':remote_first,'second':remote_second,'replay':replay,'code_sha256':digest(Path(__file__)),
        'publisher_code_sha256':digest(ROOT/'ml/mlflow_data_versions.py'),
        'scope':'Tiny synthetic JSON fixture in separate oracle experiment/store, actual MLflow HTTP/artifacts. Corruption targets only newly created fixture blobs and is repaired; no native data/model modified.'})
    print(json.dumps({'passed':True,'two_data_run_ids':[remote_first['data_run_id'],remote_second['data_run_id']],'replay_reuses_second':True,'remote_corruption_rejected_and_fixture_repaired':True}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--tracking-uri',required=True)
    run(parser.parse_args())
