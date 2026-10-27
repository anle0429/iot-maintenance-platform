"""Export only bounded variant/version metrics from the persistent experiment ledger, including fixed-bin drift."""
from prometheus_client import CollectorRegistry,generate_latest
from prometheus_client.core import CounterMetricFamily,GaugeMetricFamily,HistogramMetricFamily
from services.ab_api.runtime import VARIANTS


class LedgerCollector:
    """A scrape observes one immutable ledger snapshot; restarts do not reset completed trial counters."""
    def __init__(self,snapshot):
        self.snapshot=snapshot

    def collect(self):
        """Keep machine/request IDs out of metric labels and preserve zero counts for both variants."""
        data=self.snapshot;labels=['experiment','variant','version']
        requests=CounterMetricFamily('iot_ab_requests_total','Unique completed trial IDs',labels=labels)
        failures=CounterMetricFamily('iot_ab_failures_total','Unique non200 trial outcomes',labels=labels)
        scores=GaugeMetricFamily('iot_ab_mean_risk_score','Mean score of successful unique trials; not accuracy',labels=labels)
        unique=GaugeMetricFamily('iot_ab_unique_snapshots','Distinct machine and feature-time snapshots',labels=labels)
        psi=GaugeMetricFamily('iot_ab_feature_psi','Shared-reference marginal PSI without ground truth',labels=labels+['feature'])
        latency=HistogramMetricFamily('iot_ab_request_seconds','Server model path duration, excluding lock wait and network',labels=labels)
        for variant in VARIANTS:
            values=[data['experiment'],variant,data[variant+'_pin']['version']];trials=[row for row in data['observations'] if row['variant']==variant]
            requests.add_metric(values,data['metrics'][variant]['requests']);failures.add_metric(values,data['metrics'][variant]['failures'])
            durations=[row['latency_ms']/1000 for row in trials]
            buckets=[(str(bound),sum(value<=bound for value in durations)) for bound in [.005,.01,.025,.05,.1,.25,.5,1,5]]+[('+Inf',len(durations))]
            latency.add_metric(values,buckets,sum(durations));successful=[row['prediction']['risk_score'] for row in trials if row['status']==200]
            if successful:scores.add_metric(values,sum(successful)/len(successful))
            drift=data['drift'][variant];unique.add_metric(values,drift['unique_rows'])
            if drift['ready']:
                for name,value in drift['features'].items():psi.add_metric(values+[name],value['psi'])
        yield from [requests,failures,latency,scores,unique,psi]


def exposition(snapshot):
    """Use the official client exporter so escaping, histogram counts and counter suffixes stay compatible."""
    registry=CollectorRegistry();registry.register(LedgerCollector(snapshot));return generate_latest(registry)
