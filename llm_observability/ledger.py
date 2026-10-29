"""Persist only approved telemetry facts with replay-safe identities and an explicit storage admission limit."""
from hashlib import sha256
from math import isfinite
from pathlib import Path
from threading import RLock
import json,re,sqlite3
from llm_observability.contracts import MODEL,ROLES,CATEGORIES,TOOLS,token_usage

RESULTS={'ok','policy_rejected','invalid_request','upstream_error','transport_error','invalid_response','cancelled'}


def validate_fact(kind,fact):
    """Refuse unreviewed fields and free-text labels so no prompt, answer or credential can enter the telemetry database."""
    common={'agent','model','duration_seconds','usage'}
    extra={'status','result','streaming','ttft_seconds','finish_reason','policy_categories'} if kind=='request' else {'operation','tool','error','trace_id','span_id','parent_span_id','start_ns','end_ns'}
    if kind not in {'request','span'} or not isinstance(fact,dict) or set(fact)!=common|extra:raise ValueError('Unexpected telemetry fields')
    if not isinstance(fact['agent'],str) or fact['agent'] not in ROLES or not isinstance(fact['model'],str) or fact['model'] not in {MODEL,'unknown'}:raise ValueError('Unbounded model or agent label')
    elapsed=fact['duration_seconds']
    if type(elapsed) not in {int,float} or not isfinite(elapsed) or not 0<=elapsed<=3600:raise ValueError('Invalid telemetry duration')
    if fact['usage'] is not None and fact['usage']!=token_usage(fact['usage']):raise ValueError('Unreviewed usage fields')
    if kind=='request':
        if type(fact['status']) is not int or not 100<=fact['status']<=599 or fact['result'] not in RESULTS or type(fact['streaming']) is not bool:raise ValueError('Invalid request outcome')
        first=fact['ttft_seconds']
        if first is not None and (type(first) not in {int,float} or not isfinite(first) or not 0<=first<=elapsed):raise ValueError('Invalid first-output timing')
        if first is not None and not fact['streaming']:raise ValueError('Nonstreaming responses have no observed first-token time')
        if fact['finish_reason'] not in {None,'stop','length','tool_calls','content_filter','other'}:raise ValueError('Invalid finish reason')
        categories=fact['policy_categories']
        if not isinstance(categories,list) or any(not isinstance(v,str) for v in categories) or categories!=sorted(set(categories)) or not set(categories)<=CATEGORIES:raise ValueError('Invalid policy categories')
    else:
        if fact['operation'] not in {'invocation','agent','model','tool','transport','other'} or fact['tool'] not in TOOLS|{'none','delegate_retrieval','delegate_drift'} or type(fact['error']) is not bool:raise ValueError('Invalid span classification')
        for name,length in [('trace_id',32),('span_id',16),('parent_span_id',16)]:
            value=fact[name]
            if name=='parent_span_id' and value=='':continue
            if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{'+str(length)+'}',value) or int(value,16)==0:raise ValueError('Invalid trace identity')
        if any(type(fact[n]) is not int or fact[n]<0 for n in ['start_ns','end_ns']) or fact['end_ns']<fact['start_ns']:raise ValueError('Invalid span timestamps')
        if elapsed!=(fact['end_ns']-fact['start_ns'])/1e9:raise ValueError('Span duration differs from its timestamps')
    return fact


class TelemetryLedger:
    """Keep one durable fact per request/span identity and reject conflicting replay or a full owned database."""
    def __init__(self,path,max_events=100000):
        """Open an explicit local database, using one locked SQLite writer and bounded event admission."""
        if type(max_events) is not int or not 1<=max_events<=1000000:raise ValueError('Invalid telemetry capacity')
        self.path=Path(path);self.limit=max_events;self.lock=RLock()
        self.connection=sqlite3.connect(self.path,timeout=5,check_same_thread=False)
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('CREATE TABLE IF NOT EXISTS telemetry_events (kind TEXT NOT NULL,event_id TEXT NOT NULL,facts TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(kind,event_id))')
        self.connection.commit()

    def put(self,kind,event_id,fact):
        """Accept an exact replay once, preserve the first record on conflict, and never evict receipts to hide capacity."""
        validate_fact(kind,fact)
        if not isinstance(event_id,str) or not re.fullmatch('[a-f0-9-]{16,80}',event_id):raise ValueError('Invalid event identity')
        raw=json.dumps(fact,sort_keys=True,separators=(',',':'),allow_nan=False);digest=sha256(raw.encode()).hexdigest()
        with self.lock,self.connection:
            previous=self.connection.execute('SELECT digest FROM telemetry_events WHERE kind=? AND event_id=?',(kind,event_id)).fetchone()
            if previous:
                if previous[0]!=digest:raise ValueError('Conflicting telemetry replay')
                return False
            count=self.connection.execute('SELECT COUNT(*) FROM telemetry_events').fetchone()[0]
            if count>=self.limit:raise ValueError('Telemetry storage admission limit reached')
            self.connection.execute('INSERT INTO telemetry_events VALUES(?,?,?,?)',(kind,event_id,raw,digest));return True

    def rows(self):
        """Return deterministic approved facts for metrics, independent reconciliation and restart checks."""
        with self.lock:
            return [{'kind':k,'event_id':i,'facts':json.loads(f)} for k,i,f in self.connection.execute('SELECT kind,event_id,facts FROM telemetry_events ORDER BY kind,event_id')]

    def close(self):
        """Close the owned connection after pending facts have been committed."""
        with self.lock:self.connection.close()
