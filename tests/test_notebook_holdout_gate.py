"""Reject notebook holdout access when saved-model validation is absent, failed or belongs to other weights."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from ml.notebook_runtime import NotebookRuntime
from ml.training_contract import digest


class NotebookHoldoutGateTests(unittest.TestCase):
    """Keep the expensive holdout component closed for three plausible invalid lineage states."""

    def test_invalid_validation_never_starts_holdout_container(self):
        """A success flag alone cannot authorize evaluation of a different or already-open selection."""
        with tempfile.TemporaryDirectory() as temporary:
            runtime=object.__new__(NotebookRuntime);runtime.workspace=Path(temporary)
            selected=runtime.workspace/'selection.json';selected.write_text('{"fixture":"sealed-model"}')
            valid={'passed':True,'holdout_rows_opened':0,'selection_sha256':digest(selected)}
            for changed in [{'passed':False},{'holdout_rows_opened':1},{'selection_sha256':'0'*64}]:
                with self.subTest(changed=changed):
                    (runtime.workspace/'validation-reload.json').write_text(json.dumps({**valid,**changed}))
                    runtime.container=Mock(side_effect=AssertionError('Holdout container must stay closed'))
                    with self.assertRaisesRegex(ValueError,'Validate this sealed selection'):
                        runtime.spark('evaluate')
                    runtime.container.assert_not_called()

    def test_missing_validation_never_starts_holdout_container(self):
        """Calling the last cell directly must fail before loading any model or test rows."""
        with tempfile.TemporaryDirectory() as temporary:
            runtime=object.__new__(NotebookRuntime);runtime.workspace=Path(temporary)
            runtime.container=Mock(side_effect=AssertionError('Holdout container must stay closed'))
            with self.assertRaises(FileNotFoundError):runtime.spark('evaluate')
            runtime.container.assert_not_called()


if __name__=='__main__':unittest.main()
