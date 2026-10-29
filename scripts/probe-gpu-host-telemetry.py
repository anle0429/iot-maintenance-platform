"""Bracket one actual exporter response with independent GPU-host OS readings, including its physical network interface."""
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import build_opener,ProxyHandler
import argparse,ipaddress,json,os,re,socket,subprocess,time


def scalars(raw,name):
    """Parse only the selected scalar metric without importing the exporter's implementation."""
    values={}
    for line in raw.decode().splitlines():
        match=re.fullmatch(re.escape(name)+r'(?:\{([^}]*)\})? ([-+0-9.eE]+)',line)
        if match:
            labels=tuple(sorted(re.findall(r'([a-z_]+)="([^"\\]*)"',match[1] or '')))
            assert labels not in values;values[labels]=float(match[2])
    return values


def snapshot():
    """Read real kernel counters and root filesystem capacity directly on the worker."""
    stat=Path('/proc/stat').read_text();memory={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
    net={}
    for line in Path('/proc/1/net/dev').read_text().splitlines():
        if ':' not in line:continue
        name,body=line.split(':',1);fields=body.split();net[name.strip()]={'receive_bytes':int(fields[0]),'transmit_bytes':int(fields[8])}
    disk={}
    for line in Path('/proc/diskstats').read_text().splitlines():
        fields=line.split();disk[fields[2]]={'read_bytes':int(fields[5])*512,'written_bytes':int(fields[9])*512}
    fs=os.statvfs('/');ticks=os.sysconf('SC_CLK_TCK')
    return {'utc':datetime.now(timezone.utc).isoformat(),'hostname':socket.gethostname(),'logical_processors':os.cpu_count(),
        'idle_seconds':{line.split()[0][3:]:int(line.split()[4])/ticks for line in stat.splitlines() if re.match(r'^cpu[0-9]+ ',line)},
        'memory_bytes':{k:memory[k] for k in ['MemTotal','MemAvailable']},'network':net,'disk':disk,
        'root_filesystem':{'size_bytes':fs.f_blocks*fs.f_frsize,'available_bytes':fs.f_bavail*fs.f_frsize}}


def run(args):
    """Keep small declared sampling tolerances; a Ready Pod alone cannot establish host-metric correctness."""
    os.umask(0o077);out=args.output.resolve()
    assert out.is_relative_to('/srv/iot-inference/outputs') and socket.gethostname().split('.')[0]=='iot-cw-gpu'
    assert ipaddress.ip_address(args.pod_ip) in ipaddress.ip_network('10.52.1.0/24') and ipaddress.ip_address(args.node_ip).is_private
    out.mkdir(parents=True,exist_ok=False)
    addresses=json.loads(subprocess.check_output(['ip','-j','-4','address','show']))
    physical=[v['ifname'] for v in addresses if any(a.get('local')==args.node_ip for a in v.get('addr_info',[]))]
    assert len(physical)==1;interface=physical[0]
    before=snapshot();started=time.perf_counter()
    with build_opener(ProxyHandler({})).open('http://'+args.pod_ip+':9100/metrics',timeout=10) as response:
        raw=response.read(2*1024**2+1);assert response.status==200 and len(raw)<=2*1024**2
    elapsed=time.perf_counter()-started;after=snapshot()
    (out/'metrics.txt').write_bytes(raw)
    for name,value in [('before.json',before),('after.json',after),('addresses.json',addresses)]:
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    success=scalars(raw,'node_scrape_collector_success')
    assert {dict(k)['collector']:v for k,v in success.items()}=={'cpu':1,'meminfo':1,'filesystem':1,'diskstats':1,'netdev':1}
    idle={dict(k)['cpu']:v for k,v in scalars(raw,'node_cpu_seconds_total').items() if dict(k)['mode']=='idle'}
    assert len(idle)==before['logical_processors']==after['logical_processors']==4 and set(idle)==set(before['idle_seconds'])
    for cpu,value in idle.items():assert before['idle_seconds'][cpu]-.01<=value<=after['idle_seconds'][cpu]+.01
    total=list(scalars(raw,'node_memory_MemTotal_bytes').values());assert total==[before['memory_bytes']['MemTotal']]==[after['memory_bytes']['MemTotal']]
    available=list(scalars(raw,'node_memory_MemAvailable_bytes').values());assert len(available)==1
    assert min(before['memory_bytes']['MemAvailable'],after['memory_bytes']['MemAvailable'])-4*1024**2<=available[0]<=max(before['memory_bytes']['MemAvailable'],after['memory_bytes']['MemAvailable'])+4*1024**2
    for metric,field,tolerance in [('node_filesystem_size_bytes','size_bytes',0),('node_filesystem_avail_bytes','available_bytes',1024**2)]:
        values=[v for k,v in scalars(raw,metric).items() if dict(k)['mountpoint']=='/'];assert len(values)==1
        assert min(before['root_filesystem'][field],after['root_filesystem'][field])-tolerance<=values[0]<=max(before['root_filesystem'][field],after['root_filesystem'][field])+tolerance
    for metric,field in [('node_network_receive_bytes_total','receive_bytes'),('node_network_transmit_bytes_total','transmit_bytes')]:
        values=[v for k,v in scalars(raw,metric).items() if dict(k)['device']==interface];assert len(values)==1
        assert before['network'][interface][field]<=values[0]<=after['network'][interface][field]
    for metric,field in [('node_disk_read_bytes_total','read_bytes'),('node_disk_written_bytes_total','written_bytes')]:
        values=scalars(raw,metric);assert values
        for labels,value in values.items():
            device=dict(labels)['device'];assert device in before['disk'] and device in after['disk']
            assert before['disk'][device][field]<=value<=after['disk'][device][field]
    report={'passed':True,'checked_utc':datetime.now(timezone.utc).isoformat(),'hostname':before['hostname'],'node_ip':args.node_ip,'pod_ip':args.pod_ip,
        'physical_interface':interface,'logical_processors':4,'memory_total_bytes':total[0],'http_seconds':elapsed,'all_five_collectors_succeeded':True,
        'declared_sampling_tolerances':{'cpu_seconds':.01,'available_memory_bytes':4*1024**2,'available_filesystem_bytes':1024**2},
        'scope':'One real exporter response agrees with independently bracketed GPU-host kernel counters and root capacity, including the host interface. Cumulative disk/network counters are not instantaneous throughput; Prometheus rates and Grafana remain separate.'}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--pod-ip',required=True);parser.add_argument('--node-ip',required=True)
    run(parser.parse_args())
