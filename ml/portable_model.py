"""Expose verified Spark scaler/logistic coefficients as an MLflow Python model without a JVM."""

import json
from pathlib import Path
import pandas as pd
from mlflow.pyfunc import PythonModel
from ml.training_contract import score_portable


class PortablePumpModel(PythonModel):
    """Serve only the eight allowlisted features in the fitted model's recorded column order."""

    def load_context(self,context):
        """Load model bytes included in the versioned MLflow artifact."""
        self.model=json.loads(Path(context.artifacts['portable']).read_text())

    def predict(self,context,model_input,params=None):
        """Return risk and a fixed decision without fitting, selecting thresholds or filling missing inputs."""
        if list(model_input.columns)!=self.model['features']:
            raise ValueError('Model requires exactly the eight named features in its recorded order')
        probabilities=[score_portable(row,self.model) for row in model_input.itertuples(index=False,name=None)]
        return pd.DataFrame({'risk_score':probabilities,
            'predicted_failure':[int(p>=self.model['threshold']) for p in probabilities]})
