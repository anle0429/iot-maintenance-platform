"""Write stream monitoring history and latest online vectors in separately acknowledged native Feast jobs."""
from pathlib import Path
import fcntl,json,shutil
import numpy as np
import pandas as pd
from feast.data_source import PushMode
from feature_store.common import create_store,digest,save,utc_microseconds,utc_now
from feature_store.outbox import FIELDS,WindowOutbox,window_payload
from feature_store.push import WindowPusher,PUSH,SERVICE,COLUMNS,feature_frame


class OfflineStage(WindowPusher):
    """Use the established native schema while leaving current online values unchanged."""
    def __enter__(self):
        """Refuse a legacy both-store state or changed stage implementation."""
        self.state.mkdir(parents=True,exist_ok=True);marker=self.state/'stage-binding.json'
        binding={'destination':'offline','source_id':self.source_id,'code_sha256':digest(Path(__file__))}
        if marker.exists():
            if json.loads(marker.read_bytes())!=binding:raise ValueError('Different offline stage binding')
        elif (self.state/'binding.json').exists():raise ValueError('Do not adopt a legacy both-store state')
        super().__enter__();save(marker,binding);return self

    def push(self,row,fault=''):
        """Append only absent offline keys; read back a pending write before acknowledging it."""
        if fault not in ['', 'after_offline']:raise ValueError('Unknown offline fault')
        intent=self.outbox.observe(row)
        if intent['completed_utc'] is not None:return 'completed_offline_replay_noop'
        frame=feature_frame([intent['payload']]);history=self.offline_frame()
        actual=history[(history.machine_id==frame.iloc[0].machine_id)&(history.event_timestamp==frame.iloc[0].event_timestamp)]
        if len(actual)>1:raise ValueError('Duplicate offline key')
        online_before=self.online_values([intent['payload']['machine_id']])[FIELDS]
        mode='reconciled_offline_only'
        if len(actual)==0:
            self.store.push(PUSH,frame,to=PushMode.OFFLINE);mode='native_offline_only'
            if fault:raise RuntimeError('Injected after offline write before acknowledgement')
        after=self.offline_frame();actual=after[(after.machine_id==frame.iloc[0].machine_id)&(after.event_timestamp==frame.iloc[0].event_timestamp)]
        if len(actual)!=1 or not actual[COLUMNS].reset_index(drop=True).equals(frame.reset_index(drop=True)):raise ValueError('Offline readback differs')
        if not self.online_values([intent['payload']['machine_id']])[FIELDS].equals(online_before):raise ValueError('Offline job changed online features')
        self.outbox.acknowledge(intent['key']);return mode

    def verify(self):
        """Require exact native history and historical retrieval; report online state separately."""
        intents=self.outbox.records()
        if not intents or any(v['completed_utc'] is None for v in intents):raise ValueError('Pending offline window')
        expected=feature_frame([v['payload'] for v in intents]).sort_values(['machine_id','event_timestamp']).reset_index(drop=True)
        history=self.offline_frame().sort_values(['machine_id','event_timestamp']).reset_index(drop=True)
        if not history[COLUMNS].equals(expected) or history.duplicated(['machine_id','event_timestamp']).any():raise ValueError('Offline history differs from acknowledged intents')
        native=self.store.get_historical_features(entity_df=expected[['machine_id','event_timestamp']].copy(),features=self.service,filter_by_created_timestamp=False).to_df()
        native=utc_microseconds(native,['event_timestamp']).sort_values(['machine_id','event_timestamp']).reset_index(drop=True)
        if not np.array_equal(native[FIELDS].to_numpy(dtype='float64'),expected[FIELDS].to_numpy(dtype='float64')):raise ValueError('Native historical vectors differ')
        online=self.online_values(history.machine_id.unique().tolist())
        return {'passed':True,'destination':'offline','source_id':self.source_id,'project':self.project,'offline_rows':len(history),'historical_rows':len(native),
            'machines':int(history.machine_id.nunique()),'online_all_missing':bool(online[FIELDS].isna().all().all()),'offline_sha256':digest(self.offline),'registry_sha256':digest(self.state/'registry/registry.db'),
            'rows':json.loads(history.to_json(orient='records',date_format='iso',date_unit='us')),'verified_utc':utc_now()}


