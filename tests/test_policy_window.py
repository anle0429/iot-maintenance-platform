"""Reject inflated policy counts and incomplete control cohorts using hand-specified synthetic outcomes."""
from copy import deepcopy
import unittest
from llm.policy_window import CASES,CATEGORIES,request_body,summarize
from llm_observability.contracts import PromptPolicy


class PolicyWindowTests(unittest.TestCase):
    """Unique prompts, overlapping categories and measured time have separate meanings."""
    def rows(self):
        """Build a complete synthetic receipt fixture with explicit successful controls and pre-inference rejections."""
        return [{'case_index':i,'request_id':str(i),'request_body':request_body(i),'policy_categories':v['expected_categories'],
            'status':422 if v['expected_categories'] else 200,'response_complete':True,'inference_complete':not bool(v['expected_categories']),
            'usage':None if v['expected_categories'] else {'prompt_tokens':10,'completion_tokens':2,'total_tokens':12},
            'ttft_seconds':None if v['expected_categories'] else .1,'elapsed_seconds':.2,'error_code':'prompt_policy_rejected' if v['expected_categories'] else None} for i,v in enumerate(CASES)]

    def test_cases_have_independently_specified_policy_expectations(self):
        """Check the planned native requests against the existing policy without generating expected categories from it."""
        for i,case in enumerate(CASES):
            with self.subTest(case=case['id']):self.assertEqual(PromptPolicy().inspect(request_body(i)),case['expected_categories'])

    def test_unique_prompts_are_not_summed_category_hits(self):
        """One four-category prompt increases blocked prompts once and category hits four times."""
        value=summarize(self.rows(),36)
        self.assertEqual((value['admissions'],value['allowed_prompts'],value['blocked_prompts'],value['total_category_hits']),(12,3,9,12))
        self.assertEqual(value['category_hits'],dict.fromkeys(CATEGORIES,3));self.assertEqual(value['blocked_prompts_per_observed_minute'],15)
        self.assertEqual(value['provider_reported_tokens'],{'prompt_tokens':30,'completion_tokens':6,'total_tokens':36})

    def test_incomplete_duplicate_or_changed_cohort_is_rejected(self):
        """A favorable subset, duplicated identity or edited policy result must not become a valid frequency measurement."""
        rows=self.rows()
        for value in [rows[:-1],rows+[rows[0]],rows[::-1]]:
            with self.assertRaises(ValueError):summarize(value,36)
        for field,value in [('request_id','0'),('status',200),('policy_categories',[]),('usage',{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2})]:
            changed=deepcopy(rows);changed[1][field]=value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):summarize(changed,36)

    def test_window_and_allowed_generation_are_actual_requirements(self):
        """No zero-duration rate or invented allowed-token/TTFT result may pass the summary guard."""
        for seconds in [0,-1,float('nan'),True]:
            with self.assertRaises(ValueError):summarize(self.rows(),seconds)
        for field,value in [('inference_complete',False),('ttft_seconds',None),('usage',None)]:
            rows=self.rows();rows[0][field]=value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):summarize(rows,36)
