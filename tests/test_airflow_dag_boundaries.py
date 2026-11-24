"""Check the declared Airflow contracts without starting a scheduler or a training container."""

from datetime import timedelta
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_dags(path):
    """Run the DAG definitions with a small registration stand-in for the Linux-only Airflow runtime."""
    dags = {}

    class DAG:
        """Record task registration and DAG options."""
        def __init__(self, dag_id, **options):
            """Start a registry entry for this DAG."""
            self.options = options
            self.tasks = {}
            dags[dag_id] = self

        def __enter__(self):
            """Select this DAG as the destination for new tasks."""
            active.append(self)
            return self

        def __exit__(self, *exc):
            """Restore the previous DAG registration scope."""
            active.pop()

    class Operator:
        """Record the options and dependency edges passed to each operator."""
        def __init__(self, *, task_id, **options):
            """Register an operator with its resolved DAG defaults."""
            dag = active[-1]
            self.task_id = task_id
            self.options = {**dag.options['default_args'], **options}
            self.upstream = set()
            self.downstream = set()
            self.output = object()
            dag.tasks[task_id] = self

        def __rshift__(self, downstream):
            """Record a direct dependency edge."""
            downstream.upstream.add(self.task_id)
            self.downstream.add(downstream.task_id)
            return downstream

        def __rrshift__(self, upstream):
            """Record one dependency edge for each source task."""
            for task in upstream:
                task >> self
            return self

    active = []
    modules = {
        'airflow.sdk': SimpleNamespace(DAG=DAG, get_current_context=None),
        'airflow.exceptions': SimpleNamespace(AirflowSkipException=ValueError),
        'airflow.providers.standard.operators.empty': SimpleNamespace(EmptyOperator=Operator),
        'airflow.providers.standard.operators.python': SimpleNamespace(PythonOperator=Operator),
        'airflow.providers.standard.operators.trigger_dagrun': SimpleNamespace(TriggerDagRunOperator=Operator),
        'airflow.utils.trigger_rule': SimpleNamespace(
            TriggerRule=SimpleNamespace(ALL_DONE='all_done', ALL_SUCCESS='all_success'),
        ),
        'pipelines.airflow_tasks': SimpleNamespace(execute_stage=None),
        'docker': SimpleNamespace(),
    }
    with patch.dict(sys.modules, modules):
        runpy.run_path(str(ROOT / path))
    return dags


class AirflowDagBoundaries(unittest.TestCase):
    """Ensure publication cannot set stage success and training cannot block monitor scheduling."""
    def test_validation_still_controls_exit_after_datahub_reports(self):
        """Successful publication of a failed validation cannot be the only terminal state."""
        for path in ('airflow/dags/iot_data_pipelines.py', 'infra/ci/iot_ci_data_pipelines.py'):
            dags = load_dags(path)
            self.assertEqual(len(dags), 3)
            for dag_id, dag in dags.items():
                with self.subTest(dag=dag_id):
                    publish = dag.tasks['publish_to_datahub']
                    self.assertEqual(publish.options['trigger_rule'], 'all_done')
                    self.assertEqual(publish.upstream, {'validate'})
                    self.assertEqual(dag.tasks['validate'].upstream, {'ingest'})
                    leaves = [task for task in dag.tasks.values() if not task.downstream]
                    self.assertEqual(len(leaves), 1)
                    terminal = leaves[0]
                    self.assertTrue({'validate', 'publish_to_datahub'} <= terminal.upstream)
                    self.assertEqual(terminal.options.get('trigger_rule', 'all_success'), 'all_success')

    def test_queued_training_has_its_own_run_and_timeout(self):
        """The monitor ends at durable dispatch and the child serializes the KFP work."""
        dags = load_dags('airflow/dags/iot_offline_drift.py')
        monitor = dags['iot_offline_drift']
        child = dags['iot_offline_drift_retrain']
        self.assertEqual(monitor.options['schedule'], '*/5 * * * *')
        self.assertEqual(monitor.options['max_active_runs'], 1)
        self.assertEqual(set(monitor.tasks), {'retrieve_compare_push', 'prepare_retrain_dispatch', 'dispatch_kfp_retrain'})
        self.assertTrue(all(task.options['execution_timeout'] == timedelta(minutes=5) for task in monitor.tasks.values()))
        prepare = monitor.tasks['prepare_retrain_dispatch']
        trigger = monitor.tasks['dispatch_kfp_retrain']
        self.assertEqual(prepare.upstream, {'retrieve_compare_push'})
        self.assertEqual(trigger.upstream, {'prepare_retrain_dispatch'})
        self.assertEqual(trigger.options['trigger_dag_id'], 'iot_offline_drift_retrain')
        self.assertIs(trigger.options['conf'], prepare.output)
        self.assertFalse(trigger.options['wait_for_completion'])
        self.assertFalse(trigger.options['reset_dag_run'])
        self.assertTrue(trigger.options['skip_when_already_exists'])
        self.assertIsNone(child.options['schedule'])
        self.assertEqual(child.options['max_active_runs'], 1)
        worker = child.tasks['admit_and_verify_kfp_retrain']
        self.assertEqual(worker.options['execution_timeout'], timedelta(minutes=70))


if __name__ == '__main__':
    unittest.main()
