"""Audit saved actual-PC host telemetry, source identities, same-time arithmetic and privacy filters; starts no service."""
from pathlib import Path,PurePosixPath
from hashlib import sha256
from xml.etree import ElementTree
import json,re,subprocess,sys


def main():
    """Verify retained evidence rather than claim to rerun a host or deploy the Linux profile."""
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    from tests.monitoring_oracle import expected_windows,compare_values,counter_rate
    base=root/'docs/evidence/host-monitoring-pc';read=lambda n:json.loads((base/n).read_text(encoding='utf-8-sig'));digest=lambda b:sha256(b).hexdigest()
    manifest=read('manifest.json');proof=read('verification.json');commit=proof['source_commit']
    assert re.fullmatch('[a-f0-9]{40}',commit) and manifest['source_commit']==commit and len(manifest['files'])==20
    for name,item in manifest['files'].items():
        assert re.fullmatch(r'[A-Za-z0-9_.-]+',name);raw=(base/name).read_bytes()
        assert digest(raw)==item['sha256'] and len(raw)==item['bytes']<1_000_000,name
    assert sum(x['bytes'] for x in manifest['files'].values())==manifest['payload_bytes']
    assert proof['execution_bytes_equal_git'] and len(proof['source_files'])==10
    for name,expected in proof['source_files'].items():
        assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
        raw=subprocess.check_output(['git','show',commit+':'+name],cwd=root);assert digest(raw)==expected,name
        assert (root/name).read_bytes()==raw,name
    fixture=json.loads((root/'tests/fixtures/host-monitoring-promql.json').read_text(encoding='utf-8'))
    assert len(fixture['tests'])==proof['promtool_groups']==5
    assert sum(len(x['promql_expr_test']) for x in fixture['tests'])==proof['promtool_known_answer_expressions']==35
    assert (base/'promtool-rules.txt').read_text(encoding='utf-8').strip()=='SUCCESS'
    cases=list(ElementTree.parse(base/'test-junit.xml').iter('testcase'));assert len(cases)==proof['tests_passed']==22
    assert all(not list(x) for x in cases)
    raw=read('raw-samples.json');end=proof['evaluation_time'];expected=expected_windows(raw,end)
    cores=raw['windows_cpu_time_seconds_total']['data']['result'];assert len(cores)==read('native-os-sanity.json')['logical_processors']==12
    assert all(len(x['values'])>=15 and x['metric']['mode']=='idle' for x in cores)
    native=read('native-os-sanity.json');assert float(raw['windows_memory_physical_total_bytes']['data']['result'][0]['value'][1])==native['physical_memory_bytes']
    disk_size={x['metric']['volume']:float(x['value'][1]) for x in raw['windows_logical_disk_size_bytes']['data']['result']}
    disk_free={x['metric']['volume']:float(x['value'][1]) for x in raw['windows_logical_disk_free_bytes']['data']['result']}
    assert disk_size['C:']==native['disk']['total'] and abs(disk_free['C:']-native['disk']['free'])<proof['disk_free_sanity_tolerance_bytes']==67108864
    names=read('ingested-metric-names.json')['data']
    assert not set(names)&{'windows_net_nic_info','windows_net_nic_address_info','windows_os_info'}
    for item in raw.values():
        assert item['status']=='success'
        for sample in item['data']['result']:
            labels=sample['metric'];assert labels['host']==proof['host_alias']=='pc-windows' and labels['job']=='iot-host'
            assert not set(labels)&{'mac','address','hostname','process','user'}
            if 'nic' in labels:assert re.fullmatch(r'nic-[12]',labels['nic'])
    profile=json.loads((root/'infra/monitoring/host-profiles.json').read_text(encoding='utf-8'))['profiles']['windows']
    targets={(p['id'],chr(65+i)):t for p in profile['panels'] for i,t in enumerate(p['targets'])}
    dashboard=read('grafana-dashboard-readback.json')['dashboard'];assert len(dashboard['panels'])==proof['panels']==9
    for spec,panel in zip(profile['panels'],dashboard['panels'],strict=True):
        assert spec['id']==panel['id'] and spec['unit']==panel['fieldConfig']['defaults']['unit']
        assert all(t['expr']==s['expr'] for t,s in zip(panel['targets'],spec['targets'],strict=True))
        assert panel['datasource']['uid']=='iot-host-prometheus'
    queries=read('panel-queries.json');assert len(queries)==len(expected)==proof['queries_per_engine']==11
    seen=set()
    for item in queries:
        key=(item['panel'],item['refId']);assert key not in seen;seen.add(key)
        assert item['expr']==targets[key]['expr'].replace('$host','pc-windows') and item['evaluation_time']==end
        prometheus=item['prometheus'];assert prometheus['status']=='success' and prometheus['data']['resultType']=='vector'
        samples=[(r['metric'],float(r['value'][1])) for r in prometheus['data']['result']];compare_values(samples,expected[key])
        answer=item['grafana']['results'][key[1]];assert answer.get('status',200)==200 and not answer.get('error');plotted=[]
        for frame in answer['frames']:
            for index,field in enumerate(frame['schema']['fields']):
                values=frame['data']['values'][index];assert len(values)==1
                if field['type']=='time':assert values[0]==round(end*1000)
                if field['type']=='number':plotted.append((field['labels'],values[0]))
        compare_values(plotted,expected[key])
    assert seen==expected.keys()
    cold=read('cold-start-check.json');assert cold['passed'] and cold['samples_per_cpu_series']==3 and cold['unguarded_cpu_rate_has_series']==12
    for core in cores:
        first=[point for point in core['values'] if cold['evaluation_time']-30<point[0]<=cold['evaluation_time']]
        assert len(first)==3 and counter_rate(first,cold['evaluation_time'])>=0
    assert len(cold['full_window_guarded_queries'])==proof['actual_cold_start_queries_hidden']==5
    for item in cold['full_window_guarded_queries']:
        assert item['expr']==targets[item['panel'],item['refId']]['expr'].replace('$host','pc-windows')
        assert item['response']['status']=='success' and item['response']['data']['result']==[]
    assert expected[8,'A']==expected[9,'A']==[1] and read('prometheus-targets.json')['data']['activeTargets'][0]['health']=='up'
    assert read('grafana-health.json')['database']=='ok' and proof['grafana_version']=='13.3.0' and proof['prometheus_version']=='3.13.5'
    assert read('exporter-build.json')['data']['result'][0]['metric']['version']==proof['exporter_version']=='0.32.0'
    assert read('exporter-download.json')['sha256']=='ca811672573c930c6ba0dd0fd9ac3e010b74b4e951595485fc04d06e7c608687'
    listeners=read('pc-process-observation.json')['listeners'];assert len(listeners)==3 and all(x['LocalAddress']=='127.0.0.1' for x in listeners)
    stopped=read('owned-processes-stopped.json');assert stopped['all_three_processes_terminal'] and all(type(x) is int for x in stopped['exit_codes'].values())
    assert proof['host_samples_are_real'] and proof['linux_profile_has_fixture_queries_only'] and not proof['linux_exporter_launched'] and not proof['cloud_or_kubernetes_contacted']
    for name,item in proof['redactions'].items():assert digest((base/name).read_bytes())==item['published_sha256']
    for name in ['host-overview.jpg','host-network.jpg']:assert (base/name).read_bytes()[:2]==b'\xff\xd8'
    print('PASS20payload hashes/10source bindings/22tests/35known-answer PromQL expressions/11same-time queries per engine/real OS sanity/5actual cold-start guards/loopback+cleanup. Windows PC snapshot; Linux VM evidence uses verify-linux-host.py separately.')


if __name__=='__main__':main()
