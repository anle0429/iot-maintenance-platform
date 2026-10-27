"""Verify real Windows host telemetry through standalone exporter, Prometheus and Grafana; no simulated host data."""
from contextlib import ExitStack,contextmanager
from pathlib import Path
import ctypes,json,math,os,secrets,shutil,socket,subprocess,time
import httpx,pytest
from ml.host_monitoring import DATASOURCE_UID,exporter_arguments,profile,queries,write_bundle
from tests.monitoring_oracle import expected_windows,compare_values

pytestmark=pytest.mark.skipif(os.name!='nt' or not all(os.environ.get(k) for k in ['IOT_HOST_EXPORTER_EXE','IOT_PROMETHEUS_EXE','IOT_GRAFANA_EXE','IOT_GRAFANA_HOME']),
    reason='Explicit Windows exporter/Prometheus/Grafana binaries required; no fixture substituted')


def port():
    """Allocate a candidate private port; startup must still succeed if another process wins the race."""
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def wait_for(check,seconds=60):
    """Wait for actual query/service readiness with a deadline, retaining errors instead of creating zero samples."""
    end=time.monotonic()+seconds;last=None
    while time.monotonic()<end:
        try:
            result=check()
            if result:return result
        except (httpx.HTTPError,AssertionError,KeyError,IndexError,ValueError) as error:last=error
        time.sleep(.5)
    raise AssertionError('Host telemetry did not become ready') from last


@contextmanager
def running(command,log,env):
    """Start a private owned process, retain logs and wait for its terminal state on every exit path."""
    with log.open('wb') as out:
        process=subprocess.Popen(command,stdout=out,stderr=subprocess.STDOUT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        try:yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(15)
                except subprocess.TimeoutExpired:process.kill();process.wait(10)
            assert process.poll() is not None


def physical_memory():
    """Read independent native Win32 total physical memory, without scraping the exporter or querying Prometheus."""
    class Status(ctypes.Structure):
        """Match the public MEMORYSTATUSEX layout used by GlobalMemoryStatusEx."""
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),*[(n,ctypes.c_ulonglong) for n in ['total','available','page_total','page_available','virtual_total','virtual_available','extended']]]
    status=Status();status.length=ctypes.sizeof(status);assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.total


