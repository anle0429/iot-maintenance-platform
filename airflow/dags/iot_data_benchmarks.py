"""Serialize measured schema/dedup and skew experiments, including independent result and task-metric gates."""

from datetime import datetime, timedelta, timezone
from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator
from pipelines.benchmark_airflow import preflight, run_experiment, validate_experiment


def execute(operation: str, experiment: str = '') -> dict:
    """Accept only a prepared run ID; users cannot submit commands, paths or resource overrides in DAG configuration."""
    config = get_current_context()['dag_run'].conf or {}
    if set(config) != {'benchmark_id'}:
        raise ValueError('Prepare the benchmark first and pass only benchmark_id')
    run_id = config['benchmark_id']
    if operation == 'preflight':
        return preflight(run_id)
    if operation == 'measure':
        return run_experiment(run_id, experiment)
    return validate_experiment(run_id, experiment)


with DAG(dag_id='iot_data_benchmarks', schedule=None, start_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
         catchup=False, max_active_runs=1, tags=['iot', 'benchmarks', 'spark'],
         description='Paired schema, deterministic dedup and factory-skew experiments with source/metric validation',
         default_args={'retries': 0, 'execution_timeout': timedelta(minutes=30)}) as dag:
    admit = PythonOperator(task_id='preflight', python_callable=execute, op_kwargs={'operation': 'preflight'}, pool='spark_jobs')
    corrections = PythonOperator(task_id='schema_dedup_pairs', python_callable=execute,
                                 op_kwargs={'operation': 'measure', 'experiment': 'corrections'}, pool='spark_jobs')
    corrections_gate = PythonOperator(task_id='validate_corrections', python_callable=execute,
                                      op_kwargs={'operation': 'validate', 'experiment': 'corrections'}, pool='spark_jobs')
    skew = PythonOperator(task_id='skew_pairs', python_callable=execute,
                          op_kwargs={'operation': 'measure', 'experiment': 'skew'}, pool='spark_jobs')
    skew_gate = PythonOperator(task_id='validate_skew', python_callable=execute,
                               op_kwargs={'operation': 'validate', 'experiment': 'skew'}, pool='spark_jobs')
    admit >> corrections >> corrections_gate >> skew >> skew_gate
