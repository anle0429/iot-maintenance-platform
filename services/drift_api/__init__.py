"""Provide label-free drift comparison and its serving-reference identity contract."""

MODEL_REFERENCE_FIELDS = {'model_name': 'name', 'model_version': 'version', 'model_run_id': 'run_id',
                          'data_version': 'data_version', 'protocol_sha256': 'protocol_sha256'}


def reference_matches_model(lineage, model):
    """Require one explicit training reference for the exact model and data release."""
    return (isinstance(lineage, dict) and isinstance(model, dict) and lineage.get('partition') == 'train'
            and all(isinstance(model.get(key), str) and bool(model[key]) and lineage.get(field) == model[key]
                    for field, key in MODEL_REFERENCE_FIELDS.items()))
