"""Verify ID stability, strict target separation and join cardinality with known eligible and censored samples."""
from copy import deepcopy
import unittest
from feature_store.training_samples import FEATURES,grain,sample_id,separate_tables,join_tables


class TrainingSampleTests(unittest.TestCase):
    """Protect the training boundary from duplicate joins and accidental future-label inputs."""
    def rows(self):
        """Build a known feature/target pair containing an extra future field that must never be exported."""
        feature={'machine_id':'P-001','event_timestamp':'2026-10-01T01:00:00Z',**dict.fromkeys(FEATURES,8.),
                 'next_failure_ts':'2026-10-01T01:30:00Z','failure_in_next_1h':1}
        target={'machine_id':'P-001','event_timestamp':'2026-10-01T01:00:00+00:00','label_status':'eligible','failure_in_next_1h':1}
        return feature,target

    def test_separate_exact_columns_and_join(self):
        """Future fields are excluded by construction; a reordered target table reconstructs the same training rows."""
        f,t=self.rows();result=separate_tables([f],[t],'a'*64)
        self.assertEqual(set(result['labels'][0]),{'sample_id','failure_in_next_1h'})
        self.assertNotIn('failure_in_next_1h',result['features'][0]);self.assertNotIn('next_failure_ts',result['features'][0])
        joined=join_tables(result['features'],list(reversed(result['labels'])))
        self.assertEqual(joined[0]['failure_in_next_1h'],1)
        self.assertEqual(grain(f),grain(t))

    def test_version_scoped_identity(self):
        """The same grain is stable within a dataset and distinct across immutable baseline/drift versions."""
        f,t=self.rows();key=grain(f)
        self.assertEqual(sample_id('a'*64,key),sample_id('a'*64,grain(t)))
        self.assertNotEqual(sample_id('a'*64,key),sample_id('b'*64,key))
        with self.assertRaises(ValueError):sample_id('unsealed',key)
        with self.assertRaises(ValueError):grain(dict(f,event_timestamp='2026-10-01T01:00:00'))

    def test_duplicates_and_orphans_fail(self):
        """Duplicate or missing rows fail rather than silently changing class balance or sample counts."""
        f,t=self.rows()
        for left,right in [([f,f],[t]),([f],[t,t]),([f],[dict(t,machine_id='P-002')])]:
            with self.assertRaises(ValueError):separate_tables(left,right,'a'*64)
        result=separate_tables([f],[t],'a'*64)
        for left,right in [(result['features']*2,result['labels']),(result['features'],result['labels']*2),
                           (result['features'],[dict(result['labels'][0],sample_id='other')])]:
            with self.assertRaises(ValueError):join_tables(left,right)

    def test_stops_censoring_and_reading_boundary(self):
        """Unobservable labels stay absent; five readings pass and fewer than five do not become negative examples."""
        f,t=self.rows();features=[dict(f,reading_count_10m=5)];labels=[t]
        for i,status in enumerate(['stopped','censored','maintenance'],2):
            features.append(dict(f,machine_id=f'P-{i:03}'));labels.append(dict(t,machine_id=f'P-{i:03}',label_status=status,failure_in_next_1h=None))
        features.append(dict(f,machine_id='P-005',reading_count_10m=4));labels.append(dict(t,machine_id='P-005'))
        result=separate_tables(features,labels,'a'*64)
        self.assertEqual(len(result['features']),1)
        self.assertEqual(result['excluded'],{'stopped':1,'censored':1,'maintenance':1,'insufficient_readings':1})
        for bad in [True,None,2]:
            with self.assertRaises(ValueError):separate_tables([f],[dict(t,failure_in_next_1h=bad)],'a'*64)
        with self.assertRaises(ValueError):separate_tables([f],[dict(t,label_status='censored')],'a'*64)

    def test_replay_and_corrupt_values(self):
        """Pure exports are repeatable without mutating the input; malformed numeric values or schemas fail."""
        f,t=self.rows();saved=deepcopy(f)
        self.assertEqual(separate_tables([f],[t],'a'*64),separate_tables([f],[t],'a'*64));self.assertEqual(f,saved)
        for bad in [True,float('nan'),float('inf'),None]:
            with self.assertRaises(ValueError):separate_tables([dict(f,temperature_mean_10m=bad)],[t],'a'*64)
        result=separate_tables([f],[t],'a'*64)
        with self.assertRaises(ValueError):join_tables(result['features'],[dict(result['labels'][0],label_status='eligible')])


if __name__=='__main__':unittest.main()