class OnlineStage:
    """Consume a sealed completed offline result; write only latest native online values with a separate journal."""
    def __init__(self,state,offline_state,offline_receipt):
        """Keep the online job writable only in its own checkpoint and Redis project."""
        self.state,self.offline_state,self.receipt=state,offline_state,offline_receipt

    def __enter__(self):
        """Copy the immutable native registry and reject modified upstream data or implementation."""
        receipt=self.receipt;source=self.offline_state;self.offline=source/'window_features.parquet'
        if (receipt.get('passed') is not True or receipt.get('destination')!='offline' or digest(self.offline)!=receipt['offline_sha256']
                or digest(source/'registry/registry.db')!=receipt['registry_sha256']):raise ValueError('A complete unchanged offline release is required')
        stage=json.loads((source/'stage-binding.json').read_bytes());binding=json.loads((source/'binding.json').read_bytes())
        if stage['code_sha256']!=digest(Path(__file__)) or stage['destination']!='offline' or binding['project']!=receipt['project'] or binding['source_id']!=receipt['source_id']:raise ValueError('Offline source/stage binding differs')
        for name,h in binding['code_sha256'].items():
            if digest(Path(__file__).resolve().parents[1]/name)!=h:raise ValueError('Shared stream writer source changed')
        self.state.mkdir(parents=True,exist_ok=True);self.lock=(self.state/'writer.lock').open('a');fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        marker={'destination':'online','project':receipt['project'],'offline_sha256':receipt['offline_sha256'],'registry_sha256':receipt['registry_sha256'],'code_sha256':digest(Path(__file__))}
        path=self.state/'stage-binding.json'
        if path.exists() and json.loads(path.read_bytes())!=marker:raise ValueError('Different online source binding')
        registry=self.state/'registry';registry.mkdir(exist_ok=True);copied=registry/'registry.db'
        if not copied.exists():shutil.copyfile(source/'registry/registry.db',copied)
        if digest(copied)!=receipt['registry_sha256']:raise ValueError('Native registry copy changed')
        self.store,self.backend=create_store(receipt['project'],registry);self.service=self.store.get_feature_service(SERVICE)
        self.outbox=WindowOutbox(self.state/'outbox.sqlite');self.history=utc_microseconds(pd.read_parquet(self.offline),['event_timestamp','created'])
        if len(self.history)!=receipt['offline_rows'] or self.history.duplicated(['machine_id','event_timestamp']).any():raise ValueError('Offline source count differs')
        self.latest=self.history.sort_values(['machine_id','event_timestamp']).drop_duplicates('machine_id',keep='last').sort_values('machine_id').reset_index(drop=True)
        save(path,marker);return self

    def push(self,row,fault=''):
        """Use a verified source row and the newest finalized vector; older arrivals cannot regress online state."""
        if fault not in ['', 'after_online']:raise ValueError('Unknown online fault')
        _,_,payload=window_payload(row);frame=feature_frame([payload]);same=self.history[(self.history.machine_id==payload['machine_id'])&(self.history.event_timestamp==frame.iloc[0].event_timestamp)]
        if len(same)!=1 or not np.array_equal(same[FIELDS].to_numpy(dtype='float64'),frame[FIELDS].to_numpy(dtype='float64')):raise ValueError('Window is not in the verified offline source')
        # Preserve the offline writer's first availability timestamp when a transport replay changes its audit clock.
        intent=self.outbox.observe({**row,'created':same.iloc[0].created.isoformat()})
        if intent['completed_utc'] is not None:return 'completed_online_replay_noop'
        latest=self.latest[self.latest.machine_id==payload['machine_id']][COLUMNS].reset_index(drop=True)
        self.store.push(PUSH,latest,to=PushMode.ONLINE)
        if fault:raise RuntimeError('Injected after online write before acknowledgement')
        actual=self.store.get_online_features(features=self.service,entity_rows=[{'machine_id':payload['machine_id']}]).to_df()
        if not np.array_equal(actual[FIELDS].to_numpy(dtype='float64'),latest[FIELDS].to_numpy(dtype='float64')):raise ValueError('Latest online readback differs')
        if digest(self.offline)!=self.receipt['offline_sha256']:raise ValueError('Online job changed offline history')
        self.outbox.acknowledge(intent['key']);return 'native_online_only'

    def verify(self):
        """Require all source windows acknowledged and every latest vector read through native Feast."""
        records=self.outbox.records()
        if len(records)!=self.receipt['offline_rows'] or any(v['completed_utc'] is None for v in records):raise ValueError('Incomplete online delivery')
        actual=self.store.get_online_features(features=self.service,entity_rows=[{'machine_id':m} for m in self.latest.machine_id]).to_df().sort_values('machine_id').reset_index(drop=True)
        if not np.array_equal(actual[FIELDS].to_numpy(dtype='float64'),self.latest[FIELDS].to_numpy(dtype='float64')):raise ValueError('Latest online cohort differs')
        if digest(self.offline)!=self.receipt['offline_sha256']:raise ValueError('Online push changed offline Parquet')
        return {'passed':True,'destination':'online','project':self.receipt['project'],'acknowledged_windows':len(records),'machines':len(actual),
            'offline_sha256_unchanged':digest(self.offline),'online_rows':json.loads(actual.to_json(orient='records')),
            'expected_latest_rows':json.loads(self.latest.to_json(orient='records',date_format='iso',date_unit='us')),'verified_utc':utc_now()}

    def __exit__(self,*_):
        """Preserve the independent journal and shared upstream artifact for checked retries."""
        self.backend.disconnect();self.outbox.close();self.lock.close()
