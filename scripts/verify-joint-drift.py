"""Independently verify saved joint-drift fixtures with direct Gaussian sums and all199 label permutations."""
import argparse,json,math,sys,time
from hashlib import sha256
from pathlib import Path
from random import Random

ROOT=Path(__file__).resolve().parents[1]
FIELDS=['temperature_mean_10m','vibration_max_10m','current_mean_10m','current_std_10m',
    'rpm_mean_10m','reading_count_10m','machine_age_days','prior_failures_24h']
POLICY_SHA='accaee091ab2d88002646b2e8eb64414f9fbebfcf6e991979f8bdb15e481740c'


def read_json(path):
    """Keep verification inputs bounded; the verifier never executes or mutates evidence files."""
    with path.open('rb') as stream:raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Verification input exceeds1MiB')
    return json.loads(raw)


def run(args):
    """Recompute from input values, independently of the cached-row-total production estimator."""
    if args.output.exists():raise FileExistsError('Preserve existing independent verification')
    raw=(ROOT/'ml/protocols/joint-drift-v1.json').read_bytes()
    if sha256(raw).hexdigest()!=POLICY_SHA:raise ValueError('Joint policy differs from its frozen declaration')
    policy=json.loads(raw);summary={};started=time.perf_counter()
    for name in ['unchanged','association','marginal-offset','constant','nonlinear','maximum']:
        folder=args.evidence/name;reference=read_json(folder/'reference.json');window=read_json(folder/'window.json');report=read_json(folder/'report.json')
        assert policy==report['policy'] and report['policy_sha256']==POLICY_SHA
        if not 32<=len(reference['samples'])<=128 or not 32<=len(window['observations'])<=128:raise ValueError('Verification cohorts require32..128 rows')
        left=sorted(tuple(r[f] for f in FIELDS) for r in reference['samples'])
        right=sorted(tuple(r['features'][f] for f in FIELDS) for r in window['observations']);points=left+right;n=len(points);m=len(left)
        scales=[policy['feature_scales'][f] for f in FIELDS]
        def kernel(a,b):
            """Calculate each Gaussian entry directly from engineering units."""
            return math.exp(-math.fsum(((x-y)/s)**2 for x,y,s in zip(a,b,scales))/(2*policy['sigma']**2))
        gram=[[kernel(a,b) for b in points] for a in points]
        def naive(a,b):
            """Use all AA/BB/AB terms instead of the production row-sum shortcut."""
            return max(0.0,math.fsum(gram[i][j] for i in a for j in a)/len(a)**2+
                math.fsum(gram[i][j] for i in b for j in b)/len(b)**2-2*math.fsum(gram[i][j] for i in a for j in b)/(len(a)*len(b)))
        observed=naive(list(range(m)),list(range(m,n)));assert abs(observed-report['joint']['mmd_squared'])<1e-12
        rng=Random(policy['seed']);permutations=[]
        for _ in range(policy['permutations']):
            indices=list(range(n));rng.shuffle(indices);permutations.append(naive(indices[:m],indices[m:]))
        delta=max(abs(a-b) for a,b in zip(permutations,report['joint']['permutation_statistics'],strict=True));assert delta<1e-12
        count=sum(v>=observed-policy['tie_tolerance'] for v in permutations);tail=(1+count)/(policy['permutations']+1)
        assert count==report['joint']['permutation_exceedances'] and tail==report['joint']['permutation_tail_fraction']
        summary[name]={'independent_observed_mmd_squared':observed,'permutation_statistics_checked':len(permutations),
            'max_permutation_delta':delta,'same_tail_fraction':tail}
    result={'passed':True,'method':'Independent direct AA/BB/AB Gaussian sums; no production estimator import or cached row-total formula',
        'source_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'policy_sha256':POLICY_SHA,'cases':summary,
        'elapsed_seconds':time.perf_counter()-started,'scope':'Numerical verification of saved diagnostic fixtures; no calibrated false-alarm claim'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as stream:stream.write((json.dumps(result,indent=2)+'\n').encode())
    print(json.dumps({'passed':True,'cases':len(summary),'permutations_checked':sum(v['permutation_statistics_checked'] for v in summary.values())}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args())
