'use strict';
const $=id=>document.getElementById(id);
let csrf='',runId='',batch=null,dictionary=[],busy=false,dirty=false;
let settings=null,systemDirty=false,authorized=false;
let approvedDictionary=[];
let batchRunning=false,stopRequested=false,remainingBatches=null;
let remoteBusy=false,activityPolling=false,activitySignature='',controlledCompleted=null,controlledRefreshPending=false;
let testRoundPolling=false;
let branchBusy=false,branchEnabled=false,branchSession='',branchPolling=false,branchCompleted=null,branchRefreshPending=false;
async function syncBranches(){
 if(!runId||branchPolling)return;branchPolling=true;
 try{
  const data=await api('branches/status?run_id='+encodeURIComponent(runId));branchEnabled=data.enabled;branchBusy=['running','unconfirmed'].includes(data.state);branchSession=data.session_id||'';
  if(branchBusy&&data.concurrency)$('branch-limit').value=String(data.concurrency);
  const words={idle:'尚未開始',running:'執行中',complete:'本次批數已完成',paused:'已暫停',blocked:'部分分支失敗，結果已保留',exhausted:'目前可執行分支已走完，匯合點仍另列',unconfirmed:'程序狀態未確認，不自動重送',failed:'排程失敗'};
   const c=data.counts||{};$('branch-status').textContent=`${words[data.state]||data.state}${data.requested?` · 已提交 ${data.completed} / ${data.requested} 批 · 同時 ${c.running||0} / ${data.concurrency} 支 · 排隊 ${c.ready||0} · 等待 ${c.waiting||0} · 匯合待確認 ${c.context_review||0} · 失敗 ${c.failed||0}`:''}${data.error?' · '+data.error:''}`;
  if(data.request_gate){const g=data.request_gate;$('branch-status').textContent+=` · 模型請求 ${g.active_requests} / ${g.effective_concurrency} · 請求排隊 ${g.queued_requests}${g.rpm?' · 金鑰上限 '+g.rpm+'次／分鐘':''}`;}
  const labels={ready:'排隊',running:'處理中',waiting:'等待共同節點',context_review:'匯合語境待確認',failed:'失敗保留'};
  $('branch-jobs').replaceChildren(...(data.jobs||[]).map(job=>{const row=el('article');row.append(el('p',`${job.node} · ${job.stage||labels[job.state]||job.state}${job.error?' · '+job.error:''}`));if(job.batch_id)row.append(button('查看本批',async()=>{requireSaved();await loadBatch(job.batch_id);}));return row;}));
  $('stop-branches').hidden=data.state!=='running';
  const changed=branchCompleted!==null&&branchCompleted!==data.completed;branchCompleted=data.completed;
  if(changed)branchRefreshPending=true;
  lockBulkEditors();
  if(branchRefreshPending&&!busy&&!batchRunning&&!dirty&&!systemDirty){branchRefreshPending=false;if(batch)await loadBatch(batch.id);else await progress();await glossary();}
 }catch(error){$('branch-status').textContent='分支狀態暫時無法更新：'+error.message+'；不會重送。';}
 finally{branchPolling=false;}
}
async function syncTestRound(){
 if(testRoundPolling)return;testRoundPolling=true;
 try{
  const data=await api('test-round-status');
  $('test-round-title').textContent=data.isolated===false?'正式批量執行（結果直接保存）':'實測進度（不改正式進度）';
  const labels={running:'進行中',created:'準備中',complete:'已完成',failed:'已停止',handed_over:'已切換至分支佇列',unconfirmed:'執行狀態未確認',idle:'尚無實測'};
  $('test-round-status').textContent=data.count?`${labels[data.state]||data.state}：${data.completed} / ${data.count} 批${data.current_ordinal?' · 第 '+data.current_ordinal+' 批':''} · ${data.stage}${data.error?' · '+data.error:''}`:'尚無隔離實測。';
  $('test-round-progress').max=Math.max(1,data.count);$('test-round-progress').value=data.completed;
  $('test-round-detail').textContent=data.count?`本輪 ${data.calls} 次模型請求 · 已回報 US$${Number(data.reported_cost||0).toFixed(6)}（不含尚未回報的用量） · 最後狀態更新 ${new Date(data.updated).toLocaleTimeString()} · 每2.5秒查詢，刷新不會重送。`:'';
 }catch(error){$('test-round-status').textContent='實測狀態暫時無法更新：'+error.message+'；不會重送模型請求。';}
 finally{testRoundPolling=false;}
}
function updateGenerate(){const draft=batch?.status==='draft',locked=batchRunning||remoteBusy||branchBusy;$('generate-batch').disabled=locked||!authorized||!draft;$('generate-test').disabled=locked||!settings;$('preview-test').disabled=locked||!settings;$('generate-bulk').disabled=locked||branchEnabled||!authorized||!settings||remainingBatches===0;$('start-branches').disabled=locked||!authorized||!settings;}
function el(tag,text){const item=document.createElement(tag);if(text!==undefined)item.textContent=text;return item;}
function showLookup(result){const lookup=result.dictionary_snapshot||{search:result.dictionary_search,entries:result.dictionary};if(!lookup.search)return;const box=$('request-dictionary');box.replaceChildren(el('p',`已先搜尋 ${lookup.search.searched_units} 段原文，字典 v${lookup.search.version}，命中 ${lookup.search.matched_entries} 個已核准條目。`));for(const term of lookup.entries||[]){box.append(el('p',`${term.source} → ${term.target}｜${term.sense}｜${term.usage}${term.term_type==='name'?'｜人名保留原文':''}`));}if(!lookup.entries?.length)box.append(el('p','查無相關已核准詞條，本次提供空字典，不假裝已有譯法。'));}
async function showProposals(){
 const data=await api('dictionary-results'+(batch?'?batch_id='+batch.id:''));
 if(runId&&!batch){data.proposals=[];data.counts={approved:0,rejected:0,candidate:0,added:0};data.review=null;}
 const pending=data.counts.candidate+data.counts.added;
  $('dictionary-summary').textContent=`正式字典 ${data.approved_entries.length} 條｜本批審詞核准 ${data.counts.approved} 條、未採用 ${data.counts.rejected} 條、待自動審詞 ${pending} 條｜版本 ${data.version}`;
 approvedDictionary=data.approved_entries;renderDictionaryContent();
 const kinds={add_term:'新詞',add_sense:'新詞義',add_variant:'新譯法'};
  $('glossary-proposals').replaceChildren(...(data.proposals.length?data.proposals.map(p=>{const card=el('article');card.className='unit-card';card.append(el('h4',`${p.source} → ${p.target}`),el('p',`${kinds[p.action]||'用詞'}｜${p.sense}｜${p.usage}`),el('p',p.reason),el('p',(p.status==='approved'?'已核准，已入正式字典':p.status==='rejected'?'審詞未採用':'待預設自動審詞')+(p.review_reason?'：'+p.review_reason:'')));return card;}):[el('p','本批尚無新增用詞提案。')]));
 $('review-output').value=JSON.stringify(data,null,2);renderRetained();lockBulkEditors();showBudget(batch?.budget_plan);
  const reviewer=data.review?.model?.includes('pro')?'Pro':data.review?.model?.includes('flash')?'Flash':'模型';
  $('review-status').textContent=data.review?.state==='failed'?reviewer+' 審詞失敗：'+data.review.error+'；提案已保存，後續測試／翻譯自動續審。':data.review?.state==='pending'?reviewer+' 審詞處理中。':data.review?.state==='complete'?reviewer+' 自動審詞結果已保存。':pending?'待審詞已保存，後續測試／翻譯會自動審詞，不需另外按核准。':'字典結果已載入。';
}
function showReview(result){const review=result.glossary_review||result;const text=review.state==='failed'?(review.error+'；'+review.notice):(review.notice||'');$('review-status').textContent=text;return text;}
function modelUsage(result){const part=result.adaptive_summary;return (part?` 本次 ${part.requests} 個子請求，沿用 ${part.resumed_units} 段；`:'')+(result.usage?' 本次翻譯合計用量：'+JSON.stringify(result.usage):'')+(result.correction_attempts?' 局部修正 '+result.correction_attempts+' 次，用量：'+JSON.stringify(result.correction_usage):'');}
function showBudget(plan){if(!plan){$('token-budget').textContent='';return;}if(plan.adaptive_enabled===false){$('token-budget').textContent=`拆包暫停 · 每批一次送出 · max_tokens=${plan.profile.output_limit.toLocaleString()}（含推理）`;return;}const values=plan.estimates.map(e=>e.estimated_output_tokens);$('token-budget').textContent=plan.error?'預算檢查：'+plan.error:`整批初始預估 ${plan.parts} 包 · 每包輸出預估最多 ${Math.max(0,...values).toLocaleString()} / ${plan.profile.output_limit.toLocaleString()} token（含推理；續跑依保留結果調整，非精確值）`;}
function lockBulkEditors(){
 const locked=batchRunning||remoteBusy||branchBusy;
 if(batch?.approval_mode==='automatic'&&batch.status==='approved')$('batch-title').textContent=`第 ${batch.ordinal} 批 · 已提交（機器檢查，非人工審閱）`;
 for(const input of document.querySelectorAll('#draft-units .target-editor,#draft-units .decision-note'))input.readOnly=locked||batch?.status==='approved';
 for(const input of document.querySelectorAll('#draft-units .decision-terms'))input.disabled=locked||batch?.status==='approved';
 $('system-prompt').readOnly=locked;$('bulk-count').disabled=locked;$('confirm-bulk').disabled=locked;$('stop-bulk').hidden=!batchRunning;
 for(const id of ['save-draft','refresh-dictionary','build-prompt'])$(id).disabled=locked||batch?.status!=='draft';
 $('approve-next').disabled=locked||batch?.status!=='draft'||!!batch?.retained_results?.some(r=>!r.test);
 $('prepare').disabled=locked||branchEnabled;for(const id of ['save-system','default-system','branch-count','branch-limit','confirm-branches'])$(id).disabled=locked;updateGenerate();
}
async function syncActivity(){
 if(!runId){$('flow-status').textContent='尚無執行中的流程。';return;}
 if(activityPolling)return;activityPolling=true;
 try{
  const data=await api('activity?run_id='+encodeURIComponent(runId));const wasBusy=remoteBusy;remoteBusy=data.blocked;
  const prefix=data.current?`第 ${data.current.ordinal} 批 · `:'';
  const outcome=data.state==='running'?`${data.stage}執行中（伺服器）`:data.state==='unconfirmed'?`${data.stage}狀態未確認；不重送，需確認先前請求`:data.state==='failed'?`${data.stage}失敗：${data.current.error||'請查看流程紀錄'}`:data.state==='submitted'?'已提交':data.current?.kind==='test'?'測試已完成':data.current?'結果已保存；未自動續跑':'尚無模型請求';
  $('flow-status').textContent=prefix+outcome;
  const labels={pending:'等待回覆',needs_review:'未通過',failed:'失敗',saved_draft:'譯文已保存',test_complete:'測試完成',complete:'審詞完成'};
  $('flow-history').replaceChildren(...data.requests.map(r=>el('li',`第 ${r.ordinal} 批 · ${r.stage} · ${labels[r.state]||r.state} · ${r.created||''} UTC${r.error?' · '+r.error:''}`)));
  lockBulkEditors();
  const signature=JSON.stringify([data.state,data.current?.kind,data.current?.id,data.current?.state,data.batch?.revision]);
  const advanced=data.controlled_run&&data.controlled_run.isolated===false&&controlledCompleted!==null&&controlledCompleted!==data.controlled_run.completed;
  if(data.controlled_run)controlledCompleted=data.controlled_run.completed;
  if((wasBusy&&!remoteBusy&&signature!==activitySignature)||advanced)controlledRefreshPending=true;
  if(controlledRefreshPending&&!busy&&!batchRunning&&!dirty&&!systemDirty){controlledRefreshPending=false;await restoreWorkspace();await glossary();}
  activitySignature=signature;
 }catch(error){$('flow-status').textContent='流程狀態暫時無法更新：'+error.message+'；不會重送模型請求。';}
 finally{activityPolling=false;}
}
function renderRetained(){
 const box=$('retained-results'),results=batch?.retained_results||[];box.replaceChildren();box.hidden=!results.length;
 if(results.some(r=>!r.test))$('approve-next').disabled=true;
 for(const result of results){
  const card=el('article');card.className='retained-result';
  card.append(el('h3',`${result.test?'測試':'正式翻譯'}未通過 · 結果已保留 · 局部修正 ${result.repair_attempts} 次`),el('p',result.issues.general),el('p','未核准、未推進。相同版本與設定再次送出時，優先沿用保留結果，只修尚有問題的部分。'));
  const adaptive=result.issues.adaptive;if(adaptive){card.append(el('p',`自適應分包：${adaptive.requests} 個子請求，${adaptive.adaptive_rounds||0} 輪縮包；已回報用量 ${JSON.stringify(adaptive.reported_usage||{})}${adaptive.usage_incomplete?'（供應商未回報完整用量）':''}`));if(adaptive.metadata_incomplete)card.append(el('p','⚠ 新詞提案尚未完整補回，不代表沒有新詞；譯文已保留。'));}
  if(result.revision!==batch.revision)card.append(el('p','這是較早版本的保留結果，不會自動覆寫目前草稿。'));
  const bad=new Set((result.issues.units||[]).map(i=>i.id)),good=new Set(result.issues.valid_unit_ids||[]);
  for(const issue of result.issues.units||[]){const n=batch.units.findIndex(u=>u.id===issue.id)+1;card.append(el('p',`⚠ 第 ${n||'?'} 段需修正：${issue.error}`));}
  for(const issue of result.issues.proposals||[])card.append(el('p',`⚠ 用詞 ${issue.proposal?.source||''} 需修正：${issue.error}`));
  const units=Array.isArray(result.output?.translations)?result.output.translations:[];
  const detail=el('details');detail.append(el('summary',`查看全部保留譯文（${units.length} 段，問題段落另有標記）`));
  for(const unit of units){if(!unit||typeof unit!=='object')continue;const n=batch.units.findIndex(u=>u.id===unit.id)+1;const row=el('p',`${bad.has(unit.id)?'⚠ 需修正':good.has(unit.id)?'✓ 已驗證保留':'待確認'} · 第 ${n||'?'} 段：${unit.target??''}`);row.className=bad.has(unit.id)?'retained-issue':'retained-text';detail.append(row);}
  if(result.output?.raw_output)detail.append(el('pre',String(result.output.raw_output)));
  card.append(detail);box.append(card);
 }
}
function renderDictionaryContent(){const query=$('dictionary-content-search').value.trim().toLocaleLowerCase();const matches=approvedDictionary.filter(t=>[t.source,t.target,t.sense,t.usage].some(v=>String(v).toLocaleLowerCase().includes(query)));$('dictionary-content-count').textContent=`正式字典 ${approvedDictionary.length} 條`;$('dictionary-content-matches').textContent=query?`找到 ${matches.length} 條，共 ${approvedDictionary.length} 條`:`共 ${approvedDictionary.length} 條，包含同詞不同詞義與譯法`;$('dictionary-approved').replaceChildren(...(matches.length?matches.map(t=>{const card=el('article');card.className='unit-card';card.append(el('h4',`${t.source} → ${t.target}`),el('p',`詞義：${t.sense}`),el('p',`語境：${t.usage}`));if(t.term_type==='name')card.append(el('p','人物姓名 · 保留原文'));return card;}):[el('p',query?'沒有符合的詞條。':'目前尚無已入字典的詞條。')]));}
$('dictionary-content-search').oninput=renderDictionaryContent;
function status(text){$('work-status').textContent=text;}
async function api(path,body){const response=await fetch('/api/workflow/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Atlas-CSRF':csrf},body:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw new Error(value.error||'操作未完成');return value;}
async function action(fn){if(busy)return;busy=true;status('處理中…');try{await fn();}catch(error){status(error.message);}finally{busy=false;}}
function button(text,fn){const item=el('button',text);item.type='button';item.onclick=()=>action(fn);return item;}
function tab(name){$('tab-'+name).click();updateGenerate();}
for(const item of document.querySelectorAll('[role=tab]'))item.onclick=()=>{for(const other of document.querySelectorAll('[role=tab]')){const active=other===item;other.setAttribute('aria-selected',String(active));other.tabIndex=active?0:-1;$(other.getAttribute('aria-controls')).hidden=!active;}};
document.querySelector('.tabs').onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;const tabs=[...document.querySelectorAll('[role=tab]')],index=tabs.indexOf(document.activeElement);if(index<0)return;event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();};
function requireSaved(){if(dirty)throw new Error('請先儲存目前草稿，再切換或更新批次。');}
async function bootstrap(){const data=await api('bootstrap');csrf=data.csrf;$('notice').textContent=data.notice;$('candidate-size').textContent=`候選主線約 ${data.candidate_characters.toLocaleString()} 字元，${data.candidate_nodes} 個事件；${data.catalog.review||0} 個原文單位仍需抽取審核。`;$('candidate-path').replaceChildren(...data.candidate_path.map(node=>el('li',node.name)));if(data.runs.length>1)throw new Error('存在多份翻譯進度，請先確認保留資料。');runId=data.runs[0]?.id||'';$('prepare').disabled=false;}
async function progress(){
 if(!runId)return;const data=await api('run?id='+encodeURIComponent(runId));remainingBatches=data.remaining_batches;$('bulk-count').max=Math.max(1,remainingBatches);
  $('overall-progress').textContent=data.remaining_batches===null?`分支模式 · 已完成 ${data.approved_batches} 批 · 已建立 ${data.total_batches} 批`:`已完成 ${data.approved_batches} / ${data.total_batches} 批 · 目前第 ${data.current_batch} 批`;$('overall-progress').title=data.progress_scope;
 $('overall-progress-bar').max=Math.max(1,data.total_batches);$('overall-progress-bar').value=data.approved_batches;
  $('run-progress').textContent=data.remaining_batches===null?`已提交 ${data.approved_units} 段；分支尚未全展開，最終總批數未定。`:`已提交 ${data.approved_units} 段 · 共 ${data.total_batches} 批 · 剩餘 ${data.remaining_batches} 批`;
 $('batches').replaceChildren(...data.batches.map(row=>{const label=row.status!=='approved'?'草稿':row.approval_mode==='automatic'?'已提交（機器檢查）':'已核准';const item=button(`第 ${row.ordinal} 批 · ${label}`,async()=>{requireSaved();await loadBatch(row.id);status('已載入批次。');});item.setAttribute('aria-current',String(batch?.id===row.id));return item;}));
 $('merge-list').replaceChildren(...(data.merge_reviews.length?data.merge_reviews.map(item=>el('p',item.node+' · 需確認能否沿用譯文')):[el('p','目前沒有待確認匯合點。')]));await showProposals();
}
function cards(items,reference=false){
 if(!reference){$('confirm-model').checked=false;$('confirm-test').checked=false;$('test-request').value='';$('test-output').value='';$('test-counts').textContent='';$('test-status').textContent='';$('generation-result').textContent='';updateGenerate();}
 return items.map((unit,index)=>{
 const article=el('article');article.className='unit-card';article.append(el('h3',`段落 ${index+1} · ${unit.characters} 字元`));const columns=el('div');columns.className='unit-columns';const source=el('p',unit.source);source.className='source-text';const label=el('label',reference?'已核准譯文':'繁體中文譯文');const target=el('textarea');target.className='target-editor';target.value=unit.target;target.rows=5;target.dataset.unit=unit.id;target.readOnly=reference||batch.status==='approved';target.setAttribute('aria-label',(reference?'參考譯文':'段落譯文')+' '+(index+1));const changed=()=>{dirty=true;status('草稿有修改，尚未儲存。');};if(!reference)target.oninput=changed;label.append(target);columns.append(source,label);article.append(columns);
 if(!reference){const decision=batch.decisions[unit.id]||{term_ids:[],note:''};const detail=el('details');detail.append(el('summary','本段用詞與疑義'));const chooseLabel=el('label','採用的字典譯法（可複選）');const choose=el('select');choose.multiple=true;choose.className='decision-terms';choose.dataset.unit=unit.id;choose.disabled=batch.status==='approved';choose.setAttribute('aria-label','段落 '+(index+1)+' 採用譯法');for(const term of batch.dictionary){const option=new Option(`${term.source} → ${term.target} · ${term.sense}`,String(term.id));option.selected=decision.term_ids.includes(term.id);choose.append(option);}choose.onchange=changed;chooseLabel.append(choose);const noteLabel=el('label','疑義／新譯法候選');const note=el('textarea');note.rows=2;note.maxLength=2000;note.className='decision-note';note.dataset.unit=unit.id;note.value=decision.note||'';note.readOnly=batch.status==='approved';note.oninput=changed;noteLabel.append(note);detail.append(chooseLabel,noteLabel);article.append(detail);}
 return article;
});}
async function loadBatch(id){batch=await api('batch?id='+id);dirty=false;$('batch-title').textContent=`第 ${batch.ordinal} 批 · ${batch.status==='approved'?'已核准':'待翻譯／審核'}`;$('batch-counts').textContent=`待翻 ${batch.source_characters.toLocaleString()} 字元｜參考 ${batch.reference_characters.toLocaleString()} 字元｜字典 v${batch.dictionary_version}`;$('batch-empty').textContent=batch.oversized?'本批有超過一萬字元的完整單位，需要人工確認安全切分，尚未自動切斷。':'原文保留片段以 ⟪P數字⟫ 標示；翻譯時請完整保留且不要改動順序。';$('draft-units').replaceChildren(...cards(batch.units));$('reference-units').replaceChildren(...(batch.references.length?cards(batch.references,true):[el('p','本批沒有已核准的共同前文。')]));$('reviewed').checked=false;$('prompt-text').value='';$('copy-prompt').disabled=true;for(const id of['save-draft','approve-next','refresh-dictionary','build-prompt'])$(id).disabled=batch.status!=='draft';$('term-candidates').replaceChildren(...batch.term_candidates.map(term=>button(`${term.source} (${term.occurrences})`,async()=>{$('term-form').reset();$('term-id').value='';$('term-source').value=term.source;tab('dictionary');$('term-sense').focus();status('請補上詞義、譯法與適用語境；可為同一詞保存多個譯法。');})));await progress();}
async function prepare(){requireSaved();if(!runId)runId=(await api('run',{})).id;const next=await api('prepare',{run_id:runId});if(next.id){await loadBatch(next.id);tab('draft');status('已準備下一批；尚未送出模型。');}else{await progress();status(next.status==='context_review'?'可處理的新節點已走完；匯合點仍需確認，不能算全遊戲翻譯完成。':'目前起點可處理的文字已走完；未解析及待審原文仍另列。');}}
async function save(){if(!batch)throw new Error('請先選擇批次');const targets={},decisions={};for(const input of $('draft-units').querySelectorAll('.target-editor'))targets[input.dataset.unit]=input.value;for(const input of $('draft-units').querySelectorAll('.decision-terms'))decisions[input.dataset.unit]={term_ids:[...input.selectedOptions].map(item=>Number(item.value)),note:''};for(const input of $('draft-units').querySelectorAll('.decision-note'))decisions[input.dataset.unit].note=input.value;const saved=await api('save',{batch_id:batch.id,revision:batch.revision,targets,decisions});batch.revision=saved.revision;dirty=false;status('草稿與用詞紀錄已儲存在遠端，尚未核准或推進。');}
function clearBatch(){batch=null;dirty=false;$('draft-units').replaceChildren();$('reference-units').replaceChildren();$('batch-title').textContent='準備下一批開始試譯';$('batch-counts').textContent='';$('prompt-text').value='';$('prepare').disabled=!runId;for(const id of['save-draft','approve-next','refresh-dictionary','build-prompt','copy-prompt'])$(id).disabled=true;$('confirm-test').checked=false;$('test-request').value='';$('test-output').value='';$('test-counts').textContent='';$('test-status').textContent='';updateGenerate();}
$('prepare').onclick=()=>action(prepare);
$('save-draft').onclick=()=>action(save);
$('approve-next').onclick=()=>action(async()=>{if(!$('reviewed').checked)throw new Error('請先勾選已完成原文對照審核。');await save();await api('approve',{batch_id:batch.id,revision:batch.revision,reviewed:true});await loadBatch(batch.id);await prepare();window.scrollTo({top:0,left:0,behavior:'auto'});});
async function glossary(){const data=await api('glossary');dictionary=data.entries;$('dict-version').textContent=`字典版本 ${data.version}；同一詞可有多個詞義／譯法。每次送出前自動搜尋最新已核准詞條；既有已用快照留存。`;renderTerms();}
const typeLabel=el('label','詞條類型');const typeSelect=el('select');typeSelect.id='term-term_type';typeSelect.append(new Option('一般用詞','general'),new Option('人物姓名（保留原文）','name'));typeLabel.append(typeSelect);$('term-form').querySelector('.form-grid').append(typeLabel);
function renderTerms(){const term=$('term-search').value.toLocaleLowerCase();$('glossary-list').replaceChildren(...dictionary.filter(item=>(item.source+' '+item.sense+' '+item.target).toLocaleLowerCase().includes(term)).map(item=>{const article=el('article');article.append(el('strong',item.source+' → '+item.target),el('p',item.sense+' · '+(item.status==='approved'?'已核准':'候選')+(item.term_type==='name'?' · 人名保留原文':'')),el('p',item.usage));article.append(button('編輯這個譯法',async()=>{for(const field of['id','source','sense','target','usage','status','term_type'])$('term-'+field).value=item[field]||'general';$('term-source').focus();status('編輯現有譯法；若要增加變體，請按「新增另一個譯法」。');}));return article;}));}
$('term-search').oninput=renderTerms;
$('clear-term').onclick=()=>{$('term-form').reset();$('term-id').value='';};
$('term-form').onsubmit=event=>{event.preventDefault();action(async()=>{const values={};for(const field of['source','sense','target','usage','status','term_type'])values[field]=$('term-'+field).value;if($('term-id').value)values.id=Number($('term-id').value);await api('term',values);await glossary();$('term-form').reset();$('term-id').value='';status('譯法已保存；後續每次送出都會先搜尋最新已核准字典。');});};
$('refresh-dictionary').onclick=()=>action(async()=>{requireSaved();await api('refresh-dictionary',{batch_id:batch.id});await loadBatch(batch.id);status('本批已更新字典快照，版本與舊快照均有記錄。');});
$('build-prompt').onclick=()=>action(async()=>{requireSaved();requireSystemSaved();const result=await api('prompt?id='+batch.id);if(result.settings_revision!==settings.revision)throw new Error('System prompt 版本已變更，請重新載入。');$('prompt-text').value=result.prompt;showLookup(result);$('copy-prompt').disabled=false;updateGenerate();status('已先搜尋字典並組裝含固定 JSON Schema 的完整請求；尚未呼叫模型。');});
$('copy-prompt').onclick=()=>action(async()=>{try{await navigator.clipboard.writeText($('prompt-text').value);status('已複製提示。');}catch{ $('prompt-text').focus();$('prompt-text').select();status('瀏覽器未允許自動複製；已選取提示，請使用複製快捷鍵。'); }});
function requireSystemSaved(){if(systemDirty)throw new Error('請先保存 System prompt，再組裝或送出本批。');}
async function authStatus(){const data=await api('auth/status');authorized=data.authorized;const configured=data.credential_source==='configured_api_key';$('auth-state').textContent=configured?'已連接 · 指定 API 金鑰':authorized?'已授權 · 僅手動送出本批':'未授權 · 不會呼叫模型';$('oauth-disconnect').disabled=!authorized||configured;$('oauth-disconnect').hidden=configured;$('oauth-connect').hidden=configured;$('oauth-connect').textContent=authorized?'重新授權（會建立新金鑰）':'使用 BazaarLink 授權';$('oauth-notice').textContent=data.notice;updateGenerate();}
function showSettings(data){settings=data;systemDirty=false;$('system-prompt').value=data.system_prompt;$('system-version').textContent=`已保存版本 v${data.revision}；修改不影響既有草稿，每次送出保存請求快照。`;$('format-contract').textContent=data.format_contract;}
$('system-prompt').oninput=()=>{systemDirty=true;$('prompt-text').value='';$('copy-prompt').disabled=true;$('system-version').textContent='System prompt 有未保存修改。';};
$('save-system').onclick=()=>action(async()=>{showSettings(await api('settings',{system_prompt:$('system-prompt').value,revision:settings.revision}));$('prompt-text').value='';$('copy-prompt').disabled=true;status('System prompt 已保存；固定 JSON 契約保持不變。');});
$('default-system').onclick=()=>{$('system-prompt').value=settings.default_system_prompt;$('system-prompt').oninput();};
$('oauth-connect').onclick=()=>action(async()=>{requireSaved();requireSystemSaved();const result=await api('auth/start',{});location.assign(result.authorize_url);});
$('oauth-disconnect').onclick=()=>action(async()=>{const result=await api('auth/disconnect',{});await authStatus();$('oauth-notice').textContent=result.notice;status(result.notice);});
$('generate-batch').onclick=()=>action(async()=>{
 requireSaved();requireSystemSaved();if(!batch||batch.status!=='draft')throw new Error('請先準備待翻批次。');if(!authorized)throw new Error('請先完成 BazaarLink 授權。');if(!$('confirm-model').checked)throw new Error('請先確認傳送本批資料及可能費用。');
 try{
 const result=await api('generate',{batch_id:batch.id,revision:batch.revision,settings_revision:settings.revision,confirmed:true});
 $('confirm-model').checked=false;await loadBatch(batch.id);await glossary();showLookup(result);tab('draft');const reviewText=showReview(result);$('generation-result').textContent=result.notice+' '+reviewText+modelUsage(result);status(result.notice+' '+reviewText);
 }catch(error){try{await loadBatch(batch.id);}catch{}throw error;}
});
async function ensureTestBatch(){
 requireSaved();requireSystemSaved();
 if(batch?.status==='draft')return;
 if(!runId)runId=(await api('run',{})).id;
 const run=await api('run?id='+encodeURIComponent(runId));
 const draft=[...run.batches].reverse().find(item=>item.status==='draft');
 if(draft){await loadBatch(draft.id);return;}
 const next=await api('prepare',{run_id:runId});
 if(!next.id)throw new Error('目前沒有可用待翻批次，請查看翻譯進度與匯合點待確認。');
 await loadBatch(next.id);
}
$('preview-test').onclick=()=>action(async()=>{try{await ensureTestBatch();const result=await api('prompt?id='+batch.id+'&test=1');$('test-request').value=result.prompt;showLookup(result);$('test-counts').textContent=`本次實際 ${result.source_nodes} 個節點、${result.unit_ids.length} 段、${result.source_characters.toLocaleString()} 字元；共同前文 0 段。`;$('test-status').textContent='已先搜尋字典並組裝測試請求，尚未送出模型。';status('10 節點測試請求已組裝，尚未送出模型。');}catch(error){$('test-status').textContent=error.message;throw error;}});
$('generate-test').onclick=()=>action(async()=>{
 let sent=false;
 try{
  requireSaved();requireSystemSaved();if(!authorized)throw new Error('尚未授權，請先按上方「使用 BazaarLink 授權」。');
  if(!$('confirm-test').checked)throw new Error('請先勾選按鈕上方的小量測試資料傳送及可能費用確認。');
  // Consent was explicit before loading; loadBatch resets the visible checkbox.
  await ensureTestBatch();$('test-status').textContent='測試處理中，不會自動重送…';
  sent=true;const result=await api('generate-test',{batch_id:batch.id,revision:batch.revision,settings_revision:settings.revision,confirmed:true});$('test-output').value=JSON.stringify(result.output,null,2);showLookup(result);await showProposals();await glossary();const reviewText=showReview(result);$('test-counts').textContent=`實際送出 ${result.source_nodes} 個節點、${result.source_characters.toLocaleString()} 字元；共同前文 0 段。`;$('test-status').textContent=result.notice+' '+reviewText+modelUsage(result);status(result.notice+' '+reviewText);
 }catch(error){if(sent&&batch){try{await loadBatch(batch.id);}catch{}}$('test-status').textContent=error.message;throw error;}finally{$('confirm-test').checked=false;}
});
// Tab changes can occur directly through keyboard/mouse, not just tab().
document.querySelector('.tabs').addEventListener('click',updateGenerate);
$('stop-bulk').onclick=()=>{stopRequested=true;$('bulk-status').textContent='本批完成後停止，不再送出下一批；不取消已送出的模型請求。';};
$('generate-bulk').onclick=()=>action(async()=>{
 requireSaved();requireSystemSaved();if(!authorized)throw new Error('請先連接 BazaarLink。');
 const count=Number($('bulk-count').value);if(!Number.isSafeInteger(count)||count<1)throw new Error('批數必須是正整數。');
 if(!$('confirm-bulk').checked)throw new Error('請先確認指定批數費用及機器檢查後自動提交。');
 let completed=0;batchRunning=true;stopRequested=false;lockBulkEditors();
 try{
  await ensureTestBatch();if(count>remainingBatches)throw new Error(`目前只剩 ${remainingBatches} 批可處理，請調整批數。`);
  for(let i=0;i<count&&!stopRequested;i++){
   requireSystemSaved();await ensureTestBatch();const id=batch.id;
   $('bulk-status').textContent=`批量執行 ${completed} / ${count} 批 · 正在處理第 ${batch.ordinal} 批`;
   let result;
   if(!batch.machine_ready&&batch.units.some(u=>u.target))throw new Error('目前草稿已有保存內容，批量執行不自動覆蓋；請先審閱提交。');
   if(batch.machine_ready){const review=await api('review-saved-draft',{batch_id:id,revision:batch.revision,confirmed:true});result={notice:'沿用已完成的模型草稿，沒有重跑 Flash。',glossary_review:review};}
   else result=await api('generate',{batch_id:id,revision:batch.revision,settings_revision:settings.revision,confirmed:true});
   await loadBatch(id);await glossary();showLookup(result);showReview(result);$('generation-result').textContent=result.notice+modelUsage(result);
    if(result.glossary_review?.state==='failed')throw new Error('Flash 審詞失敗，譯文已保存；已停止後續批次。');
   await api('auto-submit',{batch_id:id,revision:batch.revision,confirmed:true});completed++;await loadBatch(id);window.scrollTo({top:0,left:0,behavior:'auto'});
  }
  if(completed&&remainingBatches>0&&!stopRequested)await prepare();
  $('bulk-status').textContent=`批量執行${stopRequested?'已停止':'完成'}：${completed} / ${count} 批（機器檢查後提交，非人工審閱）`;status($('bulk-status').textContent);
 }catch(error){if(batch){try{await loadBatch(batch.id);}catch{}}$('bulk-status').textContent=`批量執行已停止：${completed} / ${count} 批；${error.message}`;throw error;}
 finally{batchRunning=false;$('confirm-bulk').checked=false;lockBulkEditors();}
});
$('start-branches').onclick=()=>action(async()=>{
 requireSaved();requireSystemSaved();if(!authorized)throw new Error('請先連接 BazaarLink。');
 if(!$('confirm-branches').checked)throw new Error('請先確認並行分支的批數、修正及審詞費用。');
 const count=Number($('branch-count').value),concurrency=Number($('branch-limit').value);
 if(!Number.isSafeInteger(count)||count<1||!Number.isSafeInteger(concurrency)||concurrency<1||concurrency>30)throw new Error('批數需為正整數，同時分支數為1～30。');
 if(!runId)runId=(await api('run',{})).id;
 const result=await api('branches/start',{run_id:runId,count,concurrency,confirmed:true});$('confirm-branches').checked=false;
 await syncBranches();await restoreWorkspace();status(result.notice);
});
$('stop-branches').onclick=()=>action(async()=>{const result=await api('branches/stop',{session_id:branchSession});status(result.notice);await syncBranches();});
window.addEventListener('beforeunload',event=>{if(dirty||systemDirty||batchRunning){event.preventDefault();event.returnValue='';}});
async function restoreWorkspace(){if(!runId){$('overall-progress').textContent='尚未開始翻譯';return;}const run=await api('run?id='+encodeURIComponent(runId));const latest=[...run.batches].reverse().find(b=>b.status==='draft')||run.batches.at(-1);if(latest)await loadBatch(latest.id);else await progress();}
(async()=>{await syncTestRound();await bootstrap();await glossary();showSettings(await api('settings'));await authStatus();await syncActivity();await syncBranches();await restoreWorkspace();await showProposals();const outcome=new URLSearchParams(location.search).get('oauth');if(outcome){history.replaceState(null,'','/translate');$('oauth-notice').textContent=outcome==='success'&&authorized?'BazaarLink 授權完成。尚未呼叫模型。':'授權未完成、狀態不符或交換失敗，請重新授權；不會自動重試。';}status('工作區已就緒。正在執行的流程可從上方查看，刷新不會重送。');setInterval(()=>{syncActivity();syncTestRound();syncBranches();},2500);})().catch(error=>status(error.message));
