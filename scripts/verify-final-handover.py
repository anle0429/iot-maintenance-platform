"""Independently verify frozen repository tests, actual Airflow task graphs and refreshed model6 predictions offline."""
from pathlib import Path
from hashlib import sha256
from datetime import datetime,timezone
import ast,json,math,subprocess,xml.etree.ElementTree as ET
from evidence_integrity import ROOT,verified,source_bindings,documentation_equivalent


def payload(base,count):
    """Require exactly the manifest-listed public files and their unchanged original byte hashes."""
    manifest=json.loads((base/'manifest.json').read_bytes());actual={p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file() and p!=base/'manifest.json'};assert actual==set(manifest['files']) and len(actual)==count
    for name,item in manifest['files'].items():
        assert '..' not in Path(name).parts and not Path(name).is_absolute();raw=(base/name).read_bytes();assert len(raw)==item['bytes'] and sha256(raw).hexdigest()==item['sha256'],name
    return lambda name:json.loads((base/name).read_bytes())


def repository():
    """Bind the full PC suite to307 unchanged Git inputs and distinguish documentation-only edits from verifier changes."""
    base=ROOT/'docs/evidence/repository-audit';read=payload(base,47);report=read('frozen/verification.json');assert report['passed'] and report['source_commit']=='b8f0ef81d9d5305ad63a9e3099435801499c5f0c'
    seal=read('frozen/source-before.json');after=read('frozen/source-after.json');assert source_bindings(seal)==307 and seal['working_sha256']==after['working_sha256'] and seal['commit']==after['commit']==report['source_commit']
    assert len(report['results'])==19 and all(v['exit']==0 for v in report['results'].values())
    suite=ET.parse(base/'frozen/junit.xml');cases=suite.findall('.//testcase');assert len(cases)==739 and int(suite.find('testsuite').attrib['tests'])==739+292 and len(suite.findall('.//skipped'))==7 and not suite.findall('.//failure') and not suite.findall('.//error')
    console=(base/'frozen/pytest.stdout').read_bytes();assert b'732 passed, 7 skipped' in console and b'292 subtests passed' in console
    audit=read('frozen/audit/verification.json');assert audit['passed'] and audit['python_files']==307 and audit['definitions']==1637 and audit['missing_docstrings']==audit['forbidden_tracked_paths']==[]
    edits=read('docstring-edits.json');assert edits['added_docstrings']==419 and len(edits['files'])==75 and sum(r['added_docstrings'] for r in edits['files'] if r['documentation_only'])==418
    for row in edits['files']:
        old=subprocess.check_output(['git','show',edits['base_commit']+':'+row['path']],cwd=ROOT);new=subprocess.check_output(['git','show',seal['commit']+':'+row['path']],cwd=ROOT)
        assert sha256(old).hexdigest()==row['before_git_sha256'] and sha256(new).hexdigest()==row['after_sha256']
        if row['documentation_only']:assert documentation_equivalent(old,new,'.py'),row['path']
        else:assert row['path']=='scripts/verify-ab-monitoring.py' and row['added_docstrings']==1
    # New verifier modules after the frozen run must also satisfy the documentation rule.
    paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','*.py'],cwd=ROOT,text=True).splitlines()
    for name in set(paths):
        if name.startswith('docs/evidence/'):continue
        tree=ast.parse((ROOT/name).read_bytes());assert ast.get_docstring(tree),name
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):assert ast.get_docstring(node),(name,node.name)
    return {'pc_tests':732,'subtests':292,'skipped':7,'frozen_python_inputs':307,'frozen_definitions_documented':1637,'verification_commands':19}


