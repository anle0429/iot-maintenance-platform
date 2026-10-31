/** Capture authored SVG motion as GitHub previews; no platform requests or runtime data. */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(here,'../..');
const require=createRequire(path.join(process.env.DIAGRAM_NODE_MODULES||path.join(here,'node_modules'),'../package.json'));
const {chromium}=require('playwright');
const index=JSON.parse(fs.readFileSync(path.join(root,'docs/images/diagram-index.json')));
const selected=process.argv.indexOf('--view');
const names=process.argv.includes('--all')?index.views:index.views.filter(v=>v.name===(selected<0?'platform-system':process.argv[selected+1]));
assert(names.length,'Select an existing view with --view NAME or use --all');
const sha=b=>createHash('sha256').update(b).digest('hex');
/** Allow a brief Windows reader lock; never silently skip an unwritten manifest. */
async function writeReceipt(filename,body){
 for(let attempt=0;;attempt++){
  try{fs.writeFileSync(filename,body);return;}
  catch(error){
   if(attempt>=5||!['EBUSY','EPERM','UNKNOWN'].includes(error.code))throw error;
   await new Promise(resolve=>setTimeout(resolve,150*(attempt+1)));
  }
 }
}
const browser=await chromium.launch({headless:true});
try{
 for(const v of names){
  const source=path.join(root,'docs/images',v.name+'.animated.svg'),output=path.join(root,'docs/images',v.name+'.gif');
  const receiptPath=path.join(root,'docs/images',v.name+'.preview.json');
  const sourceHash=sha(fs.readFileSync(source,'utf8').replaceAll('\r\n','\n'));
  const width=Math.min(1920,v.width),height=Math.round(v.height*width/v.width);
  if(!process.argv.includes('--force')&&fs.existsSync(receiptPath)&&fs.existsSync(output)){
   const r=JSON.parse(fs.readFileSync(receiptPath));
   if(r.source_sha256===sourceHash&&r.sha256===sha(fs.readFileSync(output))&&r.marker_count===v.arrows.length&&r.width===width&&r.height===height){
    console.log(JSON.stringify({view:v.name,reused_exact_preview:true,sha256:r.sha256}));continue;
   }
  }
  const scratch=fs.mkdtempSync(path.join(os.tmpdir(),'iot-diagram-preview-'));
  const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:1,reducedMotion:'no-preference'});
  try{
   const external=[],errors=[];await context.route(/^https?:/,route=>{external.push(route.request().url());return route.abort();});
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
   await page.setContent(`<!doctype html><meta charset="utf-8"><style>body{margin:0}#diagram{display:block;width:${width}px;height:${height}px}</style>`+fs.readFileSync(source,'utf8'));
   await page.evaluate(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const s=document.getElementById('diagram');s.pauseAnimations();document.getElementById('illustrative-motion').style.display='none';});
   await page.locator('#diagram').screenshot({path:path.join(scratch,'static.png')});
   await page.evaluate(()=>{document.getElementById('illustrative-motion').style.display='';document.querySelectorAll('animateMotion').forEach((a,i)=>{a.setAttribute('dur','6s');a.setAttribute('begin',String(-(i*0.61803398875%1)*6)+'s');});});
   assert.equal(await page.locator('.flow-particle').count(),v.arrows.length);
   for(let i=0;i<36;i++){
    await page.evaluate(t=>document.getElementById('diagram').setCurrentTime(t),i/6);
    await page.locator('#diagram').screenshot({path:path.join(scratch,`frame-${String(i).padStart(2,'0')}.png`)});
   }
   assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
   const encoded=spawnSync(process.env.DIAGRAM_PYTHON||'python',['-B',path.join(here,'encode-preview.py'),scratch,output,'--scene',v.name],{encoding:'utf8'});
   if(encoded.status!==0)throw Error(encoded.stderr||encoded.error?.message||'GIF encoding failed');
   const facts=JSON.parse(encoded.stdout);
   const receipt={kind:'authored-architecture-preview',runtime_screenshot:false,runtime_telemetry:false,source:v.name+'.animated.svg',source_hash_encoding:'UTF-8 SVG with CRLF canonicalized to LF',source_sha256:sourceHash,marker_count:v.arrows.length,motion:'Illustrative direction only; independent staggered phases; arbitrary six-second loop; no execution chronology',...facts};
   await writeReceipt(receiptPath,JSON.stringify(receipt,null,2)+'\n');
   console.log(JSON.stringify({view:v.name,...receipt}));
  }finally{
   await context.close();
   if(path.dirname(scratch)!==path.resolve(os.tmpdir())||!path.basename(scratch).startsWith('iot-diagram-preview-'))throw Error('Unexpected preview scratch path');
   fs.rmSync(scratch,{recursive:true,force:true});
  }
 }
}finally{await browser.close();}
