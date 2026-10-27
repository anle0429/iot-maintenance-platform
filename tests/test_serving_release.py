"""Verify production selection and exact model/data provenance using small registry fixtures."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest

from ml.protocol import PROTOCOL_SHA256
from ml.serving_release import MODEL,authorized_version,export

ROOT=Path(__file__).resolve().parents[1]
PROTOCOL=ROOT/'ml/protocols/medium-60d-v1.json'
encode=lambda value:(json.dumps(value,indent=2)+'\n').encode()
H=lambda raw:sha256(raw).hexdigest()


class RegistryFixture:
    """Represent one immutable completed run with real portable model bytes and independent descriptors."""
    def __init__(self,incremental=False):
        self.run_id='a'*32;self.get_run_calls=0;self.download_calls=[];self.version_reads=0;self.demote=False
        self.version=NS(name=MODEL,version='1',status='READY',run_id=self.run_id,
            tags={'production':'true'},current_stage='None',source='models:/m-fixture')
        portable=(ROOT/'models/pump-failure-v1/portable.json').read_bytes()
        split={'passed':True,'kind':'temporal-split','protocol_sha256':PROTOCOL_SHA256,'native_report_sha256':'d'*64}
        selected={'passed':True,'kind':'validation-selected-model','protocol_sha256':PROTOCOL_SHA256,
            'split_descriptor_sha256':H(encode(split)),'scaler_fit_partition':'train',
            'threshold_selection_partition':'validation','holdout_rows_opened':0,'portable_path':'/training/trained/portable.json',
            'validation':{'threshold':0.7},'artifacts':[{'path':'/training/trained','files':{'portable.json':H(portable)}}]}
        evaluation={'passed':True,'kind':'holdout-evaluation','protocol_sha256':PROTOCOL_SHA256,
            'selection_sha256':H(encode(selected)),'fixed_threshold':0.7}
        self.tags={'protocol_sha256':PROTOCOL_SHA256,'evaluation_sha256':H(encode(evaluation)),
            'selection_sha256':H(encode(selected)),'training_data_version':'d'*64,'code_git_commit':'c'*40}
        self.payloads={'portable/portable.json':portable,'lineage/evaluation.json':encode(evaluation),
            'lineage/selection.json':encode(selected),'lineage/split.json':encode(split)}
        self.data_tags={};self.status='FINISHED'
        if incremental:
            manifest=encode({'files':{'known-part.parquet':'e'*64}});data_version=H(manifest)
            self.tags.update(training_data_version=data_version,dataset_run_id='b'*32)
            self.data_tags={'data_version':data_version}
            self.payloads.update({'lineage/incremental-data-reference.json':encode({'data_version':data_version,
                'data_run_id':'b'*32,'native_report_sha256':'d'*64}),'dataset-version/manifest.json':manifest})

    def get_model_version(self,name,version):
        """Return a separate metadata value so the first read cannot silently mutate later."""
        self.version_reads+=1;value=deepcopy(self.version)
        if self.demote and self.version_reads>1:value.tags['production']='false'
        return value

    def get_run(self,run_id):
        """Provide the actual referenced model/data status without artifact inference."""
        self.get_run_calls+=1
        return NS(info=NS(status=self.status),data=NS(tags=deepcopy(self.tags if run_id==self.run_id else self.data_tags)))

    def list_artifacts(self,run_id,folder):
        """Expose real fixture byte sizes before bounded downloads."""
        return [NS(path=name,is_dir=False,file_size=len(raw)) for name,raw in self.payloads.items() if name.startswith(folder+'/')]

    def download_artifacts(self,run_id,path,dst_path):
        """Write the fixture through the same SDK destination contract used by the exporter."""
        self.download_calls.append(path);destination=Path(dst_path)/path
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(self.payloads[path]);return str(destination)


class ServingReleaseTests(unittest.TestCase):
    """Keep failed or unapproved exports from publishing even a partial model bundle."""
    def test_export_legacy_exact_model_and_lineage(self):
        registry=RegistryFixture()
        with TemporaryDirectory() as temp:
            destination=Path(temp)/'bundle';lineage=export(registry,'1',destination,PROTOCOL)
            self.assertEqual((destination/'portable.json').read_bytes(),registry.payloads['portable/portable.json'])
            self.assertEqual(lineage['portable_sha256'],H((destination/'portable.json').read_bytes()))
            self.assertTrue(lineage['production']);self.assertEqual(lineage['data_version'],'d'*64)

    def test_incremental_data_manifest_readback(self):
        registry=RegistryFixture(incremental=True)
        with TemporaryDirectory() as temp:
            result=export(registry,'1',Path(temp)/'bundle',PROTOCOL)
        self.assertEqual(result['dataset_run_id'],'b'*32)
        self.assertIn('dataset-version/manifest.json',registry.download_calls)

    def test_candidates_rejected_before_run_or_artifact_access(self):
        registry=RegistryFixture();registry.version.tags={'production':'false'}
        with TemporaryDirectory() as temp:
            destination=Path(temp)/'bundle'
            with self.assertRaisesRegex(ValueError,'production'):export(registry,'1',destination,PROTOCOL)
            self.assertFalse(destination.exists())
        self.assertEqual(registry.get_run_calls,0);self.assertEqual(registry.download_calls,[])

    def test_stage_marker_and_contradictory_or_archived_markers(self):
        registry=RegistryFixture();registry.version.tags={};registry.version.current_stage='Production'
        self.assertEqual(authorized_version(registry.version,'1')['version'],'1')
        for tag,stage in [('false','Production'),('true','Archived'),('TRUE','None'),('','None')]:
            value=deepcopy(registry.version);value.tags={'production':tag};value.current_stage=stage
            with self.assertRaises(ValueError):authorized_version(value,'1')

    def test_wrong_model_version_status_and_unsafe_pin(self):
        registry=RegistryFixture()
        for field,value in [('name','other-model'),('version','2'),('status','PENDING_REGISTRATION'),('run_id','../run')]:
            model=deepcopy(registry.version);setattr(model,field,value)
            with self.assertRaises(ValueError):authorized_version(model,'1')
        for pin in ['0','01','../1',1,None]:
            with self.assertRaises(ValueError):authorized_version(registry.version,pin)

    def test_unfinished_protocol_and_source_conflicts_rejected(self):
        for update in [{'status':'RUNNING'},{'tag':('protocol_sha256','bad')},
                {'tag':('source_snapshot_commit','f'*40)},{'tag':('code_git_commit','not-a-commit')}]:
            registry=RegistryFixture()
            if 'status' in update:registry.status=update['status']
            if 'tag' in update:registry.tags[update['tag'][0]]=update['tag'][1]
            with TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):export(registry,'1',Path(temp)/'bundle',PROTOCOL)

    def test_portable_and_evaluation_corruption_rejected(self):
        for name in ['portable/portable.json','lineage/evaluation.json','lineage/selection.json','lineage/split.json']:
            registry=RegistryFixture();registry.payloads[name]+=b' '
            with TemporaryDirectory() as temp:
                destination=Path(temp)/'bundle'
                with self.assertRaises(ValueError):export(registry,'1',destination,PROTOCOL)
                self.assertFalse(destination.exists())

    def test_identical_duplicate_split_artifact_is_valid(self):
        registry=RegistryFixture();registry.payloads['lineage/data-inventory.json']=registry.payloads['lineage/split.json']
        with TemporaryDirectory() as temp:
            result=export(registry,'1',Path(temp)/'bundle',PROTOCOL)
        self.assertEqual(len(result['source_artifacts'][H(registry.payloads['lineage/split.json'])]),2)

    def test_incremental_manifest_corruption_rejected(self):
        registry=RegistryFixture(incremental=True);registry.payloads['dataset-version/manifest.json']+=b' '
        with TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'manifest'):export(registry,'1',Path(temp)/'bundle',PROTOCOL)

    def test_marker_changed_during_export_rejected(self):
        registry=RegistryFixture();registry.demote=True
        with TemporaryDirectory() as temp:
            destination=Path(temp)/'bundle'
            with self.assertRaises(ValueError):export(registry,'1',destination,PROTOCOL)
            self.assertFalse(destination.exists())

    def test_unsafe_or_oversized_lineage_rejected(self):
        for name in ['lineage/../escape.json','lineage/large.json']:
            registry=RegistryFixture();registry.payloads[name]=b'{}' if '..' in name else b' '*(1_000_001)
            with TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):export(registry,'1',Path(temp)/'bundle',PROTOCOL)

    def test_existing_bundle_preserved(self):
        registry=RegistryFixture()
        with TemporaryDirectory() as temp:
            destination=Path(temp)/'bundle';destination.mkdir();(destination/'keep.txt').write_text('original')
            with self.assertRaises(FileExistsError):export(registry,'1',destination,PROTOCOL)
            self.assertEqual((destination/'keep.txt').read_text(),'original')
        self.assertEqual(registry.version_reads,0)


if __name__=='__main__':unittest.main()
