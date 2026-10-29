"""Enable official kagent0.10.3 UI forwarding of the gateway-authenticated user while preserving every agent/model/pool."""
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,json,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.gateway.ui_identity import ui_identity_patch


def run(args):
    """Patch only the existing UI environment, preserving native before/after objects and its actual running image identity."""
    out=args.output;out.mkdir(parents=True,exist_ok=False);k=['/usr/local/bin/k3s','kubectl']
    def get(*values):
        """Read the actual component state without reading credentials."""
        return json.loads(subprocess.check_output(k+list(values)+['-o','json'],stderr=subprocess.PIPE,timeout=30))
    def save(name,value):
        """Record original non-secret native state and safe update/rollout facts."""
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    before=get('get','deployment','kagent-ui','-n','iot-agents');patch=ui_identity_patch(before)
    if not patch:raise ValueError('The expected UI identity setting is already present; inspect its previous release')
    save('before-ui.json',before);save('patch.json',patch)
    original={}
    for kind,name in [('modelconfig','default-model-config'),('workerpools',None),('sandboxagents',None)]:
        value=get('get',kind,*([name] if name else []),'-n','iot-agents');save('before-'+kind+'.json',value);original[kind]=value
    selector='app.kubernetes.io/component=ui,app.kubernetes.io/instance=kagent'
    pods=get('get','pods','-n','iot-agents','-l',selector);save('before-pods.json',pods)
    old_images={c['imageID'] for p in pods['items'] if not p['metadata'].get('deletionTimestamp') for c in p['status']['containerStatuses'] if c['name']=='ui'}
    assert len(old_images)==1
    native=subprocess.run(k+['patch','deployment','kagent-ui','-n','iot-agents','--type=json','--patch-file',str(out/'patch.json')],capture_output=True,timeout=30)
    (out/'patch.txt').write_bytes(native.stdout+native.stderr);assert native.returncode==0
    native=subprocess.run(k+['rollout','status','deployment/kagent-ui','-n','iot-agents','--timeout=180s'],capture_output=True,timeout=210)
    (out/'rollout.txt').write_bytes(native.stdout+native.stderr);assert native.returncode==0
    after=get('get','deployment','kagent-ui','-n','iot-agents');save('after-ui.json',after)
    expected=json.loads(json.dumps(before['spec']));expected['template']['spec']['containers'][0]['env']=patch[-1]['value'];assert after['spec']==expected
    for kind,name in [('modelconfig','default-model-config'),('workerpools',None),('sandboxagents',None)]:
        value=get('get',kind,*([name] if name else []),'-n','iot-agents');save('after-'+kind+'.json',value)
        if name:assert value['spec']==original[kind]['spec']
        else:assert {p['metadata']['uid']:p['spec'] for p in value['items']}=={p['metadata']['uid']:p['spec'] for p in original[kind]['items']}
    pods=get('get','pods','-n','iot-agents','-l',selector);save('after-pods.json',pods)
    live=[p for p in pods['items'] if not p['metadata'].get('deletionTimestamp')]
    assert live and all(c['ready'] and c['imageID'] in old_images for p in live for c in p['status']['containerStatuses'] if c['name']=='ui')
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':{name:sha256((ROOT/name).read_bytes()).hexdigest() for name in ['scripts/configure-agent-ui-identity.py','infra/gateway/ui_identity.py']},
        'ui_uid':after['metadata']['uid'],'actual_image_ids':sorted(old_images),'setting':'KAGENT_ADDITIONAL_FORWARDED_HEADERS=X-User-Id',
        'model_agents_pools_preserved':True,'scope':'Official UI header forwarding only; same image, unchanged shared model/agents/pools. Browser session behavior must be checked separately.'}
    save('verification.json',report);print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);run(parser.parse_args())
