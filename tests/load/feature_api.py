"""Load the real native feature-read endpoint over200 simulated machines and enforce the predeclared small-VM SLA."""
from pathlib import Path
import json,os
from locust import HttpUser,task,constant_pacing,events

SLA={'minimum_requests':100,'maximum_failures':0,'p95_ms':500,'clock_mode':'simulation'}


class FeatureReader(HttpUser):
    """Cycle through all real machine IDs; model prediction is a separate workload."""
    wait_time=constant_pacing(.1)
    def on_start(self):'Start the load user at the first machine in the bounded cohort.';self.number=0
    @task
    def features(self):
        'Cycle through the200 machines and exercise the feature endpoint with Locust.'
        self.number=self.number%200+1;machine=f'P-{self.number:03d}'
        with self.client.get('/v1/machines/'+machine+'/features',name='/v1/machines/{machine_id}/features',catch_response=True) as response:
            if response.status_code!=200:return
            try:value=response.json()
            except ValueError:response.failure('Invalid JSON');return
            if value.get('machine_id')!=machine or value.get('clock_mode')!='simulation' or len(value.get('features',{}))!=8:response.failure('Feature response contract differs')


@events.quitting.add_listener
def enforce_sla(environment,**kwargs):
    """Write actual Locust aggregates and return failure when the declared gate is missed."""
    stats=environment.stats.total;p95=stats.get_response_time_percentile(.95)
    passed=stats.num_requests>=SLA['minimum_requests'] and stats.num_failures<=SLA['maximum_failures'] and p95<=SLA['p95_ms']
    result={'passed':passed,'sla':SLA,'requests':stats.num_requests,'failures':stats.num_failures,
        'requests_per_second':stats.total_rps,'p50_ms':stats.get_response_time_percentile(.5),'p95_ms':p95,'p99_ms':stats.get_response_time_percentile(.99),
        'scope':'Feature retrieval only, paced clients on same shared VM; not max capacity, external network latency, RAG/LLM or prediction throughput.'}
    Path(os.environ['IOT_LOAD_SUMMARY']).write_text(json.dumps(result,indent=2)+'\n')
    if not passed:environment.process_exit_code=1
