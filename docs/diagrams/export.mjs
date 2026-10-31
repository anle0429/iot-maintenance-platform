/** Render the hand-authored native scenes with Excalidraw and add an illustrative motion layer. */
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {makePresentation} from './presentation.mjs';

const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'../..');
const modules=process.env.DIAGRAM_NODE_MODULES||path.join(here,'node_modules');
const require=createRequire(path.join(modules,'../package.json'));
const {build}=require('esbuild');
const {chromium}=require('playwright');
const views=['platform-system','platform-overview','platform-serving','platform-deployment','platform-monitoring','platform-llm-plan'];
const labels=['System architecture','Platform overview','Prediction request','Deployment topology','Observability & retraining','RAG & agents'];
const bank=JSON.parse(fs.readFileSync(path.join(here,'icons/brands.json')));
const escapeXML=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const digest=s=>createHash('sha256').update(s).digest('hex');
const writeNative=process.argv.includes('--write-native');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'iot-diagram-export-'));
let browser,server;

/** Fill required serialization defaults without inventing positions, labels or relationships. */
function prepare(scene){
  scene.files??={};
  const ids=new Set();
  for(const e of scene.elements){
    if(ids.has(e.id))throw Error('Duplicate native ID '+e.id);ids.add(e.id);
    for(const source of e.customData?.sources??[]){
      const target=path.resolve(root,source);
      if(!target.startsWith(root+path.sep)||!fs.existsSync(target))throw Error('Missing source '+source);
    }
    const hash=createHash('sha256').update(e.id).digest();
    Object.assign(e,{angle:e.angle??0,fillStyle:e.fillStyle??'solid',strokeWidth:e.strokeWidth??(e.type==='text'?1:2),strokeStyle:e.strokeStyle??'solid',roughness:0,opacity:100,seed:e.seed??hash.readUInt32LE(0)%2147483647,version:e.version??1,versionNonce:e.versionNonce??hash.readUInt32LE(4)%2147483647,isDeleted:false,groupIds:e.groupIds??[],frameId:e.frameId??null,roundness:e.roundness??null,boundElements:e.boundElements??null,link:e.link??null,locked:e.locked??false,updated:e.updated??1});
    if(e.type==='text')Object.assign(e,{fontFamily:3,textAlign:e.textAlign??'left',verticalAlign:'top',lineHeight:e.lineHeight??1.25,containerId:e.containerId??null,originalText:e.text,autoResize:true});
    if(e.type==='image'){
      const icon=bank[e.fileId];if(!icon)throw Error('Unknown icon '+e.fileId);
      scene.files[e.fileId]={id:e.fileId,mimeType:icon.mimeType??'image/svg+xml',dataURL:icon.dataURL??('data:image/svg+xml;base64,'+Buffer.from(icon.svg).toString('base64')),created:1};
    }
  }
  for(const e of scene.elements.filter(e=>e.type==='arrow')){
    if(e.angle!==0||!e.customData?.edge)throw Error('Each diagram arrow needs explicit, unrotated flow metadata');
    for(const binding of[e.startBinding,e.endBinding]){
      const target=scene.elements.find(n=>n.id===binding?.elementId);
      if(!target)throw Error('Missing arrow binding '+e.id);
      target.boundElements??=[];
      if(!target.boundElements.some(b=>b.id===e.id))target.boundElements.push({id:e.id,type:'arrow'});
    }
  }
  return scene;
}

/** Trace native points in their authored order; request and return routes must never be reversed. */
export function flowGeometry(e){
  const points=e.points.map(([x,y])=>[x+e.x,y+e.y]);
  const length=points.slice(1).reduce((n,p,i)=>n+Math.hypot(p[0]-points[i][0],p[1]-points[i][1]),0);
  return{points,d:points.map((p,i)=>(i?'L':'M')+p.join(' ')).join(' '),seconds:Math.max(2.5,Math.min(14,length/140))};
}

/** SVG motion is a separate presentation layer, not part of the editable Excalidraw editor. */
function animate(svg,elements,name){
  const edges=elements.filter(e=>e.customData?.edge);
  const groups=edges.map(e=>{
    const g=flowGeometry(e),id=escapeXML(e.id),color=escapeXML(e.strokeColor);
    return `<g id="flow-${id}" class="flow" data-edge="${id}"><title>${escapeXML(e.customData.label)}</title><path id="route-${id}" class="edge-hit" d="${g.d}" fill="none" stroke="${color}" stroke-opacity="0" stroke-width="22" pointer-events="stroke"/><circle class="flow-particle" r="5.5" fill="${color}" stroke="#ffffff" stroke-width="1.5"><animateMotion path="${g.d}" dur="${g.seconds.toFixed(3)}s" data-base-duration="${g.seconds.toFixed(3)}" repeatCount="indefinite" calcMode="paced"/></circle></g>`;
  }).join('');
  return svg.replace('</svg>',`<style>.flow-particle{pointer-events:none}.edge-hit{cursor:pointer}.flow.muted .flow-particle{opacity:.12}.flow.selected .edge-hit{stroke-opacity:.22}@media(prefers-reduced-motion:reduce){.flow-particle{display:none}}</style><g id="illustrative-motion" aria-label="Illustrative flow directions; not live telemetry">${groups}</g><metadata>${escapeXML(JSON.stringify({kind:'illustrative-motion',name,runtime_telemetry:false,ordering:'independent illustrative loops; no execution chronology',edges:edges.map(e=>({id:e.id,...flowGeometry(e)}))}))}</metadata></svg>`);
}

