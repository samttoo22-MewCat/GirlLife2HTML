// Static tree browser checks: absolutely no engine/WASM/QSP requests.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('node:child_process');const {once}=require('node:events');
const assert=require('node:assert/strict');
const python=String.raw`
from tempfile import TemporaryDirectory
from pathlib import Path
from static_tree import Builder,dfs_all
from run_static_acyclic import acyclic_dfs
from extract import write_json
from server import serve
with TemporaryDirectory(prefix='static-story-test-',dir='/private/var/folders/w7/_qmfvgpj6bl11fmcq7sdf3t80000gn/T/opencode') as folder:
 p=Path(folder);(p/'static-story').mkdir();b=Builder()
 for name in ['start','room']:b.known_locations.add(name);b.node(name,declared=True)
 b.scan('start',"act 'Start': gt 'room','door'\nact 'Unknown': gt $where")
 b.scan('room',"if $args[0]='door':\nact 'Back': gt 'start'\nend")
 g=b.finish('start');order,_=dfs_all(g);kept,removed,_=acyclic_dfs(g);write_json(p/'graph.json',g);write_json(p/'static-story/graph.json',g);write_json(p/'static-story/branches-all.json',order);write_json(p/'static-story/branches-acyclic.json',kept);write_json(p/'static-story/branches-cycles-excluded.json',removed);serve(p,0)
`;
(async()=>{
 const proc=spawn('python3',['-u','-c',python],{cwd:__dirname,stdio:['ignore','pipe','pipe']});let browser;
 try{
  let stderr='';proc.stderr.on('data',data=>stderr+=data);
  const base=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Server startup timeout '+stderr)),15000);proc.stdout.on('data',data=>{const match=String(data).match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timer);resolve(match[0]);}});proc.once('exit',code=>{clearTimeout(timer);reject(new Error('Server exit '+code+' '+stderr));});});
  browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();const errors=[],engineRequests=[],external=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==base){external.push(url.origin);return route.abort();}if(url.pathname.startsWith('/player/')||/\.wasm|\.qsp/.test(url.pathname))engineRequests.push(url.pathname);return route.continue();});
  await page.goto(base);await page.locator('#entry:not([disabled])').waitFor();await page.locator('#entry').click();
  await page.locator('#choices').getByRole('button',{name:'Start',exact:true}).click();await page.locator('#title').filter({hasText:'room / door'}).waitFor();
  assert.equal(await page.locator('#choices').getByRole('button',{name:'Back',exact:true}).count(),0);await page.locator('#entry').click();assert.equal(await page.locator('#title').innerText(),'start');
  await page.locator('#choices').getByRole('button',{name:'Unknown',exact:true}).click();assert((await page.locator('#status').innerText()).includes('動態'));
  await page.locator('#nodes summary').first().click();await page.locator('#nodes ul ul').first().waitFor();
  assert.equal(await page.locator('iframe').count(),0);assert.equal(await page.locator('#source,#metrics,#code').count(),0);
  const downloaded=page.waitForEvent('download');await page.locator('#export').click();const file=await downloaded;assert.equal(file.suggestedFilename(),'branches-acyclic.json');
  const data=JSON.parse(require('node:fs').readFileSync(await file.path(),'utf8'));assert.deepEqual(data,[0,1]);assert(data.every(Number.isInteger));
  for(const width of [375,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);}
  assert.deepEqual(errors,[]);assert.deepEqual(engineRequests,[]);assert.deepEqual(external,[]);
  console.log('PASS: static start/options/return/tree, unresolved target notice, names-only JSON, three widths, no engine/WASM/QSP/external requests');
 }finally{if(browser)await browser.close();if(proc.exitCode===null){const ended=once(proc,'exit');proc.kill('SIGTERM');await ended;}}
})().catch(error=>{console.error(error);process.exitCode=1;});
