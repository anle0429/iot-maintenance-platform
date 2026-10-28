"""Use a hand-computable two-bin fixture and intentional corruption to validate the independent PSI checker."""
from copy import deepcopy
import math,unittest
from hashlib import sha256
from tests.offline_drift_oracle import canonical,compare
from ml.drift_monitoring import FIELDS


class OfflineDriftOracleTests(unittest.TestCase):
    def test_constant_reference_and_half_shift_match_two_bin_arithmetic(self):
        reference={'samples':[{name:1 for name in FIELDS} for _ in range(32)]}
        rid=sha256(canonical(reference)).hexdigest()
        observations=[{'id':str(i),'event_timestamp':'2026-11-22T00:00:00Z','available_at':'2026-11-22T00:00:00Z',
                       'features':{name:0 if i<16 else 1 for name in FIELDS}} for i in range(32)]
        p=(.5/33,32.5/33);q=(16.5/33,16.5/33)
        psi=sum((b-a)*math.log(b/a) for a,b in zip(p,q))
        payload={'reference_id':rid,'start':'2026-11-22T00:00:00Z','end':'2026-11-22T01:00:00Z','observations':observations}
        report={'reference_id':rid,'start':payload['start'],'end':payload['end'],'unique_rows':32,'reference_rows':32,
                'features':{name:{'cuts':[1],'reference_counts':[0,32],'current_counts':[16,16],'psi':psi,'drifted':True} for name in FIELDS},
                'drifted_feature_fraction':1.,'drift_detected':True,
                'policy':{'bins':10,'psi_threshold':.2,'drifted_feature_fraction':.25,'pseudocount':.5}}
        self.assertEqual(compare(reference,payload,report)['drifted_features'],8)
        for change in [lambda r:r['features'][FIELDS[0]].update(psi=0),
                       lambda r:r.update(drift_detected=False),
                       lambda r:r['features'][FIELDS[1]].update(current_counts=[32,0])]:
            wrong=deepcopy(report);change(wrong)
            with self.assertRaises(AssertionError):compare(reference,payload,wrong)


if __name__=='__main__':unittest.main()
