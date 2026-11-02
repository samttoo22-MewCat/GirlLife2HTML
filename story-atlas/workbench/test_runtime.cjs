// Real original QSP, local isolated engine only. No production saves or model calls.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.GL_STORY_URL||'http://127.0.0.1:56375';
assert.equal(new URL(base).hostname,'127.0.0.1');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],external=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{if(new URL(route.request().url()).origin===base)return route.continue();external.push(new URL(route.request().url()).origin);return route.abort();});
  await page.goto(base);await page.locator('#entry:not([disabled])').waitFor({timeout:120000});
  await page.locator('#entry').click();await page.locator('#choices button').first().waitFor();
  const root=await page.locator('#literal-list').textContent();
  await page.locator('#choices').getByRole('button',{name:'Quick Start',exact:true}).waitFor();
  await page.locator('#choices').getByRole('button',{name:'Quick Start',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='');
  const quick=await page.locator('#literal-list').textContent();
  assert(await page.locator('#nodes ul ul').count()>0);
  await page.locator('#entry').click();await page.waitForFunction(()=>document.querySelector('#status').textContent==='');
  assert.equal(await page.locator('#literal-list').textContent(),root);
  await page.locator('#choices').getByRole('button',{name:'Quick Start',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='');
  assert.equal(await page.locator('#literal-list').textContent(),quick);
  await page.locator('#entry').click();await page.waitForFunction(()=>document.querySelector('#status').textContent==='');
  await page.locator('#choices').getByRole('button',{name:'Continue',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='');
  assert.notEqual(await page.locator('#literal-list').textContent(),root);
  const download=page.waitForEvent('download');await page.locator('#export').click();
  const saved=await download;const fs=require('node:fs');const tree=JSON.parse(fs.readFileSync(await saved.path(),'utf8'));
  assert.equal(tree.option,'開始');assert(tree.choices.find(choice=>choice.text==='Quick Start').next);
  assert(tree.choices.find(choice=>choice.text==='Continue').next);
  assert(!JSON.stringify(tree).includes('snapshot'));
  for(const width of [375,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),width);}
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS real QSP: start/action continuation, branch tree, snapshot restoration, alternate branch, clean tree JSON, three widths; zero external requests/page errors');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
