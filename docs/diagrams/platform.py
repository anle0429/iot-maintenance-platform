"""Render original English architecture illustrations as SVG and PNG from the implemented IoT lab and declared LLM plan."""
from pathlib import Path
from html import escape
from math import atan2, cos, sin, pi
import json
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/images'
COLORS = {'data': ('#eaf2ff', '#3c68aa'), 'ml': ('#f0ebff', '#7959ad'), 'store': ('#e5f4ed', '#368168'),
          'ops': ('#fff1df', '#b97a25'), 'planned': ('#f1f3f6', '#7d8795'), 'serving': ('#e8f4fa', '#327d9c')}


def font(size, bold=False):
    """Use a readable TrueType font on Windows or Linux without writing to system font directories."""
    names = [Path('C:/Windows/Fonts/seguisb.ttf' if bold else 'C:/Windows/Fonts/segoeui.ttf'),
             Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    for path in names:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


class Canvas:
    """Keep vector and raster versions aligned by rendering the same explicit drawing primitives."""
    def __init__(self, name, title, subtitle, width=1800, height=1120):
        """Start a white architecture canvas with an English title and an explicit illustration label."""
        self.name = name; self.width = width; self.height = height
        self.image = Image.new('RGB', (width, height), '#ffffff'); self.draw = ImageDraw.Draw(self.image)
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
                    f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc><rect width="100%" height="100%" fill="white"/>']
        self.nodes = {}; self.edges = []
        self.text(44, 44, title, 34, bold=True); self.text(44, 94, subtitle, 19, color='#526174')
        self.text(44, height - 32, 'Architecture illustration — not a runtime screenshot. Source: docs/diagrams/platform.py', 16, color='#657386')

    def text(self, x, y, value, size=18, bold=False, color='#16273b', center=False):
        """Draw the same single-line English text with aligned baselines in both output formats."""
        face = font(size, bold); length = self.draw.textlength(value, font=face)
        left = x - length / 2 if center else x
        self.draw.text((left, y), value, font=face, fill=color)
        anchor = 'middle' if center else 'start'; weight = '600' if bold else '400'
        self.svg.append(f'<text x="{x}" y="{y + size}" font-family="Segoe UI,DejaVu Sans,sans-serif" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{color}">{escape(value)}</text>')

    def frame(self, x, y, w, h, title):
        """Draw a runtime/workflow grouping frame rather than pretending the group is a service."""
        self.draw.rounded_rectangle((x, y, x+w, y+h), radius=16, fill='#fafbfd', outline='#d8e0e9', width=2)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#fafbfd" stroke="#d8e0e9" stroke-width="2"/>')
        self.text(x+20, y+15, title, 21, bold=True)

    def node(self, key, x, y, w, h, title, lines=(), kind='data'):
        """Place one deployable unit with bounded text, retaining a box lookup for connector endpoints."""
        fill, stroke = COLORS[kind]; self.nodes[key] = (x, y, w, h)
        self.draw.rounded_rectangle((x, y, x+w, y+h), radius=12, fill=fill, outline=stroke, width=2)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        title_lines = self.wrap(title, w-28, 21, True); content = list(lines)
        needed = len(title_lines)*27 + len(content)*25 + 24
        if needed > h: raise ValueError(f'Clipped node text: {key}')
        at = y+(h-needed)/2+9
        for line in title_lines:
            self.text(x+w/2, at, line, 21, True, center=True); at += 27
        at += 7
        for line in content:
            if self.draw.textlength(line, font=font(17)) > w-20: raise ValueError(f'Wide node line: {key}: {line}')
            self.text(x+w/2, at, line, 17, color='#3c5066', center=True); at += 25

    def wrap(self, value, width, size, bold=False):
        """Wrap only at word boundaries, using the actual raster font to detect overflow."""
        rows = []; line = ''
        for word in value.split():
            candidate = (line+' '+word).strip()
            if line and self.draw.textlength(candidate, font=font(size, bold)) > width:
                rows.append(line); line = word
            else: line = candidate
        return rows+[line]

    def edge(self, identity, points, label, label_at=None, color='#596e86'):
        """Draw an explicitly routed numbered connector without running a layout algorithm over evidence."""
        if identity in self.edges: raise ValueError('Duplicate arrow identity')
        self.edges.append(identity)
        self.draw.line(points, fill=color, width=3, joint='curve')
        self.svg.append('<polyline points="'+' '.join(f'{x},{y}' for x,y in points)+f'" fill="none" stroke="{color}" stroke-width="2.5"/>')
        x,y = points[-1]; a,b = points[-2]; angle = atan2(y-b,x-a)
        head = [(x,y),(x-12*cos(angle-pi/6),y-12*sin(angle-pi/6)),(x-12*cos(angle+pi/6),y-12*sin(angle+pi/6))]
        self.draw.polygon(head, fill=color); self.svg.append('<polygon points="'+' '.join(f'{a},{b}' for a,b in head)+f'" fill="{color}"/>')
        if label_at:
            lx,ly = label_at; value = identity+'  '+label; width = self.draw.textlength(value,font=font(15))
            self.draw.rounded_rectangle((lx-width/2-6,ly-2,lx+width/2+6,ly+23),radius=4,fill='white')
            self.svg.append(f'<rect x="{lx-width/2-6}" y="{ly-2}" width="{width+12}" height="25" rx="4" fill="white"/>')
            self.text(lx,ly,value,15,center=True)

    def save(self):
        """Save editable vectors and a matching raster image, reporting dimensions and graph identities."""
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/(self.name+'.svg')).write_text('\n'.join(self.svg+['</svg>'])+'\n',encoding='utf-8')
        self.image.save(OUT/(self.name+'.png'),optimize=True)
        return {'name':self.name,'width':self.width,'height':self.height,'nodes':list(self.nodes),'arrows':self.edges}


