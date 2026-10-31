/** Capture the authored SVG's moving markers for the GitHub README; no platform requests. */
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
const source=path.join(root,'docs/images/platform-system.animated.svg');
const output=path.join(root,'docs/images/platform-system.gif');
const scratch=fs.mkdtempSync(path.join(os.tmpdir(),'iot-poster-preview-'));
const sha=b=>createHash('sha256').update(b).digest('hex');
let browser;
try{
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1920,height:1980},deviceScaleFactor:1,reducedMotion:'no-preference'});
  const external=[],errors=[];await context.route(/^https?:/,route=>{external.push(route.request().url());return route.abort();});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const svg=fs.readFileSync(source,'utf8');
  await page.setContent('<!doctype html><meta charset="utf-8"><style>body{margin:0}#diagram{display:block;width:1920px;height:1980px}</style>'+svg);
  await page.evaluate(async()=>{await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const s=document.getElementById('diagram');s.pauseAnimations();document.getElementById('illustrative-motion').style.display='none';});
  await page.locator('#diagram').screenshot({path:path.join(scratch,'static.png')});
  await page.evaluate(()=>{
    document.getElementById('illustrative-motion').style.display='';
    document.querySelectorAll('animateMotion').forEach((a,i)=>{a.setAttribute('dur','6s');a.setAttribute('begin',String(-(i*0.61803398875%1)*6)+'s');});
  });
  assert.equal(await page.locator('.flow-particle').count(),56);
  for(let i=0;i<36;i++){
    await page.evaluate(t=>document.getElementById('diagram').setCurrentTime(t),i/6);
    await page.locator('#diagram').screenshot({path:path.join(scratch,`frame-${String(i).padStart(2,'0')}.png`)});
  }
  assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
  const encoded=spawnSync(process.env.DIAGRAM_PYTHON||'python',['-B',path.join(here,'encode-preview.py'),scratch,output],{encoding:'utf8'});
  if(encoded.status!==0)throw Error(encoded.stderr||encoded.error?.message||'GIF encoding failed');
  const facts=JSON.parse(encoded.stdout);
  const receipt={kind:'authored-architecture-preview',runtime_screenshot:false,runtime_telemetry:false,source:'platform-system.animated.svg',source_hash_encoding:'UTF-8 SVG with CRLF canonicalized to LF',source_sha256:sha(fs.readFileSync(source,'utf8').replaceAll('\r\n','\n')),marker_count:56,
    motion:'Illustrative direction only; independent staggered phases; arbitrary six-second loop; no execution chronology',...facts};
  fs.writeFileSync(path.join(root,'docs/images/platform-system.preview.json'),JSON.stringify(receipt,null,2)+'\n');
  console.log(JSON.stringify(receipt,null,2));
}finally{
  if(browser)await browser.close();
  if(path.dirname(scratch)!==path.resolve(os.tmpdir())||!path.basename(scratch).startsWith('iot-poster-preview-'))throw Error('Unexpected preview scratch path');
  fs.rmSync(scratch,{recursive:true,force:true});
}
