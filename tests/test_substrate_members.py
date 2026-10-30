"""Reject topology changes and foreign members before an address-only recovery can alter the cluster."""
from copy import deepcopy
from pathlib import Path
import importlib.util
import unittest

spec = importlib.util.spec_from_file_location('cluster_recovery', Path(__file__).resolve().parents[1] / 'scripts/restore-substrate-members.py')
recovery = importlib.util.module_from_spec(spec); spec.loader.exec_module(recovery)


def cluster_views():
    """Build six views of one complete three-primary cluster with a distinct local identity in each view."""
    views = {}
    for observer in range(6):
        lines = []
        for index in range(6):
            flags = ('myself,' if observer == index else '') + ('master' if index < 3 else 'slave')
            primary = '-' if index < 3 else f'{index - 2:040x}'
            slots = ['0-5460', '5461-10922', '10923-16383', '', '', ''][index]
            lines.append(f'{index + 1:040x} 10.52.0.{index + 10}:6379@16379 {flags} {primary} 0 0 1 connected {slots}')
        views['valkey-cluster-' + str(observer)] = recovery.parse_nodes('\n'.join(lines))
    return views


class ClusterRecoveryTests(unittest.TestCase):
    """Membership, slot ownership and local disk identity must be known before reconnecting Pods."""

    def test_complete_original_cluster_is_accepted(self):
        """A single consistent complete assignment identifies six retained local nodes."""
        local, assignment = recovery.identities(cluster_views())
        self.assertEqual(len(set(local.values())), 6)
        self.assertEqual(sum(len(slots) for _, slots in assignment.values()), 16384)

    def test_foreign_member_in_one_view_is_rejected(self):
        """A six-member count alone cannot authorize an unknown cluster member."""
        views = cluster_views(); nodes = views['valkey-cluster-0']
        nodes['f' * 40] = nodes.pop(f'{6:040x}')
        with self.assertRaises(ValueError): recovery.identities(views)

    def test_disagreeing_replication_is_rejected(self):
        """Reconnect only when all nodes agree about original replica ownership."""
        views = cluster_views(); views['valkey-cluster-0'][f'{4:040x}']['primary'] = f'{2:040x}'
        with self.assertRaises(ValueError): recovery.identities(views)

    def test_slot_holes_and_overlaps_are_rejected(self):
        """Address repair cannot conceal missing data partitions or a conflicting owner."""
        for slots in [[], [0, 1, 5461]]:
            with self.subTest(slots=slots):
                views = cluster_views()
                for nodes in views.values(): nodes[f'{1:040x}']['slots'] = slots
                with self.assertRaises(ValueError): recovery.identities(views)

    def test_duplicate_local_identity_is_rejected(self):
        """Two Pods cannot be treated as separate retained members with the same local identity."""
        views = cluster_views(); views['valkey-cluster-1'] = deepcopy(views['valkey-cluster-0'])
        with self.assertRaises(ValueError): recovery.identities(views)

    def test_consistently_invalid_replica_target_is_rejected(self):
        """Agreement across all nodes cannot authorize an orphan replica or two replicas of one primary."""
        views = cluster_views()
        for nodes in views.values(): nodes[f'{4:040x}']['primary'] = f'{2:040x}'
        with self.assertRaises(ValueError): recovery.identities(views)

    def test_current_addresses_are_healthy_and_stale_addresses_are_not(self):
        """A repeated check can avoid administrative mutations only when every view uses all current addresses."""
        views = cluster_views(); local, _ = recovery.identities(views)
        pods = {'valkey-cluster-' + str(i): {'ip': '10.52.0.' + str(i + 10)} for i in range(6)}
        self.assertTrue(recovery.healthy_addresses(views, local, pods))
        views['valkey-cluster-0'][f'{2:040x}']['address'] = '10.52.0.220:6379@16379'
        self.assertFalse(recovery.healthy_addresses(views, local, pods))

    def test_active_migration_and_invalid_ranges_are_rejected(self):
        """Transient migration metadata requires investigation instead of automatic repair."""
        for slot in ['[10->-' + 'a' * 40 + ']', '-1', '16384', '5-2', '0-5 4-8']:
            with self.subTest(slot=slot):
                with self.assertRaises(ValueError):
                    recovery.parse_nodes(f'{1:040x} 10.52.0.10:6379@16379 myself,master - 0 0 1 connected {slot}')


if __name__ == '__main__':
    unittest.main()
