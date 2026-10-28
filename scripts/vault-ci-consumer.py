"""A real Jenkins consumer: fetch only Airflow credentials from Vault, authenticate, and prove cross-policy/revocation denial."""
from pathlib import Path
import argparse,json,os,sys
from urllib.request import Request,urlopen
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from infra.security.vault_client import VaultClient,VaultRequestError


def run(args):
    """Never persist Vault tokens or secret values; archive only native lease/version/access results."""
    role=json.loads(os.environ['IOT_VAULT_APPROLE']);client=VaultClient(args.address,args.ca,role);role=None
    lease=client.login();data,version=client.read('iot/data/ci/airflow');assert lease['token_policies']==['iot-ci'] and 0<lease['lease_duration']<=300
    if data['url']!='http://127.0.0.1:18480' or data['username']!='learner':raise ValueError('Use only the declared existing private Airflow identity')
    request=Request(data['url']+'/auth/token',data=json.dumps({'username':data['username'],'password':data['password']}).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=15) as r:
        login_http=r.status;assert login_http in [200,201];token=json.load(r)['access_token']
    data=None
    with urlopen(Request('http://127.0.0.1:18480/api/v2/dags?limit=1',headers={'Authorization':'Bearer '+token}),timeout=15) as r:
        assert r.status==200;dags=json.load(r)
    token=None
    try:client.read('iot/data/gateway/basic')
    except VaultRequestError as error:assert error.status==403;cross=error.status
    else:raise AssertionError('CI AppRole can unexpectedly read gateway credentials')
    previous=client._token;client.revoke();client._token=previous
    try:client.read('iot/data/ci/airflow')
    except VaultRequestError as error:assert error.status==403;revoked=error.status
    else:raise AssertionError('Revoked Vault token remains usable')
    finally:client._token=None;previous=None
    proof={'passed':True,'consumer':'Jenkins','authentication':'AppRole','role_policy':'iot-ci','lease':lease,'secret_path':'iot/data/ci/airflow','secret_version':version,'airflow_login_http':login_http,'airflow_dag_list_http':200,'returned_dag_ids':[v['dag_id'] for v in dags['dags']],'cross_gateway_read_http':cross,'revoked_read_http':revoked,'scope':'Actual private Airflow login/read using a secret fetched at job execution; no root token, secret values or auth responses archived.'}
    args.output.write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--address',required=True);p.add_argument('--ca',type=Path,required=True);p.add_argument('--output',type=Path,required=True);run(p.parse_args())