def overview():
    """Show the measured Data/ML workflows while keeping file handoffs and separate stream runs explicit."""
    c = Canvas('platform-overview','IoT Maintenance Platform','Data and ML workflows verified in separate lab runs; the LLM extension is shown separately.',height=1210)
    c.frame(35,150,1730,270,'DATA PLATFORM — persisted JSONL / Parquet handoffs')
    items=[('gen','Generator job',['Seeded pump simulation','Immutable source parts']),('dp1','DP1 Spark ingest',['Bronze Parquet','100 GB source verified']),('dp2','DP2 Spark job',['Dedup and cleaning','Silver / curated outputs']),('dp3','DP3 feature job',['Eight rolling inputs','60-day ML dataset']),('mat','Materialization job',['Feast SDK inside job','Incremental / replay']),('redis','Redis online store',['Batch-stream project','Separate API namespace'])]
    for i,(key,title,body) in enumerate(items):c.node(key,55+288*i,225,245,145,title,body,'store' if key=='redis' else 'data')
    for i,label in enumerate(['Source files','Bronze Parquet','Silver / Gold','Feature Parquet','Online vectors']):c.edge('D'+str(i+1),[(300+288*i,295),(343+288*i,295)],label,(321+288*i,195))
    c.frame(35,485,1730,290,'ML PLATFORM — five pipeline tasks; two executor Pods on the same VM')
    for i,(key,title,body) in enumerate([('kfp','Kubeflow API',['Orchestrates five tasks']),('tasks','ML task Pods',['Fresh Feast retrieval','Two-executor Spark fit']),('mlflow','MLflow registry',['Data / model versions','Model 7 is a candidate']),('ci','Jenkins controller',['Tests, build and gates','A/B / Helm rollback']),('serve','KServe predictor',['Recorded serving pin: 6','Model artifact in image'])]):c.node(key,65+350*i,560,270,145,title,body,'ml')
    c.edge('T1',[(335,625),(415,625)],'Run parameters',(375,530));c.edge('T2',[(685,625),(765,625)],'Artifacts',(725,530))
    c.edge('C1',[(1035,625),(1115,625)],'Selected bundle',(1075,530));c.edge('C2',[(1385,625),(1465,625)],'Image / release',(1425,530))
    c.edge('D6',[(1041,370),(1041,455),(550,455),(550,560)],'Offline features via Feast SDK',(800,438))
    c.frame(35,830,1730,265,'STREAM STORE CHECKS — retained Kafka/Flink windows; separate bounded replay workload')
    values=[('kafka','Kafka broker',['Arrival-ordered replay']),('flink','Flink job',['Event-time windows','Dedup / late side output']),('off','Offline writer job',['Parquet history','Replay is a no-op']),('on','Online writer job',['Latest monitoring vector','No label substitution']),('streamredis','Redis online store',['Finalized-window project','Different feature schema'])]
    for i,(key,title,body) in enumerate(values):c.node(key,65+350*i,900,270,145,title,body,'store' if key=='streamredis' else 'data')
    for i,label in enumerate(['Readings','Finalized windows','Verified history','Latest vectors']):c.edge('S'+str(i+1),[(335+350*i,970),(415+350*i,970)],label,(375+350*i,872))
    c.text(45,1130,'100 GB covers Bronze/Silver. Feature engineering and ML use a separate 60-day source. Repeated store boxes are workflow views.',17)
    return c.save()


