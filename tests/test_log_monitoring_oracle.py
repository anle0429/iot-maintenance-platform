"""Protect independent nanosecond/source-label and interval-boundary arithmetic with known answers."""
from copy import deepcopy
import unittest
from tests.log_monitoring_oracle import timestamp_ns,raw_records,compare_logs,compare_counts


class LogOracleTests(unittest.TestCase):
    def test_fraction_and_timezone_are_exact_to_one_nanosecond(self):
        self.assertEqual(timestamp_ns('1970-01-01T00:00:00.000000001Z'),1)
        self.assertEqual(timestamp_ns('1970-01-01T01:00:00.123456789+01:00'),123456789)
        with self.assertRaises(ValueError):timestamp_ns('2026-10-28T00:00:00')

    def test_raw_host_and_cri_messages_keep_their_original_clocks_and_labels(self):
        mapping=[{'file':'host','kind':'host-file','time_key':'time','labels':{'service':'prometheus','source':'host-file'}},
            {'file':'pod','kind':'cri','labels':{'service':'predictor','source':'cri','pod':'known-pod'}}]
        records=raw_records(mapping,{'host':b'time=1970-01-01T00:00:01Z level=INFO msg="two words"\n',
            'pod':b'1970-01-01T00:00:02.000000003Z stderr F INFO: actual message\n'})
        self.assertEqual([r['timestamp_ns'] for r in records],[10**9,2*10**9+3])
        self.assertEqual(records[1]['line'],'INFO: actual message');self.assertEqual(records[1]['labels']['pod'],'known-pod')
        self.assertEqual(records[0]['labels']['detected_level'],'info')
        response={'status':'success','data':{'resultType':'streams','result':[{'stream':r['labels'],'values':[[str(r['timestamp_ns']),r['line']]]} for r in records]}}
        self.assertEqual(compare_logs(records,response,0,3*10**9),2)
        wrong=deepcopy(response);wrong['data']['result'][1]['values'][0][0]=str(2*10**9+4)
        with self.assertRaises(ValueError):compare_logs(records,wrong,0,3*10**9)

    def test_incomplete_source_records_or_partial_cri_are_not_claimed_complete(self):
        mapping=[{'file':'x','kind':'cri','labels':{'service':'predictor','source':'cri'}}]
        for raw in [b'1970-01-01T00:00:00Z stderr P INFO: partial\n',b'1970-01-01T00:00:00Z stderr F INFO: no-newline']:
            with self.assertRaises(ValueError):raw_records(mapping,{'x':raw})

    def test_distinct_messages_with_the_same_original_time_must_keep_that_time(self):
        mapping=[{'file':'x','kind':'host-file','time_key':'time','labels':{'service':'node-exporter','source':'host-file'}}]
        raw=b'time=1970-01-01T00:00:01Z level=INFO msg=first\ntime=1970-01-01T00:00:01Z level=INFO msg=second\n'
        records=raw_records(mapping,{'x':raw});self.assertEqual([r['timestamp_ns'] for r in records],[10**9,10**9])
        response={'status':'success','data':{'resultType':'streams','result':[{'stream':records[0]['labels'],
            'values':[[str(10**9),records[0]['line']],[str(10**9+1),records[1]['line']]]}]}}
        with self.assertRaises(ValueError):compare_logs(records,response,0,2*10**9)
        response['data']['result'][0]['values'][1][0]=str(10**9)
        self.assertEqual(compare_logs(records,response,0,2*10**9),2)

    def test_count_window_is_left_open_and_right_closed_and_missing_positives_fail(self):
        records=[{'timestamp_ns':n*10**9,'labels':{'service':'predictor','source':'cri','stream':'stderr'}} for n in [0,1,60,61]]
        response={'status':'success','data':{'resultType':'matrix','result':[{'metric':{'service':'predictor'},'values':[[60,'2'],[120,'1']]}]}}
        proof=compare_counts(records,response,60*10**9,120*10**9,60*10**9)
        self.assertEqual(proof['positive_samples'],2)
        response['data']['result'][0]['values'][0][1]='3'
        with self.assertRaises(ValueError):compare_counts(records,response,60*10**9,120*10**9,60*10**9)


if __name__=='__main__':unittest.main()
