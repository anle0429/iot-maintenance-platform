"""Check evidence-question scoring without generating, repairing or selecting a model answer."""
import unittest
from llm.data_validation import score_answer,request_body


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


if __name__=='__main__':unittest.main()
