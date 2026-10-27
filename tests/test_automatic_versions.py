"""Protect the automatic persistence boundary against mismatched validation and altered execution source."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json,unittest
from ml.automatic_versions import context,validation_gate


class AutomaticVersionGateTests(unittest.TestCase):
    """Use tiny known fixtures to reject bad lineage before real registry side effects."""
    def test_validation_binds_exact_weights_and_keeps_test_closed(self):
        """Changing selection bytes after validation cannot authorize the final stage."""
        with TemporaryDirectory() as folder:
            selection=Path(folder)/'selection.json';verification=Path(folder)/'validation.json'
            selection.write_bytes(b'{"model":"A"}')
            valid={'passed':True,'holdout_rows_opened':0,'selection_sha256':sha256(selection.read_bytes()).hexdigest()}
            verification.write_text(json.dumps(valid));validation_gate(selection,verification)
            selection.write_bytes(b'{"model":"B"}')
            with self.assertRaises(ValueError):validation_gate(selection,verification)
            for change in [{'passed':False},{'holdout_rows_opened':1}]:
                verification.write_text(json.dumps({**valid,**change}))
                with self.assertRaises(ValueError):validation_gate(selection,verification)

    def test_context_rejects_other_endpoint_and_mutated_code(self):
        """Neither an arbitrary registry destination nor an uncommitted source mutation is accepted."""
        with TemporaryDirectory() as folder,patch('ml.automatic_versions.ROOT',Path(folder)):
            code=Path(folder)/'source.py';code.write_bytes(b'print(7)\n')
            value={'format':'iot-source-snapshot-v1','commit':'f'*40,'files':{'source.py':sha256(code.read_bytes()).hexdigest()},'tracking_uri':'http://tracking:5000'}
            binding=Path(folder)/'binding.json';binding.write_text(json.dumps(value));self.assertEqual(context(binding)['commit'],'f'*40)
            value['tracking_uri']='http://other:5000';binding.write_text(json.dumps(value))
            with self.assertRaises(ValueError):context(binding)
            value['tracking_uri']='http://tracking:5000';binding.write_text(json.dumps(value));code.write_bytes(b'print(8)\n')
            with self.assertRaises(ValueError):context(binding)


if __name__=='__main__':unittest.main()
