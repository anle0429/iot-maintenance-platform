"""Plot every matched native actor-startup pair without removing unfavorable snapshot results."""
from pathlib import Path
from hashlib import sha256
import json
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]


def render():
    """Keep individual paired observations and medians visible, clearly separate from native screenshots."""
    evidence = ROOT / 'docs/evidence/agent-warmup'
    rows = json.loads((evidence / 'measurements.json').read_bytes())
    report = json.loads((evidence / 'verification.json').read_bytes())
    assert report['passed'] and len(rows) == 24
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'svg.hashsalt':'iot-agent-warmup-29b'})
    figure, axes = plt.subplots(1,3,figsize=(12.8,5.6),sharey=True)
    for axis, role in zip(axes,['retrieval','drift','coordinator']):
        selected = [row for row in rows if row['role'] == role]
        for pair in range(4):
            values = {row['mode']:row['resume_to_http_card_seconds'] for row in selected if row['pair'] == pair}
            axis.plot([0,1],[values['cold'],values['warm']],color='#bdc5cf',linewidth=1.4,zorder=1)
        for index, mode, color in [(0,'cold','#315c9b'),(1,'warm','#0b7e85')]:
            values = [row['resume_to_http_card_seconds'] for row in selected if row['mode'] == mode]
            axis.scatter([index]*4,values,color=color,s=35,zorder=3)
            mid = report['roles'][role][mode+'_median_seconds']
            axis.plot([index-.15,index+.15],[mid,mid],color='#182536',linewidth=3,zorder=4)
            axis.text(index,.40,f'Median {mid:.3f} s',ha='center',fontsize=10,color='#26384c')
        axis.set_title(role.capitalize(),fontweight='bold',pad=14)
        axis.set_xticks([0,1],['Boot from template','Golden snapshot']); axis.set_xlim(-.45,1.45)
        axis.set_ylim(.35,max(1.02,max(row['resume_to_http_card_seconds'] for row in rows)+.08))
        axis.grid(axis='y',color='#e3e8ef'); axis.spines[['top','right']].set_visible(False)
        axis.spines[['left','bottom']].set_color('#bcc5d1')
    axes[0].set_ylabel('Resume request through actual HTTP readiness (seconds)')
    figure.suptitle('Agent startup: boot versus golden snapshot',x=.08,y=.97,ha='left',fontsize=18,fontweight='bold',color='#202e40')
    figure.text(.08,.90,'4 matched pairs per role · fresh actors · same cached images and warm worker pools',color='#536376')
    figure.text(.08,.055,'Source: native agent-warmup-29b · 29 October 2026 · CLI/TLS/HTTP overhead included; model generation excluded',fontsize=9,color='#627085')
    figure.subplots_adjust(left=.08,right=.98,top=.78,bottom=.20,wspace=.14)
    output = ROOT / 'docs/images/agent-startup-pairs'
    figure.savefig(output.with_suffix('.png'),dpi=180,facecolor='white')
    figure.savefig(output.with_suffix('.svg'),facecolor='white',metadata={'Date':None}); plt.close(figure)
    svg=output.with_suffix('.svg');svg.write_bytes(svg.read_bytes().replace(b'\r\n',b'\n'))
    output.with_suffix('.json').write_text(json.dumps({'kind':'plot-of-native-matched-pairs','runtime_screenshot':False,
        'source_run':'agent-warmup-29b','measurements':24,'source_archive_sha256':sha256((evidence/'native-receipts.tar.gz').read_bytes()).hexdigest()},indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'rendered':True,'measurements':24,'all_pairs_shown':True}))


if __name__ == '__main__': render()
