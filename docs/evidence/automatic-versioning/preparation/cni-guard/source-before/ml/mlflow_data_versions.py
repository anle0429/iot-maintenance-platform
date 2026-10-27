"""Publish only new dataset blobs to MLflow, referencing previous completed data runs for unchanged bytes."""
import argparse,json,tempfile,uuid,shutil
from hashlib import sha256
from datetime import datetime,timezone
from pathlib import Path
import mlflow
from mlflow.tracking import MlflowClient
from ml.data_versions import ContentAddressedDatasetStore,canonical_bytes,file_digest
from ml.training_contract import digest,save


def atomic_index(path,value):
    """Keep a recoverable local pointer index; content hashes and remote readback establish correctness."""
    pending=path.with_suffix('.pending');pending.write_bytes(canonical_bytes(value));pending.replace(path)


class MlflowDatasetPublisher:
    """Deduplicate data across MLflow runs while keeping each dataset version independently restorable."""
    def __init__(self,store,tracking_uri,experiment_name='iot-feast-data-versions'):
        """Use the existing tracking server; require an explicitly separate content-addressed local store."""
        self.store=ContentAddressedDatasetStore(store);self.experiment_name=experiment_name
        mlflow.set_tracking_uri(tracking_uri);self.client=MlflowClient()

    def publish(self,version,restore_to):
        """Seal a version only after every remote blob matches; retry the same pending context without duplicate runs."""
        with self.store.locked():
            manifest=self.store.manifest(version);manifest_path=self.store.root/'manifests'/(version+'.json')
            index_path=self.store.root/'mlflow-index.json'
            index=json.loads(index_path.read_text()) if index_path.exists() else {'store_id':uuid.uuid4().hex,'experiment_name':self.experiment_name,'blobs':{},'versions':{},'pending':{}}
            if index['experiment_name']!=self.experiment_name:raise ValueError('Preserve the dataset store experiment binding')
            identity=index['store_id'];client=self.client;root='dataset-version'
            existing=index['versions'].get(version);pending=index['pending'].get(version)
            experiment=client.get_experiment_by_name(self.experiment_name)
            experiment_id=experiment.experiment_id if experiment else client.create_experiment(self.experiment_name)
            if existing or pending:
                run_id=(existing or pending)['run_id'];prior=client.get_run(run_id)
                if prior.data.tags.get('cas_store_id')!=identity or prior.data.tags.get('data_version')!=version:
                    raise ValueError('Remote data run belongs to another store/version')
                if existing and prior.info.status!='FINISHED':raise ValueError('Published dataset run is no longer finished')
            else:
                created=client.create_run(experiment_id,tags={'mlflow.runName':'feast-data-'+version[:12],
                    'cas_store_id':identity,'data_version':version,'kind':'dataset_version_not_model_training',
                    'protocol_sha256':manifest['metadata']['protocol_sha256']})
                run_id=created.info.run_id;index['pending'][version]={'run_id':run_id};atomic_index(index_path,index)
            uploaded=[];metadata_bytes=0;locations={};seen={}
            try:
                for item in manifest['files'].values():
                    h=item['sha256']
                    if h in locations:continue
                    local=self.store.blob(h)
                    if local.is_symlink() or file_digest(local)!=h or local.stat().st_size!=item['size']:
                        raise ValueError('Local blob changed before remote publication')
                    location=index['blobs'].get(h)
                    if location is None:
                        prefix='blobs/'+h[:2]
                        if prefix not in seen:seen[prefix]={x.path for x in client.list_artifacts(run_id,prefix)}
                        remote_path=prefix+'/'+h
                        if remote_path not in seen[prefix]:
                            client.log_artifact(run_id,str(local),prefix);uploaded.append({'sha256':h,'bytes':item['size']})
                            seen[prefix].add(remote_path)
                        location={'run_id':run_id,'artifact_path':remote_path}
                    owner=client.get_run(location['run_id'])
                    if owner.data.tags.get('cas_store_id')!=identity:raise ValueError('Blob locator refers to a different dataset store')
                    with tempfile.TemporaryDirectory() as directory:
                        restored=Path(client.download_artifacts(location['run_id'],location['artifact_path'],directory))
                        if file_digest(restored)!=h or restored.stat().st_size!=item['size']:raise ValueError('Remote blob differs byte-for-byte')
                    locations[h]=location;index['blobs'][h]=location;atomic_index(index_path,index)
                location_document={'format':'iot-mlflow-blob-locations-v1','data_version':version,'store_id':identity,'blobs':locations}
                location_bytes=canonical_bytes(location_document)
                downloaded_metadata={}
                with tempfile.TemporaryDirectory() as directory:
                    directory=Path(directory);m=directory/'manifest.json';m.write_bytes(manifest_path.read_bytes())
                    loc=directory/'locations.json';loc.write_bytes(location_bytes)
                    remote_names={x.path for x in client.list_artifacts(run_id,root)}
                    for path in [m,loc]:
                        expected_bytes=path.read_bytes()
                        if root+'/'+path.name not in remote_names:
                            if existing:raise ValueError('Completed dataset version is missing immutable metadata')
                            client.log_artifact(run_id,str(path),root);metadata_bytes+=path.stat().st_size
                        fetched=Path(client.download_artifacts(run_id,root+'/'+path.name,str(directory)))
                        downloaded_metadata[path.name]=fetched.read_bytes()
                        if downloaded_metadata[path.name]!=expected_bytes:raise ValueError('Remote version metadata changed')
                location_hash=sha256(location_bytes).hexdigest()
                if existing and existing['locations_sha256']!=location_hash:raise ValueError('Blob locator map differs from the sealed version')
                # Reconstruct from the remote references into a fresh store. No local input blob is used here.
                restore_to=Path(restore_to);restore_to.mkdir(parents=True,exist_ok=False)
                fetched_store=ContentAddressedDatasetStore(restore_to/'downloaded-content-store')
                if sha256(downloaded_metadata['manifest.json']).hexdigest()!=version:raise ValueError('Remote manifest identity differs')
                (fetched_store.root/'manifests'/(version+'.json')).write_bytes(downloaded_metadata['manifest.json'])
                downloaded_bytes=0
                remote_locations=json.loads(downloaded_metadata['locations.json'])['blobs']
                for h,location in remote_locations.items():
                    with tempfile.TemporaryDirectory() as directory:
                        fetched=Path(client.download_artifacts(location['run_id'],location['artifact_path'],directory))
                        target=fetched_store.blob(h);target.parent.mkdir(exist_ok=True)
                        # Copy only remote-downloaded bytes into the clean restoration store.
                        with target.open('xb') as stream,fetched.open('rb') as source:
                            shutil.copyfileobj(source,stream,1024*1024)
                        if file_digest(target)!=h:raise ValueError('Remote restore content differs')
                        downloaded_bytes+=target.stat().st_size
                restoration=fetched_store.restore(version,restore_to/'dataset')
                if not existing:
                    for key,value in {'locations_sha256':location_hash,'manifest_sha256':version,'complete':'true'}.items():client.set_tag(run_id,key,value)
                    for key,value in {'logical_files':len(manifest['files']),'logical_bytes':sum(x['size'] for x in manifest['files'].values()),
                        'new_blob_count':len(uploaded),'new_blob_payload_bytes':sum(x['bytes'] for x in uploaded),'new_metadata_payload_bytes':metadata_bytes}.items():client.log_metric(run_id,key,value)
                    client.set_terminated(run_id,'FINISHED')
                    index['versions'][version]={'run_id':run_id,'locations_sha256':location_hash};index['pending'].pop(version,None);atomic_index(index_path,index)
                return {'passed':True,'version':version,'data_run_id':run_id,'data_run_status':client.get_run(run_id).info.status,
                    'existing_version_reconciliation':bool(existing),'logical_files':len(manifest['files']),
                    'logical_bytes':sum(x['size'] for x in manifest['files'].values()),'new_blob_count':len(uploaded),
                    'new_blob_payload_bytes':sum(x['bytes'] for x in uploaded),'new_metadata_payload_bytes':metadata_bytes,
                    'remote_manifest_path':root+'/manifest.json','remote_locations_path':root+'/locations.json',
                    'locations_sha256':location_hash,'remote_blob_owner_run_ids':sorted({x['run_id'] for x in locations.values()}),
                    'remote_restore_downloaded_blob_bytes':downloaded_bytes,'remote_restore':restoration,
                    'blob_locations':locations,'metadata':manifest['metadata']}
            except BaseException:
                if not existing:client.set_terminated(run_id,'FAILED')
                raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store',type=Path,required=True);parser.add_argument('--version',required=True)
    parser.add_argument('--tracking-uri',required=True);parser.add_argument('--restore-to',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--experiment',default='iot-feast-data-versions');args=parser.parse_args()
    if args.output.exists():raise ValueError('Preserve the previous remote publication receipt')
    result=MlflowDatasetPublisher(args.store,args.tracking_uri,args.experiment).publish(args.version,args.restore_to)
    save(args.output,{**result,'completed_utc':datetime.now(timezone.utc).isoformat(),'code_sha256':digest(Path(__file__)),
        'scope':'Real incremental MLflow dataset publication and complete remote restoration. Upload payload counters exclude readback downloads/HTTP overhead. No model version is claimed by this data-only command.'})
    print(json.dumps({k:result[k] for k in ['passed','version','data_run_id','new_blob_count','new_blob_payload_bytes','new_metadata_payload_bytes']}))