try{
  await build({stdin:{contents:"import {exportToSvg,restore,getCommonBounds} from '@excalidraw/excalidraw'; window.diagram={exportToSvg,restore,getCommonBounds};",resolveDir:path.dirname(modules),loader:'jsx'},bundle:true,format:'iife',outfile:path.join(temporary,'export.js'),platform:'browser',define:{'process.env.NODE_ENV':'"production"'},loader:{'.woff2':'file','.woff':'file','.png':'file','.svg':'file'},logLevel:'silent'});
  const html='<!doctype html><meta charset="utf-8"><script>window.EXCALIDRAW_ASSET_PATH="/assets/"</script><script src="/export.js"></script><div id="root"></div>';
  server=http.createServer((req,res)=>{
    const url=new URL(req.url,'http://localhost');
    if(url.pathname==='/'){res.setHeader('Content-Type','text/html');res.end(html);return;}
    const base=url.pathname.startsWith('/assets/')?path.join(modules,'@excalidraw/excalidraw/dist/prod'):temporary;
    const filename=path.resolve(base,'.'+(url.pathname.startsWith('/assets/')?url.pathname.slice(7):url.pathname));
    if(!filename.startsWith(base+path.sep)||!fs.existsSync(filename)||!fs.statSync(filename).isFile()){res.writeHead(404).end();return;}
    res.setHeader('Content-Type',filename.endsWith('.js')?'text/javascript':filename.endsWith('.woff2')?'font/woff2':'application/octet-stream');res.end(fs.readFileSync(filename));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:3000,height:2200},deviceScaleFactor:1});
  const pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.waitForFunction(()=>!!window.diagram);
  await page.addStyleTag({content:'@font-face{font-family:Cascadia;src:url("/assets/fonts/Cascadia/CascadiaCode-Regular.woff2")}body{margin:0}'});
  await page.evaluate(async()=>{await document.fonts.load('20px Cascadia');await document.fonts.ready;});
  const images=path.join(root,'docs/images'),results=[];
  for(const [i,name]of views.entries()){
    const nativePath=path.join(here,name+'.excalidraw');
    const scene=prepare(JSON.parse(fs.readFileSync(nativePath)));
    const result=await page.evaluate(async data=>{
      const X=window.diagram,restored=X.restore(data,null,null,{repairBindings:true,refreshDimensions:true});
      const svg=await X.exportToSvg({elements:restored.elements,appState:{...data.appState,exportBackground:true,exportWithDarkMode:false},files:data.files,exportPadding:0});
      svg.id='diagram';document.getElementById('root').replaceChildren(svg);
      return{svg:svg.outerHTML,elements:restored.elements,bounds:X.getCommonBounds(restored.elements),width:Number(svg.getAttribute('width')),height:Number(svg.getAttribute('height'))};
    },scene);
    if(result.bounds[0]!==0||result.bounds[1]!==0)throw Error('Canvas origin changed: '+name);
    if(pageErrors.length)throw Error(pageErrors.join('\n'));
    const metadata={kind:'authored-architecture-illustration',runtime_screenshot:false,native:name+'.excalidraw',sources:[...new Set(result.elements.flatMap(e=>e.customData?.sources??[]))]};
    const svg=result.svg.replace('</svg>',`<metadata>${escapeXML(JSON.stringify(metadata))}</metadata></svg>`);
    fs.writeFileSync(path.join(images,name+'.svg'),svg+'\n');
    await page.locator('#diagram').screenshot({path:path.join(images,name+'.png'),timeout:30000});
    const animated=animate(svg,result.elements,name);
    fs.writeFileSync(path.join(images,name+'.animated.svg'),animated+'\n');
    if(writeNative){
      // Serialization markers are deterministic; they are not captured service timestamps.
      const canonical=result.elements.map(e=>({...e,updated:1,version:1,versionNonce:createHash('sha256').update(e.id).digest().readUInt32LE(4)%2147483647}));
      fs.writeFileSync(nativePath,JSON.stringify({...scene,elements:canonical},null,2)+'\n');
    }
    const edges=result.elements.filter(e=>e.customData?.edge).map(e=>({...e.customData,...flowGeometry(e)}));
    const nodes=result.elements.filter(e=>e.customData?.node).map(e=>e.customData.node);
    const view={name,title:labels[i],width:result.width,height:result.height,nodes,arrows:edges.map(e=>e.edge),native:'../diagrams/'+name+'.excalidraw',animated:name+'.animated.svg'};
    results.push({...view,svg:animated,edges});
    console.log(JSON.stringify({view:name,nodes:nodes.length,arrows:edges.length,dimensions:[view.width,view.height]}));
  }
  fs.writeFileSync(path.join(images,'diagram-index.json'),JSON.stringify({kind:'authored-architecture-illustrations',runtime_screenshot:false,source:'docs/diagrams/*.excalidraw',renderer:'@excalidraw/excalidraw@0.18.0',motion:'illustrative direction only; not live telemetry or measured timing',views:results.map(({svg,edges,...v})=>v)},null,2)+'\n');
  fs.writeFileSync(path.join(here,'architecture.html'),makePresentation(results));
  const sourceFiles=[...new Set(results.flatMap(v=>v.edges.flatMap(e=>e.sources)))].sort();
  console.log(JSON.stringify({rendered:results.length,native_embedded:writeNative,source_files:sourceFiles.length,animation_count:results.reduce((n,v)=>n+v.arrows.length,0),index_sha256:digest(fs.readFileSync(path.join(images,'diagram-index.json')))}));
}finally{
  if(browser)await browser.close();
  if(server)await new Promise(resolve=>server.close(resolve));
  if(path.dirname(temporary)!==path.resolve(os.tmpdir())||!path.basename(temporary).startsWith('iot-diagram-export-'))throw Error('Unexpected export scratch path');
  fs.rmSync(temporary,{recursive:true,force:true});
}
