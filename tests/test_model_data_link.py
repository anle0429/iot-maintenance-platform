"""Reject model/data lineage swaps even when two datasets share the same frozen protocol."""
from copy import deepcopy
from hashlib import sha256
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from ml.data_versions import canonical_bytes
from ml.model_data_link import verify_model_data_link,verify_source_snapshot


def fixture():
    """Use small descriptor fixtures; this is a contract oracle, not native data evidence."""
    metadata={'protocol_sha256':'protocol','source_manifest_sha256':'source'}
    canonical={'passed':True,'metadata':metadata,'files':[{'path':'cohort/day=2026-10-01/data.parquet','sha256':'a'*64,'bytes':12}],
        'rows':2,'native_report_sha256':'native-report','native_descriptor_sha256':'native-descriptor'}
    version=sha256(canonical_bytes({'format':'iot-content-dataset-v1','metadata':metadata,
        'files':{canonical['files'][0]['path']:{'sha256':'a'*64,'size':12}}})).hexdigest()
    parts={'passed':True,'kind':'temporal-split',**metadata,'native_report_sha256':'native-report','input_descriptor_sha256':'native-descriptor'}
    local={'passed':True,'version':version,'rows_in_version':2,'source_native_report_sha256':'native-report'}
    remote={'passed':True,'version':version,'metadata':metadata,'logical_files':1,'logical_bytes':12,'data_run_status':'FINISHED',
        'remote_restore':{'all_file_bytes_verified':True},'data_run_id':'remote-run','locations_sha256':'locations'}
    return parts,canonical,local,remote


class ModelDataLinkTest(unittest.TestCase):
    """Check externally meaningful identity and completeness gates before model publication."""
    def test_matches_and_wrong_native_pull_is_rejected(self):
        values=fixture();self.assertEqual(verify_model_data_link(*values)['rows'],2)
        for key in ['native_report_sha256','input_descriptor_sha256','source_manifest_sha256']:
            changed=deepcopy(values);changed[0][key]='another-pull'
            with self.assertRaises(ValueError):verify_model_data_link(*changed)

    def test_changed_bytes_partial_snapshot_and_unsealed_remote_are_rejected(self):
        for mutate in [lambda x:x[1]['files'][0].update(bytes=13),lambda x:x[2].update(rows_in_version=1),
            lambda x:x[3].update(data_run_status='RUNNING'),lambda x:x[3]['remote_restore'].update(all_file_bytes_verified=False)]:
            values=deepcopy(fixture());mutate(values)
            with self.assertRaises(ValueError):verify_model_data_link(*values)

    def test_source_snapshot_rejects_changed_code_and_wrong_commit(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);code=root/'job.py';code.write_bytes(b'print(1)\n');commit='f'*40
            binding={'format':'iot-source-snapshot-v1','commit':commit,'files':{'job.py':sha256(code.read_bytes()).hexdigest()}}
            verify_source_snapshot(binding,root,commit)
            with self.assertRaises(ValueError):verify_source_snapshot(binding,root,'a'*40)
            code.write_bytes(b'print(2)\n')
            with self.assertRaises(ValueError):verify_source_snapshot(binding,root,commit)


if __name__=='__main__':unittest.main()
