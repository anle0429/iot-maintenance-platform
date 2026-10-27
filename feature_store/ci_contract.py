"""Validate the isolated CI release and its initial/incremental/replay known answers."""
import re


def identity(build):
    """Reject path-like and noncanonical IDs before building filesystem or Redis names."""
    if not isinstance(build, str) or not re.fullmatch(r'[1-9][0-9]*', build):
        raise ValueError('A positive canonical Jenkins build number is required')
    return 'mat-jenkins-' + build, 'iot_ci_mat_' + build


def verify_sequence(reports, project):
    """Compare all native checkpoints, vectors and intervals across three real calls."""
    first, second, replay = [reports[name] for name in ('initial', 'incremental', 'replay')]
    binding = first['binding']
    if binding['project'] != project or any(x['binding'] != binding or not x['passed']
            or not x['online_comparison']['passed'] or x['online_comparison']['machines'] != 200
            or len(x['value_sha256_by_machine']) != 200 for x in reports.values()):
        raise ValueError('Source or all-machine native online comparison differs')
    if not (first['mode'] == 'initial' and second['mode'] == 'incremental' and replay['mode'] == 'checkpoint_replay'
            and first['cutoff'] < second['cutoff'] == replay['cutoff']
            and second['previous_cutoff'] == first['cutoff'] and replay['previous_cutoff'] == second['cutoff']
            and second['changed_machine_vectors'] == 200 and replay['changed_machine_vectors'] == 0
            and second['value_sha256_by_machine'] == replay['value_sha256_by_machine']
            and len(second['native_registry_materialization_intervals']) == len(first['native_registry_materialization_intervals']) + 1
            and replay['native_registry_materialization_intervals'] == second['native_registry_materialization_intervals']):
        raise ValueError('Native initial/incremental/replay contract failed')
    return {'passed': True, 'machines': 200, 'incremental_changed_vectors': 200, 'replay_changed_vectors': 0,
            'replay_added_native_intervals': 0}
