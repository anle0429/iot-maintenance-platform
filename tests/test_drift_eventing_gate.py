"""Reject incomplete mutation runs and unrelated source scopes before a native drift release can deploy."""
from copy import deepcopy
from pathlib import Path
import importlib.util,unittest
SPEC=importlib.util.spec_from_file_location('drift_gate',Path(__file__).resolve().parents[1]/'scripts/test-drift-eventing.py')
GATE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(GATE)


class GateTests(unittest.TestCase):
    """Use small independent result inventories with known strict threshold outcomes."""
    def fixture(self):
        """Five killed mutants in each required module are a complete ten-mutant example."""
        coverage={'files':{p:{} for p in GATE.FILES},'totals':{'num_statements':10,'covered_lines':10,'num_branches':10,'covered_branches':10,'excluded_lines':0}}
        stats=dict.fromkeys(['survived','no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault'],0);stats.update(killed=10,total=10)
        text='\n'.join(f'services.drift_api.{name}.x_receive__mutmut_{i}: killed' for name in ['app','events'] for i in range(1,6))
        return coverage,stats,text

    def test_complete_known_inventory_is_accepted(self):
        'Verify complete known inventory is accepted.'
        self.assertEqual(GATE.validate(*self.fixture()),100)

    def test_timeout_wrong_scope_duplicate_and_threshold_cannot_pass(self):
        'Verify timeout wrong scope duplicate and threshold cannot pass.'
        c,s,t=self.fixture()
        for change in ['timeout','coverage','excluded','scope','duplicate','identity','score']:
            cc,ss,tt=deepcopy(c),deepcopy(s),t
            if change=='timeout':ss.update(killed=9,timeout=1)
            elif change=='coverage':cc['totals']['covered_branches']=9
            elif change=='excluded':cc['totals']['excluded_lines']=1
            elif change=='scope':cc['files'].pop('services/drift_api/app.py')
            elif change=='duplicate':tt+='\n'+tt.splitlines()[0]
            elif change=='identity':tt=tt.replace('drift_api.app','other.app')
            else:ss.update(killed=8,survived=2);tt=tt.replace(': killed',': survived',2)
            with self.subTest(change=change),self.assertRaises(ValueError):GATE.validate(cc,ss,tt)


if __name__=='__main__':unittest.main()
