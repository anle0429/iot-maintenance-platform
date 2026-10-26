"""Three scheduled-by-dependency IoT DAGs: materialize, validate, publish metadata, then release downstream."""

from datetime import datetime, timedelta, timezone

from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.trigger_rule import TriggerRule

from pipelines.airflow_tasks import execute_stage


def run(stage: str, operation: str) -> dict:
    """Read only the safe batch/fault parameters; no arbitrary command or source path is accepted."""
    config = get_current_context()['dag_run'].conf or {}
    return execute_stage(stage, operation, config['batch_id'], config.get('fault', '') if stage == 'dp1' else '')


PIPELINES = [('dp1', 'iot_dp1_bronze', 'iot_dp2_warehouse'), ('dp2', 'iot_dp2_warehouse', 'iot_dp3_features'),
             ('dp3', 'iot_dp3_features', None)]
for stage, dag_id, next_dag in PIPELINES:
    with DAG(dag_id=dag_id, schedule=None, start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
             catchup=False, max_active_runs=1, tags=['iot', stage, 'coursework'],
             default_args={'retries': 0, 'execution_timeout': timedelta(minutes=35)},
             description=f'{stage.upper()}: immutable Spark ingest, persisted-data validation and DataHub metadata') as dag:
        ingest = PythonOperator(task_id='ingest', python_callable=run, op_kwargs={'stage': stage, 'operation': 'ingest'}, pool='spark_jobs')
        validate = PythonOperator(task_id='validate', python_callable=run, op_kwargs={'stage': stage, 'operation': 'validate'}, pool='spark_jobs')
        publish = PythonOperator(task_id='publish_to_datahub', python_callable=run, op_kwargs={'stage': stage, 'operation': 'publish'},
                                 trigger_rule=TriggerRule.ALL_DONE)
        ingest >> validate >> publish
        if next_dag:
            release = TriggerDagRunOperator(task_id='release_next_pipeline', trigger_dag_id=next_dag,
                                             trigger_run_id="{{ dag_run.conf['batch_id'] }}",
                                             conf={'batch_id': "{{ dag_run.conf['batch_id'] }}"},
                                             wait_for_completion=False, reset_dag_run=False)
            [validate, publish] >> release
    globals()[dag_id] = dag
