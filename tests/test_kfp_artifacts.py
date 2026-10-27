"""Check typed executor metadata is published only after a complete descriptor exists."""

import json,tempfile,unittest,os
from pathlib import Path
from unittest.mock import patch
from ml.kfp_artifacts import publish
from ml.training_contract import save


class KfpArtifactTests(unittest.TestCase):
    """Protect metadata identity and refuse success publication for partial component outputs."""

    def test_metadata_and_incomplete_output(self):
        """Use an independent small ExecutorInput fixture and check its exact output artifact identity."""
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'descriptor';output=root/'executor-output.json'
            artifact={'artifacts':[{'uri':str(source),'metadata':{},'type':{'schemaTitle':'system.Dataset','schemaVersion':'0.0.1'}}]}
            args=json.dumps({'outputs':{'artifacts':{'cohort':artifact},'outputFile':str(output)}})
            save(source,{'passed':False})
            with self.assertRaises(ValueError):publish(args,'cohort',source)
            self.assertFalse(output.exists());source.unlink();save(source,{'passed':True,'rows':7})
            publish(args,'cohort',source)
            self.assertEqual(json.loads(output.read_text()),{'artifacts':{'cohort':artifact}})
            with self.assertRaises(ValueError):publish(args,'cohort',source)

    def test_shared_parents_only_inside_declared_artifact_root(self):
        """Share new sibling-task directories while leaving ordinary workspace directories private."""
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);base=root/'artifacts';base.mkdir();base.chmod(0o777)
            with patch.dict(os.environ,{'IOT_KFP_LOCAL_ARTIFACT_ROOT':str(base)}):
                save(base/'pipeline'/'first'/'descriptor',{'passed':True})
                save(root/'ordinary-workspace'/'descriptor',{'passed':True})
            self.assertEqual(json.loads((base/'pipeline/first/descriptor').read_text()),{'passed':True})
            if os.name=='posix':
                self.assertEqual((base/'pipeline').stat().st_mode&0o777,0o777)
                self.assertEqual((base/'pipeline/first').stat().st_mode&0o777,0o777)
                self.assertFalse((root/'ordinary-workspace').stat().st_mode&0o002)


if __name__=='__main__':unittest.main()
