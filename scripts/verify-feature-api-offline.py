"""Compare every real HTTP feature vector and operating prediction to the immutable offline source and sealed portable model."""
from pathlib import Path
from datetime import datetime
from hashlib import sha256
import duckdb,json,math
root=Path('/workspace');state=root/'data/feature-serving-cluster-27/state';out=root/'outputs/feature-api-deploy-27'
http=json.loads((out/'http-all-machines.json').read_text());context=json.loads((state/'context.json').read_text());model=json.loads((root/'models/pump-failure-v1/portable.json').read_text())
con=duckdb.connect();con.execute("SET threads=1");con.execute("SET memory_limit='2GB'")
fields=model['features'];cut=datetime.fromisoformat(context['as_of']).replace(tzinfo=None)
expected=con.execute('SELECT machine_id,'+','.join(fields)+' FROM read_parquet(?) WHERE event_timestamp<=? QUALIFY row_number() OVER(PARTITION BY machine_id ORDER BY event_timestamp DESC)=1',
 [str(root/'data/feast/batch-medium-27/machine_rolling.parquet'),cut]).fetchdf().set_index('machine_id').to_dict(orient='index')
assert len(expected)==len(http)==200
differences=[]
for row in http:
 actual=row['features'];values=actual['features'];reference=expected[row['machine_id']]
 assert set(values)==set(fields) and all(math.isclose(values[n],reference[n],rel_tol=1e-12,abs_tol=1e-12) for n in fields)
 assert actual['as_of']==context['as_of'] and actual['source_manifest_sha256']==context['source_manifest_sha256']
 if row['prediction_http_status']==200:
  z=model['intercept']+sum(((reference[n]-m)/s if s else 0)*c for n,m,s,c in zip(fields,model['mean'],model['std'],model['coefficients'],strict=True))
  expected_score=1/(1+math.exp(-z)) if z>=0 else math.exp(z)/(1+math.exp(z))
  prediction=row['response']['prediction'];delta=abs(prediction['risk_score']-expected_score);assert delta<=1e-12 and prediction['predicted_failure']==int(expected_score>=model['threshold'])
  differences.append(delta)
 else:assert row['prediction_http_status']==409 and actual['operating_context']['status']!='operating'
result={'passed':True,'machines':200,'operating_predictions':len(differences),'stopped_rejected':200-len(differences),'max_abs_score_difference':max(differences),
 'source_compact_sha256':sha256((root/'data/feast/batch-medium-27/machine_rolling.parquet').read_bytes()).hexdigest(),'portable_sha256':sha256((root/'models/pump-failure-v1/portable.json').read_bytes()).hexdigest(),
 'http_sha256':sha256((out/'http-all-machines.json').read_bytes()).hexdigest(),'scope':'Independent offline DuckDB latest-row lookup for all200HTTP features plus portable model arithmetic for195operating predictions. Inference correctness only; no new model quality evaluation.'}
(out/'offline-comparison.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
