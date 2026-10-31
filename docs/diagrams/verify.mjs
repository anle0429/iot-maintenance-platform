/** Check editable scenes, source references, exported routes and the actual offline presentation. */
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';

const here=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(here,'../..');
const figures=JSON.parse(fs.readFileSync(path.join(root,'docs/images/diagram-index.json')));
const bank=JSON.parse(fs.readFileSync(path.join(here,'icons/brands.json')));
const expected=[
  [47,'O1 O2 D1 D2 Q1 S0 S1 S2 S3 S4 S5 F1 O3 F2 T0 T1 I1 I2 T2 C1 C2 M1 L1 L2 F3 V1 P0 P1 P2 P3 P4 P5 C3 H1 O4 R0 R1 R2 K1 K2 Q2 A1 A2 A3 A4 A5 M2 M4 M5 G0 G1 G2 G3 G4 H2 H3'],
  [18,'D1 D2 F1 F2 F3 C1 G1 T0 T1 T2 C2 C3 S0 S1 S2 S3 S4'],
  [5,'U1 U2 U3 U4 U5 U6 U7'],[42,'U1 U2 U3 G1 G2 G3 G4'],
  [14,'H1 M1 L1 L2 M2 L3 M3 O1 O2 R1 R2 R3 R4'],
  [16,'K0 K1 K2 A1 A2 A3 A4 M1 M2 M3 M4 M5 G1 G2 G3 G4 G5'],
  [7,'C1 C2 C3 C4 C5 C6'],[8,'Q1 C1 C2 D1 H1 H2 H3'],
  [10,'A1 A2 A3 M1 M2 G1 G2 G3 G4 G5 G6 G7 T0 T1 T2'],[6,'P1 P2 P3 P4 P5 P6']
];
const sha=s=>createHash('sha256').update(s).digest('hex');
const sources=new Map(),checks=[];
const report={kind:'architecture-documentation-check',runtime_workload:false,views:checks,browser:null};
const arg=process.argv.indexOf('--output'),out=arg<0?null:path.resolve(process.argv[arg+1]);
if(out){assert(out.startsWith(path.join(root,'outputs')+path.sep));assert(!fs.existsSync(out),'Use a new receipt path');fs.mkdirSync(path.dirname(out),{recursive:true});}

/** A strict interior intersection excludes intentional touches at an endpoint or a card border. */
function crosses(a,b,r){
  return a[0]===b[0]&&a[0]>r.x+2&&a[0]<r.x+r.width-2&&Math.max(a[1],b[1])>r.y+2&&Math.min(a[1],b[1])<r.y+r.height-2||a[1]===b[1]&&a[1]>r.y+2&&a[1]<r.y+r.height-2&&Math.max(a[0],b[0])>r.x+2&&Math.min(a[0],b[0])<r.x+r.width-2;
}
function overlap(a,b){return a.x+2<b.x+b.width&&a.x+a.width-2>b.x&&a.y+2<b.y+b.height&&a.y+a.height-2>b.y;}

