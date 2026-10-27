"""Check time-cut purge, validation tie breaks and portable logistic arithmetic with known answers."""

from copy import deepcopy
import math,unittest
from pathlib import Path

from ml.protocol import load_protocol
from ml.training_contract import score_portable,select_threshold,split_for_timestamp


class TrainingContractTests(unittest.TestCase):
    """Keep test labels outside threshold selection and preserve the exact cutoff exclusion."""

    def test_cutoff_and_label_availability(self):
        """A label available exactly at the cut is purged; the event at the cut enters validation."""
        p=load_protocol(Path('ml/protocols/medium-60d-v1.json'))
        self.assertEqual(split_for_timestamp('2026-11-11T22:00:00Z','2026-11-11T23:00:30Z',p),'train')
        self.assertIsNone(split_for_timestamp('2026-11-11T23:00:00Z','2026-11-12T00:00:00Z',p))
        self.assertEqual(split_for_timestamp('2026-11-12T00:00:00Z','2026-11-12T01:00:30Z',p),'validation')
        self.assertIsNone(split_for_timestamp('2026-11-30T00:00:00Z','2026-11-30T01:00:30Z',p))
        with self.assertRaises(ValueError):split_for_timestamp('2026-11-12T00:00:00','2026-11-12T01:00:30Z',p)

    def test_validation_tie_break(self):
        """F1 dominates precision; otherwise higher precision then higher threshold wins."""
        rows=[{'threshold':x/10,'f1':0.2,'precision':0.5} for x in range(1,10)]
        self.assertEqual(select_threshold(rows)['threshold'],0.9)
        rows[3]['precision']=0.9;self.assertEqual(select_threshold(rows)['threshold'],0.4)
        rows[1]['f1']=0.3;self.assertEqual(select_threshold(rows)['threshold'],0.2)
        with self.assertRaises(ValueError):select_threshold(rows[:-1])

    def test_portable_probability_zero_std_and_extremes(self):
        """Constant standardized inputs contribute zero; stable sigmoid handles both numerical extremes."""
        model={'features':list('abcdefgh'),'mean':[1]*8,'std':[2,0,1,1,1,1,1,1],
               'coefficients':[2,9999,0,0,0,0,0,0],'intercept':-1}
        self.assertAlmostEqual(score_portable([3,100,0,0,0,0,0,0],model),1/(1+math.exp(-1)))
        for intercept,answer in [(1000,1.0),(-1000,0.0)]:
            v=deepcopy(model);v['intercept']=intercept;self.assertEqual(score_portable([1]*8,v),answer)
        with self.assertRaises(ValueError):score_portable([float('nan')]*8,model)
        with self.assertRaises(ValueError):score_portable([1]*7,model)


if __name__=='__main__':unittest.main()
