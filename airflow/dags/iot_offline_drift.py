"""Publish offline PSI and dispatch selected windows without waiting for candidate training."""
from datetime import datetime, timedelta, timezone

from airflow.exceptions import AirflowSkipException
from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from feature_store.drift_airflow import execute
from feature_store.drift_retrain import execute as retrain, prepare_dispatch


RETRAIN_DAG_ID = 'iot_offline_drift_retrain'


def compare_window():
    """Use the scheduler's closed interval; a manual probe must declare its interval explicitly."""
    context = get_current_context()
    run = context['dag_run']
    interval = context.get('data_interval_end')
    # An explicit manual interval is a lab probe, not proof of an automatic scheduled run.
    conf = run.conf or {}
    if conf:
        if not set(conf) <= {'interval_end_utc', 'retrain_probe'} or 'interval_end_utc' not in conf:
            raise ValueError('Only an explicit probe interval and optional retrain probe can be configured')
        if 'retrain_probe' in conf and type(conf['retrain_probe']) is not bool:
            raise ValueError('Retrain probe must be a boolean')
        interval = conf['interval_end_utc']
    elif interval is None:
        raise ValueError('A closed scheduler interval is required')
    return execute(run.run_id, interval.isoformat() if isinstance(interval, datetime) else interval,
                   context['ti'].try_number)


def prepare_candidate():
    """Seal only eligible native monitoring output for the child run."""
    context = get_current_context()
    run = context['dag_run']
    dispatch = prepare_dispatch(context['ti'].xcom_pull(task_ids='retrieve_compare_push'), run.run_id,
                                (run.conf or {}).get('retrain_probe', False))
    if dispatch is None:
        raise AirflowSkipException('No KFP candidate to dispatch for this monitoring interval')
    return dispatch


def verify_candidate():
    """Run source-bound admission and verification in the child DAG's own execution slot."""
    context = get_current_context()
    run = context['dag_run']
    return retrain(run.conf, run.run_id, context['ti'].try_number)


with DAG(dag_id='iot_offline_drift', schedule='*/5 * * * *',
         start_date=datetime(2026, 10, 28, tzinfo=timezone.utc), catchup=False, max_active_runs=1,
         is_paused_upon_creation=True, tags=['iot', 'feast', 'drift', 'simulated-replay'],
         description='Label-free offline monitoring; inspect simulated event clocks and actual wall-clock freshness',
         default_args={'retries': 0, 'execution_timeout': timedelta(minutes=5)}) as dag:
    compare = PythonOperator(task_id='retrieve_compare_push', python_callable=compare_window)
    prepare = PythonOperator(task_id='prepare_retrain_dispatch', python_callable=prepare_candidate)
    trigger = TriggerDagRunOperator(
        task_id='dispatch_kfp_retrain', trigger_dag_id=RETRAIN_DAG_ID,
        trigger_run_id="candidate-{{ ti.xcom_pull(task_ids='prepare_retrain_dispatch')['monitoring_attempt'] }}",
        conf=prepare.output, logical_date=None, wait_for_completion=False, reset_dag_run=False,
        skip_when_already_exists=True, openlineage_inject_parent_info=False,
        retries=1, retry_delay=timedelta(seconds=15),
    )
    compare >> prepare >> trigger


with DAG(dag_id=RETRAIN_DAG_ID, schedule=None, start_date=datetime(2026, 10, 28, tzinfo=timezone.utc),
         catchup=False, max_active_runs=1, is_paused_upon_creation=False,
         tags=['iot', 'feast', 'drift', 'candidate'],
         description='Admit, submit and verify source-bound KFP candidates from offline drift dispatch',
         default_args={'retries': 0, 'execution_timeout': timedelta(minutes=70)}) as retrain_dag:
    candidate = PythonOperator(task_id='admit_and_verify_kfp_retrain', python_callable=verify_candidate)
