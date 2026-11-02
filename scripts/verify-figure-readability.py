"""Verify the readable overview, exact original images, page presentation changes and benchmark panel values."""
from pathlib import Path
from hashlib import sha256
import argparse,json,re,statistics,struct,subprocess
from documentation_presentation import ROOT,presentation,before_presentation


def check():
    """Inspect recorded data and authored exports without modifying or starting a platform workload."""
    value=presentation();base=value['base_commit']
    for name in value['files']:before_presentation(name,(ROOT/name).read_bytes())
    for name,row in value['assets'].items():
        body=(ROOT/name).read_bytes()
        if row['mode']=='lf_text':body=body.replace(b'\r\n',b'\n')
        assert sha256(body).hexdigest()==row['sha256'],name
    old_index=json.loads(subprocess.check_output(['git','show',base+':docs/images/diagram-index.json'],cwd=ROOT))
    index=json.loads((ROOT/'docs/images/diagram-index.json').read_bytes())
    assert index['views'][:10]==old_index['views'] and len(index['views'])==18
    original_images=subprocess.check_output(['git','ls-tree','-r','--name-only',base,'--','docs/images'],cwd=ROOT).decode().splitlines()
    for name in original_images:
        if name=='docs/images/diagram-index.json':continue
        old=subprocess.check_output(['git','show',base+':'+name],cwd=ROOT)
        current=(ROOT/name).read_bytes()
        if Path(name).suffix not in ['.png','.gif']:current=current.replace(b'\r\n',b'\n')
        assert old==current,name
    scene=json.loads((ROOT/'docs/diagrams/platform-reading-map.excalidraw').read_bytes())
    fonts=[e['fontSize'] for e in scene['elements'] if e['type']=='text']
    assert min(fonts)==20 and min(fonts)*896/1120==16
    assert len([e for e in scene['elements'] if e.get('customData',{}).get('node')])==12
    assert len([e for e in scene['elements'] if e['type']=='arrow'])==10
    panels=json.loads((ROOT/'docs/images/benchmark-detail-sources.json').read_bytes())
    assert panels['runtime_screenshot'] is False and panels['repetitions']==3
    for name,pin in panels['source_sha256'].items():assert sha256((ROOT/name).read_bytes()).hexdigest()==pin
    corrections=json.loads((ROOT/'docs/evidence/batch-benchmarks/final/corrections.json').read_bytes())
    skew=json.loads((ROOT/'docs/evidence/batch-benchmarks/final/skew.json').read_bytes())
    methods={'schema':['inferred','declared'],'dedup':['window','aggregate'],'skew':['baseline','broadcast']}
    assert len(panels['panels'])==3
    for panel in panels['panels']:
        experiment=panel['experiment'];report=skew if experiment=='skew' else corrections
        rows=[r for r in report['measurements'] if r.get('experiment','skew')==experiment]
        expected=[[next(r['seconds'] for r in rows if r['strategy']==method and r['repeat']==i) for i in range(3)] for method in methods[experiment]]
        assert panel['paired_seconds']==expected and panel['medians_seconds']==list(map(statistics.median,expected))
        body=(ROOT/'docs/images'/panel['path']).read_bytes()
        assert body[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',body[16:24])==(1080,684)
    for name in ['docs/edai-1/batch_corrections_benchmark.md','docs/edai-1/skew_benchmark.md']:
        current=(ROOT/name).read_text(encoding='utf-8')
        old=subprocess.check_output(['git','show',base+':'+name],cwd=ROOT).decode()
        urls=lambda s:[u for u in re.findall(r'!\[[^]]*\]\(([^)]+)\)',s) if '/evidence/' in u]
        assert urls(current)==urls(old),name
        assert not any(line.startswith('|') and line.count('![')>1 for line in current.splitlines())
    for tree in ['docs/evidence','docs/evidence-archive','references','knowledge']:
        assert not subprocess.check_output(['git','diff',base,'--name-only','--',tree],cwd=ROOT).strip(),tree
    return {'passed':True,'presentation_pages':8,'primary_architecture_views':7,'original_image_files_unchanged':len(original_images)-1,
        'overview_nodes':12,'overview_arrows':10,'minimum_text_at_896px':16,'separate_benchmark_panels':3,
        'all_measured_pairs_preserved':True,'native_screenshots_unchanged':True,'paired_screenshot_tables_remaining':0,
        'scope':'Authored figure readability and exact recorded values; not new runtime evidence or a platform/grade claim.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args();report=check()
    if args.output:
        target=args.output.resolve()
        if not target.is_relative_to(ROOT/'outputs') or target.exists():raise ValueError('Use a fresh receipt under outputs/')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
