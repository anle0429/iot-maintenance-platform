"""Derive Linux host panel values from raw same-time samples, independently of PromQL and provisioning code."""
import math
from tests.monitoring_oracle import counter_rate


def expected_linux(raw,end):
    """Require full rate windows, join filesystems by mount, and account for every selected CPU/device series."""
    def gauge(name):
        'Require a nonempty native instant-vector result for the selected gauge.'
        data=raw[name]['data'];assert data['resultType']=='vector' and data['result']
        return data['result']
    def scalar(name):
        'Require exactly one finite scalar from the native instant vector.'
        rows=gauge(name);assert len(rows)==1;value=float(rows[0]['value'][1]);assert math.isfinite(value)
        return value
    def rates(name):
        'Compute independent counter rates from sufficiently populated range vectors.'
        data=raw[name]['data'];assert data['resultType']=='matrix' and data['result']
        assert all(len(row['values'])>=15 for row in data['result'])
        return [counter_rate(row['values'],end) for row in data['result']]
    idle=rates('node_cpu_seconds_total');expected={(1,'A'):[100*(1-math.fsum(idle)/len(idle))]}
    available=scalar('node_memory_MemAvailable_bytes');total=scalar('node_memory_MemTotal_bytes');assert 0<=available<=total and total>0
    expected[2,'A']=[100*(1-available/total)];expected[3,'A']=[available]
    def by_mount(name):
        'Require exactly the three mounted filesystems with finite native values.'
        rows=gauge(name);values={row['metric']['mountpoint']:float(row['value'][1]) for row in rows};assert len(values)==len(rows)
        assert values.keys()=={'/','/srv/iot-data','/srv/iot-fast'} and all(math.isfinite(v) for v in values.values())
        return values
    free=by_mount('node_filesystem_avail_bytes');sizes=by_mount('node_filesystem_size_bytes')
    assert all(0<=free[k]<=sizes[k] and sizes[k]>0 for k in free)
    expected[4,'A']={k:100*(1-free[k]/sizes[k]) for k in free};expected[5,'A']=free
    for panel,names in [(6,['node_disk_read_bytes_total','node_disk_written_bytes_total']),
                        (7,['node_network_receive_bytes_total','node_network_transmit_bytes_total'])]:
        for ref,name in zip(['A','B'],names):expected[panel,ref]=[math.fsum(rates(name))]
    expected[8,'A']=[scalar('up')]
    collectors=gauge('node_scrape_collector_success');assert {r['metric']['collector'] for r in collectors}=={'cpu','meminfo','filesystem','diskstats','netdev'}
    expected[9,'A']=[min(float(r['value'][1]) for r in collectors)]
    return expected


def compare_linux(samples,expected):
    """Reject missing, duplicate or nonfinite series and retain filesystem mount identity."""
    if isinstance(expected,dict):
        actual={labels['mountpoint']:value for labels,value in samples};assert len(actual)==len(samples) and actual.keys()==expected.keys()
        pairs=[(actual[k],v) for k,v in expected.items()]
    else:assert len(samples)==len(expected)==1;pairs=[(samples[0][1],expected[0])]
    assert all(math.isfinite(a) and math.isclose(a,b,rel_tol=1e-8,abs_tol=1e-7) for a,b in pairs),pairs
