"""Reject filesystem, architecture or process-configuration differences while retaining distinct image build identities."""
from copy import deepcopy
from pathlib import Path
import importlib.util,unittest
SPEC=importlib.util.spec_from_file_location('stream_driver_contract',Path(__file__).resolve().parents[1]/'scripts/ci-stream-push.py')
DRIVER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(DRIVER)


class RuntimeContractTests(unittest.TestCase):
    """Use independent image inspection examples, without a Docker daemon or mocked SDK calls."""
    def test_metadata_identity_can_differ_while_actual_runtime_is_identical(self):
        """Build labels/creation metadata are not the process environment or filesystem."""
        tested={'Id':'sha256:'+'a'*64,'Architecture':'amd64','Os':'linux','RootFS':{'Type':'layers','Layers':['sha256:'+'b'*64]},'Config':{'User':'10001:10001','Cmd':['python'],'Env':['HOME=/tmp']}}
        deployed={**deepcopy(tested),'Id':'sha256:'+'c'*64,'Created':'another-build-time'}
        value=DRIVER.require_same_runtime(tested,deployed)
        self.assertEqual(value['tested'],value['deployed']);self.assertNotEqual(value['tested_image_id'],value['deployed_image_id'])
        for key in ['Architecture','Os','RootFS','Config']:
            changed=deepcopy(deployed);changed.pop(key)
            with self.subTest(missing=key),self.assertRaises(ValueError):DRIVER.require_same_runtime(tested,changed)
        for key,value in [('Architecture','arm64'),('RootFS',{'Type':'layers','Layers':['sha256:'+'d'*64]}),('Config',{'User':'0','Cmd':['python'],'Env':['HOME=/tmp']})]:
            changed={**deepcopy(deployed),key:value}
            with self.subTest(changed=key),self.assertRaises(ValueError):DRIVER.require_same_runtime(tested,changed)


if __name__=='__main__':unittest.main()
