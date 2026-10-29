"""Publish the retained knowledge lineage plus the actual CI DAG, six jobs and source/build identity."""
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re
from rag.metadata_publish import publish


def ci_identity(run_id, commit, release):
    """Bind actual Airflow links to one full Git revision and positive Jenkins build."""
    match=re.fullmatch(r'llm-rag-jenkins-([1-9][0-9]{0,8})',release)
    if not re.fullmatch(r'[a-f0-9]{40}',commit) or not match or run_id!='rag-ci-'+commit[:8]+'-'+match[1]:
        raise ValueError('RAG governance must refer to the same CI source, build and run')
    dag='iot_ci_rag_knowledge'
    return {'dag_id':dag,'run_id':run_id,'source_commit':commit,'ci_release':release,
            'external_url':'http://localhost:18480/dags/'+dag+'/runs/'+run_id}


def publish_ci(directory, run_id, commit, release):
    """Require native quality, then acknowledge and independently read back the actual CI flow and every task."""
    identity=ci_identity(run_id,commit,release);directory=Path(directory)
    canonical=publish(directory,run_id)
    from datahub.emitter.mce_builder import make_data_flow_urn,make_data_job_urn,make_dataset_urn
    from datahub.emitter.mcp import MetadataChangeProposalWrapper
    from datahub.emitter.rest_emitter import DatahubRestEmitter
    from datahub.ingestion.graph.client import DataHubGraph,DatahubClientConfig
    import datahub.metadata.schema_classes as S
    endpoint=os.environ['DATAHUB_GMS_URL'];graph=DataHubGraph(DatahubClientConfig(server=endpoint))
    emitter=DatahubRestEmitter(gms_server=endpoint);flow=make_data_flow_urn('airflow',identity['dag_id'],'iot-vm')
    properties={key:identity[key] for key in ['run_id','source_commit','ci_release']}
    quality=json.loads((directory/'quality.json').read_bytes())
    urns={name:make_dataset_urn('feast' if name=='online' else 'parquet' if name=='offline' else 'file',
                              'iot.knowledge.'+name,'DEV') for name in quality['datasets']}
    stages={'fetch':([],['documents']),'chunk':(['documents'],['chunks']),'embed':(['chunks'],['embeddings']),
            'store':(['chunks','embeddings'],['offline','online']),'validate':(['online','offline'],[]),
            'publish_to_datahub':(['documents','chunks','embeddings','offline','online'],[])}
    emitted=[];readback={}
    def emit(urn,aspect):
        """Keep the original acknowledged entity/aspect identities without recording client credentials."""
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=urn,aspect=aspect))
        emitted.append({'urn':urn,'aspect':aspect.ASPECT_NAME})
    try:
        emit(flow,S.DataFlowInfoClass(name=identity['dag_id'],description='Source-bound Jenkins RAG release and actual six-task Airflow execution.',
                                     externalUrl=identity['external_url'],customProperties=properties))
        actual=graph.get_aspect(flow,S.DataFlowInfoClass)
        if not actual or actual.name!=identity['dag_id'] or actual.externalUrl!=identity['external_url'] or actual.customProperties!=properties:
            raise ValueError('GMS did not retain the actual CI flow identity')
        readback['flow']=actual.to_obj()
        for task,(inputs,outputs) in stages.items():
            urn=make_data_job_urn('airflow',identity['dag_id'],task,'iot-vm')
            emit(urn,S.DataJobInfoClass(name=task,type='COMMAND',flowUrn=flow,externalUrl=identity['external_url'],customProperties=properties))
            emit(urn,S.DataJobInputOutputClass(inputDatasets=[urns[n] for n in inputs],outputDatasets=[urns[n] for n in outputs]))
            actual=graph.get_aspect(urn,S.DataJobInfoClass);io=graph.get_aspect(urn,S.DataJobInputOutputClass)
            if (not actual or actual.name!=task or actual.flowUrn!=flow or actual.externalUrl!=identity['external_url']
                    or actual.customProperties!=properties or not io
                    or io.inputDatasets!=[urns[n] for n in inputs] or io.outputDatasets!=[urns[n] for n in outputs]):
                raise ValueError('GMS task identity or lineage differs: '+task)
            readback[task]={'urn':urn,'info':actual.to_obj(),'lineage':io.to_obj()}
        result={'passed':True,**identity,'flow_urn':flow,'readback':readback,'emitted':emitted,
                'canonical_publication_sha256':sha256((directory/'datahub-publication.json').read_bytes()).hexdigest(),
                'canonical_knowledge_flow':canonical['flow'],'quality_sha256':sha256((directory/'quality.json').read_bytes()).hexdigest()}
        with (directory/'ci-datahub-publication.json').open('x') as stream:json.dump(result,stream,indent=2,allow_nan=False)
        return result
    finally:emitter.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state',type=Path,required=True);parser.add_argument('--run',required=True)
    args=parser.parse_args();print(json.dumps(publish_ci(args.state,args.run,os.environ['IOT_CI_COMMIT'],os.environ['IOT_CI_RELEASE']),indent=2))
