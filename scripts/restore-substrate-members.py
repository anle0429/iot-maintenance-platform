"""Reconnect the retained six-node Substrate Valkey cluster after Pod IP changes, preserving node IDs and slots."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import ipaddress
import json
import os
import re
import subprocess
import time


def parse_nodes(text):
    """Read cluster metadata only; reject malformed, migrating or duplicate members before any repair."""
    nodes = {}
    for line in text.strip().splitlines():
        fields = line.split()
        if len(fields) < 8 or not re.fullmatch(r'[0-9a-f]{40}', fields[0]) or fields[0] in nodes:
            raise ValueError('Invalid or duplicated cluster node identity')
        slots = set()
        for value in fields[8:]:
            if not re.fullmatch(r'\d+(?:-\d+)?', value):
                raise ValueError('Preserve active slot migration instead of recovering addresses')
            bounds = [int(v) for v in value.split('-')]
            low, high = bounds[0], bounds[-1]
            if not 0 <= low <= high < 16384:
                raise ValueError('Invalid slot range')
            current = set(range(low, high + 1))
            if slots & current:
                raise ValueError('Duplicated slots')
            slots |= current
        nodes[fields[0]] = {'address': fields[1], 'flags': fields[2].split(','), 'primary': fields[3],
            'link': fields[7], 'slots': sorted(slots)}
    if len(nodes) != 6:
        raise ValueError('Require exactly the existing six members')
    return nodes


def identities(views):
    """Require one local identity per owned PVC and an identical complete slot/replica assignment in every view."""
    local = {}
    assignments = []
    for pod, nodes in views.items():
        own = [identity for identity, row in nodes.items() if 'myself' in row['flags']]
        if len(own) != 1 or own[0] in local.values():
            raise ValueError('Require six distinct original local node IDs')
        local[pod] = own[0]
        assignment = {identity: (row['primary'], row['slots']) for identity, row in nodes.items()}
        assignments.append(assignment)
        primary = [row for row in nodes.values() if 'master' in row['flags']]
        replica = [row for row in nodes.values() if 'slave' in row['flags']]
        if len(primary) != 3 or len(replica) != 3:
            raise ValueError('Preserve a different cluster topology')
        primary_ids = {identity for identity, row in nodes.items() if 'master' in row['flags']}
        if sorted(row['primary'] for row in replica) != sorted(primary_ids) or any(row['slots'] for row in replica):
            raise ValueError('Require one replica per original primary without replica-owned slots')
        all_slots = [slot for row in primary for slot in row['slots']]
        if sorted(all_slots) != list(range(16384)):
            raise ValueError('Do not recover a cluster with missing or overlapping slots')
    expected = set(local.values())
    if len(local) != 6 or any(set(nodes) != expected for nodes in views.values()):
        raise ValueError('Refuse foreign or missing members')
    if any(value != assignments[0] for value in assignments):
        raise ValueError('Preserve disagreeing slot/replica assignments for manual investigation')
    return local, assignments[0]


def healthy_addresses(views, local, pods):
    """Recognize converged original members at their actual Pod IPs so a second invocation makes no changes."""
    return all(not {'fail', 'fail?', 'handshake', 'noaddr'} & set(row['flags'])
        and row['link'] == 'connected' and row['address'].split(':')[0] == pods[name]['ip']
        for nodes in views.values() for name, identity in local.items() for row in [nodes[identity]])


def run(root, output):
    """Use CONFIG SET/CLUSTER MEET only on verified retained members and capture original metadata receipts."""
    root, output = root.resolve(), output.resolve()
    if os.name != 'posix' or os.geteuid() != 0 or not root.is_relative_to('/srv/iot-data') or not output.is_relative_to(root / 'outputs'):
        raise ValueError('Use sudo on the existing lab VM and a fresh project output directory')
    import fcntl
    session = json.loads((root / 'data/runtime/ci/session.json').read_bytes())
    if (datetime.fromisoformat(session['auto_stop_utc']) - datetime.now(timezone.utc)).total_seconds() < 900:
        raise ValueError('Require fifteen approved session minutes')
    with (root / 'data/runtime/valkey-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = subprocess.check_output(['systemctl', 'list-units', '--state=running', '--no-legend', 'iot-agent-chat-*'])
        if active.strip():
            raise ValueError('Wait for the active agent conversation')
        output.mkdir(exist_ok=False)
        source = Path(__file__).read_bytes(); (output / 'source.py').write_bytes(source)
        k = ['/usr/local/bin/k3s', 'kubectl']; sequence = 0

        def command(label, arguments):
            """Retain fixed metadata-command output; never read application keys or credentials."""
            nonlocal sequence
            sequence += 1; prefix = f'{sequence:03d}-' + label
            result = subprocess.run(arguments, capture_output=True, timeout=30)
            (output / (prefix + '.stdout')).write_bytes(result.stdout)
            (output / (prefix + '.stderr')).write_bytes(result.stderr)
            (output / (prefix + '.exit')).write_text(str(result.returncode) + '\n')
            if result.returncode:
                raise RuntimeError('Metadata command failed: ' + label)
            return result.stdout.decode()

        def cli(pod, *arguments):
            """Execute only a fixed administrative metadata command in an owned Valkey Pod."""
            text = command(pod + '-' + arguments[0].lower(), k + ['exec', '-n', 'ate-system', pod, '--', 'valkey-cli', '--raw'] + list(arguments))
            if text.startswith(('ERR ', 'CLUSTERDOWN ', '(error)')):
                raise RuntimeError('Valkey rejected ' + arguments[0])
            return text

        report = {'passed': False, 'source_sha256': sha256(source).hexdigest(), 'new_resources': False,
            'application_values_exported': False, 'reset_flush_reshard_used': False}
        try:
            pods = {}; views = {}; counts = {}
            for number in range(6):
                name = 'valkey-cluster-' + str(number)
                pod = json.loads(command('pod-before-' + str(number), k + ['get', 'pod', name, '-n', 'ate-system', '-o', 'json']))
                owner = pod['metadata'].get('ownerReferences', [])
                if pod['metadata']['labels'].get('app') != 'valkey-cluster' or not any(p['kind'] == 'StatefulSet' and p['name'] == 'valkey-cluster' for p in owner):
                    raise ValueError('Require the owned Valkey StatefulSet')
                data = next(v for v in pod['spec']['volumes'] if v['name'] == 'data')
                if data['persistentVolumeClaim']['claimName'] != 'data-' + name:
                    raise ValueError('Preserve different PVC bindings')
                ip = pod['status']['podIP']; ipaddress.IPv4Address(ip)
                if not ip.startswith('10.52.'):
                    raise ValueError('Keep the existing Pod network')
                pods[name] = {'ip': ip, 'uid': pod['metadata']['uid'], 'pvc': data['persistentVolumeClaim']['claimName']}
                views[name] = parse_nodes(cli(name, 'CLUSTER', 'NODES'))
                counts[name] = int(cli(name, 'DBSIZE').strip())
            local, assignment = identities(views)
            (output / 'before.json').write_text(json.dumps({'pods': pods, 'node_ids': local, 'key_counts': counts}, indent=2) + '\n')
            changed = not healthy_addresses(views, local, pods)
            if changed:
                for name, pod in pods.items():
                    if cli(name, 'CONFIG', 'SET', 'cluster-announce-ip', pod['ip']).strip() != 'OK':
                        raise RuntimeError('Failed to announce actual owned Pod IP')
                seed = 'valkey-cluster-0'
                for name, pod in pods.items():
                    if name != seed and cli(seed, 'CLUSTER', 'MEET', pod['ip'], '6379', '16379').strip() != 'OK':
                        raise RuntimeError('Failed member reconnection')
            deadline = time.monotonic() + 120
            while True:
                after = {name: parse_nodes(cli(name, 'CLUSTER', 'NODES')) for name in pods}
                new_local, new_assignment = identities(after)
                if new_local != local or new_assignment != assignment:
                    raise ValueError('Node IDs or slot/replica assignment changed; preserve receipts')
                if healthy_addresses(after, local, pods):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Original members did not converge')
                time.sleep(3)
            infos = {}
            for name in pods:
                info = dict(line.split(':', 1) for line in cli(name, 'CLUSTER', 'INFO').strip().splitlines() if ':' in line)
                if info['cluster_state'] != 'ok' or int(info['cluster_slots_ok']) != 16384 or int(info['cluster_known_nodes']) != 6:
                    raise ValueError('Require every original node healthy with all slots')
                if changed:
                    cli(name, 'CLUSTER', 'SAVECONFIG')
                infos[name] = info
            report.update(passed=True, node_ids=local, before_key_counts=counts, cluster_info=infos,
                node_ids_preserved=True, slot_and_replica_assignment_preserved=True,
                address_repair_applied=changed, membership_commands=5 if changed else 0,
                checked_utc=datetime.now(timezone.utc).isoformat(),
                scope='Owned cluster address recovery only. In-memory announcement must be reapplied after future Pod IP changes. API/controller readiness and agent conversation remain separate checks.')
        except Exception as error:
            report.update(error_type=type(error).__name__, error=str(error))
        (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2)); return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); raise SystemExit(run(args.runtime_root, args.output))
