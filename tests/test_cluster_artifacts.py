"""Prove descriptor transport survives deletion of a previous pod's temporary download and refuses overwrite/wrong kinds."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,unittest
from ml.cluster_training_job import persistent_input


class ClusterArtifactsTest(unittest.TestCase):
    """Check observable descriptor lifetime independently of a running Spark or Kubernetes service."""
    def test_persistent_split_reference_and_no_overwrite(self):
        'Verify persistent split reference and no overwrite.'
        with TemporaryDirectory() as directory:
            base=Path(directory);source=base/'ephemeral-partitions';payload=b'{"kind":"temporal-split","passed":true,"artifacts":[]}'
            source.write_bytes(payload);args=['--step','train','--workspace',str(base/'workspace'),'--input',str(source),'--output','artifact-output']
            result=persistent_input(args);target=Path(result[result.index('--input')+1]);source.unlink()
            self.assertEqual(target.read_bytes(),payload);self.assertEqual(result[-2:],['--output','artifact-output'])
            self.assertEqual(args[args.index('--input')+1],str(source))
            source.write_bytes(payload)
            with self.assertRaises(FileExistsError):persistent_input(args)

    def test_wrong_stage_or_unsealed_input_is_rejected(self):
        'Verify wrong stage or unsealed input is rejected.'
        with TemporaryDirectory() as directory:
            base=Path(directory);source=base/'input'
            for value in [{'passed':False,'kind':'temporal-split'},{'passed':True,'kind':'holdout-evaluation'}]:
                source.write_text(json.dumps(value))
                with self.assertRaises(ValueError):persistent_input(['--step','train','--workspace',str(base/'workspace'),'--input',str(source)])
            self.assertFalse((base/'workspace').exists())


if __name__=='__main__':unittest.main()
