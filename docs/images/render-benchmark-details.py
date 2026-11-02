"""Render one readable panel per native three-pair experiment without changing original reports or plots."""
from pathlib import Path
from hashlib import sha256
import json
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent


def main():
    """Keep all paired observations, zero-origin axes and the slower dedup result visible."""
    files=['docs/evidence/batch-benchmarks/final/corrections.json','docs/evidence/batch-benchmarks/final/skew.json']
    raw=[(ROOT/name).read_bytes() for name in files];corrections,skew=map(json.loads,raw)
    specs=[('schema','Declared schema versus full inference',corrections,['inferred','declared'],['Full inference','Declared schema'],
            'Complete JSON read, including inference and all-column action'),
           ('dedup','Keep the production window selector',corrections,['window','aggregate'],['Window','Aggregate candidate'],
            'Warmed normalized input; shared conflict checks excluded'),
           ('skew','Broadcast the small factory-policy table',skew,['baseline','broadcast'],['Sort merge','Broadcast'],
            'Separate 17.28M Silver-row query; unchanged 70/15/15% input')]
    outputs=[]
    for experiment,title,report,strategies,labels,scope in specs:
        assert report['passed'] and report['repetitions']==3
        rows=[r for r in report['measurements'] if r.get('experiment','skew')==experiment]
        assert len(rows)==6
        values=[[next(r['seconds'] for r in rows if r['strategy']==method and r['repeat']==i) for i in range(3)] for method in strategies]
        medians=list(map(statistics.median,values));change=100*(medians[1]/medians[0]-1)
        fig,ax=plt.subplots(figsize=(9,5.7));fig.patch.set_facecolor('white')
        bars=ax.bar([0,1],medians,width=.48,color=['#1e40af','#c2410c'],zorder=2)
        for i in range(3):
            offset=(i-1)*.06
            ax.plot([offset,1+offset],[values[0][i],values[1][i]],marker='o',markersize=7,
                    linewidth=1.6,color='#64748b',alpha=.9,zorder=3,label='All three measured pairs' if i==0 else None)
        ax.bar_label(bars,labels=[f'{v:.3f} s' for v in medians],padding=10,fontsize=18,weight='bold',
                     bbox={'facecolor':'white','edgecolor':'none','pad':2},zorder=5)
        ax.set_xticks([0,1],labels,fontsize=16)
        ax.set_ylabel('Seconds per measured action',fontsize=15);ax.tick_params(axis='y',labelsize=14)
        ax.set_ylim(0,max(max(v) for v in values)*1.28);ax.set_axisbelow(True);ax.grid(axis='y',color='#dbeafe')
        ax.spines[['top','right']].set_visible(False)
        fig.suptitle(title,x=.06,y=.97,ha='left',fontsize=21,weight='bold',color='#1e3a5f')
        result=f'Candidate median: {abs(change):.1f}% '+('higher; no promotion' if change>0 else 'lower on this workload')
        fig.text(.06,.89,result,fontsize=15,color='#374151')
        fig.text(.06,.115,'Bars: medians | Connected dots: every measured pair (n = 3)',fontsize=12,color='#374151')
        fig.text(.06,.065,scope,fontsize=11,color='#374151')
        fig.text(.06,.025,'Derived from original native reports; not a service screenshot or end-to-end speedup.',fontsize=10,color='#64748b')
        fig.tight_layout(rect=(.03,.17,.99,.86))
        name='benchmark-'+experiment+'-readable.png';fig.savefig(OUT/name,dpi=120);plt.close(fig)
        outputs.append({'path':name,'experiment':experiment,'medians_seconds':medians,'paired_seconds':values})
    value={'kind':'readable-native-benchmark-panels','runtime_screenshot':False,'repetitions':3,
        'source_sha256':{name:sha256(body).hexdigest() for name,body in zip(files,raw)},'panels':outputs}
    (OUT/'benchmark-detail-sources.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
