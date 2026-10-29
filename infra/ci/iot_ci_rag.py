"""Execute the CI-selected sealed RAG release without replacing the existing manual pipeline's Python package."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import os
import re
from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator


def run(operation):
    """Authenticate the selected task module before executing code from the trusted CI source packet."""
    conf = get_current_context()['dag_run'].conf or {}
    if set(conf) != {'run_id'} or not re.fullmatch(r'rag-ci-[a-f0-9]{8}-[1-9][0-9]{0,8}', conf['run_id']):
        raise ValueError('Use one CI-owned RAG run identifier')
    root = Path(os.environ['IOT_ROOT']).resolve()
    manifest = json.loads((root / 'data/rag-releases' / (conf['run_id'] + '.json')).read_bytes())
    source = (root / manifest['source_directory']).resolve()
    if not source.is_relative_to(root / 'data/llm-source') or manifest['run_id'] != conf['run_id']:
        raise ValueError('The CI RAG source escapes its prepared lab release')
    for name, digest in manifest['source_files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('The prepared task source changed')
    name = 'rag/ci_tasks.py'
    if name not in manifest['source_files']: raise ValueError('The selected launcher must be sealed')
    spec = importlib.util.spec_from_file_location('iot_ci_selected_rag_tasks', source / name)
    tasks = importlib.util.module_from_spec(spec); spec.loader.exec_module(tasks)
    return tasks.execute(operation, conf['run_id'])


with DAG(dag_id='iot_ci_rag_knowledge', schedule=None, start_date=datetime(2026,10,1,tzinfo=timezone.utc),
         catchup=False, max_active_runs=1, tags=['iot','rag','ci'],
         default_args={'retries':0,'execution_timeout':timedelta(minutes=12)}) as dag:
    previous = None
    for operation in ['fetch','chunk','embed','store','validate','publish']:
        task = PythonOperator(task_id='publish_to_datahub' if operation == 'publish' else operation,
                              python_callable=run, op_kwargs={'operation':operation}, pool='feast_jobs')
        if previous is not None: previous >> task
        previous = task
