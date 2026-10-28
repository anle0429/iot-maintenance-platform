"""Bind ML artifacts, frozen chronological splits and portable Spark logistic coefficients."""

from datetime import datetime,timezone
from hashlib import sha256
import json,math,os
from pathlib import Path

from ml.protocol import PROTOCOL_SHA256,load_protocol


def digest(path):
    """Hash bounded artifacts without assuming filenames establish their identity."""
    value=sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def save(path,value):
    """Persist JSON atomically; never replace a completed output or emit nonfinite metrics."""
    path=Path(path)
    shared=os.environ.get('IOT_KFP_LOCAL_ARTIFACT_ROOT')
    if shared:
        base=Path(shared).resolve()
        try:parts=path.parent.resolve().relative_to(base).parts
        except ValueError:parts=()
        current=base
        for part in parts:
            current=current/part
            try:current.mkdir()
            except FileExistsError:continue
            # Only this newly created nonsecret local artifact directory is shared across image UIDs.
            current.chmod(0o777)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():raise ValueError('Preserve completed ML evidence: '+str(path))
    pending=path.with_suffix('.pending');pending.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    pending.replace(path)


def inventory(path):
    """Bind every actual file of a model/dataset rather than hashing just a directory name."""
    root=Path(path)
    return {p.relative_to(root).as_posix():digest(p) for p in sorted(root.rglob('*')) if p.is_file()}


def verify_descriptor(path,kind):
    """Reject cross-stage output reuse or byte changes before opening training or holdout rows."""
    value=json.loads(Path(path).read_text())
    if not value.get('passed') or value.get('kind')!=kind or value.get('protocol_sha256')!=PROTOCOL_SHA256:
        raise ValueError('Unverified ML input or frozen protocol mismatch')
    for item in value.get('artifacts',[]):
        if inventory(item['path'])!=item['files']:raise ValueError('ML artifact inventory changed')
    return value


def split_for_timestamp(event_ts,label_available_ts,protocol):
    """Implement the declared half-open partitions and label-availability purge on aware times."""
    def utc(value):
        'Require timezone-aware training timestamps and normalize them to UTC.'
        result=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
        if result.tzinfo is None:raise ValueError('ML timestamps must have an explicit timezone')
        return result.astimezone(timezone.utc)
    t,available=utc(event_ts),utc(label_available_ts);p=protocol['chronological_split']
    start,train,val,end=[utc(p[n]) for n in ['start_inclusive','train_end_exclusive','validation_end_exclusive','observation_end_exclusive']]
    if not start<=t<end:return None
    name,cut=('train',train) if t<train else ('validation',val) if t<val else ('test',end)
    return name if available<cut else None


def select_threshold(candidates):
    """Use only validation confusion counts and the frozen F1/precision/higher-threshold tie break."""
    if len(candidates)!=9 or sorted(x['threshold'] for x in candidates)!=[n/10 for n in range(1,10)]:
        raise ValueError('Threshold candidates differ from the declared validation grid')
    return max(candidates,key=lambda x:(x['f1'],x['precision'],x['threshold']))


def score_portable(values,model):
    """Reproduce fitted Spark StandardScaler and binary logistic probability without a JVM."""
    if len(values)!=len(model['features']) or any(not math.isfinite(float(x)) for x in values):
        raise ValueError('Exactly eight finite model inputs are required')
    z=model['intercept']+sum(((float(x)-m)/s if s else 0.0)*c
        for x,m,s,c in zip(values,model['mean'],model['std'],model['coefficients']))
    return 1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
