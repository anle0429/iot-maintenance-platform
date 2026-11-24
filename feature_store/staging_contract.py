"""Bind separate stream-store CI jobs to exact retained Kafka/Flink windows and known source code."""
from hashlib import sha256
from pathlib import Path
import json,re
from feature_store.outbox import window_payload


# The retained windows were captured with the first hashes. The second hashes
# bind the audit-only revision, which leaves finalized-window records unchanged.
_DUPLICATE_AUDIT_SOURCE_PINS = {
    'pipelines/stream_job.py': (
        '478c4c9c7b38e56a93e53fd5236c432016e7fe2c3ee829202a4dc7979a9c46db',
        'a06c5cd181782cd3b3c10c57a3aa1282359876e7149de4d471b379af3d292bdf',
    ),
    'pipelines/stream_contract.py': (
        'cd2a85c2bd8dda070ac0e174d12ac787201412425f75899a63c4580d69f9de06',
        '1ba35b27d67db88afba100c407da24c34e877acdcd7e0ba6aae59af657841211',
    ),
}


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
    source_versions = set()
    for name,h in value['code_sha256'].items():
        if not name.startswith('pipelines/') or '..' in Path(name).parts:
            raise ValueError('Invalid Kafka/Flink source path')
        pins = _DUPLICATE_AUDIT_SOURCE_PINS.get(name, (h,))
        current = sha256((root/name).read_bytes()).hexdigest()
        if h != pins[0] or current not in pins:
            raise ValueError('Kafka/Flink source code changed')
        if name in _DUPLICATE_AUDIT_SOURCE_PINS:
            source_versions.add(pins.index(current))
    if not _DUPLICATE_AUDIT_SOURCE_PINS.keys() <= value['code_sha256'].keys() or len(source_versions) != 1:
        raise ValueError('Kafka/Flink source files are from different revisions')
    values=[window_payload(row) for row in value['windows']]
    if len(values)!=3 or len({key for key,_,_ in values})!=3:raise ValueError('Require all three distinct finalized windows')
    return value
