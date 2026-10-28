"""Reconstruct timestamped log records and interval counts directly from raw source bytes, independent of Alloy/Loki code."""
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
import calendar,json,re,shlex


def timestamp_ns(value):
    """Keep all nine fractional digits and explicit UTC offsets without floating-point epoch conversion."""
    match=re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)',value)
    if not match:raise ValueError('Expected a complete timestamp with an explicit timezone')
    stamp=datetime.fromisoformat(match[1]+match[3].replace('Z','+00:00')).astimezone(timezone.utc)
    return calendar.timegm(stamp.timetuple())*10**9+int((match[2] or '').ljust(9,'0'))


def raw_records(mapping,raw_files):
    """Audit complete logfmt/CRI-F records; unparseable or partial chunks cannot silently become original-time proof."""
    records=[]
    for item in mapping:
        raw=raw_files[item['file']]
        if raw and not raw.endswith(b'\n'):raise ValueError('Capture ends in an incomplete source record')
        for line in raw.decode('utf-8').splitlines():
            labels=dict(item['labels'])
            if item['kind']=='host-file':
                fields=dict(token.split('=',1) for token in shlex.split(line) if '=' in token)
                stamp=timestamp_ns(fields[item['time_key']]);labels['level']=fields['level']
                level=fields['level'].lower();message=line
            elif item['kind']=='cri':
                clock,stream,flag,message=line.split(' ',3)
                if flag!='F':raise ValueError('This captured-record oracle requires full CRI records')
                stamp=timestamp_ns(clock);labels['stream']=stream
                match=re.match(r'\s*(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b',message)
                if not match:raise ValueError('Captured CRI severity is not explicit')
                level=match[1].lower()
            else:raise ValueError('Unknown source format')
            # Loki3.7 adds service_name and returns detected_level structured metadata as query labels.
            labels.update(service_name=labels['service'],detected_level='warn' if level=='warning' else level)
            records.append({'timestamp_ns':stamp,'labels':labels,'line':message})
    return records


def key(record):
    """Compare message text, timestamp and every returned label; a changed Pod or omitted level is observable."""
    return (record['timestamp_ns'],tuple(sorted(record['labels'].items())),record['line'])


def query_records(response):
    """Read native Loki stream responses without trusting query-execution statistics as log evidence."""
    if response['status']!='success' or response['data']['resultType']!='streams':raise ValueError('Expected a successful stream query')
    result=[]
    for stream in response['data']['result']:
        labels=stream['stream'];assert all(isinstance(k,str) and isinstance(v,str) for k,v in labels.items())
        for value in stream['values']:
            if len(value) not in [2,3] or not value[0].isdigit() or not isinstance(value[1],str):raise ValueError('Invalid native log record')
            result.append({'timestamp_ns':int(value[0]),'labels':dict(labels),'line':value[1]})
    return result


def compare_logs(expected,response,start,end,source=None):
    """Require exact source-byte equality for every timestamped record in one fixed inclusive query window."""
    wanted=Counter(key(r) for r in expected if start<=r['timestamp_ns']<=end and (source is None or r['labels']['source']==source))
    actual=Counter(key(r) for r in query_records(response))
    if actual!=wanted:raise ValueError('Log source/readback differs: missing='+str(sum((wanted-actual).values()))+', unexpected='+str(sum((actual-wanted).values())))
    return sum(wanted.values())


def compare_counts(records,response,start,end,step,source=None):
    """Recompute all count_over_time60s samples using (t-60s,t], preserving absent zero series."""
    if response['status']!='success' or response['data']['resultType']!='matrix':raise ValueError('Expected a successful metric range query')
    fields=['service'] if source is None else ['service','stream']
    selected=[r for r in records if source is None or r['labels']['source']==source]
    actual={}
    for series in response['data']['result']:
        labels=series['metric'];assert set(labels)==set(fields)
        group=tuple(labels[n] for n in fields)
        for stamp,value in series['values']:
            ns=int(Decimal(str(stamp))*10**9)
            if (group,ns) in actual:raise ValueError('Duplicate metric sample')
            actual[group,ns]=Decimal(value)
    expected={}
    for stamp in range(start,end+1,step):
        counts=Counter(tuple(r['labels'][n] for n in fields) for r in selected if stamp-60*10**9<r['timestamp_ns']<=stamp)
        expected.update({(group,stamp):Decimal(n) for group,n in counts.items()})
    if any(value!=expected.get(k,Decimal(0)) for k,value in actual.items()) or any(actual.get(k)!=value for k,value in expected.items()):
        raise ValueError('Metric window arithmetic or source groups differ')
    return {'returned_samples':len(actual),'positive_samples':len(expected),'groups':len({k[0] for k in expected})}
