"""Audit joint sensor drift with bounded Gaussian MMD, retaining PSI and explicit inference limits."""
from array import array
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
from random import Random

from services.drift_api.contracts import CompareRequest
from services.drift_api.detector import DriftDetector,canonical_bytes
from services.predictor.contracts import FeatureVector

POLICY_PATH=Path(__file__).resolve().parent/'protocols/joint-drift-v1.json'
POLICY_SHA256='accaee091ab2d88002646b2e8eb64414f9fbebfcf6e991979f8bdb15e481740c'


def load_policy(path=POLICY_PATH):
    """Reject policy edits made after the known-answer experiment was declared."""
    raw=Path(path).read_bytes()
    if sha256(raw).hexdigest()!=POLICY_SHA256:raise ValueError('Joint drift policy differs from the frozen declaration')
    return json.loads(raw)


def gaussian_gram(points,scales,sigma):
    """Store one symmetric bounded matrix as float64 values; scales never fit the compared data."""
    size=len(points);gram=array('d',[0.0])*(size*size)
    for i,left in enumerate(points):
        gram[i*size+i]=1.0
        for j in range(i):
            distance=math.fsum(((a-b)/scale)**2 for a,b,scale in zip(left,points[j],scales,strict=True))
            value=math.exp(-distance/(2*sigma*sigma))
            gram[i*size+j]=value;gram[j*size+i]=value
    return gram


def partition_mmd_squared(gram,row_sums,total,left):
    """Evaluate the biased statistic using a group's sum and cached full-matrix row totals."""
    size=len(row_sums);m=len(left);n=size-m
    aa=math.fsum(gram[i*size+j] for i in left for j in left)
    ab=math.fsum(row_sums[i] for i in left)-aa
    bb=total-aa-2*ab
    # A Gaussian Gram matrix is positive semidefinite; clamp only cancellation below zero.
    return max(0.0,aa/(m*m)+bb/(n*n)-2*ab/(m*n))


class JointDriftAudit:
    """Compare a predefined small cohort without changing the deployed PSI API or triggering training."""
    def __init__(self,bundle):
        """Validate and copy the reference, then freeze its feature tuples and operational policy."""
        self.policy=load_policy();limit=self.policy['max_unique_rows_per_group']
        if not isinstance(bundle,dict) or not isinstance(bundle.get('samples'),list) or not self.policy['min_unique_rows']<=len(bundle['samples'])<=limit:
            raise ValueError('Joint reference requires32..128 rows; no implicit subsampling')
        self.detector=DriftDetector(deepcopy(bundle));self.fields=list(FeatureVector.model_fields)
        self.scales=[self.policy['feature_scales'][n] for n in self.fields]
        vectors=[FeatureVector.model_validate(row).model_dump() for row in bundle['samples']]
        self.reference=sorted(tuple(row[n] for n in self.fields) for row in vectors)

    def compare(self,request:CompareRequest):
        """Validate availability/replay identity before allocating the capped matrix or resampling labels."""
        window=request.canonical();count=len(window['observations'])
        if window['reference_id']!=self.detector.reference_id:raise ValueError('Reference does not match this joint audit')
        if count>self.policy['max_unique_rows_per_group']:raise ValueError('Joint window exceeds128 unique rows; no implicit subsampling')
        current=sorted(tuple(row['features'][n] for n in self.fields) for row in window['observations'])
        points=self.reference+current;size=len(points);m=len(self.reference)
        gram=gaussian_gram(points,self.scales,self.policy['sigma'])
        row_sums=[math.fsum(gram[i*size:(i+1)*size]) for i in range(size)];total=math.fsum(row_sums)
        observed=partition_mmd_squared(gram,row_sums,total,list(range(m)))
        rng=Random(self.policy['seed']);permutations=[]
        for _ in range(self.policy['permutations']):
            labels=list(range(size));rng.shuffle(labels)
            permutations.append(partition_mmd_squared(gram,row_sums,total,labels[:m]))
        exceedances=sum(value>=observed-self.policy['tie_tolerance'] for value in permutations)
        tail=(1+exceedances)/(len(permutations)+1)
        return {'format':'iot-joint-drift-audit-v1','reference_id':self.detector.reference_id,
            'window_id':window['window_id'],'window_sha256':sha256(canonical_bytes(window)).hexdigest(),
            'policy_sha256':POLICY_SHA256,'policy':deepcopy(self.policy),'reference_rows':m,'current_unique_rows':count,
            'psi':deepcopy(self.detector.compare(window)),
            'joint':{'mmd_squared':observed,'permutation_tail_fraction':tail,'permutation_exceedances':exceedances,
                'permutation_statistics':permutations,'diagnostic_alert':tail<=self.policy['diagnostic_tail_threshold'],
                'p_value':None,'exchangeability_verified':False},
            'resources':{'matrix_rows':size,'gram_payload_bytes':len(gram)*gram.itemsize,
                'gram_payload_cap_bytes':(2*self.policy['max_unique_rows_per_group'])**2*gram.itemsize},
            'scope':self.policy['inference_scope']}
