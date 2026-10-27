"""Reject replayed CI receipts, mismatched model lineage and insufficient execution windows."""
from copy import deepcopy
from datetime import datetime,timezone
import unittest
from hashlib import sha256
import importlib.util,io,json,tarfile
from pathlib import Path
from unittest.mock import Mock,patch
from ml.ci_training_contract import identity,admission,verify_result


def fixture():
    """Describe a newly submitted non-production run with one matching data/model reference."""
    return [ {'checked_utc':'2026-10-27T10:01:00+00:00','source_snapshot_commit':'A'},
        {'run_id':'new-run'},
        {'passed':True,'run_id':'new-run','production':False,'registered_model_version':'6','model_run_id':'model-run','data_version':'root','data_run_id':'data-run'},
        {'passed':True,'source_snapshot_commit':'A','data_version':'root','data_run_id':'data-run'},
        {'passed':True,'source_snapshot_commit':'A','production':False,'version':'6','model_run_id':'model-run',
         'incremental_data_reference':{'data_version':'root','data_run_id':'data-run'}}]


class TrainingCITests(unittest.TestCase):
    """Use independent bad inputs to protect the CI completion boundary."""
    def test_fresh_candidate_known_answer(self):
        self.assertTrue(verify_result(*fixture(),'A','2026-10-27T10:00:00+00:00')['passed'])

    def test_previous_run_rejected(self):
        values=fixture();values[0]['checked_utc']='2026-10-26T10:00:00+00:00'
        with self.assertRaises(ValueError):verify_result(*values,'A','2026-10-27T10:00:00+00:00')

    def test_other_submission_or_source_rejected(self):
        for index,key,value in [(1,'run_id','old-run'),(0,'source_snapshot_commit','B'),(3,'source_snapshot_commit','B'),(4,'source_snapshot_commit','B')]:
            values=fixture();values[index][key]=value
            with self.assertRaises(ValueError):verify_result(*values,'A','2026-10-27T10:00:00+00:00')

    def test_production_or_failed_outputs_rejected(self):
        for index,key,value in [(2,'production',True),(4,'production',True),(2,'passed',False),(3,'passed',False),(4,'passed',False)]:
            values=fixture();values[index][key]=value
            with self.assertRaises(ValueError):verify_result(*values,'A','2026-10-27T10:00:00+00:00')

    def test_other_data_or_model_rejected(self):
        for key in ['data_version','data_run_id','registered_model_version','model_run_id']:
            values=fixture();values[2][key]='other'
            with self.assertRaises(ValueError):verify_result(*values,'A','2026-10-27T10:00:00+00:00')

    def test_actual_resource_boundaries(self):
        now=datetime(2026,10,27,10,tzinfo=timezone.utc)
        self.assertTrue(admission('2026-10-27T11:30:00Z',now,24*1024**3,[40_000_000_000]*2)['passed'])
        for stop,ram,disks in [('2026-10-27T11:29:59Z',24*1024**3,[40_000_000_000]*2),
                ('2026-10-27T11:30:00Z',24*1024**3-1,[40_000_000_000]*2),
                ('2026-10-27T11:30:00Z',24*1024**3,[40_000_000_000,39_999_999_999])]:
            with self.assertRaises(ValueError):admission(stop,now,ram,disks)

    def test_release_identity_cannot_escape_paths(self):
        self.assertEqual(identity('2'),'train-jenkins-2')
        for value in ['0','01','../1','1;echo token','-1','1/2',None,1,'1'*11]:
            with self.assertRaises(ValueError):identity(value)


class DockerConfigIdentityTests(unittest.TestCase):
    """Use actual Docker-save layouts to distinguish a config blob from a manifest identifier."""
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).resolve().parents[1]/'scripts/ci-training.py'
        spec=importlib.util.spec_from_file_location('training_ci_export_test',path)
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)

    def exported(self,path,config,manifest=None):
        """Supply a tiny tar stream in place of a real Docker daemon, retaining real tar parsing."""
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w') as archive:
            for name,raw in [(path,config),('manifest.json',json.dumps(manifest or [{'Config':path,'RepoTags':['test:one'],'Layers':[]}]).encode())]:
                member=tarfile.TarInfo(name);member.size=len(raw);archive.addfile(member,io.BytesIO(raw))
        stream.seek(0);process=Mock(stdout=stream);process.wait.return_value=0;process.poll.return_value=0
        with patch.object(self.module.subprocess,'Popen',return_value=process):
            return self.module.exported_config_digest('test:one')

    def test_legacy_and_oci_layouts_bind_same_actual_config(self):
        raw=b'{"architecture":"amd64","config":{"Labels":{"source":"A"}}}'
        expected=sha256(raw).hexdigest()
        self.assertEqual(self.exported(expected+'.json',raw),'sha256:'+expected)
        self.assertEqual(self.exported('blobs/sha256/'+expected,raw),'sha256:'+expected)

    def test_changed_blob_changes_identity_even_when_path_is_reused(self):
        first=self.exported('config.json',b'{"source":"A"}')
        second=self.exported('config.json',b'{"source":"B"}')
        self.assertNotEqual(first,second)

    def test_missing_or_ambiguous_config_is_rejected(self):
        for manifest in [[{'Config':'absent.json'}],[{'Config':'config.json'},{'Config':'config.json'}]]:
            with self.assertRaises(ValueError):self.exported('config.json',b'{}',manifest)


if __name__=='__main__':unittest.main()