def serving():
    """Draw the actual feature-to-KServe path and its distinct request/response directions."""
    c=Canvas('platform-serving','One inference request','Feature API validates schema, both clocks, operating state and the pinned model before returning a score.',height=900)
    for args in [('user',50,285,260,145,'Client',['machine_id: P-001'],'serving'),('gateway',400,285,260,145,'NGINX gateway',['Private CA / Basic auth','Rate limiting'],'serving'),('api',750,285,300,145,'Feature API',['FastAPI + Feast SDK','Async bounded dispatch'],'serving'),('predictor',1320,285,350,145,'KServe predictor',['Portable logistic model','Recorded model version 6'],'ml'),('redis',750,535,300,140,'Redis API store',['Eight inputs + timestamps','Sealed serving context'],'store')]:c.node(*args)
    c.edge('U1',[(310,350),(400,350)],'HTTPS request',(355,250));c.edge('U2',[(660,350),(750,350)],'Routed request',(705,250))
    c.edge('U3',[(830,430),(830,535)],'Lookup',(755,475));c.edge('U4',[(960,535),(960,430)],'Values + times',(1080,475))
    c.edge('U5',[(1050,325),(1320,325)],'Eight-feature vector',(1185,280));c.edge('U6',[(1320,395),(1050,395)],'Score + model identity',(1185,420))
    c.edge('U7',[(750,405),(700,405),(700,730),(180,730),(180,430)],'Prediction + feature trace',(430,710))
    c.text(50,785,'Checks: stopped machine ->409; unknown machine ->404; malformed ID ->422; stale or unavailable data ->503.',19)
    return c.save()


def deployment():
    """Show one physical VM and distinguish Kubernetes, Docker, host processes and retained storage."""
    c=Canvas('platform-deployment','Deployment view','One Google Cloud VM; private administration through IAP and loopback forwards. No multi-host HA claim.',height=1140)
    c.frame(35,155,300,850,'DEVELOPER PC')
    c.node('pc',60,265,250,160,'Local tools',['Git / Python','Editor / offline checks','IAP client'],'ops')
    c.node('browser',60,555,250,160,'Browser',['Swagger / Airflow','Grafana / DataHub','MLflow / Jenkins'],'ops')
    c.frame(380,155,1380,850,'GOOGLE CLOUD VM — 8 vCPU / 64 GiB RAM')
    c.frame(410,225,1320,245,'K3s — Kubernetes workloads')
    for i,(key,title,body) in enumerate([('api','Feature / drift APIs',['Helm Deployments']),('kserve','KServe predictors',['ML inference / Eventing']),('kfp','Kubeflow Pipelines',['API / task Pods']),('mesh','Gateway / test mesh',['Ingress / identity tests'])]):c.node(key,435+320*i,290,285,145,title,body,'ml')
    c.frame(410,500,1320,210,'Docker containers and jobs')
    for i,(key,title) in enumerate([('batch','Spark / Kafka / Flink'),('orch','Airflow / DataHub'),('registry','MLflow / Jenkins')]):c.node(key,445+430*i,560,395,115,title,['Separate images, limits and state'],'data')
    c.frame(410,740,1320,210,'Host services')
    for i,(key,title) in enumerate([('metrics','Prometheus / Grafana'),('logs','Loki / Alloy / exporter'),('vault','Vault / Pushgateway')]):c.node(key,445+430*i,800,395,115,title,['Retained state; recovery CLI'],'ops')
    c.text(410,1035,'Storage: 50 GiB boot + 500 GiB data HDD + 150 GiB pd-balanced scratch. Datasets, registry and secrets stay on VM volumes.',17)
    c.text(45,1080,'Administration: IAP-forwarded SSH22; UI listeners use loopback. A Git clone contains code and documentation, not live service state.',17)
    return c.save()


