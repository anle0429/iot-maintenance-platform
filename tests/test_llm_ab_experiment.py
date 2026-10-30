"""Check known labeling boundaries, ambiguous model answers and the complete paired comparison denominator."""
from copy import deepcopy
import json,unittest
from llm.ab_experiment import CASES,MODELS,request_body,score_answer,summarize,prometheus_metrics


class ModelExperimentTests(unittest.TestCase):
    """Reject truncated, duplicated, incorrectly typed or selectively omitted model outcomes."""
    def test_four_hand_checked_contract_cases(self):
        """The inclusive upper horizon, true negative, censored observation and downtime are distinct decisions."""
        self.assertEqual([v['expected']['label'] for v in CASES],[1,0,None,None])
        for index,case in enumerate(CASES):
            self.assertTrue(score_answer(index,json.dumps(case['expected']))['correct'])
        for text in ['{"label":1,"label":0}','not JSON','{}','{"label":true,"machine_operating":true,"reason":"failure_in_horizon"}',
                     '{"label":0,"machine_operating":true,"reason":"no_failure_in_horizon"}']:
            self.assertFalse(score_answer(0,text)['correct'])
        self.assertFalse(score_answer(2,'{"label":0,"machine_operating":true,"reason":"no_failure_in_horizon"}')['correct'])
    def records(self):
        """Build explicit unit-test receipts for the scorer/inventory, never native execution evidence."""
        result=[]
        for phase,repeats in [('warmup',[-1]),('measured',range(3))]:
            for variant,model in MODELS.items():
                for repeat in repeats:
                    for case in range(4):
                        text=json.dumps(CASES[case]['expected'])
                        result.append({'phase':phase,'variant':variant,'repeat':repeat,'case_index':case,'request_body':request_body(variant,case),
                            'model':model,'status':200,'complete':True,'text':text,'usage':{'prompt_tokens':80,'completion_tokens':20,'total_tokens':100},
                            'cached_tokens':0,'ttft_seconds':.1,'elapsed_seconds':.4,'score':score_answer(case,text)})
        return result
    def test_wrong_answers_stay_in_the_complete_denominator(self):
        """One incorrect response lowers the measured contract score; it does not disappear as a discarded request."""
        rows=self.records();row=next(v for v in rows if v['phase']=='measured' and v['variant']=='small')
        row['text']='{"label":0,"machine_operating":true,"reason":"no_failure_in_horizon"}';row['score']=score_answer(row['case_index'],row['text'])
        result=summarize(rows);self.assertEqual(result['small']['measured_requests'],12);self.assertEqual(result['small']['correct_contract_answers'],11)
        self.assertEqual(result['large']['correct_contract_answers'],12);self.assertIn(b'variant="small"} 11',prometheus_metrics(result))
    def test_changed_inventory_parameters_usage_and_scores_are_rejected(self):
        """Avoid a favorable but unpaired workload, cached answers or an edited quality result."""
        original=self.records()
        for mutate in [lambda v:v.pop(),lambda v:v.append(deepcopy(v[0])),lambda v:v[0]['request_body'].update(seed=8),
            lambda v:v[0]['usage'].update(total_tokens=101),lambda v:v[0].update(cached_tokens=1),lambda v:v[0].update(ttft_seconds=-1),
            lambda v:v[0]['score'].update(correct=False),lambda v:v[0].update(model=MODELS['large'])]:
            values=deepcopy(original);mutate(values)
            with self.subTest(mutation=mutate),self.assertRaises(ValueError):summarize(values)


if __name__=='__main__':unittest.main()
