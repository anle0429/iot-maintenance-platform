"""Run source-sealed knowledge ingestion, native Feast validation and DataHub publication as independent Airflow tasks."""
from datetime import datetime, timedelta, timezone
from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator
from rag.airflow_tasks import execute


def run(operation):
    """Only a trusted prepared run identifier is accepted from DAG configuration."""
    conf = get_current_context()['dag_run'].conf or {}
    if set(conf) != {'run_id'}: raise ValueError('DAG configuration must contain only run_id')
    return execute(operation, conf['run_id'])


with DAG(dag_id='iot_rag_knowledge', schedule=None, start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
         catchup=False, max_active_runs=1, tags=['iot', 'rag', 'feast'], description='Versioned references to verified native vectors and DataHub lineage',
         default_args={'retries': 0, 'execution_timeout': timedelta(minutes=12)}) as dag:
    fetch = PythonOperator(task_id='fetch', python_callable=run, op_kwargs={'operation': 'fetch'}, pool='feast_jobs')
    chunk = PythonOperator(task_id='chunk', python_callable=run, op_kwargs={'operation': 'chunk'}, pool='feast_jobs')
    embed = PythonOperator(task_id='embed', python_callable=run, op_kwargs={'operation': 'embed'}, pool='feast_jobs')
    store = PythonOperator(task_id='store', python_callable=run, op_kwargs={'operation': 'store'}, pool='feast_jobs')
    validate = PythonOperator(task_id='validate', python_callable=run, op_kwargs={'operation': 'validate'}, pool='feast_jobs')
    publish = PythonOperator(task_id='publish_to_datahub', python_callable=run, op_kwargs={'operation': 'publish'}, pool='feast_jobs')
    fetch >> chunk >> embed >> store >> validate >> publish
