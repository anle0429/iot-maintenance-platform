"""Build bounded evidence-grounded LLM questions from real generator and stream-store results, keeping their source versions distinct."""
from datetime import datetime
import json
import math
from feature_store.training_samples import FEATURES


def cases(generator,offline,online):
    """Derive factual answer contracts from actual completed native jobs rather than provide canned model answers."""
    if generator.get('passed') is not True or not generator['same_physical_truth_and_labels']:
        raise ValueError('Require the completed paired generator study')
    if not offline.get('passed') or not online.get('passed') or online['offline_release']!=offline['release']:
        raise ValueError('Require a successful linked offline/online CI pair')
    if offline['commit']!=online['commit']:raise ValueError('Stream-store releases must use the same tested source')
    base=generator['versions']['baseline'];drift=generator['versions']['drift']
    for version in [base,drift]:
        if (version.get('passed') is not True or version['feature_column_names']!=list(FEATURES)
                or version['target_columns']!=['sample_id','failure_in_next_1h']
                or not version['feature_rows']==version['joined_rows']==version['parquet_readback_rows']
                or sum(version['target_counts'].values())!=version['feature_rows']):
            raise ValueError('A source export or target join failed its native contract')
    if base['dataset_id']==drift['dataset_id'] or base['target_counts']!=drift['target_counts']:
        raise ValueError('Paired versions must be distinct and preserve target truth')
    history=offline['reports']['replay']['rows'];latest={}
    for row in history:
        stamp=datetime.fromisoformat(row['event_timestamp'].replace('Z','+00:00'))
        if row['machine_id'] not in latest or stamp>latest[row['machine_id']][0]:latest[row['machine_id']]=(stamp,row)
    native=online['reports']['replay']['online_rows']
    if len(native)!=len(latest) or {r['machine_id'] for r in native}!=set(latest):raise ValueError('Actual online machine inventory differs')
    for row in native:
        if row['temperature_mean']!=latest[row['machine_id']][1]['temperature_mean']:raise ValueError('Actual latest values differ')
    unchanged=(offline['reports']['write']['offline_sha256']==offline['reports']['replay']['offline_sha256']
               ==online['reports']['write']['offline_sha256_unchanged']==online['reports']['replay']['offline_sha256_unchanged'])
    if not unchanged:raise ValueError('Replay or online processing changed the offline source')
    common={'generator_commit':generator['source_commit'],'stream_commit':offline['commit'],
            'scope':'Separate small synthetic workloads; retained finalized windows are not a new live stream. These are evaluation inputs, not a new deployed MCP tool.'}
    return [
        {'id':'generator_versions','context':{**common,'paired_study':generator},
         'question':'Summarize the baseline training export. Return exactly feature_rows (integer), positive_labels (integer), labels_are_separate (boolean), physical_truth_changed (boolean). The target schema is the explicit two-column table.',
         'expected':{'feature_rows':base['feature_rows'],'positive_labels':int(base['target_counts']['1']),
                     'labels_are_separate':base['target_columns']==['sample_id','failure_in_next_1h'],'physical_truth_changed':False}},
        {'id':'stream_history_latest','context':{**common,'offline_release':offline['release'],'online_release':online['release'],
            'history':history,'online_rows':native,'offline_hash_unchanged':unchanged,'required_predictor_features':list(FEATURES),
            'window_semantics':'Monitoring uses finalized tumbling windows. The predictor uses rolling features with event/arrival availability and additional input fields.'},
         'question':'Return exactly offline_rows (integer), online_machines (integer), p001_latest_temperature (number), replay_changed_offline (boolean), monitoring_is_complete_predictor_input (boolean). Infer the last field by comparing actual monitoring columns with required predictor features.',
         'expected':{'offline_rows':len(history),'online_machines':len(native),'p001_latest_temperature':latest['P-001'][1]['temperature_mean'],
                     'replay_changed_offline':False,'monitoring_is_complete_predictor_input':False}},
        {'id':'label_eligibility','context':{**common,'source_grains':base['source_grains'],'eligible_rows':base['feature_rows'],
            'excluded_rows':base['excluded'],'feature_columns':base['feature_column_names'],'target_columns':base['target_columns'],
            'rule':'Future targets belong to training/evaluation. Stopped or unobserved horizons are excluded, not converted into a negative class.'},
         'question':'Return exactly censored_rows (integer), stopped_rows (integer), insufficient_readings_rows (integer), future_target_in_inference_features (boolean). Use the actual excluded counts and feature schema.',
         'expected':{'censored_rows':base['excluded']['censored'],'stopped_rows':base['excluded']['stopped'],
                     'insufficient_readings_rows':base['excluded']['insufficient_readings'],'future_target_in_inference_features':False}},
    ]


def request_body(case):
    """Ask the existing observed 7B model to interpret evidence with fixed generation controls and no tool declaration."""
    return {'model':'iot-qwen2.5-7b','messages':[
        {'role':'system','content':'Interpret only the supplied coursework evidence. Keep distinct dataset versions distinct. Return one JSON object with exactly the requested keys and native JSON types. No markdown or additional prose.'},
        {'role':'user','content':json.dumps(case['context'],sort_keys=True)+'\nQuestion: '+case['question']}],
        'temperature':0,'seed':7,'max_tokens':256,'cache_prompt':False,'stream':True,'stream_options':{'include_usage':True}}


def unique_object(pairs):
    """Reject contradictory duplicate keys rather than silently choosing the last answer field."""
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Duplicate answer key')
        value[key]=item
    return value


def score_answer(text,expected):
    """Keep wrong answers in the cohort and compare exact schemas/types, allowing only harmless numeric representation differences."""
    try:actual=json.loads(text,object_pairs_hook=unique_object)
    except (ValueError,TypeError):return {'correct':False,'reason':'invalid_json','actual':None}
    if not isinstance(actual,dict) or set(actual)!=set(expected):return {'correct':False,'reason':'schema','actual':actual}
    for key,wanted in expected.items():
        got=actual[key]
        if type(wanted) in {int,float}:
            try:valid=type(got) in {int,float} and math.isfinite(got) and abs(got-wanted)<1e-6
            except OverflowError:valid=False
        else:valid=type(got) is type(wanted) and got==wanted
        if not valid:return {'correct':False,'reason':'field:'+key,'actual':actual}
    return {'correct':True,'reason':'matched','actual':actual}