assert.equal(figures.runtime_screenshot,false);assert.equal(figures.views.length,10);
for(const [i,v]of figures.views.entries()){
  const s=JSON.parse(fs.readFileSync(path.join(here,v.name+'.excalidraw'))),elements=s.elements;
  assert.equal(s.type,'excalidraw');assert.equal(s.version,2);assert.equal(new Set(elements.map(e=>e.id)).size,elements.length);
  const nodes=elements.filter(e=>e.customData?.node),arrows=elements.filter(e=>e.type==='arrow'),texts=elements.filter(e=>e.type==='text');
  assert.equal(nodes.length,expected[i][0]);assert.deepEqual(arrows.map(e=>e.id).sort(),expected[i][1].split(' ').sort());
  assert.deepEqual(v.nodes,nodes.map(e=>e.customData.node));assert.deepEqual(v.arrows,arrows.map(e=>e.id));
  const canvas=elements.find(e=>e.id.endsWith('-canvas'));assert.equal(canvas.x,0);assert.equal(canvas.y,0);assert.equal(canvas.width,v.width);assert.equal(canvas.height,v.height);
  for(const e of elements){
    assert.equal(e.isDeleted,false);assert.equal(e.roughness,0);assert.equal(e.opacity,100);assert.equal(e.angle,0);
    if(e.type!=='arrow'){assert(e.x>=0&&e.y>=0&&e.x+e.width<=v.width+1&&e.y+e.height<=v.height+1,'Out of canvas: '+e.id);}
    if(e.type==='text')assert.equal(e.fontFamily,3);
    for(const source of e.customData?.sources??[]){const f=path.resolve(root,source);assert(f.startsWith(root+path.sep)&&fs.statSync(f).isFile());sources.set(source,sha(fs.readFileSync(f)));}
    if(e.type==='image'){
      assert(bank[e.fileId]);const icon=bank[e.fileId],file=s.files[e.fileId];assert.equal(file.mimeType,icon.mimeType??'image/svg+xml');
      if(icon.dataURL)assert.equal(file.dataURL,icon.dataURL);else assert.equal(Buffer.from(file.dataURL.split(',')[1],'base64').toString(),icon.svg);
      assert(e.width<=(i===0?360:64)&&e.height<=(i===0?96:64),'Native scene must not be flattened into a screenshot');
    }
  }
  for(const set of[nodes,texts])for(let a=0;a<set.length;a++)for(let b=a+1;b<set.length;b++)assert(!overlap(set[a],set[b]),'Overlapping elements: '+set[a].id+' / '+set[b].id);
  const svg=fs.readFileSync(path.join(root,'docs/images',v.name+'.svg'),'utf8');
  const animated=fs.readFileSync(path.join(root,'docs/images',v.name+'.animated.svg'),'utf8');
  for(const xml of[svg,animated]){
    assert(xml.startsWith('<svg '));assert(!/<(?:script|foreignObject)\b/i.test(xml));
    assert(!/(?:href|src)=["']https?:/i.test(xml));assert(!/url\(["']?https?:/i.test(xml));
    assert(xml.includes('data:font/woff2')||xml.includes('data:application/font-woff'));
  }
  const png=fs.readFileSync(path.join(root,'docs/images',v.name+'.png'));
  assert.equal(png.subarray(0,8).toString('hex'),'89504e470d0a1a0a');assert.equal(png.readUInt32BE(16),v.width);assert.equal(png.readUInt32BE(20),v.height);
  assert.equal((animated.match(/<animateMotion\b/g)||[]).length,arrows.length);
  for(const e of arrows){
    assert.equal(e.startBinding.elementId,e.customData.from);assert.equal(e.endBinding.elementId,e.customData.to);
    for(const binding of[e.startBinding,e.endBinding])assert(elements.find(n=>n.id===binding.elementId).boundElements.some(b=>b.id===e.id&&b.type==='arrow'));
    const points=e.points.map(([x,y])=>[x+e.x,y+e.y]);
    for(let p=1;p<points.length;p++){
      assert(points[p][0]===points[p-1][0]||points[p][1]===points[p-1][1],'Route is not orthogonal: '+e.id);
      for(const n of nodes.filter(n=>![e.customData.from,e.customData.to].includes(n.id)))assert(!crosses(points[p-1],points[p],n),'Arrow crosses resource: '+e.id+'/'+n.id);
      for(const t of texts)assert(!crosses(points[p-1],points[p],t),'Arrow crosses text: '+e.id+'/'+t.id);
    }
    const d=points.map((p,i)=>(i?'L':'M')+p.join(' ')).join(' ');
    assert(animated.includes(`id="route-${e.id}" class="edge-hit" d="${d}"`));
    assert(animated.includes(`<animateMotion path="${d}"`),'Motion must follow native points in their original direction');
    assert(e.customData.sources.length&&e.customData.note&&e.customData.kind);
  }
  checks.push({name:v.name,nodes:nodes.length,arrows:arrows.length,native_elements:elements.length,embedded_icons:Object.keys(s.files).length,passed:true});
}
assert.equal(checks.reduce((n,v)=>n+v.nodes,0),173);assert.equal(checks.reduce((n,v)=>n+v.arrows,0),151);
report.source_bindings=Object.fromEntries([...sources.entries()].sort());
const preview=JSON.parse(fs.readFileSync(path.join(root,'docs/images/platform-system.preview.json')));
const gif=fs.readFileSync(path.join(root,'docs/images/platform-system.gif'));
assert.equal(preview.runtime_screenshot,false);assert.equal(preview.runtime_telemetry,false);
assert.equal(preview.source_hash_encoding,'UTF-8 SVG with CRLF canonicalized to LF');
assert.equal(preview.source_sha256,sha(fs.readFileSync(path.join(root,'docs/images/platform-system.animated.svg'),'utf8').replaceAll('\r\n','\n')));
assert.equal(preview.sha256,sha(gif));assert.equal(preview.marker_count,56);
assert.equal(gif.subarray(0,6).toString(),'GIF89a');assert.equal(gif.readUInt16LE(6),1920);assert.equal(gif.readUInt16LE(8),1980);
assert.equal(preview.frames,36);assert.equal(preview.distinct_frames,36);assert.equal(preview.duration_ms,6000);
const readme=fs.readFileSync(path.join(root,'README.md'),'utf8'),picture=readme.match(/<picture>[\s\S]*?<\/picture>/)?.[0];
assert(picture?.includes('prefers-reduced-motion: reduce')&&picture.includes('docs/images/platform-system.png')&&picture.includes('docs/images/platform-system.gif'));
report.readme_preview={passed:true,frames:36,duration_ms:6000,markers:56,bytes:gif.length,sha256:preview.sha256};
report.previews=[];
for(const v of figures.views){
  const receipt=JSON.parse(fs.readFileSync(path.join(root,'docs/images',v.name+'.preview.json')));
  const bytes=fs.readFileSync(path.join(root,'docs/images',v.name+'.gif'));
  const width=Math.min(1920,v.width),height=Math.round(v.height*width/v.width);
  assert.equal(receipt.runtime_screenshot,false);assert.equal(receipt.runtime_telemetry,false);
  assert.equal(receipt.source,v.name+'.animated.svg');assert.equal(receipt.source_hash_encoding,'UTF-8 SVG with CRLF canonicalized to LF');
  assert.equal(receipt.source_sha256,sha(fs.readFileSync(path.join(root,'docs/images',v.name+'.animated.svg'),'utf8').replaceAll('\r\n','\n')));
  assert.equal(receipt.sha256,sha(bytes));assert.equal(receipt.bytes,bytes.length);assert.equal(receipt.marker_count,v.arrows.length);
  assert.equal(bytes.subarray(0,6).toString(),'GIF89a');assert.equal(bytes.readUInt16LE(6),width);assert.equal(bytes.readUInt16LE(8),height);
  assert.equal(receipt.width,width);assert.equal(receipt.height,height);assert.equal(receipt.frames,36);assert.equal(receipt.distinct_frames,36);assert.equal(receipt.duration_ms,6000);
  report.previews.push({name:v.name,passed:true,markers:v.arrows.length,sha256:receipt.sha256,bytes:bytes.length,width,height});
}

if(process.argv.includes('--browser')){
  const require=createRequire(path.join(process.env.DIAGRAM_NODE_MODULES||path.join(here,'node_modules'),'../package.json'));
  const {chromium}=require('playwright'),browser=await chromium.launch({headless:true});
  try{
    const context=await browser.newContext({viewport:{width:1600,height:1000},reducedMotion:'no-preference'}),page=await context.newPage();
    const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));
    await context.route(/^https?:/,route=>{requests.push(route.request().url());return route.abort();});
    await page.goto(pathToFileURL(path.join(here,'architecture.html')).href);await page.waitForSelector('article.active svg');
    const moving=[];
    for(let i=0;i<figures.views.length;i++){
      await page.selectOption('#view',String(i));assert.equal(await page.locator('#edge option').count(),expected[i][1].split(' ').length+1);
      const before=await page.locator('article.active .flow-particle').evaluateAll(es=>es.map(e=>{const m=e.getCTM();return[m.e,m.f];}));
      await page.waitForTimeout(320);
      const after=await page.locator('article.active .flow-particle').evaluateAll(es=>es.map(e=>{const m=e.getCTM();return[m.e,m.f];}));
      assert.equal(before.length,after.length);
      for(let n=0;n<before.length;n++)assert(Math.hypot(after[n][0]-before[n][0],after[n][1]-before[n][1])>1,'Particle did not advance: '+i+'/'+n);
      moving.push(after.length);
      for(const id of expected[i][1].split(' ')){await page.selectOption('#edge',id);assert((await page.locator('#detail-title').textContent()).startsWith(id+' · '));assert(await page.locator('#sources li').count()>0);}
    }
    await page.click('#play');assert.equal(await page.locator('#play').textContent(),'Play');
    const paused=await page.locator('article.active svg').evaluate(e=>e.getCurrentTime());await page.waitForTimeout(220);
    assert.equal(await page.locator('article.active svg').evaluate(e=>e.getCurrentTime()),paused);
    await page.selectOption('#speed','2');
    assert(await page.locator('animateMotion').evaluateAll(es=>es.every(e=>Math.abs(parseFloat(e.getAttribute('dur'))*2-Number(e.dataset.baseDuration))<0.001)));
    await page.locator('#zoom').fill('150');await page.locator('#zoom').dispatchEvent('input');assert.equal(await page.locator('#zoom-value').textContent(),'150%');
    await page.click('#fit');assert.equal(await page.locator('#zoom-value').textContent(),'100%');
    await page.selectOption('#view','5');await page.selectOption('#edge','');
    const click=await page.locator('article.active [data-edge="M5"] .edge-hit').evaluate(p=>{const q=p.getPointAtLength(40),m=p.getScreenCTM();return{x:m.a*q.x+m.c*q.y+m.e,y:m.b*q.x+m.d*q.y+m.f};});
    await page.mouse.click(click.x,click.y);assert.equal(await page.locator('#edge').inputValue(),'M5');
    await page.click('#play');assert.equal(await page.locator('#play').textContent(),'Pause');
    if(out)await page.screenshot({path:path.join(path.dirname(out),'viewer-desktop.png')});
    const reduced=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'}),mobile=await reduced.newPage();
    await reduced.route(/^https?:/,route=>{requests.push(route.request().url());return route.abort();});
    await mobile.goto(pathToFileURL(path.join(here,'architecture.html')).href);
    for(let i=0;i<figures.views.length;i++){
      await mobile.selectOption('#view',String(i));
      assert.equal(await mobile.locator('#play').textContent(),'Play');
      assert(await mobile.locator('article.active svg').evaluate(s=>s.animationsPaused()));
      assert.equal(await mobile.locator('article.active .flow-particle').first().evaluate(e=>getComputedStyle(e).display),'none');
      assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
    assert.equal(await mobile.locator('#play').textContent(),'Play');assert(await mobile.locator('article.active svg').evaluate(s=>s.animationsPaused()));
    assert.equal(await mobile.locator('article.active .flow-particle').first().evaluate(e=>getComputedStyle(e).display),'none');
    assert(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await mobile.click('#play');assert.equal(await mobile.locator('article.active .flow-particle').first().evaluate(e=>getComputedStyle(e).display),'block');
    if(out)await mobile.screenshot({path:path.join(path.dirname(out),'viewer-mobile.png'),fullPage:true});
    const hero=await context.newPage();hero.on('pageerror',e=>errors.push(e.message));
    const heroHTML='<style>body{margin:0}img{display:block;max-width:960px;height:auto}</style>'+picture;
    await hero.goto(pathToFileURL(path.join(root,'README.md')).href);await hero.setContent(heroHTML);
    await hero.locator('img').evaluate(e=>e.decode());
    assert((await hero.locator('img').evaluate(e=>e.currentSrc)).endsWith('platform-system.gif'));
    const first=await hero.locator('img').screenshot();await hero.waitForTimeout(550);
    const second=await hero.locator('img').screenshot();assert.notEqual(sha(first),sha(second),'README GIF must actually animate');
    if(out)await hero.screenshot({path:path.join(path.dirname(out),'readme-preview.png'),fullPage:true});
    const still=await reduced.newPage();still.on('pageerror',e=>errors.push(e.message));
    await still.goto(pathToFileURL(path.join(root,'README.md')).href);await still.setContent(heroHTML);await still.locator('img').evaluate(e=>e.decode());
    assert((await still.locator('img').evaluate(e=>e.currentSrc)).endsWith('platform-system.png'));
    const gifViews=[];
    for(const v of figures.views){
      await hero.setContent('<style>body{margin:0}img{max-width:1080px;height:auto}</style><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/images/'+v.name+'.png"><img src="docs/images/'+v.name+'.gif"></picture>');
      await hero.locator('img').evaluate(e=>e.decode());const a=await hero.locator('img').screenshot();await hero.waitForTimeout(550);
      assert.notEqual(sha(a),sha(await hero.locator('img').screenshot()),'GIF does not animate: '+v.name);
      await still.setContent('<picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/images/'+v.name+'.png"><img src="docs/images/'+v.name+'.gif"></picture>');
      await still.locator('img').evaluate(e=>e.decode());assert((await still.locator('img').evaluate(e=>e.currentSrc)).endsWith(v.name+'.png'));
      gifViews.push(v.name);
    }
    assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
    report.browser={passed:true,actual_moving_particles_by_view:moving,total:151,all_arrow_details:true,click_path:true,pause_resume:true,speed:true,zoom_fit:true,reduced_motion:true,mobile_layout:true,mobile_reduced_motion_views:figures.views.length,readme_gif_actually_animates:true,readme_reduced_motion_selects_png:true,all_gif_previews_animate:gifViews,all_gif_reduced_motion_fallbacks:true,external_requests:requests,page_errors:errors};
  }finally{await browser.close();}
}
report.passed=true;
if(out)fs.writeFileSync(out,JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify({passed:true,views:checks,nodes:173,arrows:151,source_files:sources.size,browser:report.browser},null,2));
