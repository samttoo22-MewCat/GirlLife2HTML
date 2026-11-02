"""Remote-only read-only live snapshot, no-model static queue/token forecast.

All simulated approvals occur in a disposable SQLite COPY, never the live DB.
Future prompts are unknown; token totals are fitted from actual recent usage,
with reasoning already included in completion tokens (not counted twice).
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import uuid
from urllib.request import Request,build_opener

from bazaarlink import NoRedirect,REVIEW_MODEL
from branch_scheduler import BranchScheduler,approve_branch_batch
from translation_catalog import Sources
from translation_workspace import Workspace
from workspace_location import workspace_directory


def fit_prediction(points,count,characters):
    n=len(points)
    if not n:return 0.
    sx=sum(x for x,y in points);sy=sum(y for x,y in points);sxx=sum(x*x for x,y in points);sxy=sum(x*y for x,y in points)
    denominator=n*sxx-sx*sx
    slope=max(0.,(n*sxy-sx*sy)/denominator) if denominator else 0.
    intercept=(sy-slope*sx)/n
    if intercept<0:intercept=0.;slope=sxy/sxx if sxx else 0.
    return max(0.,intercept*count+slope*characters)


def summarize_usage(db,workspace,limit=500):
    rows=list(db.execute("SELECT id,units FROM batches WHERE status='approved' ORDER BY id DESC LIMIT ?",(limit,)))
    chars={r['id']:sum(workspace.units[uid]['characters'] for uid in json.loads(r['units'])) for r in rows}
    values={bid:{name:defaultdict(float) for name in ('flash_initial','flash_corrections','pro')} for bid in chars};missing=0;model_totals={};category_models=defaultdict(set)
    for table in ('model_attempts','glossary_reviews'):
        for row in db.execute('SELECT batch_id,request,response FROM '+table+' WHERE response IS NOT NULL'):
            if row['batch_id'] not in chars:continue
            response=json.loads(row['response']);response=response if isinstance(response,dict) else {};usage=response.get('usage')
            if not isinstance(usage,dict):missing+=1;continue
            request=json.loads(row['request']);wire=request.get('request',request)
            category='pro' if table=='glossary_reviews' else 'flash_corrections' if request.get('repair_round') or request.get('correction_of') else 'flash_initial'
            target=values[row['batch_id']][category]
            target['requests']+=1
            target['prompt']+=usage.get('prompt_tokens',0) or 0
            target['completion']+=usage.get('completion_tokens',0) or 0
            target['cached']+=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',0) or 0
            target['reasoning']+=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0
            target['cost']+=usage.get('cost',usage.get('bz_cost',0)) or 0
            model=str(response.get('model') or wire.get('model','')).split('/')[-1];category_models[category].add(model)
            totals=model_totals.setdefault(model,defaultdict(float));totals['prompt']+=usage.get('prompt_tokens',0) or 0;totals['completion']+=usage.get('completion_tokens',0) or 0;totals['cached']+=(usage.get('prompt_tokens_details') or {}).get('cached_tokens',0) or 0;totals['cost']+=usage.get('cost',usage.get('bz_cost',0)) or 0
    return dict(characters=chars,values=values,missing_usage_records=missing,model_totals=model_totals,category_models={k:sorted(v) for k,v in category_models.items()})


def forecast_tokens(sample,count,characters,prices):
    result={}
    for category in ('flash_initial','flash_corrections','pro'):
        model=REVIEW_MODEL.split('/')[-1] if category=='pro' else 'deepseek-v4.1-flash';price=prices[model]
        observed={metric:sum(v[category].get(metric,0) for v in sample['values'].values()) for metric in ('requests','prompt','completion','cached','reasoning','cost')}
        predicted={metric:fit_prediction([(sample['characters'][bid],v[category].get(metric,0)) for bid,v in sample['values'].items()],count,characters) for metric in ('requests','prompt','completion','cached','reasoning')}
        predicted['cached']=min(predicted['cached'],predicted['prompt']);predicted['reasoning']=min(predicted['reasoning'],predicted['completion']);predicted['total']=predicted['prompt']+predicted['completion']
        def catalog_cost(v):return (v['prompt']-v['cached'])*price['prompt']+v['cached']*price.get('cached',price['prompt'])+v['completion']*price['completion']
        calibration=sample.get('model_totals',{}).get(model)
        if calibration is None and category=='pro' and model=='deepseek-v4.1-flash':
            calibration={metric:sum(v['flash_initial'].get(metric,0)+v['flash_corrections'].get(metric,0) for v in sample['values'].values()) for metric in ('prompt','completion','cached','cost')}
        calibration=calibration or observed
        nominal_observed=catalog_cost(calibration);factor=calibration['cost']/nominal_observed if nominal_observed else 1.
        predicted['catalog_cost_usd']=catalog_cost(predicted);predicted['historical_account_price_factor']=factor;predicted['estimated_cost_usd']=predicted['catalog_cost_usd']*factor
        result[category]=dict(model=model,role='glossary_review' if category=='pro' else category,historical_models=sample.get('category_models',{}).get(category,[]),review_model_switch_caveat=category=='pro' and any(m!=model for m in sample.get('category_models',{}).get(category,[])),observed=observed,predicted=predicted)
    result['total_tokens']=sum(result[k]['predicted']['total'] for k in ('flash_initial','flash_corrections','pro'))
    result['estimated_cost_usd']=sum(result[k]['predicted']['estimated_cost_usd'] for k in ('flash_initial','flash_corrections','pro'))
    result['catalog_cost_usd']=sum(result[k]['predicted']['catalog_cost_usd'] for k in ('flash_initial','flash_corrections','pro'))
    result['planning_range_usd']=[result['estimated_cost_usd']*.65,result['estimated_cost_usd']*1.5]
    return result


def simulate(workspace,output):
    scheduler=BranchScheduler(workspace);run=workspace.summary()['runs'][0]['id']
    # Reference lookup/dictionary projection do not influence take/branch/source
    # grouping. Omit only these COST projections in the disposable dry run.
    workspace._refs=lambda *args:[];workspace._dictionary=lambda *args:(0,[])
    seen=set();groups=[];deadline=time.monotonic()+1800
    with workspace.db() as db:
        approved={r[0] for r in db.execute('SELECT unit_id FROM translations WHERE run_id=?',(run,))}
        original_session=dict(db.execute('SELECT * FROM branch_sessions WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run,)).fetchone())
        current_approved=db.execute("SELECT COUNT(*) FROM branch_batches WHERE session_id=? AND state='approved'",(original_session['id'],)).fetchone()[0]
        sample=summarize_usage(db,workspace)
        def record(batch_id):
            row=db.execute('SELECT units FROM batches WHERE id=?',(batch_id,)).fetchone();ids=json.loads(row['units'])
            if set(ids)&(approved|seen):raise ValueError('Dry run would repeat an approved or scheduled source')
            seen.update(ids);groups.append(dict(characters=sum(workspace.units[u]['characters'] for u in ids),units=len(ids)))
        pending=list(db.execute("SELECT bb.batch_id FROM branch_batches bb JOIN batches b ON b.id=bb.batch_id WHERE b.run_id=? AND bb.state='prepared'",(run,)))
        for row in pending:
            record(row['batch_id']);approve_branch_batch(db,row['batch_id']);db.execute("UPDATE batches SET status='approved' WHERE id=?",(row['batch_id'],))
        sid='estimate-'+uuid.uuid4().hex
        db.execute("INSERT INTO branch_sessions(id,run_id,requested,concurrency,status)VALUES(?,?,1000000000,1,'running')",(sid,run))
        # Independent reachable-source inventory, cutting already-completed owners.
        completed_nodes={r[0] for r in db.execute("SELECT node FROM branch_nodes WHERE run_id=? AND state='done'",(run,))}
        frontier=[json.loads(r[0])[-1]['node'] for r in db.execute("SELECT path FROM branch_jobs WHERE run_id=? AND state='ready'",(run,))]
        visited=set();expected=set(seen)
        while frontier:
            node=frontier.pop()
            if node in visited or node in completed_nodes:continue
            visited.add(node);expected.update(set(workspace.by_node[node])-approved)
            frontier.extend(edge['target'] for edge in workspace.children[node])
        while True:
            job=db.execute("SELECT * FROM branch_jobs WHERE run_id=? AND state='ready' ORDER BY rowid LIMIT 1",(run,)).fetchone()
            if job is None:break
            if job['batch_id']:raise ValueError('Unexpected unsimulated prepared batch')
            bid=scheduler._plan(db,job,sid)
            if bid is not None:
                record(bid);approve_branch_batch(db,bid);db.execute("UPDATE batches SET status='approved' WHERE id=?",(bid,))
            if len(groups)%100==0:
                (output/'progress.json').write_text(json.dumps(dict(batches=len(groups),unique_units=len(seen))))
                if time.monotonic()>deadline:raise ValueError('Forecast exceeded 30 minutes; do not claim complete')
                if shutil.disk_usage(output).free<1024*1024*1024:raise ValueError('Forecast disk reserve reached; do not claim complete')
        eligible={u['id'] for u in workspace.units.values() if u['role']=='eligible'}
        uncovered=eligible-approved-seen
        remaining_states={r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM branch_jobs WHERE run_id=? GROUP BY state',(run,))}
        report=dict(original_session=dict(id=original_session['id'],requested=original_session['requested'],completed=current_approved,remaining=original_session['requested']-current_approved,state=original_session['status']),remaining_batches=len(groups),remaining_units=len(seen),remaining_characters=sum(g['characters'] for g in groups),approved_units=len(approved),eligible_units=len(eligible),outside_current_queue_units=len(uncovered),outside_current_queue_characters=sum(workspace.units[u]['characters'] for u in uncovered),remaining_states=remaining_states,independent_inventory_matches=expected==seen,inventory_missing=len(expected-seen),inventory_extra=len(seen-expected),sample_batches=len(sample['characters']),sample_source_characters=sum(sample['characters'].values()),sample_missing_usage_records=sample['missing_usage_records'])
        return report,sample,groups


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parent;data=root/'data';active=workspace_directory(data);snapshot=datetime.now(timezone.utc).isoformat()
    if shutil.disk_usage(output).free<3*1024**3:raise ValueError('Not enough forecast space')
    with tempfile.TemporaryDirectory(prefix='forecast-copy-',dir=output) as temporary:
        folder=Path(temporary)
        for name in ('translation','static-story'):(folder/name).mkdir()
        for rel in ('translation/catalog.json','static-story/graph.json','static-story/branches-acyclic.json'):(folder/rel).symlink_to(active/rel)
        with closing(sqlite3.connect('file:'+str(active/'translation/workspace.sqlite3')+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(folder/'translation/workspace.sqlite3')) as target:source.backup(target)
        workspace=Workspace(folder,Sources(data));report,sample,groups=simulate(workspace,output)
        key=json.loads((data/'private-oauth/configured-key.json').read_text())['key']
        def get(path):
            with build_opener(NoRedirect).open(Request('https://api.bazaarlink.ai/v1/'+path,headers={'Authorization':'Bearer '+key}),timeout=25) as response:return json.load(response)
        prices={}
        for model in get('models').get('data',[]):
            if model.get('id') in ('deepseek-v4.1-flash','deepseek-v4-pro-0813'):
                p=model['pricing'];prices[model['id']]=dict(prompt=float(p['prompt']),completion=float(p['completion']),cached=float(p.get('input_cache_read',p['prompt'])))
        report['token_forecast']=forecast_tokens(sample,report['remaining_batches'],report['remaining_characters'],prices)
        # Separate the active round's remaining quota from the full queued scope.
        current_count=report['original_session']['remaining'];mean_chars=report['remaining_characters']/report['remaining_batches'] if report['remaining_batches'] else 0
        report['active_round_forecast']=forecast_tokens(sample,current_count,mean_chars*current_count,prices)
        report.update(snapshot=snapshot,finished=datetime.now(timezone.utc).isoformat(),current_credits=get('credits')['data'],prices=prices,paid_model_calls=0,production_unmodified=True,method='Static source-queue dry run in disposable DB; nonnegative per-batch+source-character token fit over last 500 approved batches; empirical account-vs-catalog calibration; reasoning included in completion; 0.65–1.5 planning band is NOT confidence interval; no tokenizer installed')
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(output/'batch-sizes.json').write_text(json.dumps(groups))
        print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
