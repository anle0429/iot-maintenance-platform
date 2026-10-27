"""Reject local threads, idle second executors and tasks from unrelated jobs as distributed-fit proof."""
import unittest
from ml.distributed_evidence import summarize


def events(second_task=True,group=True):
    """Construct a small known-answer Spark listener trace with a genuine LR fit stage."""
    trace=[{'Event':'SparkListenerExecutorAdded','Executor ID':str(n),'Executor Info':{'Host':'worker-'+str(n)}} for n in [1,2]]
    trace += [{'Event':'SparkListenerJobStart','Stage IDs':[7],'Properties':{'spark.jobGroup.id':'iot-distributed-fit' if group else 'other'}},
        {'Event':'SparkListenerStageCompleted','Stage Info':{'Stage ID':7,'Stage Name':'treeAggregate at LogisticRegression.scala:123','Number of Tasks':8}}]
    trace += [{'Event':'SparkListenerTaskEnd','Stage ID':7,'Task End Reason':{'Reason':'Success'},'Task Info':{'Executor ID':str(n)}} for n in ([1,2] if second_task else [1])]
    return trace+[{'Event':'SparkListenerApplicationEnd'}]


class DistributedEvidenceTest(unittest.TestCase):
    """Require actual successful work, correct training attribution and a completed application."""
    def test_two_active_executors_are_required(self):
        self.assertTrue(summarize(events())['passed'])
        for trace in [events(False),events(group=False),events()[:-1]]:
            self.assertFalse(summarize(trace)['passed'])

    def test_failed_attempt_and_local_driver_are_not_success(self):
        trace=events();trace[0]['Executor ID']='driver'
        for item in trace:
            if item.get('Task Info',{}).get('Executor ID')=='1':item['Task Info']['Executor ID']='driver'
        self.assertFalse(summarize(trace)['passed'])
        trace=events();trace.append({'Event':'SparkListenerTaskEnd','Stage ID':7,'Task End Reason':{'Reason':'ExceptionFailure'},'Task Info':{'Executor ID':'2'}})
        self.assertFalse(summarize(trace)['passed'])


if __name__=='__main__':unittest.main()
