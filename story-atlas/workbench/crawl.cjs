'use strict';
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const path=require('node:path');
const crypto=require('node:crypto');
const fs=require('node:fs');
const {crawl}=require('./dfs-core.cjs');
const args=process.argv.slice(2);
function option(name,fallback){const index=args.indexOf(name);return index<0?fallback:args[index+1];}
const base=option('--url','http://127.0.0.1:56375');
if(!['127.0.0.1','localhost'].includes(new URL(base).hostname))throw new Error('Local server required');
const directory=path.resolve(option('--output',path.join(__dirname,'data/dfs-runs',new Date().toISOString().replace(/[:.]/g,'-'))));
const maxNodes=option('--max-nodes','300')==='all'?Infinity:Number(option('--max-nodes','300')),maxDepth=Number(option('--max-depth','30'));
const stepLimit=args.includes('--step-limit')?Number(option('--step-limit')):Infinity;
if((maxNodes!==Infinity&&(!Number.isInteger(maxNodes)||maxNodes<1))||!Number.isInteger(maxDepth)||maxDepth<0)throw new Error('Invalid DFS bounds');
if(stepLimit!==Infinity&&(!Number.isInteger(stepLimit)||stepLimit<0))throw new Error('Invalid verification step limit');
async function response(url){const result=await fetch(url);if(!result.ok)throw new Error('Local server unavailable: '+result.status);return result;}
async function main(){
 fs.mkdirSync(directory,{recursive:true});const lock=path.join(directory,'run-lock.json');
 if(fs.existsSync(lock)){
  const previous=JSON.parse(fs.readFileSync(lock));
  try{process.kill(previous.pid,0);throw new Error('Another DFS process owns this run');}catch(error){if(error.code!=='ESRCH')throw error;}
  fs.unlinkSync(lock);
 }
 const lockFd=fs.openSync(lock,'wx',0o600);fs.writeFileSync(lockFd,JSON.stringify({pid:process.pid,started:new Date().toISOString()}));fs.closeSync(lockFd);
 let browser;
 try{
 const summary=await (await response(base+'/api/summary')).json();
 const bundle=Buffer.from(await(await response(base+'/player/assets/index-BuT4OyUC.js')).arrayBuffer());
 const bundleHash=crypto.createHash('sha256').update(bundle).digest('hex');
 browser=await chromium.launch({channel:'msedge',headless:true});
 let currentFrame;
 try{
  const page=await browser.newPage({viewport:{width:1280,height:900}});const external=[];
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.origin!==new URL(base).origin){external.push(url.origin);return route.abort();}
   if(url.pathname==='/player/assets/index-BuT4OyUC.js'){
    const original=await route.fetch();const body=await original.text();
    // Same read-only state query used by the engine's own debug callback.
    // Kept outside the default player response so existing checkpoints remain compatible.
    const nameBridge=`\nwindow.atlasNodeName=()=>{const module=qspApi$.value?.module;if(!module)return '';const line=s$1(module),action=s$1(module);try{return u(module,pointer=>module._getCurStateData(pointer,line,action));}finally{module._free(line);module._free(action);}};`;
    return route.fulfill({status:original.status(),contentType:'text/javascript',body:body+nameBridge});
   }
   return route.continue();
  });
  await page.goto(base);await page.locator('#entry:not([disabled])').waitFor({timeout:120000});
  currentFrame=page.frames().find(frame=>frame.url()===base+'/player/');
  if(!currentFrame)throw new Error('Runtime frame missing');
  async function operation(fn,arg){
   let timeout;
   try{return await Promise.race([currentFrame.evaluate(fn,arg),new Promise((_,reject)=>{timeout=setTimeout(()=>{const error=new Error('Local engine operation timed out; run paused for resume');error.fatal=true;reject(error);},20000);})]);}
   finally{clearTimeout(timeout);}
  }
  // All HTML stays inside an inert template; no source HTML is attached.
  const observe=async()=>operation(async()=>{
   const engine=window.atlasEngine;
   let last='',stable=0,state;
   for(let attempt=0;attempt<150;attempt++){
    state=engine.read();const serialized=JSON.stringify(state);stable=serialized===last?stable+1:0;last=serialized;
    if(stable>=3)break;
    if(attempt===149)throw new Error('Game state did not settle');
    await new Promise(resolve=>setTimeout(resolve,100));
   }
   engine.pause();
   const fragment=html=>{const template=document.createElement('template');template.innerHTML=html;for(const item of template.content.querySelectorAll('script,style,iframe,object,template'))item.remove();return template.content;};
   const plain=html=>{const doc=fragment(html);for(const item of doc.querySelectorAll('br,p,div,tr,li,h1,h2,h3'))item.before(document.createTextNode('\n'));for(const item of doc.querySelectorAll('td,th'))item.before(document.createTextNode(' '));return doc.textContent.replace(/[ \t]+/g,' ').replace(/\n[ \t]+/g,'\n').replace(/\n{3,}/g,'\n\n').trim();};
   const text=[plain(state.html),state.message?plain(state.message.html):''].filter(Boolean).join('\n\n');
   let choices=[];
   const name=window.atlasNodeName?.()||'';
   if(state.input)return{name,text,input:plain(state.input.html),choices};
   if(state.message)choices=[{label:'繼續',type:'confirm'}];
   else if(state.menu)choices=state.menu.map(item=>({label:plain(item.html)||'選項',type:'menu',index:item.index}));
   else choices=[...state.actions.map(item=>({label:plain(item.html)||'選項',type:'action',index:item.index})),...[...fragment(state.html).querySelectorAll('a')].map(link=>({label:link.textContent.trim()||link.getAttribute('title')||link.querySelector('img')?.getAttribute('alt')||'查看',href:link.getAttribute('href')||'',type:'link'})).filter(link=>link.href.toLowerCase().startsWith('exec:'))];
   return{name,text,input:null,choices};
  });
  const adapter={
   source:summary.source.sha256+':'+bundleHash+':dfs-v1',
   async start(){const state=await observe();const start=state.choices.find(choice=>choice.type==='action'&&choice.label==='Start');if(start)await this.perform(start);return observe();},
   async observe(){if(external.length){const error=new Error('External request blocked; stopping isolated run');error.fatal=true;throw error;}return observe();},
   async checkpoint(){return operation(()=>{
    const context=window.atlasEngine.checkpoint();if(!context)return null;
    const encode=buffer=>{const bytes=new Uint8Array(buffer);let result='';for(let index=0;index<bytes.length;index+=32768)result+=String.fromCharCode(...bytes.subarray(index,index+32768));return btoa(result);};
    return{save:encode(context.save),files:context.files.map(([key,bytes])=>[key,encode(bytes)])};
   });},
   async restore(context){await operation(async value=>{
    const decode=encoded=>Uint8Array.from(atob(encoded),char=>char.charCodeAt(0)).buffer;
    await window.atlasEngine.restoreCheckpoint({save:decode(value.save),files:value.files.map(([key,bytes])=>[key,decode(bytes)])});
   },context);},
   async perform(choice){await operation(value=>{
    const engine=window.atlasEngine;if(value.type==='action')engine.act(value.index);else if(value.type==='link')engine.link(value.href);else if(value.type==='menu')engine.choose(value.index);else if(value.type==='confirm')engine.confirm();else throw new Error('Unsupported choice; no automatic free-text input');
   },choice);await observe();}
  };
  const state=await crawl({adapter,directory,maxNodes,maxDepth,stepLimit,resume:args.includes('--resume')});
  const counts={};for(const node of state.nodes)counts[node.status]=(counts[node.status]||0)+1;
  console.log(JSON.stringify({directory,nodes:state.nodes.length,pending:state.frontier.length,complete:state.complete,stopReason:state.stopReason,statuses:counts,errors:state.errors.length,externalRequests:external.length},null,2));
 }finally{await browser.close();browser=null;}
 }finally{if(browser)await browser.close();if(fs.existsSync(lock)&&JSON.parse(fs.readFileSync(lock)).pid===process.pid)fs.unlinkSync(lock);}
}
main().catch(error=>{console.error('DFS stopped:',error.message,'Records:',directory);process.exitCode=1;});
