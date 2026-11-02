"""Explicitly authorized bounded round; an existing workspace may be promoted live.

Run on the remote host only. --confirmed is required. Reusing --directory resumes
the same bounded round after an error; completed batches are never sent again.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3

from bazaarlink import request_json
from translation_catalog import Sources
from translation_workspace import Workspace
from workspace_location import workspace_directory


def log_event(folder, event):
    """Durable local events, independent of the launching SSH stdout pipe.

    Do not swallow filesystem errors: inability to retain an audit must stop work.
    Never print here, including the error path, since the caller may disconnect.
    """
    record=dict(event,logged=datetime.now(timezone.utc).isoformat())
    with (folder/'runner-events.jsonl').open('a',encoding='utf-8') as output:
        output.write(json.dumps(record,ensure_ascii=False)+'\n')
        output.flush()
        os.fsync(output.fileno())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--count',type=int,default=10)
    parser.add_argument('--from-round',type=Path,help='Continue a completed isolated round, never copy its results to live')
    parser.add_argument('--confirmed',action='store_true')
    args=parser.parse_args()
    if not args.confirmed or args.count not in (10,50):parser.error('Explicit consent and a 10- or 50-batch round required')
    root=Path(__file__).resolve().parent;live=root/'data';folder=args.directory.resolve()
    if folder==live.resolve() or not folder.is_relative_to((live/'test-rounds').resolve()):
        parser.error('Directory must be below data/test-rounds, never the live workspace')
    folder.mkdir(parents=True,exist_ok=True)
    with (live/'test-rounds/active-runner.lock').open('a') as global_lock:
        fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with (folder/'runner.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return run_round(args,parser,live,folder)


def run_round(args,parser,live,folder):
    statefile=folder/'round.json'
    if statefile.exists():
        state=json.loads(statefile.read_text(encoding='utf-8'))
    else:
        source_folder=workspace_directory(live)
        if args.from_round:
            source_folder=args.from_round.resolve()
            if not source_folder.is_relative_to((live/'test-rounds').resolve()) or source_folder==folder:
                parser.error('Source round must be a different isolated round below data/test-rounds')
            prior=json.loads((source_folder/'round.json').read_text(encoding='utf-8'))
            if prior.get('status')!='complete':parser.error('Source round must be complete')
        for name in ('translation','static-story'):(folder/name).mkdir()
        for rel in ('translation/catalog.json','static-story/graph.json','static-story/branches-acyclic.json'):
            (folder/rel).symlink_to(live/rel)
        with closing(sqlite3.connect('file:'+str(source_folder/'translation/workspace.sqlite3')+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(folder/'translation/workspace.sqlite3')) as target:
            for table in ('model_attempts','model_tests','glossary_reviews','model_parts'):
                if source.execute(f"SELECT COUNT(*) FROM {table} WHERE state='pending'").fetchone()[0]:
                    raise ValueError('Live request is pending; do not clone unknown in-flight work')
            source.backup(target)
        state=dict(count=args.count,completed=[],calls=[],status='created',created=datetime.now(timezone.utc).isoformat(),source_round=source_folder.name if args.from_round else None)
    if state['count']!=args.count:raise ValueError('Unexpected existing round size')
    def save():
        temporary=statefile.with_suffix('.writing')
        temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(statefile)
    save()
    index=live/'test-rounds/active-round.json';temporary=index.with_suffix('.writing')
    temporary.write_text(json.dumps(dict(directory=folder.name)),encoding='utf-8');temporary.replace(index)
    key=json.loads((live/'private-oauth/configured-key.json').read_text(encoding='utf-8'))['key']
    service=Workspace(folder,Sources(live))
    runs=service.summary()['runs']
    if len(runs)!=1:raise ValueError('Expected the existing sole translation progress')
    run_id=runs[0]['id']
    if state.get('status')=='failed':
        state.setdefault('failures',[]).append(dict(ordinal=state.get('current_ordinal'),error=state.get('error'),stopped=state.get('stopped')))
    if 'start_ordinal' not in state and state['completed']:
        state['start_ordinal']=min(b['ordinal'] for b in state['completed'])
    if 'start_ordinal' in state:
        # Recover an isolated approval if interrupted between SQLite commit and
        # the JSON checkpoint, rather than accidentally paying for an 11th batch.
        known={b['batch_id']:b for b in state['completed']}
        with closing(sqlite3.connect(folder/'translation/workspace.sqlite3')) as db:
            approved=list(db.execute("SELECT id,ordinal,units FROM batches WHERE run_id=? AND ordinal>=? AND status='approved' ORDER BY ordinal",(run_id,state['start_ordinal'])))
        if len(approved)>args.count:raise ValueError('Round exceeds requested isolated approvals; stop for audit')
        state['completed']=[known.get(bid,dict(batch_id=bid,ordinal=ordinal,recovered_checkpoint=True)) for bid,ordinal,_ in approved]
    state.pop('error',None);state.pop('stopped',None);save()
    def transport(url,request,credential):
        payload=json.loads(request['messages'][1]['content'])
        event=dict(model=request['model'],task=payload.get('task_type'),started=datetime.now(timezone.utc).isoformat())
        state['calls'].append(event);save()
        try:
            response=request_json(url,request,credential)
            event.update(state='received',usage=response.get('usage'));save()
            return response
        except ValueError as error:
            event.update(state='failed',error=str(error));save()
            raise
    try:
        while len(state['completed'])<args.count:
            prepared=service.prepare(run_id)
            if not prepared.get('id'):raise ValueError('No next batch available: '+str(prepared))
            batch=service.batch(prepared['id'])
            state.setdefault('start_ordinal',batch['ordinal'])
            state.update(status='running',current_batch=batch['id'],current_ordinal=batch['ordinal']);save()
            log_event(folder,dict(event='start',position=len(state['completed'])+1,count=args.count,ordinal=batch['ordinal'],characters=batch['source_characters']))
            if batch['machine_ready']:
                result=dict(glossary_review=service.review_saved_draft(batch['id'],batch['revision'],key,True,transport))
            else:
                result=service.generate(batch['id'],batch['revision'],service.settings()['revision'],key,True,transport)
            if result.get('glossary_review',{}).get('state')=='failed':
                raise ValueError(result['glossary_review']['error'])
            batch=service.batch(batch['id'])
            service.approve(batch['id'],batch['revision'],False,automatic=True)
            record=dict(batch_id=batch['id'],ordinal=batch['ordinal'],characters=batch['source_characters'],completed=datetime.now(timezone.utc).isoformat())
            state['completed'].append(record);save()
            log_event(folder,dict(event='complete',position=len(state['completed']),count=args.count,ordinal=batch['ordinal']))
        state.update(status='complete',finished=datetime.now(timezone.utc).isoformat());save()
        log_event(folder,dict(event='round_complete',batches=args.count,calls=len(state['calls']),directory=str(folder)))
    except Exception as error:
        state.update(status='failed',error=str(error),stopped=datetime.now(timezone.utc).isoformat());save()
        log_event(folder,dict(event='round_failed',completed=len(state['completed']),error=str(error)))
        raise


if __name__=='__main__':main()
