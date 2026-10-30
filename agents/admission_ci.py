"""Bind the additional OPA admission phase to the same trusted Jenkins job, build and complete source revision."""
import re


def require_policy_phase(value,identity,source_digest):
    """A successful decision from another build, source, scenario or model identity cannot authorize production deployment."""
    if (value.get('schema_version')!=1 or value.get('phase')!='policy' or value.get('passed') is not True
            or any(value.get(k)!=v for k,v in identity.items()) or value.get('source_digest')!=source_digest
            or value.get('scenario')!='production' or not isinstance(value.get('model_uid'),str) or not value['model_uid']
            or not re.fullmatch('[a-f0-9]{64}',str(value.get('rendered_manifest_sha256','')))):
        raise ValueError('Require this exact production build and its original admitted model/manifest binding')
    decision=value.get('decision',{})
    if decision.get('allowed') is not True or decision.get('gate_exit')!=0 or decision.get('violations')!=[]:
        raise ValueError('The production policy phase was not admitted')
    return value
