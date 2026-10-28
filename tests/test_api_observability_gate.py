"""Reject incomplete mutation inventories and inaccurate or failed native JUnit reports."""
from pathlib import Path
import importlib.util,tempfile,unittest

SPEC=importlib.util.spec_from_file_location('observe_gate',Path(__file__).resolve().parents[1]/'scripts/test-api-observability.py')
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)


def fixture(killed=9,survived=1):
    """Create explicit finished statuses with two native identity forms across multiple lines."""
    stats={k:0 for k in ['no_tests','skipped','suspicious','timeout','check_was_interrupted_by_user','segfault']}
    stats.update(killed=killed,survived=survived,total=killed+survived)
    text='\n'.join('    services.observed.'+('x_observe' if i%2 else 'xǁPrivateSpanExporterǁexport')+'__mutmut_'+str(i+1)+': '+('killed' if i<killed else 'survived') for i in range(killed+survived))+'\n'
    return stats,text


class MutationGateTests(unittest.TestCase):
    """The strict gate must fail even when coverage was perfect or a console looked complete."""
    def test_complete_multiline_unicode_inventory(self):
        stats,text=fixture();self.assertEqual(MODULE.validate_mutation(stats,text),90)

    def test_exactly_eighty_and_lower_are_rejected(self):
        for killed in [0,7,8]:
            with self.subTest(killed=killed),self.assertRaisesRegex(ValueError,'exceed80'):MODULE.validate_mutation(*fixture(killed,10-killed))

    def test_missing_duplicate_or_foreign_identity_is_rejected(self):
        stats,text=fixture()
        for altered in [text.split('\n',1)[1],text+text.splitlines()[0]+'\n',text.replace('services.observed','services.other'),text.replace('__mutmut_1:', '__mutmut_0:'),text.replace(': killed',': survived',1)]:
            with self.subTest(text=altered),self.assertRaisesRegex(ValueError,'inventory'):MODULE.validate_mutation(stats,altered)

    def test_unfinished_negative_boolean_or_inconsistent_counts_are_rejected(self):
        stats,text=fixture()
        for changed in [{'timeout':1},{'no_tests':1},{'killed':True},{'survived':-1},{'total':11},{'total':0}]:
            with self.subTest(changed=changed),self.assertRaises(ValueError):MODULE.validate_mutation({**stats,**changed},text)


class RegressionReportTests(unittest.TestCase):
    """Prevent subtest suite totals and malformed pass counters from misrepresenting executed cases."""
    def report(self,xml):
        """Validate actual XML bytes through the production report reader."""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'junit.xml';path.write_text(xml)
            return MODULE.regression_counts(path)

    def test_suite_count_with_subtests_is_not_a_physical_case_count(self):
        self.assertEqual(self.report('<testsuites><testsuite tests="3"><testcase name="a"/><testcase name="b"/></testsuite></testsuites>'),
                         {'regression_main_testcase_elements':2,'regression_suite_reported_tests':3})

    def test_unexecuted_skipped_or_hidden_case_failure_is_rejected(self):
        for xml in ['<testsuites/>','<testsuite tests="0"/>','<testsuite tests="2" skipped="1"><testcase/></testsuite>',
                    '<testsuite tests="1"><testcase><failure/></testcase></testsuite>',
                    '<testsuite tests="1"><testcase><error/></testcase></testsuite>',
                    '<testsuite tests="1"><testcase><skipped/></testcase></testsuite>',
                    '<testsuite tests="1"><testcase/><testcase/></testsuite>']:
            with self.subTest(xml=xml),self.assertRaises(ValueError):self.report(xml)

    def test_nested_aggregate_suite_cannot_double_count_cases(self):
        with self.assertRaisesRegex(ValueError,'flat'):
            self.report('<testsuite tests="1"><testsuite tests="1"><testcase/></testsuite></testsuite>')


if __name__=='__main__':unittest.main()
