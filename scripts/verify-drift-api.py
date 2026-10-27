"""Exercise a running drift API over real HTTP with reference replay, controlled shift and invalid windows."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def run(args):
    """Use labelled test cases without supplying any ground-truth label to a successful assessment."""
    if args.output.exists():
        raise ValueError('Preserve existing HTTP verification')
    bundle=json.loads(args.reference.read_text())
    reference=sha256(json.dumps(bundle,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    def send(path, body=None):
        """Capture actual status and JSON response, including expected client errors."""
        payload=None if body is None else json.dumps(body,allow_nan=False).encode()
        request=Request(args.base_url.rstrip('/')+path,data=payload,headers={'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=20) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    status,initial=send('/health/ready');assert status==200 and initial['reference_id']==reference
    start='2026-10-01T00:00:00Z';end='2026-10-01T01:00:00Z'
    body={'reference_id':reference,'window_id':args.run+'-normal','start':start,'end':end,
          'observations':[{'id':f'probe-{i}','event_timestamp':'2026-10-01T00:30:00Z','available_at':'2026-10-01T00:31:00Z','features':row}
                          for i,row in enumerate(bundle['samples'])]}
    status,normal=send('/v1/drift:compare',body);assert status==200 and not normal['drift_detected']
    assert all(value['psi']==0 for value in normal['features'].values())
    retry=deepcopy(body);retry['observations'].reverse();retry['observations']+=deepcopy(retry['observations'][:10])
    status,replay=send('/v1/drift:compare',retry);assert status==200 and replay==normal
    shifted=deepcopy(body);shifted['window_id']=args.run+'-shift'
    for row in shifted['observations']:
        row['features']['temperature_mean_10m']+=20.0
        row['features']['vibration_max_10m']*=1.8
    status,shift=send('/v1/drift:compare',shifted);assert status==200 and shift['drift_detected']
    assert shift['features']['temperature_mean_10m']['drifted'] and shift['features']['vibration_max_10m']['drifted']
    assert shift['drifted_feature_fraction']==.25
    for name,value in shift['features'].items():
        if name not in ['temperature_mean_10m','vibration_max_10m']:assert value['psi']==0
    statuses={}
    conflict=deepcopy(shifted);conflict['window_id']=body['window_id'];statuses['conflicting_window']=send('/v1/drift:compare',conflict)[0]
    wrong=deepcopy(body);wrong['reference_id']='a'*64;statuses['wrong_reference']=send('/v1/drift:compare',wrong)[0]
    small=deepcopy(body);small['observations']=small['observations'][:31];statuses['31_rows']=send('/v1/drift:compare',small)[0]
    exact=deepcopy(body);exact['window_id']=args.run+'-32';exact['observations']=exact['observations'][:32];statuses['32_rows']=send('/v1/drift:compare',exact)[0]
    invalid=deepcopy(body);invalid['observations'][0]['features']['label']=1;statuses['label_forbidden']=send('/v1/drift:compare',invalid)[0]
    future=deepcopy(body);future['observations'][0]['available_at']='2026-10-01T01:00:01Z';statuses['unavailable_at_window_end']=send('/v1/drift:compare',future)[0]
    assert statuses=={'conflicting_window':409,'wrong_reference':409,'31_rows':422,'32_rows':200,'label_forbidden':422,'unavailable_at_window_end':422}
    status,final=send('/health/ready');assert status==200 and final['stored_windows']==initial['stored_windows']+3
    report={'passed':True,'run':args.run,'reference_id':reference,'lineage':bundle['lineage'],'normal':normal,'controlled_shift':shift,
            'replay_identical':True,'statuses':statuses,'initial_ready':initial,'final_ready':final,
            'code_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope':'Real HTTP contract probes using training-reference vectors and controlled sensor offsets. Probe timestamps are explicit simulation fixtures, not actual recent machine readings. No ground-truth labels used.'}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'normal_drift':normal['drift_detected'],'shift_drift':shift['drift_detected'],'statuses':statuses}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True);parser.add_argument('--run',required=True)
    parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);run(parser.parse_args())
