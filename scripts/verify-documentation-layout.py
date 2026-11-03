"""Verify every moved document, navigation-only edit, module index and unchanged evidence/artwork tree."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
import argparse
import json
import re
import subprocess
from documentation_paths import ROOT, layout, historical_document, path_map
from documentation_presentation import presentation, before_presentation


def check():
    """Authenticate all original text and inventory current navigation without running platform workloads."""
    value=layout();mapping=path_map(value)
    display=presentation()
    for name in display['files']:
        before_presentation(name,(ROOT/name).read_bytes())
    for name,row in display['assets'].items():
        body=(ROOT/name).read_bytes()
        if row['mode']=='lf_text':body=body.replace(b'\r\n',b'\n')
        from hashlib import sha256
        assert sha256(body).hexdigest()==row['sha256'],name
    before=subprocess.check_output(['git','ls-tree','--name-only',value['base_commit']+':docs'],cwd=ROOT).decode().splitlines()
    assert set(mapping)=={'docs/'+name for name in before if name.endswith('.md') and name!='README.md'}
    for name in value['files'] | value['linked_files']:historical_document(name)
    for old,new in mapping.items():assert not (ROOT/old).exists() and (ROOT/new).is_file()
    counts={}
    for part in ['edai-1','edai-2']:
        folder=ROOT/'docs'/part;text=(folder/'README.md').read_text(encoding='utf-8')
        names={p.name for p in folder.glob('*.md')} - {'README.md'}
        links={target for target in re.findall(r'\]\(([^)]+)\)',text) if '/' not in target and target.endswith('.md')}
        assert names==links,(part,names-links,links-names)
        counts[part]=len(names)+1
    assert counts=={'edai-1':82,'edai-2':37}
    html_links=0
    for name in [*mapping.values(),'docs/edai-1/README.md','docs/edai-2/README.md']:
        page=ROOT/name
        for target in re.findall(r'(?:href|src|srcset)=["\']([^"\']+)["\']',page.read_text(encoding='utf-8')):
            parts=urlsplit(target)
            if parts.scheme or parts.netloc or not parts.path:continue
            destination=(page.parent/unquote(parts.path)).resolve()
            assert destination.is_relative_to(ROOT) and destination.is_file(),(name,target)
            html_links+=1
    preserved=[]
    for tree in ['docs/evidence','docs/evidence-archive','docs/images','docs/rubric','references','knowledge']:
        delta=subprocess.check_output(['git','diff',value['base_commit'],'--name-only','--',tree],cwd=ROOT).decode().splitlines()
        assert all(name in display['assets'] for name in delta),(tree,delta)
        preserved.append(tree)
    scenes=subprocess.check_output(['git','ls-files','docs/diagrams/*.excalidraw'],cwd=ROOT).decode().splitlines()
    original_scenes=0
    for name in scenes:
        if name in display['assets'] and display['assets'][name]['new']:continue
        original_scenes+=1
        original=subprocess.check_output(['git','show',value['base_commit']+':'+name],cwd=ROOT)
        assert original==(ROOT/name).read_bytes().replace(b'\r\n',b'\n'),name
    viewer=(ROOT/'docs/diagrams/architecture.html').read_text(encoding='utf-8')
    original=subprocess.check_output(['git','show',value['base_commit']+':docs/diagrams/architecture.html'],cwd=ROOT).decode()
    old_articles=re.findall(r'<article[^>]*>([\s\S]*?)</article>',original)
    new_articles=re.findall(r'<article[^>]*>([\s\S]*?)</article>',viewer)
    assert new_articles[:10]==old_articles and len(new_articles)==19
    assert 'const documentPaths=' in viewer and 'documentPaths[source]??source' in viewer
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    assert readme.split('## Contributors',1)[1]==subprocess.check_output(['git','show',value['base_commit']+':README.md'],cwd=ROOT).decode().split('## Contributors',1)[1]
    return {'passed':True,'moved_pages':len(mapping),'link_only_other_pages':len(value['linked_files']),
        'part_pages_including_readmes':counts,'html_image_and_source_links':html_links,'preserved_trees':preserved,'native_scenes_unchanged':original_scenes,
        'original_viewer_drawings_unchanged':True,'authored_readability_view_added':True,'presentation_pages':len(display['files']),
        'historical_text_and_navigation_checked':True,
        'scope':'Documentation paths and source-proof compatibility only; no runtime, measurement or native evidence mutation.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
    report=check()
    if args.output:
        target=args.output.resolve()
        if not target.is_relative_to(ROOT/'outputs') or target.exists():raise ValueError('Use a fresh receipt under outputs/')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
