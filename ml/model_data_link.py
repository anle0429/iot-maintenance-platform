"""Bind a model's actual split input to a canonical content version and a completed remote dataset run."""
from hashlib import sha256
from pathlib import Path
import re
from ml.data_versions import canonical_bytes,relative_name


def verify_source_snapshot(binding,root,expected_commit):
    """Check the Git-derived execution subset on snapshot-based VMs without inventing a runtime Git HEAD."""
    if (binding.get('format')!='iot-source-snapshot-v1' or not re.fullmatch('[0-9a-f]{40}',expected_commit)
        or binding.get('commit')!=expected_commit or not binding.get('files')):
        raise ValueError('Source snapshot commit/format differs')
    root=Path(root).resolve()
    for name,h in binding['files'].items():
        relative_name(name);path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or sha256(path.read_bytes()).hexdigest()!=h:
            raise ValueError('Actual execution source differs from the named commit: '+name)


def verify_model_data_link(partitions,canonical,local_version,remote_version):
    """Reject a plausible but unrelated data version, including the same protocol with a different native pull."""
    if not all(x.get('passed') is True for x in [partitions,canonical,local_version,remote_version]):
        raise ValueError('Require completed split/canonical/local/remote receipts')
    metadata=canonical['metadata'];entries={}
    for item in canonical['files']:
        name=relative_name(item['path'])
        if name in entries:raise ValueError('Duplicate canonical logical path')
        entries[name]={'sha256':item['sha256'],'size':item['bytes']}
    version=sha256(canonical_bytes({'format':'iot-content-dataset-v1','metadata':metadata,'files':entries})).hexdigest()
    if (partitions['kind']!='temporal-split' or partitions['protocol_sha256']!=metadata['protocol_sha256']
        or partitions['source_manifest_sha256']!=metadata['source_manifest_sha256']
        or partitions['native_report_sha256']!=canonical['native_report_sha256']
        or partitions['input_descriptor_sha256']!=canonical['native_descriptor_sha256']
        or local_version['source_native_report_sha256']!=canonical['native_report_sha256']):
        raise ValueError('Dataset version does not belong to the actual model split/native retrieval')
    if (version!=local_version['version'] or version!=remote_version['version'] or metadata!=remote_version['metadata']
        or canonical['rows']!=local_version['rows_in_version'] or remote_version['logical_files']!=len(entries)
        or remote_version['logical_bytes']!=sum(v['size'] for v in entries.values())
        or remote_version['data_run_status']!='FINISHED' or remote_version['remote_restore']['all_file_bytes_verified'] is not True):
        raise ValueError('Unsealed, partial or different incremental data version')
    return {'data_version':version,'data_run_id':remote_version['data_run_id'],
        'locations_sha256':remote_version['locations_sha256'],'native_report_sha256':canonical['native_report_sha256'],
        'native_descriptor_sha256':canonical['native_descriptor_sha256'],'rows':canonical['rows'],'logical_files':len(entries)}