def test_real_windows_exporter_prometheus_grafana(tmp_path):
    """Check all eleven panel queries against raw same-time samples and native OS facts, then keep optional real UI proof."""
    root=tmp_path/'host-monitor';root.mkdir();exporter_port,prometheus_port,grafana_port=port(),port(),port()
    while len({exporter_port,prometheus_port,grafana_port})!=3:exporter_port,prometheus_port,grafana_port=port(),port(),port()
    endpoint=f'http://127.0.0.1:{exporter_port}';host='pc-windows'
    launch=write_bundle(root/'runtime',endpoint,'windows',host,'PC Windows actual host',prometheus_port,grafana_port)
    env={**os.environ,'GOMEMLIMIT':'384MiB','GOMAXPROCS':'2','GF_SECURITY_ADMIN_USER':'local-audit','GF_SECURITY_ADMIN_PASSWORD':secrets.token_urlsafe(32)}
    binary=Path(os.environ['IOT_PROMETHEUS_EXE']).with_name('promtool.exe')
    check=subprocess.run([str(binary),'check','config',str(root/'runtime/prometheus.json')],capture_output=True,text=True,timeout=20)
    (root/'promtool.txt').write_text(check.stdout+check.stderr,encoding='utf-8');assert check.returncode==0
    with ExitStack() as stack,httpx.Client(timeout=15,trust_env=False) as client:
        exporter=stack.enter_context(running([os.environ['IOT_HOST_EXPORTER_EXE'],*exporter_arguments('windows',exporter_port)],root/'exporter.log',env))
        prom=stack.enter_context(running([os.environ['IOT_PROMETHEUS_EXE'],*launch['prometheus_arguments']],root/'prometheus.log',env))
        graf=stack.enter_context(running([os.environ['IOT_GRAFANA_EXE'],*launch['grafana_arguments'],'--homepath='+os.environ['IOT_GRAFANA_HOME']],root/'grafana.log',env))
        purl,gurl=launch['prometheus_url'],launch['grafana_url'];wait_for(lambda:client.get(purl+'/-/ready').status_code==200)
        health=wait_for(lambda:(r.json() if (r:=client.get(gurl+'/api/health')).status_code==200 else None));assert health['database']=='ok'
        def query(expr,at=None):
            """Get genuine instant or raw range-vector results from the real query engine at a fixed time."""
            params={'query':expr}
            if at is not None:params['time']=format(at,'.3f')
            response=client.get(purl+'/api/v1/query',params=params);response.raise_for_status();body=response.json();assert body['status']=='success';return body
        wait_for(lambda:query('up{job="iot-host"}')['data']['result'][0]['value'][1]=='1')
        # Fill the full30s rate window; an exporter starting halfway through would bias the initial extrapolated CPU value.
        wait_for(lambda:min(float(x['value'][1]) for x in query('count_over_time(windows_cpu_time_seconds_total{mode="idle"}[30s])')['data']['result'])>=15)
        end=math.floor(time.time()*1000)/1000
        sel='{job="iot-host",host="pc-windows"}';raw={}
        for name in profile('windows')['metric_names'][:-1]:
            if name=='windows_cpu_time_seconds_total':expression=name+'{job="iot-host",host="pc-windows",mode="idle"}[30s]'
            elif name.startswith('windows_net_bytes_'):expression=name+'{job="iot-host",host="pc-windows",nic!~"(?i).*loopback.*"}[30s]'
            elif name.endswith('_total'):expression=name+sel+'[30s]'
            else:expression=name+sel
            raw[name]=query(expression,end)
        raw['up']=query('up'+sel,end);expected=expected_windows(raw,end)
        native={'logical_processors':os.cpu_count(),'physical_memory_bytes':physical_memory(),'disk':shutil.disk_usage(Path.cwd().anchor)._asdict()}
        assert native['logical_processors']==len(raw['windows_cpu_time_seconds_total']['data']['result'])
        assert native['physical_memory_bytes']==float(raw['windows_memory_physical_total_bytes']['data']['result'][0]['value'][1])
        sizes={r['metric']['volume']:float(r['value'][1]) for r in raw['windows_logical_disk_size_bytes']['data']['result']}
        frees={r['metric']['volume']:float(r['value'][1]) for r in raw['windows_logical_disk_free_bytes']['data']['result']}
        drive=Path.cwd().anchor.rstrip('\\');assert sizes[drive]==native['disk']['total'] and abs(frees[drive]-native['disk']['free'])<64*1024**2
        assert expected[(8,'A')]==expected[(9,'A')]==[1]
        document=wait_for(lambda:(r.json() if (r:=client.get(gurl+'/api/dashboards/uid/'+launch['dashboard_uid'])).status_code==200 else None))
        assert len(document['dashboard']['panels'])==9 and document['dashboard']['templating']['list'][0]['current']['value']==host
        datasource=client.get(gurl+'/api/datasources/uid/'+DATASOURCE_UID);datasource.raise_for_status();assert datasource.json()['url']==purl
        results=[]
        for target in queries('windows',host):
            number,ref=target['panel'],target['refId'];promql=query(target['expr'],end);assert promql['data']['resultType']=='vector'
            samples=[(r['metric'],float(r['value'][1])) for r in promql['data']['result']];compare_values(samples,expected[(number,ref)])
            response=client.post(gurl+'/api/ds/query',json={'from':str(round((end-300)*1000)),'to':str(round(end*1000)),
                'queries':[{'refId':ref,'datasource':{'uid':DATASOURCE_UID,'type':'prometheus'},'expr':target['expr'],
                'instant':True,'range':False,'format':'time_series','intervalMs':2000,'maxDataPoints':100}]})
            response.raise_for_status();grafana=response.json();answer=grafana['results'][ref];assert answer.get('status',200)==200 and not answer.get('error');plotted=[]
            for frame in answer['frames']:
                for i,field in enumerate(frame['schema']['fields']):
                    values=frame['data']['values'][i];assert len(values)==1
                    if field['type']=='time':assert values[0]==round(end*1000)
                    if field['type']=='number':plotted.append((field['labels'],values[0]))
            compare_values(plotted,expected[(number,ref)])
            results.append({**target,'evaluation_time':end,'prometheus':promql,'grafana':grafana,'same_time_oracle_passed':True})
        discarded=query('{__name__=~"windows_net_nic_.*"}',end);assert not discarded['data']['result']
        names=client.get(purl+'/api/v1/label/__name__/values').json();assert not any(n in names['data'] for n in ['windows_net_nic_info','windows_net_nic_address_info','windows_os_info'])
        targets=client.get(purl+'/api/v1/targets').json();assert targets['data']['activeTargets'][0]['health']=='up'
        build=query('windows_exporter_build_info'+sel,end);assert build['data']['result'][0]['metric']['version']=='0.32.0'
        proof={'passed':True,'family':'windows','host_alias':host,'exporter_version':'0.32.0','prometheus_version':client.get(purl+'/api/v1/status/buildinfo').json()['data']['version'],
            'grafana_version':health['version'],'dashboard_uid':launch['dashboard_uid'],'panels':9,'queries_per_engine':11,'evaluation_time':end,
            'same_time_raw_sample_oracle':True,'native_cpu_memory_disk_sanity_passed':True,'disk_free_sanity_tolerance_bytes':64*1024**2,
            'mac_and_ip_info_metrics_discarded':True,'all_four_collectors_success':True,'host_samples_are_real':True,'cloud_or_kubernetes_contacted':False,
            'scope':'Actual Windows PC host-wide exporter metrics, Prometheus and Grafana; includes other applications. No VM deployment, job attribution, peak-resource benchmark, authentication or service-mesh claim.'}
        for name,value in [('verification.json',proof),('raw-samples-private.json',raw),('panel-queries.json',results),('grafana-dashboard-readback.json',document),
                ('native-os-sanity.json',native),('prometheus-targets.json',targets),('exporter-build.json',build),('ingested-metric-names.json',names),('grafana-health.json',health)]:
            (root/name).write_bytes(json.dumps(value,indent=2,allow_nan=False).encode()+b'\n')
        hold=os.environ.get('IOT_HOST_MONITORING_HOLD_FILE')
        if hold:
            (root/'ui-ready.json').write_bytes(json.dumps({'dashboard_url':gurl+'/d/'+launch['dashboard_uid']+'?orgId=1&var-host='+host+'&from=now-5m&to=now',
                'pids':[exporter.pid,prom.pid,graf.pid]}).encode()+b'\n')
            deadline=time.monotonic()+600
            while not Path(hold).exists() and time.monotonic()<deadline:
                assert all(p.poll() is None for p in [exporter,prom,graf]);time.sleep(.5)
    (root/'owned-processes-stopped.json').write_bytes(json.dumps({'exit_codes':{k:p.returncode for k,p in [('exporter',exporter),('prometheus',prom),('grafana',graf)]},'all_three_processes_terminal':True}).encode()+b'\n')
