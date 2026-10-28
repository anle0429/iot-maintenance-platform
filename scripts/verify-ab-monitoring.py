"""Independently check saved PC monitoring receipts, source hashes and all26 plotted values without project imports."""
from collections import Counter
from hashlib import sha256
from pathlib import Path,PurePosixPath
import ast,json,math,re,struct,subprocess,sys,tomllib


def main():
    """Audit existing evidence; this does not start services, rerun the experiment or prove native deployment."""
    root=Path(__file__).resolve().parents[1];base=root/'docs/evidence/ab-monitoring-pc'
    read=lambda n:json.loads((base/n).read_text(encoding='utf-8-sig'));digest=lambda b:sha256(b).hexdigest()
    manifest=read('manifest.json');proof=read('verification.json');commit=proof['source_commit']
    assert re.fullmatch('[a-f0-9]{40}',commit) and manifest['source_commit']==commit and len(manifest['files'])==20
    for name,item in manifest['files'].items():
        assert re.fullmatch(r'[A-Za-z0-9_.-]+',name)
        raw=(base/name).read_bytes();assert len(raw)==item['bytes']<1_000_000 and digest(raw)==item['sha256'],name
    assert sum(x['bytes'] for x in manifest['files'].values())==manifest['payload_bytes']
    assert len(proof['source_files'])==len(proof['executed_worktree_sha256'])==77
    changed=[];superseded=[]
    for name,expected in proof['source_files'].items():
        assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
        git=subprocess.check_output(['git','show',commit+':'+name],cwd=root);assert digest(git)==expected,name
        raw=(root/name).read_bytes()
        if raw!=git and raw.replace(b'\r\n',b'\n')!=git:
            # Native CI later changed export permissions and mutation workspace copying, outside this PC HTTP experiment.
            if name=='scripts/ci-serving.py':
                functions=lambda text:{n.name:ast.dump(n,include_attributes=False) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
                old,new=functions(git.decode()),functions(raw.decode())
                for used in ['http','http_text','json_response']:assert old[used]==new[used],used
            elif name=='pyproject.toml':
                old,new=tomllib.loads(git.decode()),tomllib.loads(raw.decode())
                old['tool']['mutmut']['also_copy']=[('scripts/' if n=='scripts/write-model-pin.py' else n) for n in old['tool']['mutmut']['also_copy']]
                assert old==new
            elif name in ['tests/api/test_ab_api.py','tests/api/test_model_binding.py']:
                # Later tests strengthen native wiring, persisted identity and metric consumer contracts.
                # The saved experiment still binds the exact historical test bytes above, not this expanded suite.
                functions=lambda text:{n.name:ast.dump(n,include_attributes=False) for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
                old,new=functions(git.decode()),functions(raw.decode())
                assert set(old)<=set(new)
                assert all(old[n]==new[n] for n in old if n!='test_native_startup_uses_existing_sdk_state_and_two_fixed_urls')
            elif name in ['ml/serving_ci.py','tests/test_serving_ci.py']:
                # The old PC HTTP experiment did not invoke the later native mutation inventory parser.
                omitted={'test_gate'} if name=='ml/serving_ci.py' else {'gate','test_native_mutmut_class_and_function_names_bind_to_the_same_seven_files'}
                old,new=ast.parse(git.decode()),ast.parse(raw.decode())
                for tree in [old,new]:
                    for parent in [tree]+[n for n in ast.walk(tree) if isinstance(n,ast.ClassDef)]:
                        parent.body=[n for n in parent.body if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) or n.name not in omitted]
                assert ast.dump(old,include_attributes=False)==ast.dump(new,include_attributes=False)
            else:raise AssertionError('PC experiment runtime source changed: '+name)
            superseded.append(name)
        execution=proof['executed_worktree_sha256'][name]
        if execution!=expected:
            assert digest(git.replace(b'\n',b'\r\n'))==execution;changed.append(name)
    assert changed==proof['newline_only_git_differences']
    projected=read('ledger-observations.json');rows=[dict(zip(projected['columns'],row,strict=True)) for row in projected['rows']]
    assert len(rows)==len({r['request_id'] for r in rows})==800
    counts=Counter();snapshots={v:set() for v in ['baseline','candidate']};cycles={};expected={i:{} for i in range(1,7)}
    portable=json.loads((root/'models/pump-failure-v1/portable.json').read_text(encoding='utf-8'))
    features=projected['feature_values'];z=portable['intercept']+sum((features[n]-mean)/std*weight if std else 0 for n,mean,std,weight in zip(portable['features'],portable['mean'],portable['std'],portable['coefficients'],strict=True))
    score=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
    for row in rows:
        variant='baseline' if sha256((projected['experiment']+'\0'+row['machine_id']).encode()).digest()[0]<128 else 'candidate'
        assert row['variant']==variant and row['version']==projected['pins'][variant]['version'] and row['status']==200
        assert math.isfinite(row['latency_ms']) and row['latency_ms']>=0 and math.isclose(row['risk_score'],score,abs_tol=1e-12)
        experiment,cycle,machine=row['request_id'].split(':');assert experiment==projected['experiment'] and machine==row['machine_id']
        cycles.setdefault(machine,set()).add(int(cycle));counts[variant]+=1;snapshots[variant].add((machine,row['feature_event_ts']))
    assert counts==proof['cohort_counts']=={'baseline':372,'candidate':428}
    assert all(value==set(range(4)) for value in cycles.values())
    assert {v:len(x) for v,x in snapshots.items()}==proof['unique_snapshots']=={'baseline':93,'candidate':107}
    for variant in counts:
        selected=[r for r in rows if r['variant']==variant];key=(variant,projected['pins'][variant]['version'],'');n=len(selected)
        expected[1][key]=n;expected[2][key]=0;expected[3][key]=len(snapshots[variant]);expected[5][key]=sum(r['risk_score'] for r in selected)/n
        lower_bound=lower_count=0;rank=.95*n
        for upper in [.005,.01,.025,.05,.1,.25,.5,1,5]:
            count=sum(r['latency_ms']/1000<=upper for r in selected)
            if count>=rank:
                expected[4][key]=lower_bound+(upper-lower_bound)*(rank-lower_count)/(count-lower_count);break
            lower_bound,lower_count=upper,count
        else:expected[4][key]=5.
        drift=projected['drift'][variant];unique=len(snapshots[variant]);assert drift['ready'] and drift['unique_rows']==unique and len(drift['features'])==8
        for feature,value in drift['features'].items():
            assert value['cuts']==[features[feature]] and value['current_counts']==[0,unique] and value['reference_counts']==[0,64]
            p=[.5/65,64.5/65];q=[.5/(unique+1),(unique+.5)/(unique+1)];psi=sum((b-a)*math.log(b/a) for a,b in zip(p,q))
            assert math.isclose(psi,value['psi'],abs_tol=1e-14);expected[6][(*key[:2],feature)]=psi
    def compare(actual,wanted):
        assert len(actual)==len(wanted)
        actual=dict(actual);assert actual.keys()==wanted.keys()
        assert all(math.isfinite(actual[k]) and math.isclose(actual[k],v,rel_tol=1e-10,abs_tol=1e-12) for k,v in wanted.items())
    template=json.loads((root/'infra/ab-api/grafana-dashboard.json').read_text(encoding='utf-8'))
    rendered=read('grafana-dashboard-readback.json')['dashboard'];assert '${DS_PROMETHEUS}' not in json.dumps(rendered)
    assert len(rendered['panels'])==len(template['panels'])==6
    results=read('panel-queries.json');assert [r['panel'] for r in results]==list(range(1,7))
    for panel,item in zip(template['panels'],results,strict=True):
        number=item['panel'];assert item['expr']==panel['targets'][0]['expr'].replace('$experiment',projected['experiment'])
        assert rendered['panels'][number-1]['targets']==panel['targets']
        assert rendered['panels'][number-1]['datasource']['uid']==proof['datasource_uid']
        prom=item['prometheus'];assert prom['status']=='success' and prom['data']['resultType']=='vector'
        samples=[]
        for r in prom['data']['result']:
            labels=r['metric'];assert labels.get('experiment')==(None if number==4 else projected['experiment'])
            samples.append(((labels['variant'],labels['version'],labels.get('feature','')),float(r['value'][1])))
        compare(samples,expected[number]);assert item['series_count']==len(samples)
        answer=item['grafana']['results']['A'];assert answer.get('status',200)==200 and not answer.get('error');plotted=[]
        for frame in answer['frames']:
            for i,field in enumerate(frame['schema']['fields']):
                if field['type']=='number':
                    labels=field['labels'];values=frame['data']['values'][i];assert len(values)==1
                    assert labels.get('experiment')==(None if number==4 else projected['experiment'])
                    plotted.append(((labels['variant'],labels['version'],labels.get('feature','')),values[0]))
        compare(plotted,expected[number])
    for number,values in read('ledger-expected-values.json').items():compare([((r['variant'],r['version'],r['feature']),r['value']) for r in values],expected[int(number)])
    assert read('prometheus-targets.json')['data']['activeTargets'][0]['health']=='up'
    assert read('scrape-down.json')['data']['activeTargets'][0]['health']=='down'
    assert read('grafana-health.json')['database']=='ok' and proof['grafana_version']=='13.3.0' and proof['prometheus_version']=='3.13.5'
    observation=read('pc-process-observation.json');assert len(observation['listeners'])==2 and all(x['LocalAddress']=='127.0.0.1' for x in observation['listeners'])
    stopped=read('owned-processes-stopped.json');assert type(stopped['prometheus_exit_code']) is int and type(stopped['grafana_exit_code']) is int and stopped['all_api_threads_joined']
    assert proof['concurrent_replays']==40 and proof['replay_added_trials']==0 and proof['query_series_per_engine']==26
    assert proof['actual_grafana_ui_observed'] and proof['registry_and_feast_are_fixtures'] and proof['both_versions_use_same_legacy_weights'] and not proof['cloud_or_kubernetes_contacted']
    for name in ['grafana-overview.jpg','grafana-psi.jpg']:
        raw=(base/name).read_bytes();assert raw[:2]==b'\xff\xd8';offset=2;dimensions=None
        while offset<len(raw):
            assert raw[offset]==255;marker=raw[offset+1];offset+=2
            if marker==0xda:break
            size=struct.unpack('>H',raw[offset:offset+2])[0]
            if marker in [0xc0,0xc1,0xc2]:
                height,width=struct.unpack('>HH',raw[offset+3:offset+7]);dimensions=(width,height);break
            offset+=size
        assert dimensions==(1280,720)
    print('PASS20payload hashes /77historical source bindings /800trial assignments+scores /26values per query engine /6panels /actual scrape loss+recovery /loopback+cleanup. Superseded CI/test metadata only:',superseded,'; used HTTP helpers/API/monitoring unchanged. Historical tests are not the expanded current suite. No native deployment claim.')


if __name__=='__main__':main()
