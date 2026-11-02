'use strict';
const $=id=>document.getElementById(id);
let graph,current,path=[],opened=false;
const nodes=new Map(),outgoing=new Map();
function el(tag,text){const item=document.createElement(tag);if(text!==undefined)item.textContent=text;return item;}
function button(label,action){const item=el('button',label);item.type='button';item.onclick=action;return item;}
function targetName(edge){return nodes.get(edge.target)?.name||'尚待解析';}
function label(edge){const template=document.createElement('template');template.innerHTML=(edge.option||targetName(edge)).replace(/<<[\s\S]*?>>/g,'〔動態文字〕');for(const item of template.content.querySelectorAll('script,style,iframe,object'))item.remove();return template.content.textContent.trim()||'查看';}
function treeItem(id,ancestors=[],depth=0,via=null){
 const node=nodes.get(id),li=el('li');const caption=via||node.name;
 li.append(button(caption,()=>{path=[...ancestors];select(id);}));
 if(ancestors.includes(id)){li.append(el('span',' ↩'));return li;}
 const edges=outgoing.get(id)||[];
 if(!edges.length)return li;
 const detail=el('details'),summary=el('summary','展開分支');detail.append(summary);let loaded=false;
 detail.ontoggle=()=>{
  if(!detail.open||loaded)return;loaded=true;const list=el('ul');
  for(const edge of edges){
   if(edge.target===null){const leaf=el('li',label(edge)+' · 待解析');list.append(leaf);}
   else list.append(treeItem(edge.target,[...ancestors,id],depth+1,label(edge)));
  }
  detail.append(list);
 };
 li.append(detail);return li;
}
function edgeButton(edge){
 return button(label(edge),()=>{
  if(edge.target===null){$('status').textContent='這個選項的目的地由遊戲動態決定，尚未解析。';return;}
  if(edge.resolution==='event_needs_review')$('status').textContent='原碼有引用這個事件，但尚未確認其定義。';
  else $('status').textContent='';
  select(edge.target);
 });
}
function select(id){
 if(!nodes.has(id))return;
 const prior=path.indexOf(id);path=prior>=0?path.slice(0,prior+1):[...path,id];
 current=id;$('title').textContent=nodes.get(id).name;
 const trail=el('nav');trail.setAttribute('aria-label','目前路徑');trail.className='path-links';
 path.forEach((node,index)=>trail.append(button(index===0?'開始':nodes.get(node).name,()=>{path=path.slice(0,index);select(node);})));$('literal-list').replaceChildren(trail);
 const edges=outgoing.get(id)||[];
 const choices=edges.filter(edge=>edge.kind==='choice'||edge.kind==='transition');
 const calls=edges.filter(edge=>edge.kind==='call');
 $('choices').replaceChildren(...(choices.length?choices.map(edgeButton):[el('p','這個節點沒有辨識到下一個選項。')]));
 $('related-choices').replaceChildren(...calls.map(edgeButton));$('related').hidden=!calls.length;
 if(!opened){const list=el('ul');list.append(treeItem(graph.root,[],0,'開始'));$('nodes').replaceChildren(list);opened=true;}
}
$('entry').onclick=()=>{
 path=[];$('status').textContent='';let id=graph.root;const seen=new Set();
 // Follow a single explicit startup transition; don't turn initializer calls
 // into fake player choices or evaluate any condition.
 while(!seen.has(id)){
  seen.add(id);const edges=outgoing.get(id)||[];
  if(edges.some(edge=>edge.kind==='choice'))break;
  const next=edges.filter(edge=>edge.kind==='transition'&&edge.target!==null);
  if(next.length!==1)break;
  path.push(id);id=next[0].target;
 }
 select(id);
};
$('export').onclick=async()=>{
 try{
  const response=await fetch('/download/branches-acyclic.json');if(!response.ok)throw new Error('分支編號尚未備妥。');
  const url=URL.createObjectURL(await response.blob());const link=el('a');link.href=url;link.download='branches-acyclic.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(error){$('status').textContent=error.message;}
};
(async()=>{
 $('status').textContent='載入節點關係…';const response=await fetch('/api/static-graph');if(!response.ok)throw new Error('節點資料尚未備妥。');graph=await response.json();
 for(const node of graph.nodes)nodes.set(node.id,node);
 for(const edge of graph.edges){if(!outgoing.has(edge.source))outgoing.set(edge.source,[]);outgoing.get(edge.source).push(edge);}
 $('entry').disabled=false;$('export').disabled=false;$('status').textContent='';
})().catch(error=>{$('status').textContent=error.message;});