def llm_plan():
    """Distinguish verified CPU inference from the still-planned RAG and agent components."""
    c=Canvas('platform-llm-plan','LLM / agent extension — staged implementation','Blue: verified CPU inference. Gray: planned/in progress. Green: existing ML APIs. Illustration, not runtime evidence.',width=2200,height=1400)
    items=[('ui',60,200,290,145,'Agent chat UI',['PLANNED','Authenticated ingress']),('gw',430,200,290,145,'Agent gateway',['PLANNED','MCP / A2A / model routes']),('coord',800,200,300,145,'Coordinator agent',['PLANNED','Multi-replica / autoscale']),('llmrouter',1370,200,330,145,'llm-d inference router',['RUNNING: llm-d 1.0','Agent model config pending']),('registry',1800,200,320,145,'Agent registry',['PLANNED','Published agent versions']),('rag',60,500,290,145,'Airflow RAG job',['PLANNED','Text -> chunks -> Feast']),('data',800,500,300,145,'Data / RAG agent',['PLANNED','Sandboxed tool access']),('drift',1300,500,300,145,'Drift agent',['PLANNED','Sandboxed tool access']),('warm',1800,500,320,145,'Warm worker pool',['PLANNED','Agent startup benchmark']),('embed',60,800,290,145,'Embedding job',['IN PROGRESS','Pinned MiniLM CPU']),('mcpdata',800,800,300,145,'Data / chunk MCP',['PLANNED','Async validated tools']),('mcpdrift',1300,800,300,145,'Drift MCP server',['PLANNED','Async validated tools']),('telemetry',1800,800,320,145,'Agent / LLM telemetry',['PLANNED','Tokens / TTFT / tool calls']),('kb',60,1100,290,145,'Knowledge store',['PLANNED','Feast registry / vectors'])]
    for key,x,y,w,h,title,body in items:c.node(key,x,y,w,h,title,body,'serving' if key=='llmrouter' else 'planned')
    # The platform router and custom backend are distinct deployable units.
    c.node('customserver',1800,1100,320,145,'Custom model server',['RUNNING: Qwen2.5 1.5B','CPU / Q4_K_M benchmark'],'serving')
    c.node('feature',800,1100,300,145,'Existing Feature API',['ML implementation exists','Agent integration pending'],'store');c.node('driftapi',1300,1100,300,145,'Existing Drift API',['ML implementation exists','Agent integration pending'],'store')
    c.edge('A1',[(350,270),(430,270)],'Chat request',(390,170));c.edge('A2',[(720,270),(800,270)],'Dispatch',(760,170))
    c.edge('A3',[(1100,270),(1370,270)],'Generation via gateway',(1235,235))
    c.edge('G1',[(1700,270),(1740,270),(1740,1140),(1800,1140)],'Model request',(1890,980))
    c.edge('G2',[(1800,1210),(1710,1210),(1710,370),(1530,370),(1530,345)],'Token stream',(1880,1030))
    c.edge('A4',[(950,345),(950,500)],'A2A via gateway',(810,410));c.edge('A5',[(1050,345),(1050,420),(1450,420),(1450,500)],'A2A via gateway',(1300,390))
    c.edge('M1',[(950,645),(950,800)],'MCP tool call',(1090,710));c.edge('M2',[(1450,645),(1450,800)],'MCP tool call',(1600,710))
    c.edge('M3',[(950,945),(950,1100)],'Read features / inference',(1110,1010));c.edge('M4',[(1450,945),(1450,1100)],'Read drift result',(1610,1010))
    c.edge('K1',[(205,645),(205,800)],'Chunk artifact',(220,710))
    c.edge('K2',[(350,870),(450,870),(450,570),(350,570)],'Vector artifact',(550,710))
    c.edge('K3',[(60,575),(30,575),(30,1170),(60,1170)],'Chunks and vectors via Feast',(230,1010))
    c.edge('K4',[(350,1170),(650,1170),(650,870),(800,870)],'Retrieved chunks',(590,1135))
    c.text(60,1300,'Remaining proof: RAG lineage, MCP/agents, global config, sandbox, registry, warm-up, CI, A/B and full token/tool telemetry.',20)
    return c.save()


