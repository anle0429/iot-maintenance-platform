"""Enforce known machines, both clocks, native feature timestamps and operating eligibility before KServe."""
from datetime import datetime,timezone
from pathlib import Path
from hashlib import sha256
import json,os,urllib.request
from services.predictor.contracts import FeatureVector
from services.predictor.binding import LEGACY,validate_pin

MODEL='iot-pump-failure-v1'
FEATURE_SERVICE='iot_failure_prediction_v1'


class Unavailable(ValueError):
    """The native feature/model response cannot safely support a prediction."""


class NotOperating(ValueError):
    """The prediction target is undefined for a machine that is currently stopped."""


def aware(value):
    """Reject timestamp ambiguity rather than assuming the server's local timezone."""
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise Unavailable('Timestamp lacks timezone')
    return result.astimezone(timezone.utc)


class FeatureRuntime:
    """Keep eligibility independent of the numeric model and future failure labels."""
    def __init__(self,context,backend,clock=None,model_pin=None):
        'Bind eligibility context, backend, clock and validated model lineage before serving requests.'
        self.context=context;self.backend=backend;self.clock=clock or (lambda:datetime.now(timezone.utc))
        if context['clock_mode']!='simulation' or not context['machines']:raise Unavailable('Explicit simulation context required')
        self.as_of=aware(context['as_of']);self.prepared=aware(context['prepared_wall_utc'])
        self.model_pin=validate_pin(LEGACY if model_pin is None else model_pin)
        if self.model_pin['protocol_sha256']!=context['protocol_sha256']:raise Unavailable('Feature context protocol differs from the pinned model')

    def features(self,machine):
        """Refuse expired demo lease, unknown context, stale/future features and arrival violations."""
        if not 0<=(self.clock()-self.prepared).total_seconds()<3600:raise Unavailable('Demo lease expired; prepare a fresh separately bound namespace')
        if self.context['protocol_sha256']!=self.model_pin['protocol_sha256']:raise Unavailable('Feature context protocol differs from the pinned model')
        if machine not in self.context['machines']:raise KeyError(machine)
        status=self.context['machines'][machine]
        event=aware(status['event_ts']);received=aware(status['received_ts'])
        if status['status'] not in ['operating','failed','maintenance'] or not 0<=(self.as_of-event).total_seconds()<=600 or received>self.as_of:raise Unavailable('Operating context is stale or unavailable')
        raw=self.backend.fetch(machine)
        fields=list(FeatureVector.model_fields)
        if set(raw['values'])!=set(fields) or set(raw['timestamps'])!=set(fields):raise Unavailable('Incomplete native feature schema')
        timestamps=[aware(raw['timestamps'][n]) for n in fields]
        if len(set(timestamps))!=1 or not 0<=(self.as_of-timestamps[0]).total_seconds()<=600:raise Unavailable('Features are stale, inconsistent or from the future')
        vector=FeatureVector.model_validate(raw['values']).model_dump()
        return {'machine_id':machine,'features':vector,'feature_event_ts':timestamps[0].isoformat(),
            'feature_age_seconds':(self.as_of-timestamps[0]).total_seconds(),'operating_context':status,
            'as_of':self.as_of.isoformat(),'clock_mode':'simulation','prepared_wall_utc':self.prepared.isoformat(),
            'protocol_sha256':self.context['protocol_sha256'],'source_manifest_sha256':self.context['source_manifest_sha256']}

    def predict(self,machine):
        """Call the deployed model only for an operating machine and preserve end-to-end provenance."""
        features=self.features(machine)
        if features['operating_context']['status']!='operating':raise NotOperating('Prediction is defined only while the machine is operating')
        result=self.backend.predict(features['features'])
        metadata=result['model'];predictions=result['predictions']
        if type(metadata.get('production')) is not bool or any(metadata.get(n)!=value for n,value in self.model_pin.items()):
            raise Unavailable('KServe model provenance differs')
        if len(predictions)!=1:raise Unavailable('Expected exactly one model prediction')
        score=predictions[0]['risk_score'];decision=predictions[0]['predicted_failure']
        if isinstance(score,bool) or not isinstance(score,(float,int)) or not 0<=score<=1 or type(decision) is not int or decision!=int(score>=self.model_pin['threshold']):raise Unavailable('Invalid model prediction')
        return {'prediction':predictions[0],'model':metadata,'feature_trace':features}


class FeastKServeBackend:
    """Use native Feast online reads and a fixed private KServe endpoint; no write/materialize HTTP tools."""
    def __init__(self,state,predictor):
        'Verify the sealed serving state and configure native Feast reads plus a fixed KServe endpoint.'
        from feast import FeatureStore
        from feast.repo_config import RepoConfig
        state=Path(state);seal=json.loads((state/'_READY.json').read_text());verification=json.loads((state/'verification.json').read_text())
        digest=lambda p:sha256(p.read_bytes()).hexdigest()
        if not verification['passed'] or digest(state/'verification.json')!=seal['verification_sha256'] or digest(state/'context.json')!=seal['context_sha256']:raise Unavailable('Serving state integrity differs')
        self.context=json.loads((state/'context.json').read_text())
        self.store=FeatureStore(config=RepoConfig(project='iot_serving_demo',provider='local',registry=str(state/'registry/registry.db'),
            offline_store={'type':'duckdb'},online_store={'type':'redis','connection_string':os.environ['IOT_FEAST_REDIS']+',socket_timeout=2,socket_connect_timeout=2','key_ttl_seconds':3600}))
        self.service=self.store.get_feature_service(FEATURE_SERVICE);self.endpoint=predictor.rstrip('/')

    def fetch(self,machine):
        """Keep the event timestamp of every field; missing Redis values fail validation upstream."""
        from google.protobuf.json_format import MessageToDict
        response=self.store.get_online_features(features=self.service,entity_rows=[{'machine_id':machine}])
        raw=response.to_dict(include_event_timestamps=True)
        native=MessageToDict(response.proto,preserving_proto_field_name=True)
        vectors=dict(zip(native['metadata']['feature_names']['val'],native['results'],strict=True))
        fields=list(FeatureVector.model_fields)
        if raw['machine_id']!=[machine] or any(vectors[n]['statuses']!=['PRESENT'] for n in fields):raise Unavailable('Native feature status is not present')
        return {'values':{n:raw[n][0] for n in fields},'timestamps':{n:datetime.fromtimestamp(raw[n+'__ts'][0],timezone.utc).isoformat() for n in fields}}

    def predict(self,features):
        """Bound the downstream wait and avoid caller-controlled URLs, model names or headers."""
        request=urllib.request.Request(self.endpoint+'/v1/models/'+MODEL+':predict',data=json.dumps({'instances':[features]}).encode(),headers={'content-type':'application/json'})
        with urllib.request.urlopen(request,timeout=3) as response:return json.load(response)
