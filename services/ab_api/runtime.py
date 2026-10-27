"""Route stable machine cohorts and persist each trial once before computing shared-reference drift."""
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import json,re,sqlite3,time
from services.feature_api.runtime import NotOperating,Unavailable,aware
from services.predictor.binding import FIELDS,validate_pin
from services.drift_api.contracts import CompareRequest
from services.drift_api.detector import DriftDetector,canonical_bytes

VARIANTS=('baseline','candidate')


class TrialConflict(ValueError):
    """A request ID already names a different machine; preserve its original outcome."""


class TrialCapacity(RuntimeError):
    """The declared experiment retention is full; never evict earlier trials to accept more."""


class UnknownMachine(ValueError):
    """The requested machine is outside the sealed experiment context."""


def cohort(experiment,machine):
    """Assign the machine, not its caller-chosen request ID or score, using a fixed SHA256 half split."""
    return VARIANTS[sha256((experiment+'\0'+machine).encode()).digest()[0]//128]


class ABRuntime:
    """Keep bounded private experiment state durable across workers; serialized writes prioritize replay correctness."""
    def __init__(self,experiment,runtimes,reference,database,max_requests=2048):
        """Bind the database to both pins, the exact shared context and immutable reference identity."""
        if not isinstance(experiment,str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,64}',experiment):raise ValueError('Invalid experiment identity')
        if type(max_requests) is not int or not 1<=max_requests<=2048 or str(database)==':memory:':raise ValueError('Use bounded persistent trial storage')
        if set(runtimes)!=set(VARIANTS):raise ValueError('Require exactly two named variants')
        self.experiment=experiment;self.runtimes=dict(runtimes);self.context=deepcopy(runtimes['baseline'].context)
        self.pins={name:validate_pin(deepcopy(runtime.model_pin)) for name,runtime in runtimes.items()}
        self.detector=DriftDetector(reference);self.database=str(database);self.max_requests=max_requests
        if (self.pins['baseline']['version']==self.pins['candidate']['version'] or self.pins['candidate']['production'] is not True
                or any(canonical_bytes(runtime.context)!=canonical_bytes(self.context) for runtime in runtimes.values())
                or any(pin['protocol_sha256']!=self.context['protocol_sha256'] for pin in self.pins.values())
                or reference['lineage'].get('protocol_sha256')!=self.context['protocol_sha256']):raise ValueError('Variant context, versions or reference protocol differs')
        seal=canonical_bytes({'experiment':experiment,'pins':self.pins,'context':self.context,'reference_id':self.detector.reference_id,'max_requests':max_requests}).decode()
        Path(database).parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as connection,connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY CHECK(id=1), seal TEXT NOT NULL)')
            row=connection.execute('SELECT seal FROM identity WHERE id=1').fetchone()
            if row is not None and row[0]!=seal:raise TrialConflict('Database belongs to another experiment, context, model pair or capacity')
            connection.execute('INSERT OR IGNORE INTO identity VALUES(1,?)',(seal,))
            connection.execute('CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY, machine TEXT NOT NULL, variant TEXT NOT NULL, response TEXT NOT NULL)')

    def connect(self):
        """Use a thread-owned connection and an explicit bounded lock wait on the existing node volume."""
        return sqlite3.connect(self.database,timeout=5)

    def ready(self):
        """Actually score an operating machine through both pinned backends without creating experiment trials."""
        machine=next(m for m,value in self.context['machines'].items() if value['status']=='operating')
        for variant in VARIANTS:
            result=self.runtimes[variant].predict(machine)
            if validate_pin({name:result['model'][name] for name in FIELDS})!=self.pins[variant]:raise ValueError('Readiness model identity differs')
        with closing(self.connect()) as connection:connection.execute('SELECT COUNT(*) FROM trials').fetchone()
        return {'ready':True,'experiment':self.experiment,'reference_id':self.detector.reference_id,'pins':self.pins}

    def predict(self,machine,request_id):
        """Cache failures and successes alike; exact retries are byte-equivalent and never add another trial."""
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,100}',request_id):raise ValueError('Invalid request identity')
        if machine not in self.context['machines']:raise UnknownMachine(machine)
        if any(not 0<=(runtime.clock()-runtime.prepared).total_seconds()<3600 for runtime in self.runtimes.values()):raise Unavailable('Experiment feature lease expired, including cached trials')
        variant=cohort(self.experiment,machine)
        with closing(self.connect()) as connection,connection:
            connection.execute('BEGIN IMMEDIATE')
            previous=connection.execute('SELECT machine,response FROM trials WHERE id=?',(request_id,)).fetchone()
            if previous:
                if previous[0]!=machine:raise TrialConflict('Request ID already belongs to another machine')
                return json.loads(previous[1])
            if connection.execute('SELECT COUNT(*) FROM trials').fetchone()[0]>=self.max_requests:raise TrialCapacity('Experiment full; retain its evidence and start an explicitly new experiment')
            started=time.perf_counter()
            try:
                result=self.runtimes[variant].predict(machine)
                if validate_pin({name:result['model'][name] for name in FIELDS})!=self.pins[variant]:raise ValueError('Experiment model identity differs')
                status=200
            except NotOperating:
                result={'detail':'Prediction is defined only while operating'};status=409
            except Exception:
                result={'detail':'Feature/model data is unavailable or failed validation'};status=503
            response={'experiment':self.experiment,'request_id':request_id,'machine_id':machine,'variant':variant,
                'status':status,'latency_ms':1000*(time.perf_counter()-started),**result}
            encoded=canonical_bytes(response).decode()
            connection.execute('INSERT INTO trials VALUES(?,?,?,?)',(request_id,machine,variant,encoded))
            return json.loads(encoded)

    def snapshot(self):
        """Read a consistent durable ledger; deduplicate snapshots before drift and never invent ground truth."""
        with closing(self.connect()) as connection:
            rows=[json.loads(row[0]) for row in connection.execute('SELECT response FROM trials ORDER BY id')]
        metrics={};drift={}
        for variant in VARIANTS:
            trials=[row for row in rows if row['variant']==variant];observations=[]
            for row in trials:
                if row['status']!=200:continue
                trace=row['feature_trace']
                observations.append({'id':sha256((row['machine_id']+'\0'+trace['feature_event_ts']).encode()).hexdigest(),
                    'event_timestamp':trace['feature_event_ts'],'available_at':trace['as_of'],'features':trace['features']})
            canonical={}
            for row in observations:
                if row['id'] in canonical and canonical[row['id']]!=row:raise ValueError('Conflicting feature snapshot under one identity')
                canonical[row['id']]=row
            observations=list(canonical.values());unique=len(observations)
            metrics[variant]={'requests':len(trials),'failures':sum(row['status']!=200 for row in trials)}
            drift[variant]={'ready':False,'unique_rows':unique,'reference_id':self.detector.reference_id}
            if unique>=32:
                start=min(aware(row['event_timestamp']) for row in observations);end=aware(self.context['as_of'])+timedelta(microseconds=1)
                request=CompareRequest.model_validate({'reference_id':self.detector.reference_id,'window_id':self.experiment+':'+variant,
                    'start':start,'end':end,'observations':observations})
                drift[variant]={'ready':True,**self.detector.compare(request.canonical())}
        return {'experiment':self.experiment,'baseline_pin':self.pins['baseline'],'candidate_pin':self.pins['candidate'],
            'reference_id':self.detector.reference_id,'ground_truth_used':False,'assignment':'sha256(experiment NUL machine_id),first-byte half split',
            'observations':rows,'metrics':metrics,'drift':drift,
            'scope':'Bounded serialized private experiment; metrics count unique request IDs. Replays are cached; server latency excludes lock wait/network. Marginal drift does not measure accuracy.'}
