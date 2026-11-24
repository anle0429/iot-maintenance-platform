"""Call the actual private MCP transport and retain original business-tool results without credential headers."""
from pathlib import Path
from hashlib import sha256
import argparse
import asyncio
import json
import os
import sys
import httpx2
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def operating_cohort():
    """Select 32 operating demo machines using real private Feature API reads."""
    base='http://iot-feature-api.iot-features.svc.cluster.local'
    cohort=[]
    async with httpx2.AsyncClient(timeout=8,trust_env=False) as client:
        for index in range(1,201):
            machine_id=f'P-{index:03}'
            response=await client.get(base+'/v1/machines/'+machine_id+'/features')
            if response.status_code==404:
                continue
            response.raise_for_status()
            record=response.json()
            if record['machine_id']!=machine_id:
                raise ValueError('Feature API returned a different drift machine')
            if record['operating_context']['status']=='operating':
                cohort.append(machine_id)
                if len(cohort)==32:
                    return cohort
    raise ValueError('Need 32 available operating machines for the native drift probe')


async def probe(role, run):
    """Verify native discovery, identity/source relationships, negative calls and stable replay for one role."""
    token=Path(os.environ['IOT_MCP_TOKEN_FILE']).read_text().strip()
    base='http://iot-'+role+'-mcp.iot-agents.svc.cluster.local:8080'
    captured={'role':role,'run':run,'tools':[],'calls':[]}
    async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=60,trust_env=False) as client:
        dependency_response=await client.get(base+'/health/dependencies')
        dependency_response.raise_for_status()
        dependencies=dependency_response.json()
        if dependencies.get('ready') is not True or dependencies.get('role')!=role:
            raise ValueError('Native MCP dependencies are not ready for this role')
        captured['dependencies']=dependencies
        unauthorized=await client.get(base+'/v1/unknown',headers={'Authorization':'Bearer wrong'})
        assert unauthorized.status_code==401
        captured['wrong_token_status']=unauthorized.status_code
        async with streamable_http_client(base+'/mcp',http_client=client) as (incoming,outgoing):
            async with ClientSession(incoming,outgoing) as session:
                initialized=await session.initialize();captured['initialized']=initialized.model_dump(mode='json',by_alias=True)
                listed=await session.list_tools();captured['tools']=listed.model_dump(mode='json',by_alias=True)
                async def call(name, arguments, error=False):
                    """Keep the actual SDK response and refuse an unexpected successful/error outcome."""
                    result=await session.call_tool(name,{'request':arguments})
                    raw=result.model_dump(mode='json',by_alias=True)
                    captured['calls'].append({'tool':name,'arguments':arguments,'response':raw})
                    assert token not in json.dumps(captured['calls'][-1])
                    print(json.dumps({'observed_call':captured['calls'][-1]}),file=sys.stderr,flush=True)
                    assert result.is_error is error
                    return None if error else json.loads(result.content[0].text)
                if role=='retrieval':
                    assert {tool.name for tool in listed.tools}=={'get_machine_features','predict_failure','search_knowledge'}
                    first=await call('get_machine_features',{'machine_id':'P-001'})
                    assert first==await call('get_machine_features',{'machine_id':'P-001'}) and first['machine_id']=='P-001'
                    prediction=await call('predict_failure',{'machine_id':'P-001'})
                    assert prediction['feature_trace']==first and prediction['model']['version']=='6'
                    await call('get_machine_features',{'machine_id':'P-999'},True)
                    await call('predict_failure',{'machine_id':'P-006'},True)
                    await call('get_machine_features',{'machine_id':'../invalid'},True)
                    question={'question':'Does high vibration alone prove pump failure?','top_k':3}
                    found=await call('search_knowledge',question)
                    assert found==await call('search_knowledge',question)
                    # The release verifier supplies the sealed source inventory to this
                    # one-off probe. The running MCP service has no snapshot mount.
                    inventory = sys.stdin.buffer.read()
                    assert sha256(inventory).hexdigest() == found['snapshot_sha256']
                    chunks={row['chunk_id']:row for row in json.loads(inventory)}
                    assert len(found['matches'])==3 and 'monitoring' in [row['document_id'] for row in found['matches']]
                    for row in found['matches']:
                        assert all(row[name]==chunks[row['chunk_id']][name] for name in ['text','document_id','revision','source_sha256','source_file','section'])
                    await call('search_knowledge',{'question':'valid question','top_k':0},True)
                    captured['native_snapshot_rows']=len(chunks)
                else:
                    assert [tool.name for tool in listed.tools]==['assess_feature_drift']
                    scope=os.environ.get('IOT_DRIFT_SCOPE','serving_model')
                    cohort=await operating_cohort() if scope=='serving_model' else [f'P-{i:03}' for i in range(1,33)]
                    body={'window_id':run,'machine_ids':cohort}
                    first=await call('assess_feature_drift',body)
                    replay=await call('assess_feature_drift',dict(body,machine_ids=list(reversed(body['machine_ids']))))
                    assert first==replay and first['unique_rows']==32
                    captured['drift_input_scope']=scope
                    captured['assessment_reference_id']=first['reference_id']
                    captured['assessment_sha256']=first['assessment_sha256']
                    await call('assess_feature_drift',dict(body,machine_ids=['P-001']*32),True)
    assert token not in json.dumps(captured)
    captured['passed']=True;return captured


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--role',choices=['retrieval','drift'],required=True)
    parser.add_argument('--run',required=True);args=parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.role,args.run)),indent=2))
