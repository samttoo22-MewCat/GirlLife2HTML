'use strict';
// Durable, bounded depth-first traversal. No text-only state merging.
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const zlib=require('node:zlib');
const hash=value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
function writeBytes(file,encoded){
 const free=fs.statfsSync(path.dirname(file));
 if(Number(free.bavail)*Number(free.bsize)-Buffer.byteLength(encoded)<512*1024*1024)throw new Error('Disk reserve reached; keeping 512 MiB free');
 const temp=file+'.tmp';const fd=fs.openSync(temp,'w',0o600);try{fs.writeFileSync(fd,encoded);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}fs.renameSync(temp,file);
}
function writeJson(file,value){writeBytes(file,JSON.stringify(value));}
function writeCheckpoint(file,value){writeBytes(file,zlib.gzipSync(Buffer.from(JSON.stringify(value))));}
function readCheckpoint(file){const bytes=fs.readFileSync(file);return JSON.parse(file.endsWith('.gz')?zlib.gunzipSync(bytes):bytes);}
function observationHash(observation){const{name,...original}=observation;return hash(original);}
function textLength(value){return Array.from(value||'').filter(char=>!(/\s/u.test(char))).length;}
function savedLocation(checkpoint){
 const data=Buffer.from(checkpoint.save||'','base64').toString('utf16le').split('\r\n');
 if(data[0]!=='QSPSAVEDGAME'||data.length<11)return null;
 return data[10].split('').map(char=>{const code=char.charCodeAt(0);return String.fromCharCode(code===65531?5:code?(code+5)&65535:0);}).join('');
}
function cleanTree(state,id=0){
 const node=state.nodes[id];
 return {name:node.name||node.via,option:node.via,status:node.status,choices:node.choices.map(choice=>({option:choice.label,status:choice.status,next:choice.child===undefined?null:cleanTree(state,choice.child)}))};
}
async function crawl({adapter,directory,maxNodes=300,maxDepth=30,resume=false,stepLimit=Infinity}){
 fs.mkdirSync(directory,{recursive:true});const manifest=path.join(directory,'state.json');
 if(fs.existsSync(manifest)&&!resume)throw new Error('Run exists: pass --resume or choose a new directory');
 let state;
 if(resume){state=JSON.parse(fs.readFileSync(manifest));if(state.source!==adapter.source)throw new Error('Source/bridge changed; cannot resume this run');}
 else state={version:2,source:adapter.source,created:new Date().toISOString(),nodes:[],frontier:[],errors:[],complete:false};
 if(state.version===1){
  const before=fs.readFileSync(manifest),backup=path.join(directory,'pre-names-only-state.json.gz');
  if(!fs.existsSync(backup)){const compressed=zlib.gzipSync(before);if(!zlib.gunzipSync(compressed).equals(before))throw new Error('Backup verification failed');writeBytes(backup,compressed);}
  for(const node of state.nodes){node.name=node.via;node.nameKind='choice_label';node.textLength=textLength(node.text);delete node.text;}
  const renamed=new Map();
  for(const task of state.frontier){
   const old=task.point.file;
   if(!old.endsWith('.gz')){
    const checkpoint=readCheckpoint(path.join(directory,old)),compressed=old+'.gz';
    if(!renamed.has(old)){writeCheckpoint(path.join(directory,compressed),checkpoint);renamed.set(old,compressed);}
    const owner=Number(old.match(/^checkpoint-(\d+)/)?.[1]),name=savedLocation(checkpoint);
    if(Number.isInteger(owner)&&state.nodes[owner]&&name){state.nodes[owner].name=name;state.nodes[owner].nameKind='location';}
    task.point={...task.point,file:compressed};
   }
  }
  state.version=2;state.migratedToNamesOnly=new Date().toISOString();
 }
 state.stopReason='running';state.bounds={maxDepth,maxNodes:Number.isFinite(maxNodes)?maxNodes:null};
 const persist=()=>{
  state.updated=new Date().toISOString();state.complete=state.frontier.length===0&&state.nodes.every(node=>node.status==='expanded'||node.status==='terminal'||(node.status==='depth_limit'&&node.depth===maxDepth))&&state.nodes.every(node=>node.choices.every(choice=>choice.status==='visited'||(choice.status==='depth_limit'&&node.depth===maxDepth)));
  writeJson(manifest,state);if(state.nodes.length)writeJson(path.join(directory,'tree.json'),cleanTree(state));
  // Delete only superseded checkpoints owned by THIS run, after durable manifest.
  const keep=new Set(state.frontier.map(task=>task.point.file));
   for(const name of fs.readdirSync(directory))if(/^checkpoint-\d+\.json(?:\.gz)?$/.test(name)&&!keep.has(name))fs.unlinkSync(path.join(directory,name));
 };
 async function add(observation,via,depth,parentPoint){
  const id=state.nodes.length;
  const node={id,name:observation.name||via,nameKind:observation.name?'location':'choice_label',via,depth,textLength:textLength(observation.text),observationHash:observationHash(observation),choices:observation.choices.map(choice=>({...choice,status:'pending'})),status:'expanded'};
  state.nodes.push(node);
  if(observation.input){node.status='input_required';node.textLength+=textLength(observation.input);return id;}
  if(!node.choices.length){node.status='terminal';return id;}
  if(depth>=maxDepth){node.status='depth_limit';node.choices.forEach(choice=>choice.status='depth_limit');return id;}
  let point=parentPoint;
  const checkpoint=await adapter.checkpoint();
  if(checkpoint){const file=`checkpoint-${id}.json.gz`;writeCheckpoint(path.join(directory,file),checkpoint);point={file,steps:[]};}
  if(!point){node.status='checkpoint_unavailable';node.choices.forEach(choice=>choice.status='checkpoint_unavailable');return id;}
  // Reverse-push so the first displayed choice is visited first, depth first.
  for(let index=node.choices.length-1;index>=0;index--)state.frontier.push({parent:id,index,point});
  return id;
 }
 if(!state.nodes.length)await add(await adapter.start(),'開始',0,null);
 persist();
 let steps=0;
 while(state.frontier.length&&state.nodes.length<maxNodes&&steps<stepLimit){
  const task=state.frontier[state.frontier.length-1];const parent=state.nodes[task.parent];const choice=parent.choices[task.index];
  const frontierBefore=state.frontier.slice(),nodeCount=state.nodes.length;
  try{
   const checkpoint=readCheckpoint(path.join(directory,task.point.file));
   await adapter.restore(checkpoint);
   for(const prior of task.point.steps)await adapter.perform(prior);
   const restored=await adapter.observe();
   if(observationHash(restored)!==parent.observationHash){choice.status='replay_changed';state.errors.push({parent:task.parent,choice:task.index,reason:'Restored text/options differ; stale choice was NOT clicked'});state.frontier.pop();persist();steps++;continue;}
   if(restored.name){parent.name=restored.name;parent.nameKind='location';}
   await adapter.perform(choice);
   const observation=await adapter.observe();
   state.frontier.pop();
   // Restoring this checkpoint then replaying these choices reconstructs a
   // pending modal which cannot itself be saved by QSP.
   const point={file:task.point.file,steps:[...task.point.steps,choice]};
   const child=await add(observation,choice.label,parent.depth+1,point);
   choice.child=child;choice.status='visited';persist();
  }catch(error){
   // A failed browser operation may have changed game state. Never continue
   // from that state: every remaining task starts with a saved checkpoint.
   state.frontier=frontierBefore;state.nodes.length=nodeCount;delete choice.child;
   state.errors.push({parent:task.parent,choice:task.index,reason:String(error.message)});
   if(error.fatal){choice.status='pending';state.stopReason='operation_timeout';persist();throw error;}
   choice.status='failed';
   const top=state.frontier[state.frontier.length-1];if(top===task)state.frontier.pop();persist();
  }
  steps++;
 }
 state.stopReason=state.frontier.length?(state.nodes.length>=maxNodes?'node_limit':'paused'):(state.complete?'finished':'unresolved_branches');persist();
 return state;
}
module.exports={crawl,cleanTree,writeJson};
