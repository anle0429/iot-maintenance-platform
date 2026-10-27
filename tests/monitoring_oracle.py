"""Check Windows host dashboard values from captured raw samples without importing monitoring implementation."""
import math


def counter_rate(samples,end,window=30):
    """Evaluate float-counter reset and boundary extrapolation semantics for a fixed Prometheus range."""
    end=round(end*1000);start=end-round(window*1000)
    points=[(round(t*1000),float(value)) for t,value in samples]
    if (len(points)<2 or window<=0 or any(not start<t<=end or not math.isfinite(v) or v<0 for t,v in points)
            or any(b[0]<=a[0] for a,b in zip(points,points[1:]))):raise ValueError('Need ordered finite counter samples inside the open-left window')
    span=(points[-1][0]-points[0][0])/1000;spacing=span/(len(points)-1)
    change=points[-1][1]-points[0][1]+math.fsum(a[1] for a,b in zip(points,points[1:]) if b[1]<a[1])
    left=(points[0][0]-start)/1000;right=(end-points[-1][0])/1000
    if left>=1.1*spacing:left=spacing/2
    if change>0:left=min(left,span*points[0][1]/change)
    if right>=1.1*spacing:right=spacing/2
    return change*(span+left+right)/span/window


def expected_windows(raw,end):
    """Derive eleven plotted values from gauge vectors and unaggregated counter matrices at the same timestamp."""
    expected={}
    def gauge(name):
        data=raw[name]['data'];assert data['resultType']=='vector'
        return data['result']
    def scalar(name):
        rows=gauge(name);assert len(rows)==1;return float(rows[0]['value'][1])
    def rates(name):
        data=raw[name]['data'];assert data['resultType']=='matrix' and data['result']
        return [counter_rate(row['values'],end) for row in data['result']]
    expected[(1,'A')]=[100*(1-math.fsum(rates('windows_cpu_time_seconds_total'))/len(raw['windows_cpu_time_seconds_total']['data']['result']))]
    available=scalar('windows_memory_available_bytes');total=scalar('windows_memory_physical_total_bytes')
    assert 0<=available<=total;expected[(2,'A')]=[100*(1-available/total)];expected[(3,'A')]=[available]
    free={x['metric']['volume']:float(x['value'][1]) for x in gauge('windows_logical_disk_free_bytes')}
    sizes={x['metric']['volume']:float(x['value'][1]) for x in gauge('windows_logical_disk_size_bytes')};assert free.keys()==sizes.keys() and free
    expected[(4,'A')]={k:100*(1-v/sizes[k]) for k,v in free.items()};expected[(5,'A')]=free
    for number,names in [(6,['windows_logical_disk_read_bytes_total','windows_logical_disk_write_bytes_total']),
            (7,['windows_net_bytes_received_total','windows_net_bytes_sent_total'])]:
        for ref,name in zip(['A','B'],names):expected[(number,ref)]=[math.fsum(rates(name))]
    expected[(8,'A')]=[scalar('up')]
    successes=gauge('windows_exporter_collector_success');assert {x['metric']['collector'] for x in successes}=={'cpu','memory','logical_disk','net'}
    expected[(9,'A')]=[min(float(x['value'][1]) for x in successes)]
    return expected


def compare_values(samples,expected):
    """Match all series, retaining disk volume identity and rejecting NaN or absent values."""
    if isinstance(expected,dict):
        actual={labels['volume']:value for labels,value in samples};assert len(actual)==len(samples) and actual.keys()==expected.keys()
        pairs=[(actual[k],v) for k,v in expected.items()]
    else:
        assert len(samples)==len(expected)==1;pairs=[(samples[0][1],expected[0])]
    assert all(math.isfinite(a) and math.isclose(a,b,rel_tol=1e-8,abs_tol=1e-7) for a,b in pairs),pairs
