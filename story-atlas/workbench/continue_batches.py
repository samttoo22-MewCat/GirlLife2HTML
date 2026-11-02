"""Explicit one-shot 500→1000 continuation supervisor, run detached remotely.

Never retries a mutation, never starts on unconfirmed/failed work. A repair
notification wakes the owning agent; this script cannot silently weaken guards.
"""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import fcntl
import json
from pathlib import Path
import sqlite3
import time
from urllib.request import Request,urlopen

from workspace_location import workspace_directory


def decision(status,expected):
    if status.get('session_id')!=expected:return 'needs_review'
    if status.get('state')=='running':return 'wait'
    if status.get('state')!='complete' or status.get('completed')!=status.get('requested'):
        return 'needs_repair'
    if status.get('error') or any(status.get('counts',{}).get(k,0) for k in ('failed','running','waiting')):
        return 'needs_repair'
    return 'audit'


def audit(data,run_id):
    folder=workspace_directory(data)
    with closing(sqlite3.connect('file:'+str(folder/'translation/workspace.sqlite3')+'?mode=ro',uri=True)) as db:
        check=db.execute('PRAGMA quick_check').fetchone()[0]
        pending={table:db.execute(f"SELECT COUNT(*) FROM {table} a JOIN batches b ON a.batch_id=b.id WHERE b.run_id=? AND a.state='pending'",(run_id,)).fetchone()[0] for table in ('model_attempts','model_tests','glossary_reviews','model_parts')}
        duplicates=db.execute("SELECT j.value,COUNT(*) FROM batches b,json_each(b.units) j WHERE b.run_id=? AND b.status='approved' GROUP BY j.value HAVING COUNT(*)>1 LIMIT 10",(run_id,)).fetchall()
        failed=db.execute("SELECT id,batch_id,error FROM branch_jobs WHERE run_id=? AND state='failed'",(run_id,)).fetchall()
    return dict(ok=check=='ok' and not any(pending.values()) and not duplicates and not failed,quick_check=check,pending=pending,duplicates=duplicates,failed=failed)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--run-id',required=True);parser.add_argument('--after-session',required=True);parser.add_argument('--confirmed',action='store_true');args=parser.parse_args()
    if not args.confirmed:parser.error('Explicit authorization required')
    root=Path(__file__).resolve().parent;data=root/'data';directory=args.directory.resolve();directory.mkdir(parents=True,exist_ok=True)
    statefile=directory/'status.json'
    with (data/'translation/continuation-supervisor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if statefile.exists():
            old=json.loads(statefile.read_text())
            if old.get('start_post_attempted'):raise ValueError('Start already attempted; inspect actual session, never retry blindly')
        state=dict(state='waiting_500',after_session=args.after_session,run_id=args.run_id,next_count=1000,concurrency=5)
        def save():
            state['updated']=datetime.now(timezone.utc).isoformat();temp=statefile.with_suffix('.writing');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2));temp.replace(statefile)
        def get(route):
            with urlopen('http://127.0.0.1:33325/api/workflow/'+route,timeout=30) as response:return json.load(response)
        save()
        try:
            while True:
                status=get('branches/status?run_id='+args.run_id);action=decision(status,args.after_session)
                state.update(previous_state=status['state'],completed=status.get('completed'),requested=status.get('requested'),counts=status.get('counts'),error=status.get('error'));save()
                if action=='wait':time.sleep(5);continue
                if action!='audit':state['state']=action;save();return
                result=audit(data,args.run_id);state['audit']=result
                if not result['ok']:state['state']='needs_repair';save();return
                with closing(sqlite3.connect('file:'+str(workspace_directory(data)/'translation/workspace.sqlite3')+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(directory/'after-500.sqlite3')) as target:
                    source.backup(target)
                    if target.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
                # One mutation only. A lost response must never cause double spend.
                bootstrap=get('bootstrap');state.update(state='starting_1000',start_post_attempted=True);save()
                request=Request('http://127.0.0.1:33325/api/workflow/branches/start',json.dumps(dict(run_id=args.run_id,count=1000,concurrency=5,confirmed=True)).encode(),headers={'Content-Type':'application/json','Origin':'http://127.0.0.1:33325','X-Atlas-CSRF':bootstrap['csrf']})
                with urlopen(request,timeout=60) as response:started=json.load(response)
                state.update(state='started_1000',session_id=started['session_id']);save();return
        except Exception as error:
            state.update(state='needs_review',error=str(error));save();raise


if __name__=='__main__':main()
