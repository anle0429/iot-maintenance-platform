"""Publish a canonical native Feast dataset incrementally and optionally restore its complete snapshot."""
import argparse,json,sys
from datetime import date,datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.data_versions import ContentAddressedDatasetStore,relative_name,file_digest
from ml.training_contract import digest,save


def run(args):
    """Check canonical files before publication; parent lineage changes storage differences, not dataset identity."""
    report=json.loads(args.canonical_report.read_text())
    if not report['passed'] or report['rows']!=3_132_184 or len(report['files'])!=180 or report['code_sha256']!=digest(ROOT/'ml/canonical_dataset.py'):
        raise ValueError('Canonical source is incomplete or execution code changed')
    through=date.fromisoformat(args.through_day).isoformat() if args.through_day else None
    files={};rows=0;days=set()
    for item in report['files']:
        name=relative_name(item['path']);parts=name.split('/')
        if len(parts)!=3 or parts[0] not in ['cohort','labels','snapshot-keys'] or not parts[1].startswith('day=') or parts[2]!='data.parquet':
            raise ValueError('Unexpected canonical logical layout')
        event_day=date.fromisoformat(parts[1][4:]).isoformat()
        path=args.canonical_root/name
        if path.is_symlink() or file_digest(path)!=item['sha256'] or path.stat().st_size!=item['bytes']:raise ValueError('Canonical data bytes changed')
        if through and event_day>through:continue
        files[name]=path;days.add(event_day)
        if parts[0]=='cohort':rows+=item['rows']
    if not days or (through and max(days)!=through) or len(files)!=3*len(days):raise ValueError('Incomplete requested daily snapshot')
    store=ContentAddressedDatasetStore(args.store);result=store.publish(files,report['metadata'],args.parent_version)
    if args.restore_to:result['restore']=store.restore(result['version'],args.restore_to)
    save(args.output,{**result,'rows_in_version':rows,'days_in_version':len(days),'last_event_day':max(days),
        'canonical_report_sha256':digest(args.canonical_report),'source_native_report_sha256':report['native_report_sha256'],
        'code_sha256':digest(Path(__file__)),'completed_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Actual complete snapshot, with only unseen canonical Parquet blobs written. Native data retained separately; no100GB/versioned-model claim until registry linkage is verified.'})
    print(json.dumps({'passed':True,'version':result['version'],'rows':rows,'days':len(days),'new_blob_count':result['new_blob_count'],'new_blob_bytes':result['new_blob_bytes']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['canonical-root','canonical-report','store','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--parent-version');parser.add_argument('--through-day');parser.add_argument('--restore-to',type=Path)
    run(parser.parse_args())