def airflow():
    """Check live API readbacks against prior successful runs and reconstruct edges from actual task definitions."""
    base=ROOT/'docs/evidence/airflow-final-readback';read=verified(base,14,10);snapshot=read('snapshot.json');assert len(snapshot['runs'])==3
    expected={'iot_feast_materialization':('feast-incremental-27',{('register_bound_source','materialize_incremental'),('materialize_incremental','validate_online'),('validate_online','publish_to_datahub')}),'iot_ci_stream_offline':('stream-offline-jenkins-3',{('push_finalized_windows','replay_without_new_writes')}),'iot_ci_stream_online':('stream-online-jenkins-2',{('push_finalized_windows','replay_without_new_writes')})}
    for row in snapshot['runs']:
        dag=row['dag'];identity,edges=expected[dag];assert row['run_id']==identity and row['run']==read(dag+'-run.json') and row['instances']==read(dag+'-instances.json') and row['definitions']==read(dag+'-definitions.json')
        actual={(t['task_id'],downstream) for t in row['definitions']['tasks'] for downstream in t['downstream_task_ids']};assert actual==edges
        assert row['run']['dag_id']==dag and row['run']['dag_run_id']==identity and row['run']['state']=='success'
        tasks=row['instances']['task_instances'];assert len(tasks)==(4 if dag=='iot_feast_materialization' else 2) and all(t['state']=='success' for t in tasks)
        if dag!='iot_feast_materialization':
            directory='offline-3' if dag.endswith('offline') else 'online-2';prior=json.loads((ROOT/'docs/evidence/ci-stream-push'/directory/'ci/airflow.json').read_bytes())
            assert all(row['run'][k]==prior['run'][k] for k in ['dag_id','dag_run_id','start_date','end_date','state'])
    return {'successful_existing_dags':3,'successful_tasks':8,'image_scope':'Original Airflow UI graph screenshots for both streaming DAGs, plus explicitly labeled API reader; completed sequential jobs, not simultaneous running proof.'}


def demo():
    """Recompute all195 probabilities and trace eligibility from the original fresh200-machine HTTP capture."""
    base=ROOT/'docs/evidence/final-demo-linux';read=verified(base,16,14);proof=read('verification.json');assert proof['passed'] and source_bindings(read('source.json'))==7
    context=read('state-context.json');seal=read('state-_READY.json');state=read('state-verification.json')
    assert sha256((base/'state-context.json').read_bytes()).hexdigest()==seal['context_sha256']==proof['context_sha256'] and sha256((base/'state-verification.json').read_bytes()).hexdigest()==seal['verification_sha256'] and state['passed']
    model=read('portable.json');pin=read('model-pin.json');assert pin['version']=='6' and pin['production'] is True and sha256((base/'portable.json').read_bytes()).hexdigest()==pin['portable_sha256']==proof['portable_sha256']
    before=read('physical-ttl-before.json');after=read('physical-ttl-after.json');assert before['keys']==0 and after['keys']==200 and min(after['pttl_ms'])==proof['minimum_cache_pttl_ms']>3300000
    rows=read('http-all-machines.json');assert {r['machine_id'] for r in rows}=={f'P-{i:03d}' for i in range(1,201)} and len(rows)==200
    operating=stopped=0;maximum=0.0
    for row in rows:
        trace=row['features'];assert trace['machine_id']==row['machine_id'] and trace['clock_mode']=='simulation' and trace['prepared_wall_utc']==context['prepared_wall_utc']
        asof=datetime.fromisoformat(trace['as_of']);event=datetime.fromisoformat(trace['feature_event_ts']);arrival=datetime.fromisoformat(trace['operating_context']['received_ts']);assert asof.tzinfo and 0<=(asof-event).total_seconds()<=600 and arrival<=asof
        if trace['operating_context']['status']=='operating':
            reply=row['response'];expected_metadata={**pin,'features':model['features'],'score_kind':'uncalibrated_class_weighted_logistic','data_scope':'synthetic-medium-60d'};assert row['prediction_status']==200 and reply['model']==expected_metadata and reply['feature_trace']==trace
            vector=trace['features'];z=model['intercept']+math.fsum(((vector[n]-m)/s if s else 0)*w for n,m,s,w in zip(model['features'],model['mean'],model['std'],model['coefficients'],strict=True));score=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z));delta=abs(score-reply['prediction']['risk_score']);maximum=max(maximum,delta);assert delta<1e-12 and reply['prediction']['predicted_failure']==int(score>=pin['threshold']);operating+=1
        else:assert row['prediction_status']==409;stopped+=1
    assert (operating,stopped)==(195,5) and proof['max_abs_probability_difference']==0
    return {'native_predictions':operating,'stopped_409':stopped,'independent_max_probability_delta':maximum,'prepared_wall_utc':context['prepared_wall_utc'],'lease_expires_utc':'2026-10-28T23:10:04.654400+00:00','scope':'Historical synthetic logical clock; actual materialization28Oct, not continuous live sensors.'}


if __name__=='__main__':
    print(json.dumps({'passed':True,'repository':repository(),'airflow':airflow(),'demo':demo()},indent=2))
