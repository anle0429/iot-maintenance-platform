"""Bind separate stream-store CI jobs to exact retained Kafka/Flink windows and known source code."""
from hashlib import sha256
from pathlib import Path
import json,re
from feature_store.outbox import window_payload


def release(mode,build):
    """Keep offline and online build numbers in different owned release namespaces."""
    if mode not in ['offline','online'] or not isinstance(build,str) or re.fullmatch(r'[1-9][0-9]{0,9}',build) is None:raise ValueError('Invalid stream job identity')
    return 'stream-'+mode+'-jenkins-'+build


def fixture(root:Path):
    """Verify an exact projection of actual finalized windows; never regenerate the source or change its clocks."""
    path=root/'tests/fixtures/stream-finalized-native.json';value=json.loads(path.read_bytes())
    if value['format']!='iot-native-finalized-window-fixture-v1' or value['parent']!='docs/evidence/stream-push/warm/feast/source-report.json':raise ValueError('Unknown native source projection')
    raw=(root/value['parent']).read_bytes()
    if sha256(raw).hexdigest()!=value['parent_sha256'] or json.loads(raw)['windows']!=value['windows']:raise ValueError('Finalized source projection differs')
    for name,h in value['code_sha256'].items():
        if not name.startswith('pipelines/') or '..' in Path(name).parts or sha256((root/name).read_bytes()).hexdigest()!=h:raise ValueError('Kafka/Flink source code changed')
    values=[window_payload(row) for row in value['windows']]
    if len(values)!=3 or len({key for key,_,_ in values})!=3:raise ValueError('Require all three distinct finalized windows')
    return value
