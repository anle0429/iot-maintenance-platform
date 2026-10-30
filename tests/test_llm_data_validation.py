"""Check evidence-question scoring without generating, repairing or selecting a model answer."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from llm.data_validation import score_answer,request_body,resume_prefix
from llm_observability.contracts import StreamObservation


class DataAnswerTests(unittest.TestCase):
    """Keep numeric coercions, malformed JSON and duplicate fields from inflating factual acceptance."""
    def test_exact_types_and_numeric_representation(self):
        """Integer and float spellings can denote one measured value; boolean and string coercions cannot."""
        expected={'count':3,'flag':False}
        self.assertTrue(score_answer('{"count":3.0,"flag":false}',expected)['correct'])
        for text in ['{"count":true,"flag":false}','{"count":"3","flag":false}','{"count":3,"flag":0}',
                     '{"count":3,"flag":true}','{"count":4,"flag":false}']:
            with self.subTest(text=text):self.assertFalse(score_answer(text,expected)['correct'])

    def test_json_and_schema_rejections(self):
        """Extra fields, duplicate keys, arrays and markdown fences remain failed outcomes."""
        for text in ['{"x":1,"x":1}','{"x":1,"extra":0}','[]','```json\n{"x":1}\n```','{"x":NaN}','{"x":Infinity}']:
            with self.subTest(text=text):self.assertFalse(score_answer(text,{'x':1})['correct'])
        self.assertFalse(score_answer('{"x":'+str(10**400)+'}',{'x':1})['correct'])

    def test_expected_answer_is_not_in_request(self):
        """The evaluator sends the context/question and fixed controls, keeping the answer contract out of the prompt."""
        body=request_body({'context':{'observed':17},'question':'Return an integer count.','expected':{'secret_expected_marker':17}})
        self.assertNotIn('secret_expected_marker',str(body));self.assertEqual(body['seed'],7);self.assertEqual(body['temperature'],0)
        self.assertNotIn('tools',body);self.assertTrue(body['stream'])

    def prefix(self):
        """Construct a complete, deliberately wrong original SSE answer to verify score-independent continuation."""
        cases=[{'id':str(i),'context':{},'question':'Return x.','expected':{'x':1}} for i in range(2)]
        reply='{"x":0}'
        events=[{'choices':[{'delta':{'content':reply},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'prompt_tokens':3,'completion_tokens':4,'total_tokens':7}}]
        wire=(''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode()
        parser=StreamObservation();parser.feed(wire,.1);observation=parser.finish(.2)
        record={'case_id':'0','http_status':200,'request_id':'native-id','wire_sha256':sha256(wire).hexdigest(),
                'chunks':[{'offset':0,'bytes':len(wire),'elapsed_seconds':.1}], 'elapsed_seconds':.2,
                'observation':observation,'reply':reply,'score':score_answer(reply,cases[0]['expected'])}
        return cases,{'request':request_body(cases[0]),'wire':wire,'result':record}

    def test_resume_keeps_wrong_completed_answers(self):
        """A wrong completed first outcome must remain in the final cohort and is never regenerated to improve its score."""
        cases,item=self.prefix();resumed=resume_prefix(cases,[item],cases)
        self.assertEqual(resumed,[item['result']]);self.assertFalse(resumed[0]['score']['correct'])

    def test_resume_rejects_changed_requests_or_receipts(self):
        """Edited bytes, forged scores, changed controls and incomplete responses cannot be carried forward."""
        cases,item=self.prefix()
        for change in ['request','wire','score','incomplete','case']:
            changed=deepcopy(item)
            if change=='request':changed['request']['seed']=9
            elif change=='wire':changed['wire']+=b'edited'
            elif change=='score':changed['result']['score']['correct']=True
            elif change=='incomplete':changed['result']['observation']['complete']=False
            else:changed['result']['case_id']='1'
            with self.subTest(change=change),self.assertRaises(ValueError):resume_prefix(cases,[changed],cases)
        with self.assertRaises(ValueError):resume_prefix(cases,[],cases)
        different=deepcopy(cases);different[0]['question']='Other question.'
        with self.assertRaises(ValueError):resume_prefix(cases,[item],different)


if __name__=='__main__':unittest.main()
