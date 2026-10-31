/** Build an offline presentation from reviewed vector exports and their native edge metadata. */
export function makePresentation(views){
  const text=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
  // Each inline SVG owns an ID namespace, including Excalidraw's generated definitions.
  const namespace=(svg,prefix)=>{
    const ids=[...svg.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
    for(const id of ids){
      svg=svg.replaceAll(`id="${id}"`,`id="${prefix}-${id}"`).replaceAll(`url(#${id})`,`url(#${prefix}-${id})`).replaceAll(`href="#${id}"`,`href="#${prefix}-${id}"`);
    }
    return svg;
  };
  const data=JSON.stringify(views.map(({svg,...v})=>v)).replaceAll('<','\\u003c');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>IoT Maintenance Platform · Architecture</title>
<style>
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;color:#26374a;background:#edf2f7}header{padding:22px 30px;background:#14263e;color:white;display:flex;justify-content:space-between;align-items:center;gap:20px}h1{font-size:22px;margin:0;font-weight:650}header p{margin:4px 0 0;color:#c7d6e9;font-size:13px}header a{color:#c7e0ff;text-decoration:none;white-space:nowrap}.toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:14px 24px;background:white;border-bottom:1px solid #d5dfeb}.toolbar label{display:flex;align-items:center;gap:7px;font-size:13px;color:#43566e}select,button{font:inherit;border:1px solid #b5c5d8;border-radius:7px;background:white;color:#26374a;padding:7px 10px}button{cursor:pointer}button:hover,select:hover{border-color:#2563eb}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #60a5fa;outline-offset:2px}#play{background:#1e40af;color:white;border-color:#1e40af;min-width:80px}.workspace{display:grid;grid-template-columns:minmax(0,1fr) 320px;height:calc(100vh - 183px);min-height:550px}#viewport{overflow:auto;padding:20px;background:#e8eef5}article{display:none;background:white;box-shadow:0 5px 24px #14263e15;margin:0 auto}article.active{display:block}article svg{display:block;width:100%;height:auto;max-width:none}aside{padding:22px;overflow:auto;background:white;border-left:1px solid #d5dfeb}aside h2{font-size:18px;margin:0 0 8px}aside h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin:24px 0 8px}aside p{font-size:14px;margin:8px 0}aside a{color:#1e40af;overflow-wrap:anywhere}.pill{display:inline-block;font-size:12px;padding:3px 8px;background:#eef2ff;color:#3730a3;border-radius:5px}#sources{padding-left:17px;font-size:12px}#sources li{margin:8px 0}.downloads{display:flex;gap:8px;flex-wrap:wrap;font-size:13px}.notice{border-left:3px solid #3b82f6;padding:0 0 0 12px;color:#52677f;font-size:13px}#zoom{width:100px}#summary{font-size:12px;color:#64748b}#edge{max-width:270px}.flow.selected .flow-particle{r:8}.edge-hit:hover{stroke-opacity:.18}@media(prefers-reduced-motion:reduce){.motion-enabled .flow-particle{display:block}}@media(max-width:1000px){.workspace{grid-template-columns:1fr;height:auto}#viewport{min-height:500px;max-height:75vh}aside{border-left:0;border-top:1px solid #d5dfeb}header{padding:18px}.toolbar{padding:12px}#edge{max-width:220px}}
</style></head><body>
<header><div><h1>IoT Maintenance Platform</h1><p>Architecture · five source-reviewed views</p></div><a href="README.md">Diagram guide ↗</a></header>
<nav class="toolbar" aria-label="Diagram controls">
<label>View <select id="view">${views.map((v,i)=>`<option value="${i}">${String(i+1).padStart(2,'0')} · ${text(v.title)}</option>`).join('')}</select></label>
<button id="play" type="button" aria-pressed="false">Play</button>
<label>Speed <select id="speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select></label>
<label>Arrow <select id="edge"></select></label>
<label>Zoom <input id="zoom" type="range" min="70" max="200" value="100" step="10"><output id="zoom-value">100%</output></label>
<button id="fit" type="button">Fit</button>
</nav>
<main class="workspace"><section id="viewport" aria-label="Architecture canvas">${views.map((v,i)=>`<article id="view-${i}" aria-label="${text(v.title)}">${namespace(v.svg,'v'+i)}</article>`).join('')}</section>
<aside><p class="pill">Illustrative flow · offline</p><h2 id="detail-title">Flow directions</h2><p id="summary"></p><p id="direction"></p><p id="kind"></p><p id="note"></p><h3>Source references</h3><ul id="sources"></ul><h3>Files</h3><div class="downloads"><a id="native">Excalidraw</a><a id="static">PNG</a><a id="vector">SVG</a><a id="animated">Animated SVG</a></div><h3>Interpretation</h3><p class="notice">Moving dots indicate the authored direction of each handoff. Loops are independent, with arbitrary timing. They do not show live traffic, concurrency, throughput or execution order.</p><p class="notice">Edit shapes, text, logos and bindings in the native Excalidraw files. Animation belongs to this viewer and the animated SVG exports.</p><noscript><p>Enable JavaScript for the view selector, or open a PNG / SVG from the diagram guide.</p></noscript></aside></main>
<script>
'use strict';
const views=${data};
const $=id=>document.getElementById(id);
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
let current=0,playing=!reduced.matches;
function motion(){
  document.body.classList.toggle('motion-enabled',playing);
  document.querySelectorAll('article').forEach((a,i)=>{const svg=a.querySelector('svg');if(playing&&i===current)svg.unpauseAnimations();else svg.pauseAnimations();});
  $('play').textContent=playing?'Pause':'Play';$('play').setAttribute('aria-pressed',String(playing));
}
function chooseEdge(value){
  const v=views[current],e=v.edges.find(e=>e.edge===value),article=$('view-'+current);
  article.querySelectorAll('.flow').forEach(g=>{g.classList.toggle('muted',!!e&&g.dataset.edge!==value);g.classList.toggle('selected',g.dataset.edge===value);});
  $('detail-title').textContent=e?e.edge+' · '+e.label:v.title;
  $('direction').textContent=e?e.from+' → '+e.to:'Select an arrow or click its path to inspect the handoff.';
  $('kind').textContent=e?'Handoff: '+e.kind:'';
  $('note').textContent=e?e.note:'All numbered arrows have a moving marker. A crossing without a component is not a junction.';
  $('sources').replaceChildren();
  if(e)for(const source of e.sources){const li=document.createElement('li'),a=document.createElement('a');a.textContent=source;a.href='../../'+source;li.append(a);$('sources').append(li);}
}
function showView(index){
  current=index;const v=views[current];
  document.querySelectorAll('article').forEach((a,i)=>a.classList.toggle('active',i===index));
  $('edge').replaceChildren(new Option('All arrows',''));
  for(const e of v.edges)$('edge').add(new Option(e.edge+' · '+e.label,e.edge));
  $('summary').textContent=v.nodes.length+' components · '+v.arrows.length+' directed handoffs';
  for(const [id,suffix,prefix]of[['native','.excalidraw',''],['static','.png','../images/'],['vector','.svg','../images/'],['animated','.animated.svg','../images/']])$(id).href=prefix+v.name+suffix;
  $('viewport').scrollTo(0,0);chooseEdge('');motion();
}
$('view').addEventListener('change',e=>showView(Number(e.target.value)));
$('play').addEventListener('click',()=>{playing=!playing;motion();});
$('edge').addEventListener('change',e=>chooseEdge(e.target.value));
$('speed').addEventListener('change',e=>{for(const a of document.querySelectorAll('animateMotion'))a.setAttribute('dur',(Number(a.dataset.baseDuration)/Number(e.target.value))+'s');});
function zoom(value){$('zoom').value=value;$('zoom-value').value=value+'%';for(const a of document.querySelectorAll('article'))a.style.width=value+'%';}
$('zoom').addEventListener('input',e=>zoom(Number(e.target.value)));
$('fit').addEventListener('click',()=>{zoom(100);$('viewport').scrollTo(0,0);});
document.querySelectorAll('.edge-hit').forEach(p=>p.addEventListener('click',()=>{const edge=p.closest('[data-edge]').dataset.edge;$('edge').value=edge;chooseEdge(edge);}));
reduced.addEventListener('change',e=>{if(e.matches){playing=false;motion();}});
showView(0);
</script></body></html>\n`;
}
