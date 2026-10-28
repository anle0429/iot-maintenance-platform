"""Pace binary drift events through the real Broker; record producer acknowledgement latency and replay one data-window."""
from pathlib import Path
from uuid import uuid4
import json,os
from locust import HttpUser,task,constant_pacing,events

SLA={'minimum_requests':100,'maximum_failures':0,'p95_ack_ms':500}


class EventPublisher(HttpUser):
    """Exercise transport deliveries without creating an unbounded number of assessment windows."""
    wait_time=constant_pacing(.1)

    def on_start(self):
        """Read the exact already verified window, with simulated time independent of wall transport time."""
        self.window=json.loads(Path(os.environ['IOT_EVENT_WINDOW']).read_bytes())
        self.publisher=uuid4().hex;self.sequence=0

    @task
    def deliver(self):
        """Require a successful empty Broker reply; subscriber completion is verified separately by its metrics/ledger."""
        self.sequence+=1
        headers={'Content-Type':'application/json','Host':os.environ['IOT_BROKER_HOST'],
            'ce-specversion':'1.0','ce-type':'dev.edai.iot.drift.window','ce-source':'urn:edai:iot:window-publisher',
            'ce-id':self.publisher+':'+str(self.sequence)}
        with self.client.post(os.environ['IOT_BROKER_PATH'],json=self.window,headers=headers,name='Broker drift event acknowledgement',catch_response=True) as response:
            if response.status_code not in [200,202,204] or response.content:response.failure('Expected an empty successful Broker acknowledgement')


@events.quitting.add_listener
def enforce_sla(environment,**kwargs):
    """Fail the native load command when its predeclared paced acknowledgement gate is missed."""
    stats=environment.stats.total;p95=stats.get_response_time_percentile(.95)
    passed=stats.num_requests>=SLA['minimum_requests'] and stats.num_failures<=SLA['maximum_failures'] and p95<=SLA['p95_ack_ms']
    result={'passed':passed,'sla':SLA,'requests':stats.num_requests,'failures':stats.num_failures,
        'requests_per_second':stats.total_rps,'p50_ms':stats.get_response_time_percentile(.5),'p95_ack_ms':p95,'p99_ack_ms':stats.get_response_time_percentile(.99),
        'scope':'Paced producer acknowledgements through the private in-memory Broker on a shared VM. One historical synthetic window is replayed under distinct CloudEvent IDs. Subscriber counters/persistent rows must be checked separately; not maximum capacity, unique-window throughput, model-quality evidence or a durable transport guarantee.'}
    Path(os.environ['IOT_LOAD_SUMMARY']).write_text(json.dumps(result,indent=2)+'\n')
    if not passed:environment.process_exit_code=1
