"""Periodically compare a declared simulated offline Feast window and push fixed-reference PSI gauges."""
from datetime import datetime, timedelta, timezone
from airflow.sdk import DAG, get_current_context
from airflow.providers.standard.operators.python import PythonOperator
from feature_store.drift_airflow import execute


def compare_window():
    """Use the scheduler's closed interval; a manual probe must declare its interval explicitly."""
    context = get_current_context()
    run = context['dag_run']
    interval = context.get('data_interval_end')
    # An explicit manual interval is a lab probe, not proof of an automatic scheduled run.
    conf = run.conf or {}
    if conf:
        if set(conf) != {'interval_end_utc'}:
            raise ValueError('Only the explicit probe interval can be configured')
        interval = conf['interval_end_utc']
    elif interval is None:
        raise ValueError('A closed scheduler interval is required')
    return execute(run.run_id, interval.isoformat() if isinstance(interval, datetime) else interval,
                   context['ti'].try_number)


with DAG(dag_id='iot_offline_drift', schedule='*/5 * * * *',
         start_date=datetime(2026, 10, 28, tzinfo=timezone.utc), catchup=False, max_active_runs=1,
         is_paused_upon_creation=True, tags=['iot', 'feast', 'drift', 'simulated-replay'],
         description='Label-free offline monitoring; inspect simulated event clocks and actual wall-clock freshness',
         default_args={'retries': 0, 'execution_timeout': timedelta(minutes=5)}) as dag:
    compare = PythonOperator(task_id='retrieve_compare_push', python_callable=compare_window)
