"""Independently recompute native historical-cohort MMD with direct AA/BB/AB sums and every permutation."""
from hashlib import sha256
from random import Random
from collections import Counter
import json,math
from evidence_integrity import ROOT,verified,source_bindings
BASE=ROOT/'docs/evidence/joint-cohort-linux'


def main():
    """Audit source lineage/selection, canonical replay, exact counterfactual and all597 native permutation statistics."""
    read=verified(BASE,17,16);seal=read('source.json');assert source_bindings(seal)==13 and seal==read('source-after.json') and seal['commit']=='876c79ffb9b86b611c8cac90c6ccf971af6bb69d'
    native=read('native.json');container=read('container.json');assert native['passed'] and container['State']['ExitCode']==0 and not container['State']['OOMKilled']
    host=container['HostConfig'];assert host['Memory']==host['MemorySwap']==512*1024**2 and host['NanoCpus']==10**9 and host['NetworkMode']=='none' and host['ReadonlyRootfs'] and host['CapDrop']==['ALL'] and container['Config']['User']=='10001:10001'
    prefix='result/audit/';reference=read(prefix+'reference.json');proof=read(prefix+'verification.json');parentpath=ROOT/'docs/evidence/offline-drift-linux/reference.json';currentpath=ROOT/'docs/evidence/private-gateway-linux/feature-refresh/http-all-machines.json';parent=json.loads(parentpath.read_bytes());current=json.loads(currentpath.read_bytes());policy=json.loads((ROOT/'ml/protocols/joint-drift-v1.json').read_bytes())
    assert reference['samples']==parent['samples'][:64] and reference['lineage']['parent_training_reference']==parent['lineage'] and parent['lineage']['partition']=='train' and parent['lineage']['model_version']=='3'
    assert proof['parent_inputs']=={'reference_sha256':sha256(parentpath.read_bytes()).hexdigest(),'current_sha256':sha256(currentpath.read_bytes()).hexdigest(),'cohort_policy_sha256':sha256((ROOT/'ml/protocols/joint-cohort-v1.json').read_bytes()).hexdigest()}
    selected=sorted([r['features'] for r in current if r['prediction_status']==200 and r['features']['operating_context']['status']=='operating'],key=lambda v:v['machine_id'])[:64];assert [v['machine_id'] for v in selected]==proof['selected_machine_ids']
    window=read(prefix+'current-window.json');assert [v['features'] for v in window['observations']]==[v['features'] for v in selected]
    assert all(o['event_timestamp']==v['feature_event_ts'] and o['available_at']==v['as_of'] for o,v in zip(window['observations'],selected,strict=True))
    replay=read(prefix+'replay-window.json');assert {v['id']:v for v in replay['observations']}=={v['id']:v for v in window['observations']} and proof['canonical_replay_equal']
    rotated=read(prefix+'rotated-window.json');fields=list(policy['feature_scales']);vibrations=[v['features']['vibration_max_10m'] for v in window['observations']];assert [v['features']['vibration_max_10m'] for v in rotated['observations']]==vibrations[1:]+vibrations[:1]
    assert all(Counter(v['features'][f] for v in window['observations'])==Counter(v['features'][f] for v in rotated['observations']) for f in fields)
    maximum=0.0
    for name in ['control','current','rotated']:
        request=read(prefix+name+'-window.json');report=read(prefix+name+'-report.json');left=sorted(tuple(v[f] for f in fields) for v in reference['samples']);right=sorted(tuple(v['features'][f] for f in fields) for v in request['observations']);points=left+right;scales=list(policy['feature_scales'].values());size=128;m=64
        gram=[[math.exp(-math.fsum(((x-y)/s)**2 for x,y,s in zip(a,b,scales,strict=True))/(2*policy['sigma']**2)) for b in points] for a in points]
        def direct(a,b):
            """Sum each within/between term directly; do not import the production cached-row estimator."""
            return max(0.0,math.fsum(gram[i][j] for i in a for j in a)/len(a)**2+math.fsum(gram[i][j] for i in b for j in b)/len(b)**2-2*math.fsum(gram[i][j] for i in a for j in b)/(len(a)*len(b)))
        observed=direct(list(range(m)),list(range(m,size)));assert abs(observed-report['joint']['mmd_squared'])<1e-12
        rng=Random(policy['seed']);values=[]
        for _ in range(199):
            indices=list(range(size));rng.shuffle(indices);values.append(direct(indices[:m],indices[m:]))
        delta=max(abs(a-b) for a,b in zip(values,report['joint']['permutation_statistics'],strict=True));maximum=max(maximum,delta);assert delta<1e-12
        count=sum(v>=observed-policy['tie_tolerance'] for v in values);assert count==report['joint']['permutation_exceedances'] and (1+count)/200==report['joint']['permutation_tail_fraction'] and report['joint']['diagnostic_alert']==((1+count)/200<=.05)
        assert report['joint']['p_value'] is None and report['joint']['exchangeability_verified'] is False and report['resources']['gram_payload_bytes']==131072
    assert proof['reports']['control']=={'mmd_squared':0.0,'tail_fraction':1.0,'diagnostic_alert':False,'psi_alert':False}
    print('PASS17payload/13Gitbindings;nativeLinux1CPU512MiB/networknone;64explicittrain+64retainednativecohort;3directMMDoracles+597permutationstatistics;canonicalreplay;exactone-stepvibrationcounterfactual;maxdelta='+str(maximum)+'. Historical diagnostic only; learner confirmed MMD was not taught28Oct2026.')


if __name__=='__main__':main()
