const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const {crawl}=require('./dfs-core.cjs');
function fixture(){
 let state='root';const order=[];
 const scenes={root:{text:'root',input:null,choices:[{label:'A',target:'a'},{label:'B',target:'b'}]},a:{text:'same visible text',input:null,choices:[{label:'A1',target:'a1'},{label:'Ask',target:'input'}]},a1:{text:'same visible text',input:null,choices:[]},b:{text:'B',input:null,choices:[]},input:{text:'Name?',input:'Type a name',choices:[]}};
 return{source:'synthetic-v1',order,start:async()=>structuredClone(scenes.root),observe:async()=>structuredClone(scenes[state]),checkpoint:async()=>({state}),restore:async point=>{state=point.state},perform:async choice=>{order.push(choice.label);state=choice.target}};
}
async function withDir(run){const dir=fs.mkdtempSync(path.join('/private/var/folders/w7/_qmfvgpj6bl11fmcq7sdf3t80000gn/T/opencode','atlas-dfs-test-'));try{await run(dir);}finally{fs.rmSync(dir,{recursive:true});}}
test('DFS order, durable clean records, no text-only merging, input blocked',()=>withDir(async directory=>{
 const adapter=fixture();const state=await crawl({adapter,directory});assert.deepEqual(adapter.order,['A','A1','Ask','B']);assert.equal(state.nodes.length,5);assert.equal(state.complete,false);assert.equal(state.nodes.find(node=>node.via==='Ask').status,'input_required');
 const tree=JSON.parse(fs.readFileSync(path.join(directory,'tree.json')));assert.equal(tree.choices[0].next.choices[0].next.name,'A1');assert(!JSON.stringify(tree).includes('observationHash'));assert(!JSON.stringify(tree).includes('same visible text'));assert(state.nodes.every(node=>!('text'in node)));assert.equal(state.nodes[1].textLength,state.nodes[2].textLength);
 assert(fs.existsSync(path.join(directory,'state.json')));assert(!fs.readdirSync(directory).some(name=>name.startsWith('checkpoint-')));
}));
test('resume processes remaining DFS frontier, not already visited choices',()=>withDir(async directory=>{
 const adapter=fixture();await crawl({adapter,directory,stepLimit:1});const state=await crawl({adapter,directory,resume:true});assert.deepEqual(adapter.order,['A','A1','Ask','B']);assert.equal(state.frontier.length,0);
}));
test('node/depth bounds explicitly incomplete',()=>withDir(async directory=>{
 const state=await crawl({adapter:fixture(),directory,maxNodes:2,maxDepth:1});assert.equal(state.complete,false);assert.equal(state.stopReason,'node_limit');assert(state.frontier.length>0);assert(state.nodes.some(node=>node.status==='depth_limit'));
}));
test('all mode exhausts displayed choices through requested depth, boundary is complete',()=>withDir(async directory=>{
 const state=await crawl({adapter:fixture(),directory,maxNodes:Infinity,maxDepth:1});assert.equal(state.complete,true);assert.equal(state.bounds.maxNodes,null);assert.equal(state.frontier.length,0);assert.equal(state.nodes.length,3);assert.equal(state.stopReason,'finished');
}));
test('mismatched restored observation is recorded, stale choice never clicked',()=>withDir(async directory=>{
 const adapter=fixture();adapter.restore=async()=>{};adapter.observe=async()=>({text:'changed',input:null,choices:[]});const state=await crawl({adapter,directory});assert.deepEqual(adapter.order,[]);assert.equal(state.nodes[0].choices[0].status,'replay_changed');assert.equal(state.complete,false);
}));
test('name-only records keep location labels but not narrative, gzip checkpoints resume',()=>withDir(async directory=>{
 const adapter=fixture();const baseObserve=adapter.observe;adapter.observe=async()=>{const value=await baseObserve();return{...value,name:value.text==='root'?'start':'room'};};adapter.start=async()=>({...await baseObserve(),name:'start'});
 const first=await crawl({adapter,directory,stepLimit:1});assert.equal(first.nodes[0].name,'start');assert.equal(first.nodes[1].name,'room');assert(fs.readdirSync(directory).some(name=>name.endsWith('.json.gz')));
 const state=await crawl({adapter,directory,resume:true});assert.equal(state.frontier.length,0);assert(state.nodes.every(node=>!('text'in node)));
}));
test('v1 migration preserves frontier and removes repeated narrative after verified backup',()=>withDir(async directory=>{
 const adapter=fixture();const state=await crawl({adapter,directory,stepLimit:1});const zlib=require('node:zlib');
 for(const task of state.frontier){const file=path.join(directory,task.point.file);const old=file.replace(/\.gz$/,'');if(!fs.existsSync(old))fs.writeFileSync(old,zlib.gunzipSync(fs.readFileSync(file)));task.point={...task.point,file:path.basename(old)};}
 state.version=1;for(const node of state.nodes)node.text='legacy narrative, do not repeat';
 const raw=Buffer.from(JSON.stringify(state));fs.writeFileSync(path.join(directory,'state.json'),raw);
 const resumed=await crawl({adapter,directory,resume:true});assert.equal(resumed.version,2);assert.equal(resumed.nodes.length,5);assert(resumed.nodes.every(node=>!('text'in node)));assert(zlib.gunzipSync(fs.readFileSync(path.join(directory,'pre-names-only-state.json.gz'))).equals(raw));
}));
