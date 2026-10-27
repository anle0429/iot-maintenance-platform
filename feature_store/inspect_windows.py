"""Read actual offline and native online monitoring vectors without rewriting the completed push data."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from feature_store.common import create_store,digest,save,utc_microseconds,utc_now
from feature_store.outbox import FIELDS
from feature_store.push import SERVICE


def run(args):
    """Bind the displayed values to the completed worker report and check every machine against latest history."""
    if args.output.exists():
        raise ValueError('Keep prior inspections and choose a fresh report file')
    report=json.loads(args.worker_report.read_text());binding=json.loads((args.state/'binding.json').read_text())
    source=args.state/'window_features.parquet'
    if not report['passed'] or binding!=report['binding'] or digest(source)!=report['offline_sha256']:
        raise ValueError('Completed worker/source binding differs')
    for name,h in binding['code_sha256'].items():
        if digest(Path(__file__).parents[1]/name)!=h:
            raise ValueError('Core writer code changed since the completed result')
    frame=utc_microseconds(pd.read_parquet(source),['event_timestamp','created']).sort_values(['machine_id','event_timestamp'])
    expected=frame.drop_duplicates('machine_id',keep='last').sort_values('machine_id').reset_index(drop=True)
    store,backend=create_store(binding['project'],args.state/'registry')
    try:
        actual=store.get_online_features(features=store.get_feature_service(SERVICE),
                   entity_rows=[{'machine_id':m} for m in expected.machine_id]).to_df().sort_values('machine_id').reset_index(drop=True)
        if (actual.machine_id.tolist()!=expected.machine_id.tolist()
                or not np.array_equal(actual[FIELDS].to_numpy(dtype='float64'),expected[FIELDS].to_numpy(dtype='float64'))):
            raise ValueError('Online values are missing, expired or differ from the completed offline history')
        if digest(source)!=report['offline_sha256']:
            raise ValueError('Reading the store changed immutable offline bytes')
        save(args.output,{'passed':True,'worker_report_sha256':digest(args.worker_report),'offline_sha256':digest(source),
             'offline_rows':len(frame),'offline_values':frame.to_dict(orient='records'),'online_values':actual.to_dict(orient='records'),
             'source_file_unchanged':True,'code_sha256':digest(Path(__file__)),'inspected_utc':utc_now(),
             'scope':'Actual native online feature values and persisted offline rows, not a screenshot or model prediction. Redis expiry is an error here, not silently filled data.'})
        print({'passed':True,'offline_rows':len(frame),'online_entities':len(actual)},flush=True)
    finally:
        backend.disconnect()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('state','worker-report','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    run(parser.parse_args())
