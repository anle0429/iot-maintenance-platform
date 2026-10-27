"""Materialize an immutable medium feature version incrementally, validate online values and publish lineage."""

from datetime import datetime, timedelta, timezone

from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator

from feature_store.airflow_tasks import execute


def run(operation: str):
    """Read only the bounded run name and simulated UTC cutoff from the DAG configuration."""
    conf = get_current_context()['dag_run'].conf or {}
    return execute(operation, conf['batch_id'], conf['end_utc'])


with DAG(dag_id='iot_feast_materialization', schedule=None,
         start_date=datetime(2026, 10, 1, tzinfo=timezone.utc), catchup=False, max_active_runs=1,
         tags=['iot', 'feast', 'incremental'], description='Offline to online Feast, validated before DataHub publication',
         default_args={'retries': 0, 'execution_timeout': timedelta(minutes=10)}) as dag:
    register = PythonOperator(task_id='register_bound_source', python_callable=run, op_kwargs={'operation': 'register'}, pool='feast_jobs')
    materialize = PythonOperator(task_id='materialize_incremental', python_callable=run, op_kwargs={'operation': 'materialize'}, pool='feast_jobs')
    validate = PythonOperator(task_id='validate_online', python_callable=run, op_kwargs={'operation': 'validate'}, pool='feast_jobs')
    publish = PythonOperator(task_id='publish_to_datahub', python_callable=run, op_kwargs={'operation': 'publish'}, pool='feast_jobs')
    register >> materialize >> validate >> publish
