"""Protect Spark client identity from the observed 63-character Linux hostname truncation."""
import os,unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from ml.distributed_submit import command


class DistributedIdentityTest(unittest.TestCase):
    """Ensure long native pod names remain addressable by the executor scheduler."""
    def test_downward_api_identity_survives_long_hostname(self):
        'Verify downward api identity survives long hostname.'
        full='iot-notebook-distributed-training-v1-hvvbn-system-container-impl-1141215162'
        with TemporaryDirectory() as workspace,patch.dict(os.environ,{'HOSTNAME':full[:63],'IOT_DRIVER_POD':full,'IOT_DRIVER_IP':'10.53.0.99'}):
            cmd,address=command(workspace,['--step','train'])
            self.assertIn('spark.kubernetes.driver.pod.name='+full,cmd)
            self.assertEqual(address,'10.53.0.99')
        with TemporaryDirectory() as workspace,patch.dict(os.environ,{'IOT_DRIVER_POD':full,'IOT_DRIVER_IP':'127.0.0.1'}):
            with self.assertRaises(ValueError):command(workspace,[])


if __name__=='__main__':unittest.main()
