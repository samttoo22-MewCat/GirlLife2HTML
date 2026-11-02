"""Token-budgeted, ordered translation parts; no credentials or storage here."""
from copy import deepcopy
import json
import math

from bazaarlink import response_format, strict_json

OUTPUT_LIMIT = 65536
OUTPUT_TARGET = int(OUTPUT_LIMIT * .85)
INPUT_TARGET = 900000  # conservative headroom below the catalog's 1M context


def number(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else 0


class AdaptiveFailure(ValueError):
    def __init__(self, message, output, summary):
        super().__init__(message)
        self.output, self.summary = output, summary


def complete_prefix(text):
    """Read ONLY complete JSON array members; never close or repair a cut string."""
    def unique(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:raise ValueError('重複JSON欄位，不可安全續接')
            obj[key]=value
        return obj
    decoder=json.JSONDecoder(object_pairs_hook=unique)
    result=dict(translations=[],glossary_proposals=[])
    closed=set();seen=set();pos=0
    def space(i):
        while i<len(text) and text[i].isspace():i+=1
        return i
    try:
        pos=space(pos)
        if text[pos]!='{':return result,closed
        pos+=1
        while True:
            pos=space(pos)
            if pos>=len(text) or text[pos]=='}':break
            key,pos=decoder.raw_decode(text,pos)
            if key not in result or key in seen:raise ValueError('截斷回覆含未知或重複頂層欄位')
            seen.add(key);pos=space(pos)
            if text[pos]!=':':break
            pos=space(pos+1)
            if text[pos]!='[':break
            pos=space(pos+1)
            if pos<len(text) and text[pos]==']':closed.add(key);pos+=1
            else:
                while pos<len(text):
                    item,end=decoder.raw_decode(text,pos)
                    end=space(end)
                    # A complete value without a following delimiter can be a cut number.
                    if end>=len(text) or text[end] not in ',]':break
                    result[key].append(item)
                    if text[end]==']':closed.add(key);pos=end+1;break
                    pos=space(end+1)
            pos=space(pos)
            if pos>=len(text) or text[pos]!=',':break
            if seen==set(result):raise ValueError('截斷回覆在完整頂層欄位後又加入額外欄位')
            pos+=1
    except (json.JSONDecodeError,IndexError):
        pass
    return result,closed


def budget_profile(samples):
    reasoning=12000;visible_ratio=.9
    for sample in samples:
        usage=sample.get('usage');usage=usage if isinstance(usage,dict) else {};chars=number(sample.get('characters'))
        completion=number(usage.get('completion_tokens'))
        details=usage.get('completion_tokens_details');details=details if isinstance(details,dict) else {}
        thinking=number(details.get('reasoning_tokens'))
        if chars and completion and thinking<=completion:
            reasoning=max(reasoning,math.ceil(thinking*1.2))
            visible_ratio=max(visible_ratio,(completion-thinking)/chars*1.25)
    return dict(reasoning_reserve=reasoning,visible_ratio=visible_ratio,
                output_limit=OUTPUT_LIMIT,output_target=OUTPUT_TARGET,input_target=INPUT_TARGET)


def estimate(units,profile,request=None):
    chars=sum(len(u['source'].replace(' ','').replace('\n','').replace('\t','')) for u in units)
    output=math.ceil(profile['reasoning_reserve']+chars*profile['visible_ratio']+len(units)*48+1500)
    # A deliberately conservative character-based estimate, NOT a tokenizer result.
    input_tokens=math.ceil(len(json.dumps(request,ensure_ascii=False))*1.1) if request else 0
    return dict(source_characters=chars,units=len(units),estimated_output_tokens=output,
                estimated_input_tokens=input_tokens,**profile)


def groups(units,profile):
    if units and profile['reasoning_reserve']+1500>=profile['output_target']:
        raise ValueError('推理用量本身已超安全輸出預算，縮短段落仍無法安全估算，未再送出；結果保留待確認。')
    result=[];current=[]
    for unit in units:
        gap=bool(current and 'sequence' in current[-1] and 'sequence' in unit and unit['sequence']!=current[-1]['sequence']+1)
        if current and (gap or estimate(current+[unit],profile)['estimated_output_tokens']>profile['output_target']):
            result.append(current);current=[]
        current.append(unit)
        if estimate(current,profile)['estimated_output_tokens']>profile['output_target']:
            raise ValueError('單一完整段落預估超過安全輸出預算，未送出；不能硬拆保留片段。')
    if current:result.append(current)
    return result


def run_parts(request,profile,call,stash,cache,valid_row,seed=None):
    """call(kind, ids, request, budget); stash(ids, data, metadata_complete)."""
    profile=deepcopy(profile)
    payload=json.loads(request['messages'][1]['content'])
    units=[dict(u,sequence=i) for i,u in enumerate(payload['new_units'])];by_id={u['id']:u for u in units};order=list(by_id)
    received={};done=set();metadata_done=set();proposals=[];extra=[];usages=[]
    past_lengths=set()
    summary=dict(planned_parts=len(groups(units,profile)),requests=0,resumed_units=0,
                 salvaged_units=0,truncations=0,adaptive_rounds=0,metadata_incomplete=False,budget=profile)

    def add(data,ids,metadata_complete):
        local=set()
        for item in data.get('translations',[]):
            if isinstance(item,dict) and item.get('id') in by_id:
                uid=item['id']
                if uid in local or uid in received and received[uid]!=item:extra.append(item)
                else:received[uid]=item
                local.add(uid)
            else:extra.append(item)
        actual=set(ids)&set(received)
        done.update(actual)
        if metadata_complete:metadata_done.update(actual)
        proposals.extend(data.get('glossary_proposals',[]))

    for saved in cache:
        if saved.get('truncated_scope') is not None:
            past_lengths.add((saved['kind'],frozenset(saved['truncated_scope'])));continue
        ids=saved['unit_ids']
        if set(ids)<=set(order):add(saved['output'],ids,saved['metadata_complete'])
    summary['resumed_units']=len(done)

    def output():
        unique=[];seen=set()
        for p in proposals:
            key=json.dumps(p,sort_keys=True,ensure_ascii=False)
            if key not in seen:seen.add(key);unique.append(p)
        return dict(translations=[received[i] for i in order if i in received]+extra,glossary_proposals=unique)

    def reported_usage():
        totals={k:sum(number(u.get(k)) for u in usages) for k in ('prompt_tokens','completion_tokens','total_tokens') if any(k in u for u in usages)}
        for field in ('cost','bz_cost'):
            if any(field in u for u in usages):totals[field]=sum(number(u.get(field)) for u in usages)
        details=[u['completion_tokens_details'] for u in usages if isinstance(u.get('completion_tokens_details'),dict)]
        if details:totals['completion_tokens_details']=dict(reasoning_tokens=sum(number(d.get('reasoning_tokens')) for d in details))
        inputs=[u['prompt_tokens_details'] for u in usages if isinstance(u.get('prompt_tokens_details'),dict)]
        if inputs:totals['prompt_tokens_details']={k:sum(number(d.get(k)) for d in inputs) for k in ('cached_tokens','cache_write_tokens') if any(k in d for d in inputs)}
        return totals

    def fail(message):
        summary['failed']=True
        summary['metadata_incomplete']=bool(done-metadata_done)
        summary['metadata_pending_ids']=[i for i in order if i in done and i not in metadata_done]
        summary['reported_usage']=reported_usage()
        summary['usage_incomplete']=len(usages)<summary['requests'] or any('total_tokens' not in u for u in usages)
        raise AdaptiveFailure(message,output(),deepcopy(summary))

    def safe_groups(selected):
        try:return groups(selected,profile)
        except ValueError as error:fail(str(error))

    def envelope(response):
        if not isinstance(response,dict) or response.get('error'):fail('供應商回傳錯誤，未盲目重試。')
        choices=response.get('choices') or []
        choice=choices[0] if isinstance(choices,list) and choices and isinstance(choices[0],dict) else {}
        msg=choice.get('message')
        if not isinstance(msg,dict):fail('供應商回應結構無效，已保留完成部分，不盲目重送。')
        if msg.get('refusal') or choice.get('finish_reason')=='content_filter':fail('供應商拒絕本次請求；與長度截斷不同，不自動繞過或重送。')
        if choice.get('finish_reason') not in ('stop','length'):fail('供應商未正常結束（'+str(choice.get('finish_reason'))+'），已保留完成部分。')
        return choice.get('finish_reason'),msg.get('content') or ''

    def send(kind,selected,wire):
        budget=estimate(selected,profile,wire)
        if budget['estimated_input_tokens']>profile['input_target']:fail('必要上下文預估超過安全輸入預算，未送出。')
        wire['max_tokens']=OUTPUT_LIMIT
        summary['requests']+=1
        try:response=call(kind,[u['id'] for u in selected],wire,budget)
        except ValueError as error:fail(str(error))
        if not isinstance(response,dict):fail('供應商回應不是JSON物件，已保留完成部分。')
        usage=response.get('usage');usages.append(usage if isinstance(usage,dict) else {})
        if kind=='translation':
            observed=budget_profile([dict(characters=budget['source_characters'],usage=usage)])
            profile['reasoning_reserve']=max(profile['reasoning_reserve'],observed['reasoning_reserve'])
            profile['visible_ratio']=max(profile['visible_ratio'],observed['visible_ratio'])
        return response

    def wire_for(selected,kind):
        wire=deepcopy(request);body=deepcopy(payload);ids=[u['id'] for u in selected]
        if kind=='translation':
            body['new_units']=selected
            # The same path/choice table stays available; only genuine prior text is added.
            prior=[];size=0
            for uid in reversed(order[:order.index(ids[0])]):
                row=received.get(uid)
                if not row or not valid_row(row,proposals):continue
                source=by_id[uid]['source'];length=len(source)
                if size+length>10000:break
                prior.append(dict(id=uid,source=source,target=row['target'],sequence=by_id[uid]['sequence'],scene=by_id[uid].get('scene'),condition_evidence=by_id[uid].get('condition_evidence',[]),status='validated_draft_not_human_approved'));size+=length
            refs=[]
            for ref in reversed(body.get('references',[])):
                if size+len(ref['source'])>10000:break
                refs.append(ref);size+=len(ref['source'])
            body['references']=list(reversed(refs));body['preceding_chunks']=list(reversed(prior))
            body['part']=dict(first_unit=order.index(ids[0]),unit_positions=[by_id[i]['sequence'] for i in ids],units=len(ids),batch_units=len(order),preserve_order=True)
            wire['response_format']=response_format(ids)
            body['output_order']='先輸出glossary_proposals完整陣列，再輸出translations；譯文維持new_units來源順序，不能省略段落。'
            schema=wire['response_format']['json_schema']['schema'];properties=schema['properties']
            schema['properties']=dict(glossary_proposals=properties['glossary_proposals'],translations=properties['translations'])
        else:
            body=dict(task_type='glossary_proposal_recovery',source_units=selected,
                      translated_units=[received[i] for i in ids],dictionary=payload.get('dictionary',[]),
                      route_context=payload.get('route_context'),
                      task='譯文已完整保存，不要重翻。先前回覆截斷而未完成新詞提案；只依這些原文與既有譯文補回全部用詞提案。沒有才回空陣列，不改譯文。')
            fmt=response_format(ids);fmt['json_schema']['name']='story_atlas_recovered_proposals'
            schema=fmt['json_schema']['schema'];schema['required']=['glossary_proposals'];schema['properties'].pop('translations')
            wire['response_format']=fmt
        wire['messages']=[deepcopy(request['messages'][0]),dict(role='user',content=json.dumps(body,ensure_ascii=False))]
        return wire

    def recover_metadata(selected,depth=0):
        if not selected:return
        ids=[u['id'] for u in selected]
        if ('metadata',frozenset(ids)) in past_lengths:
            if len(selected)<2:fail('此單段提案補回曾遭截斷，已保留譯文，不盲目重送相同請求。')
            mid=len(selected)//2;recover_metadata(selected[:mid],depth+1);recover_metadata(selected[mid:],depth+1);return
        response=send('metadata',selected,wire_for(selected,'metadata'))
        reason,text=envelope(response)
        if reason=='length':
            summary['truncations']+=1
            if summary['adaptive_rounds']>=3 or len(selected)<2:fail('新詞提案補回亦超出輸出上限；已保留譯文並標記提案未完成。')
            summary['adaptive_rounds']+=1
            middle=len(selected)//2;recover_metadata(selected[:middle],depth+1);recover_metadata(selected[middle:],depth+1);return
        try:data=strict_json(text)
        except ValueError:fail('提案補回JSON無法解析；譯文已保留，不假裝沒有新詞。')
        if not isinstance(data,dict) or set(data)!={'glossary_proposals'} or not isinstance(data['glossary_proposals'],list):fail('提案補回格式錯誤；已保留譯文，待審詞尚未完成。')
        data=dict(translations=[received[i] for i in ids],glossary_proposals=data['glossary_proposals'])
        add(data,ids,True);stash(ids,data,True)

    def consume_length(selected,response,seeded=False):
        if not seeded:
            summary['truncations']+=1
            if summary['adaptive_rounds']>=3:fail('已達三輪自適應縮包上限；完成部分已保留。')
            summary['adaptive_rounds']+=1
        try:data,closed=complete_prefix(response['choices'][0]['message'].get('content') or '')
        except ValueError as error:fail(str(error))
        ids={u['id'] for u in selected};valid=[];seen=set()
        for item in data['translations']:
            if isinstance(item,dict) and item.get('id') in ids and item['id'] not in seen and valid_row(item,data['glossary_proposals']):
                seen.add(item['id']);valid.append(item)
        if valid:
            summary['salvaged_units']+=len(valid)
            accepted=[u for u in selected if u['id'] in seen]
            partial=dict(translations=valid,glossary_proposals=data['glossary_proposals'])
            complete='glossary_proposals' in closed
            add(partial,list(seen),complete);stash([u['id'] for u in accepted],partial,complete)
            if seeded:summary['resumed_units']+=len(seen)
            if not complete:
                for group in safe_groups(accepted):recover_metadata(group)
        remaining=[u for u in selected if u['id'] not in done]
        if not remaining:return
        if not seeded and len(remaining)==1 and len(selected)==1:fail('單一完整段落仍遭截斷，已保留其他譯文；不能無限重送。')
        # A cut part is never repeated at the same size.
        for group in safe_groups(remaining):
            if not seeded and len(group)>1:
                mid=len(group)//2;translate(group[:mid]);translate(group[mid:])
            else:translate(group)

    def translate(selected):
        replanned=safe_groups(selected)
        if len(replanned)>1:
            for part in replanned:translate(part)
            return
        ids=[u['id'] for u in selected]
        if ('translation',frozenset(ids)) in past_lengths:
            if len(selected)<2:fail('此完整段落曾遭截斷，已保留其他結果，不重送相同大小。')
            mid=len(selected)//2;translate(selected[:mid]);translate(selected[mid:]);return
        response=send('translation',selected,wire_for(selected,'translation'))
        reason,text=envelope(response)
        if reason=='length':consume_length(selected,response);return
        try:data=strict_json(text)
        except ValueError:fail('子請求JSON無法解析，已保留其他完成部分，不整批重翻。')
        if not isinstance(data,dict) or set(data)!={'translations','glossary_proposals'} or not isinstance(data['translations'],list) or not isinstance(data['glossary_proposals'],list):fail('子請求格式不符，已保留其他完成部分。')
        ids=[u['id'] for u in selected]
        # Preserve bad but complete rows for the existing bounded semantic repair stage.
        add(data,ids,True);stash(ids,data,True)

    if seed is not None and not done:consume_length(units,seed,seeded=True)
    for group in safe_groups([u for u in units if u['id'] not in done]):translate(group)
    for group in safe_groups([u for u in units if u['id'] in done and u['id'] not in metadata_done]):recover_metadata(group)
    summary['metadata_incomplete']=False
    totals=reported_usage()
    summary['usage_incomplete']=any('total_tokens' not in u for u in usages)
    return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(output(),ensure_ascii=False)))],
                usage=totals,adaptive_summary=summary)
