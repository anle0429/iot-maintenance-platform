"""Measure authenticated feature reads through the real MCP service's REST API with a declared small-VM SLA."""
from pathlib import Path
import json
import os
from locust import HttpUser, task, constant_pacing, events

SLA = {'minimum_requests': 100, 'maximum_failures': 0, 'p95_ms': 1000}


class McpFeatureReader(HttpUser):
    """Cycle through the native 200-machine snapshot without measuring LLM generation or knowledge encoding."""
    wait_time = constant_pacing(.1)

    def on_start(self):
        """Read a private credential file without adding its value to reports or recorded command arguments."""
        self.number = 0
        self.client.headers['Authorization'] = 'Bearer ' + Path(os.environ['IOT_MCP_LOAD_TOKEN_FILE']).read_text().strip()

    @task
    def features(self):
        """Validate real response identity, feature count and lineage in addition to HTTP success."""
        self.number = self.number % 200 + 1
        machine = f'P-{self.number:03d}'
        with self.client.get('/v1/machines/' + machine + '/features', name='/v1/machines/{machine_id}/features', catch_response=True) as response:
            if response.status_code != 200:
                return
            try:
                value = response.json()
            except ValueError:
                response.failure('Invalid JSON')
                return
            if (value.get('machine_id') != machine or value.get('clock_mode') != 'simulation'
                    or len(value.get('features', {})) != 8
                    or value.get('source_manifest_sha256') != os.environ['IOT_MCP_LOAD_SOURCE_SHA256']):
                response.failure('Feature identity or source contract differs')


@events.quitting.add_listener
def enforce_sla(environment, **kwargs):
    """Export actual aggregates and fail the process when the predeclared SLA is missed."""
    stats = environment.stats.total
    p95 = stats.get_response_time_percentile(.95)
    passed = (stats.num_requests >= SLA['minimum_requests'] and stats.num_failures <= SLA['maximum_failures']
              and p95 is not None and p95 <= SLA['p95_ms'])
    result = {'passed': passed, 'sla': SLA, 'requests': stats.num_requests, 'failures': stats.num_failures,
              'requests_per_second': stats.total_rps, 'p50_ms': stats.get_response_time_percentile(.5),
              'p95_ms': p95, 'p99_ms': stats.get_response_time_percentile(.99),
              'scope': 'Authenticated machine-feature retrieval through MCP service REST adapter; paced clients on the same shared VM. No maximum-capacity, external-network, RAG, LLM or prediction-throughput claim.'}
    Path(os.environ['IOT_MCP_LOAD_SUMMARY']).write_text(json.dumps(result, indent=2) + '\n')
    if not passed:
        environment.process_exit_code = 1
