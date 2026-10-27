"""Push finalized Flink windows through native Feast, reconciling nontransactional online/offline retries."""

from datetime import timedelta
import fcntl
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from feast import Entity, FeatureService, FeatureView, Field, FileSource, PushSource
from feast.data_format import ParquetFormat
from feast.data_source import PushMode
from feast.types import Float64, Int64
from feast.value_type import ValueType

from feature_store.common import create_store, digest, save, utc_microseconds, utc_now
from feature_store.outbox import FIELDS, WindowOutbox


PUSH = 'finalized_window_push'
VIEW = 'machine_tumbling_final_10m'
SERVICE = 'machine_window_monitoring'
COLUMNS = ['machine_id','event_timestamp','created',*FIELDS]


def feature_frame(payloads):
    """Retain exact column order and UTC microseconds required by the native offline writer."""
    frame = pd.DataFrame(payloads,columns=COLUMNS)
    frame = utc_microseconds(frame,['event_timestamp','created'])
    frame['reading_count'] = frame['reading_count'].astype('int64')
    for field in FIELDS[1:]:
        frame[field] = frame[field].astype('float64')
    return frame


class WindowPusher:
    """Use one local writer; completed window retries are no-ops and pending writes are read back before retry."""

    def __init__(self,state: Path,source_id: str):
        """Bind a stable stream/scenario identity to a separate monitoring project and journal."""
        self.state,self.source_id=state,source_id
        self.project='iot_windows_'+sha256(source_id.encode()).hexdigest()[:12]

    def __enter__(self):
        """Create only a fresh empty offline table, or verify the existing source/code binding."""
        self.state.mkdir(parents=True,exist_ok=True)
        self.lock=(self.state/'writer.lock').open('a')
        fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.offline=self.state/'window_features.parquet'
        self.binding={'source_id':self.source_id,'project':self.project,'view':VIEW,'columns':COLUMNS,
                      'code_sha256':{'feature_store/'+n:digest(Path(__file__).with_name(n)) for n in ('common.py','outbox.py','push.py')}}
        binding_path=self.state/'binding.json'
        if binding_path.exists():
            if (json.loads(binding_path.read_text())!=self.binding or not self.offline.is_file()
                    or not (self.state/'outbox.sqlite').is_file()):
                raise ValueError('Existing window push state has different source/code or missing storage')
        elif self.offline.exists() or (self.state/'outbox.sqlite').exists():
            raise ValueError('Unsealed window push initialization requires inspection')
        else:
            schema=pa.schema([('machine_id',pa.string()),('event_timestamp',pa.timestamp('us',tz='UTC')),
                              ('created',pa.timestamp('us',tz='UTC')),('reading_count',pa.int64()),
                              *[(n,pa.float64()) for n in FIELDS[1:]]])
            pq.write_table(pa.Table.from_batches([],schema=schema),self.offline,compression='snappy')
        self.outbox=WindowOutbox(self.state/'outbox.sqlite')
        self.store,self.backend=create_store(self.project,self.state/'registry')
        source=PushSource(name=PUSH,batch_source=FileSource(name='window_history',path=str(self.offline),file_format=ParquetFormat(),
                          timestamp_field='event_timestamp',created_timestamp_column='created'))
        entity=Entity(name='machine',join_keys=['machine_id'],value_type=ValueType.STRING)
        view=FeatureView(name=VIEW,entities=[entity],ttl=timedelta(hours=1),source=source,online=True,
                        schema=[Field(name=n,dtype=Int64 if n=='reading_count' else Float64) for n in FIELDS],
                        description='Final half-open10-minute Flink windows for monitoring; different from the model rolling10-minute/5-minute-grid view.')
        self.service=FeatureService(name=SERVICE,features=[view])
        self.store.apply([entity,view,self.service])
        save(binding_path,self.binding)
        return self

    def offline_frame(self):
        """Read actual native-written Parquet; malformed partial files fail rather than being regenerated."""
        return utc_microseconds(pd.read_parquet(self.offline),['event_timestamp','created'])

    def online_values(self,machines):
        """Read every supplied machine through the registered native Feast service."""
        return self.store.get_online_features(features=self.service,entity_rows=[{'machine_id':m} for m in sorted(machines)]).to_df()

    def check_latest_online(self,offline):
        """Older windows may enter history, while their online writes must preserve the latest vector."""
        if offline.empty:
            return
        expected=offline.sort_values(['machine_id','event_timestamp']).drop_duplicates('machine_id',keep='last').sort_values('machine_id').reset_index(drop=True)
        online=self.online_values(expected.machine_id.tolist()).sort_values('machine_id').reset_index(drop=True)
        if (online.machine_id.tolist()!=expected.machine_id.tolist()
                or not np.array_equal(online[FIELDS].to_numpy(dtype='float64'),expected[FIELDS].to_numpy(dtype='float64'))):
            raise ValueError('Latest native online window differs from persisted offline history')

    def push(self,row: dict,fault: str=''):
        """Retry by observed store state; never blindly append an already persisted window."""
        if fault not in ('','after_online','after_both_before_ack'):
            raise ValueError('Unsupported controlled store-boundary fault')
        intent=self.outbox.observe(row)
        if intent['completed_utc'] is not None:
            return 'completed_replay_noop'
        payload=intent['payload'];frame=feature_frame([payload]);offline=self.offline_frame()
        old=offline[(offline.machine_id==payload['machine_id']) & (offline.event_timestamp==frame.iloc[0].event_timestamp)]
        if len(old)>1:
            raise ValueError('Offline feature key already has duplicates; refuse automatic repair')
        if len(old):
            expected=frame.reset_index(drop=True);actual=old[COLUMNS].reset_index(drop=True)
            if not actual.equals(expected):
                raise ValueError('Pending intent conflicts with the persisted offline window')
            self.store.push(PUSH,frame,to=PushMode.ONLINE)
            mode='reconciled_online_only'
        else:
            if fault=='after_online':
                self.store.push(PUSH,frame,to=PushMode.ONLINE)
                raise RuntimeError('Injected after online write, before offline append')
            self.store.push(PUSH,frame,to=PushMode.ONLINE_AND_OFFLINE)
            if fault=='after_both_before_ack':
                raise RuntimeError('Injected after both stores, before durable acknowledgement')
            mode='native_online_and_offline'
        after=self.offline_frame()
        actual=after[(after.machine_id==payload['machine_id']) & (after.event_timestamp==frame.iloc[0].event_timestamp)]
        if len(actual)!=1 or not actual[COLUMNS].reset_index(drop=True).equals(frame.reset_index(drop=True)):
            raise ValueError('Native offline write readback differs from the unique intent')
        self.check_latest_online(after)
        self.outbox.acknowledge(intent['key'])
        return mode

    def verify(self):
        """Compare every completed intent and every native historical/online value; reject unacknowledged gaps."""
        records=self.outbox.records()
        if not records or any(r['completed_utc'] is None for r in records):
            raise ValueError('Every window intent must be acknowledged before declaring both stores consistent')
        expected=feature_frame([r['payload'] for r in records]).sort_values(['machine_id','event_timestamp']).reset_index(drop=True)
        actual=self.offline_frame().sort_values(['machine_id','event_timestamp']).reset_index(drop=True)
        if not actual[COLUMNS].equals(expected) or actual.duplicated(['machine_id','event_timestamp']).any():
            raise ValueError('Native offline history changed count, identity or any feature/availability value')
        self.check_latest_online(actual)
        historical=self.store.get_historical_features(entity_df=expected[['machine_id','event_timestamp']].copy(),
                     features=self.service,filter_by_created_timestamp=False).to_df()
        historical=utc_microseconds(historical,['event_timestamp']).sort_values(['machine_id','event_timestamp']).reset_index(drop=True)
        if (historical.machine_id.tolist()!=expected.machine_id.tolist() or not historical.event_timestamp.equals(expected.event_timestamp)
                or not np.array_equal(historical[FIELDS].to_numpy(dtype='float64'),expected[FIELDS].to_numpy(dtype='float64'))):
            raise ValueError('Native historical monitoring values differ from every completed window')
        return {'passed':True,'offline_rows':len(actual),'machines':int(actual.machine_id.nunique()),
                'historical_rows':len(historical),'offline_sha256':digest(self.offline),'binding':self.binding,
                'verified_utc':utc_now(),'scope':'Native finalized-window monitoring history/online values and outbox recovery; not model rolling features, distributed exactly-once or crash-safe Parquet rewrite.'}

    def __exit__(self,*_):
        """Leave external Redis/Parquet and committed intents intact for a checked restart."""
        self.backend.disconnect()
        self.outbox.close()
        self.lock.close()
