"""Use small known answers to prevent shared-state writes or false replay acceptance in CI."""
from copy import deepcopy
import unittest
from feature_store.ci_contract import identity,verify_sequence


def fixture():
    """Represent two real materialization intervals followed by a read-only replay."""
    first={'passed':True,'binding':{'project':'iot_ci_mat_1','source':'immutable-A'},
        'online_comparison':{'passed':True,'machines':200},'mode':'initial','cutoff':'2026-11-29T23:00:00+00:00',
        'previous_cutoff':None,'changed_machine_vectors':None,'value_sha256_by_machine':{str(n):'A' for n in range(200)},
        'native_registry_materialization_intervals':[['22','23']]}
    second=deepcopy(first);second.update(mode='incremental',cutoff='2026-11-30T00:00:00+00:00',
        previous_cutoff=first['cutoff'],changed_machine_vectors=200,value_sha256_by_machine={str(n):'B' for n in range(200)},
        native_registry_materialization_intervals=[['22','23'],['23','24']])
    replay=deepcopy(second);replay.update(mode='checkpoint_replay',previous_cutoff=second['cutoff'],changed_machine_vectors=0)
    return {'initial':first,'incremental':second,'replay':replay}


class MaterializationCITests(unittest.TestCase):
    """Exercise namespace boundaries and incorrect source/online/interval outcomes."""
    def test_safe_identity(self):
        'Verify safe identity.'
        self.assertEqual(identity('12'),('mat-jenkins-12','iot_ci_mat_12'))
        for value in ['0','01','../2','1;echo secret','-1','1/2','',None,1]:
            with self.assertRaises(ValueError):identity(value)

    def test_native_known_answer(self):
        'Verify native known answer.'
        self.assertTrue(verify_sequence(fixture(),'iot_ci_mat_1')['passed'])

    def test_shared_project_and_changed_source_rejected(self):
        'Verify shared project and changed source rejected.'
        for phase in ['initial','incremental','replay']:
            for field,value in [('project','iot_materialization'),('source','immutable-B')]:
                reports=fixture();reports[phase]['binding'][field]=value
                with self.assertRaises(ValueError):verify_sequence(reports,'iot_ci_mat_1')

    def test_missing_machine_or_failed_comparison_rejected(self):
        'Verify missing machine or failed comparison rejected.'
        for phase in ['initial','incremental','replay']:
            for field,value in [('machines',199),('passed',False)]:
                reports=fixture();reports[phase]['online_comparison'][field]=value
                with self.assertRaises(ValueError):verify_sequence(reports,'iot_ci_mat_1')

    def test_replay_changed_values_or_extra_interval_rejected(self):
        'Verify replay changed values or extra interval rejected.'
        for field,value in [('changed_machine_vectors',1),('value_sha256_by_machine',{'M':'C'}),
                ('native_registry_materialization_intervals',[['22','23'],['23','24'],['24','24']])]:
            reports=fixture();reports['replay'][field]=value
            with self.assertRaises(ValueError):verify_sequence(reports,'iot_ci_mat_1')

    def test_wrong_previous_checkpoint_rejected(self):
        'Verify wrong previous checkpoint rejected.'
        for phase in ['incremental','replay']:
            reports=fixture();reports[phase]['previous_cutoff']='2026-11-28T00:00:00+00:00'
            with self.assertRaises(ValueError):verify_sequence(reports,'iot_ci_mat_1')


if __name__=='__main__':unittest.main()