def monitoring():
    """Separate native PSI monitoring and manually probed retraining from unimplemented LLM telemetry."""
    c=Canvas('platform-monitoring','Monitoring and guarded retraining','Recorded ML monitoring paths; the retraining integration test was a manual probe with drift=false.',height=1110)
    rows=[(170,'OFFLINE DRIFT — fixed model3 training reference, no labels',[
        ('schedule','Airflow scheduler',['Five-minute schedule']),('driftjob','Feast drift job',['Bounded 1024-row cohort']),('push','Pushgateway',['PSI / freshness gauges']),('prom','Prometheus',['Recorded query values']),('graf','Grafana',['Result age + PSI'])],['Comparison window','Gauges','Scrape','PromQL']),
        (460,'API AND LOG TELEMETRY — original source-bound workloads',[
        ('api','API deployments',['HTTP metrics / spans']),('collector','Alloy collector',['Pod discovery / state']),('loki','Loki',['Timestamped log rows']),('dashboard','Grafana',['LogQL / metrics panels']),('jaeger','Jaeger',['OTLP trace graph'])],['CRI log files','Labeled records','LogQL','Trace view separate']),
        (750,'RETRAINING — admission, persistent intent, replay and cooldown',[
        ('airflow','Airflow task',['Sealed report + policy']),('control','Retrain controller',['Admit / hold / skip']),('kfp','Kubeflow API',['Actual five-task run']),('pods','ML task Pods',['Same fixed protocol']),('candidate','MLflow registry',['Model 7 candidate','No automatic promotion'])],['Report / policy','Run parameters','Task orchestration','Candidate artifacts'])]
    for row_index,(y,title,items,labels) in enumerate(rows):
        c.frame(35,y,1730,235,title)
        for i,(key,name,body) in enumerate(items):
            c.node(key,65+350*i,y+65,270,135,name,body,'ml' if row_index==2 else 'ops')
        for i,label in enumerate(labels):
            if row_index==1 and i==3:continue
            c.edge(('M' if row_index==0 else 'O' if row_index==1 else 'R')+str(i+1),[(335+350*i,y+130),(415+350*i,y+130)],label,(375+350*i,y+40))
    c.text(45,1020,'Jaeger receives OTLP spans directly from the APIs. LLM tokens, TTFT, safety hits and agent/tool counters are still unimplemented.',17)
    c.edge('O4',[(200,660),(200,715),(1600,715),(1600,660)],'OTLP spans from API to Jaeger',(900,703))
    return c.save()


def main():
    """Render all four views and save a machine-readable inventory of their actual nodes and arrows."""
    manifests=[overview(),serving(),deployment(),monitoring(),llm_plan()]
    (OUT/'diagram-index.json').write_text(json.dumps({'kind':'authored-architecture-illustrations','runtime_screenshot':False,'views':manifests},indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'rendered_views':len(manifests),'output':'docs/images','planned_llm_explicit':True}))


if __name__=='__main__':
    main()
