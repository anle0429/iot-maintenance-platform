"""Require successful logistic-regression tasks on two distinct native executor IDs in one Spark stage."""
import json
from collections import defaultdict


def summarize(events):
    """Read actual Spark listener events, separating scheduler admission from evidence of distributed fit."""
    executors={};stages={};task_ids=defaultdict(set);failures=[];ended=False;groups=set()
    for item in events:
        event=item.get('Event')
        if event=='SparkListenerExecutorAdded':executors[item['Executor ID']]=item['Executor Info']
        elif event=='SparkListenerJobStart':
            if item.get('Properties',{}).get('spark.jobGroup.id')=='iot-distributed-fit':groups.update(item['Stage IDs'])
        elif event in ['SparkListenerStageSubmitted','SparkListenerStageCompleted']:
            stage=item['Stage Info'];stages[stage['Stage ID']]=stage
        elif event=='SparkListenerTaskEnd':
            if item['Task End Reason']['Reason']=='Success':task_ids[item['Stage ID']].add(item['Task Info']['Executor ID'])
            else:failures.append({'stage':item['Stage ID'],'reason':item['Task End Reason']})
        elif event=='SparkListenerApplicationEnd':ended=True
    lr=[]
    for number,stage in stages.items():
        if number in groups and 'LogisticRegression' in json.dumps(stage):
            ids=sorted(task_ids[number]-{'driver'})
            lr.append({'stage_id':number,'name':stage['Stage Name'],'successful_executor_ids':ids,
                'tasks':stage['Number of Tasks'],'training_group':True})
    passed=ended and len(executors)>=2 and any(len(x['successful_executor_ids'])>=2 for x in lr) and not failures
    return {'passed':passed,'application_ended':ended,'executors':executors,'logistic_regression_stages':lr,
        'failed_task_attempts':failures,'scope':'Successful LR fit task execution on distinct executor IDs; same physical VM, not multiple machines or a speedup benchmark.'}


def read_logs(paths):
    """Stream uncompressed Spark event logs rather than loading a large log into driver memory."""
    for path in paths:
        with path.open() as stream:
            for line in stream:
                if line.strip():yield json.loads(line)
