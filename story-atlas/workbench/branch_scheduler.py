"""Durable source-graph branch queue. Model calls reuse Workspace, never on GET."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import re
import threading
import uuid
from credit_gate import CreditGate
from bazaarlink import review_label


SCHEMA='''
CREATE TABLE IF NOT EXISTS branch_runs(run_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS branch_sessions(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,requested INTEGER NOT NULL,concurrency INTEGER NOT NULL,status TEXT NOT NULL,stop INTEGER NOT NULL DEFAULT 0,error TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS branch_jobs(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,path TEXT NOT NULL,path_key TEXT NOT NULL,state TEXT NOT NULL,batch_id INTEGER,wait_node INTEGER,error TEXT,UNIQUE(run_id,path_key));
CREATE TABLE IF NOT EXISTS branch_nodes(run_id TEXT NOT NULL,node INTEGER NOT NULL,owner TEXT,state TEXT NOT NULL,PRIMARY KEY(run_id,node));
CREATE TABLE IF NOT EXISTS branch_batches(batch_id INTEGER PRIMARY KEY,job_id TEXT NOT NULL,session_id TEXT NOT NULL,advance TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'prepared');
CREATE TABLE IF NOT EXISTS branch_merges(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,job_id TEXT NOT NULL,node INTEGER NOT NULL,owner TEXT,path TEXT NOT NULL,state TEXT NOT NULL,UNIQUE(job_id,node));
CREATE INDEX IF NOT EXISTS branch_jobs_ready ON branch_jobs(run_id,state);
CREATE INDEX IF NOT EXISTS branch_batches_session ON branch_batches(session_id,state);
CREATE INDEX IF NOT EXISTS branch_sessions_run ON branch_sessions(run_id,status);
CREATE INDEX IF NOT EXISTS branch_merges_wait ON branch_merges(run_id,node,state);
'''


def branch_mode(db,run_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='branch_runs'").fetchone():return False
    return bool(db.execute('SELECT 1 FROM branch_runs WHERE run_id=? AND enabled=1',(run_id,)).fetchone())


def add_job(db,run_id,path):
    key=hashlib.sha256(json.dumps([(f['node'],f.get('child',0)) for f in path]).encode()).hexdigest()
    job=uuid.uuid4().hex
    db.execute("INSERT OR IGNORE INTO branch_jobs(id,run_id,path,path_key,state)VALUES(?,?,?,?,'ready')",(job,run_id,json.dumps(path),key))


def finish_nodes(db,run_id,job_id,nodes):
    for node in nodes:
        db.execute("UPDATE branch_nodes SET state='done' WHERE run_id=? AND node=? AND owner=?",(run_id,node,job_id))
        db.execute("UPDATE branch_jobs SET state='context_review' WHERE run_id=? AND state='waiting' AND wait_node=?",(run_id,node))
        db.execute("UPDATE branch_merges SET state='context_review' WHERE run_id=? AND node=? AND state='waiting'",(run_id,node))


def reconcile_empty_frontiers(db,workspace,run_id):
    """Explicit quiescent repair ONLY for textless nodes stranded at a merge."""
    nodes=[]
    for row in db.execute("SELECT n.node,n.owner FROM branch_nodes n JOIN branch_jobs j ON j.id=n.owner WHERE n.run_id=? AND n.state='reserved' AND j.state='context_review'",(run_id,)):
        if workspace.by_node[row['node']]:continue
        finish_nodes(db,run_id,row['owner'],[row['node']]);nodes.append(row['node'])
    return nodes


def approve_branch_batch(db,batch_id):
    """Called INSIDE the normal approval transaction; legacy cursor stays intact."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='branch_batches'").fetchone():return False
    row=db.execute('SELECT * FROM branch_batches WHERE batch_id=?',(batch_id,)).fetchone()
    if row is None:return False
    if row['state']!='prepared':raise ValueError('分支批次已提交，不得重複推進。')
    job=db.execute('SELECT * FROM branch_jobs WHERE id=?',(row['job_id'],)).fetchone()
    advance=json.loads(row['advance'])
    finish_nodes(db,job['run_id'],job['id'],advance['finished'])
    for path in advance['spawn']:add_job(db,job['run_id'],path)
    db.execute('UPDATE branch_jobs SET path=?,state=?,batch_id=NULL,error=NULL WHERE id=?',(json.dumps(advance['path']),'done' if advance['done'] else 'ready',job['id']))
    db.execute("UPDATE branch_batches SET state='approved' WHERE batch_id=?",(batch_id,))
    db.execute("UPDATE runs SET status='parallel' WHERE id=?",(job['run_id'],))
    return True


class BranchScheduler:
    def __init__(self,workspace):
        self.ws=workspace;self.lock=threading.RLock();self.live_sessions=set();self.threads={};self.request_gates={}
        with workspace.db() as db:db.executescript(SCHEMA)

    def _initialize(self,db,run_id):
        if branch_mode(db,run_id):return
        run=self.ws._run(db,run_id)
        if db.execute("SELECT 1 FROM batches WHERE run_id=? AND status='draft'",(run_id,)).fetchone():
            raise ValueError('請先完成現有草稿，再切換分支佇列；不裁切或重翻現有批次。')
        state=json.loads(run['state']);frames=state['stack']
        active={f['node'] for f in frames if f.get('entered')}
        for node in state['visited']:
            if int(node) not in active:
                db.execute("INSERT OR IGNORE INTO branch_nodes VALUES(?,?,NULL,'done')",(run_id,int(node)))
        # Deepest continuation first, then nearest unstarted sibling frontiers.
        if frames:add_job(db,run_id,deepcopy(frames))
        for index in range(len(frames)-2,-1,-1):
            frame=frames[index]
            db.execute("INSERT OR IGNORE INTO branch_nodes VALUES(?,?,NULL,'done')",(run_id,frame['node']))
            for edge_index in range(frame['child'],len(self.ws.children[frame['node']])):
                path=deepcopy(frames[:index+1]);path[-1]['child']=edge_index+1
                path.append(dict(node=self.ws.children[frame['node']][edge_index]['target'],cursor=0,child=0,entered=False))
                add_job(db,run_id,path)
        db.execute('INSERT INTO branch_runs(run_id)VALUES(?)',(run_id,))
        db.execute("UPDATE runs SET status='parallel' WHERE id=?",(run_id,))

    def start(self,run_id,count,concurrency,key,confirmed,transport=None,background=True):
        if confirmed is not True or not key:raise ValueError('請先確認分支翻譯、修正與Flash審詞費用。')
        if type(count) is not int or count<1 or type(concurrency) is not int or not 1<=concurrency<=30:
            raise ValueError('批數需為正整數，同時分支數為1～30。')
        with self.lock,self.ws.lock,self.ws.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM branch_sessions WHERE run_id=? AND status='running'",(run_id,)).fetchone():
                raise ValueError('此進度已有分支工作執行中；重新整理只查狀態，不重送。')
            for table in ('model_attempts','model_tests','glossary_reviews','model_parts'):
                if db.execute(f"SELECT 1 FROM {table} a JOIN batches b ON a.batch_id=b.id WHERE b.run_id=? AND a.state='pending'",(run_id,)).fetchone():
                    raise ValueError('仍有未確認的模型請求，不能啟動或自動接管。')
            self._initialize(db,run_id)
            # Only an explicit new start retries completed-error jobs; orphan running
            # jobs are deliberately NOT reclaimed after process interruption.
            if db.execute("SELECT 1 FROM branch_jobs WHERE run_id=? AND state='running'",(run_id,)).fetchone():
                raise ValueError('有失去程序持有者的分支工作，需先確認原請求，不自動重送。')
            db.execute("UPDATE branch_jobs SET state='ready',error=NULL WHERE run_id=? AND state='failed'",(run_id,))
            session=uuid.uuid4().hex
            db.execute("INSERT INTO branch_sessions(id,run_id,requested,concurrency,status)VALUES(?,?,?,?,'running')",(session,run_id,count,concurrency))
            self.live_sessions.add(session)
        if background:
            thread=threading.Thread(target=self._run,args=(session,key,transport),daemon=True)
            self.threads[session]=thread;thread.start()
        else:self._run(session,key,transport)
        return dict(session_id=session,notice='分支佇列已開始，最多同時'+str(concurrency)+'支；結果直接保存到正式進度。')

    def resume(self,session,concurrency,key,confirmed,transport=None,background=True):
        """Explicitly resume the SAME quota, never count completed batches twice."""
        if confirmed is not True or not key:raise ValueError('請先確認接續剩餘批次的費用。')
        if type(concurrency) is not int or not 1<=concurrency<=30:raise ValueError('同時分支數為1～30。')
        with self.lock,self.ws.lock,self.ws.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM branch_sessions WHERE id=?',(session,)).fetchone()
            if row is None or row['status'] not in ('paused','blocked') or session in self.live_sessions:
                raise ValueError('只可接續已暫停或受阻的原輪，不自動接管執行中／未確認工作。')
            run_id=row['run_id']
            if db.execute("SELECT 1 FROM branch_sessions WHERE run_id=? AND status='running'",(run_id,)).fetchone():
                raise ValueError('已有其他分支工作執行中，不能接續。')
            latest=db.execute('SELECT id FROM branch_sessions WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run_id,)).fetchone()
            if latest['id']!=session:raise ValueError('只能接續最近一輪，不能重開歷史配額。')
            if db.execute("SELECT 1 FROM branch_jobs WHERE run_id=? AND state='running'",(run_id,)).fetchone():
                raise ValueError('仍有未確認分支，不自動重送。')
            for table in ('model_attempts','model_tests','glossary_reviews','model_parts'):
                if db.execute(f"SELECT 1 FROM {table} a JOIN batches b ON a.batch_id=b.id WHERE b.run_id=? AND a.state='pending'",(run_id,)).fetchone():
                    raise ValueError('仍有未確認的模型請求，不能接續。')
            completed=db.execute("SELECT COUNT(*) FROM branch_batches WHERE session_id=? AND state='approved'",(session,)).fetchone()[0]
            if completed>=row['requested']:raise ValueError('本輪配額已完成，不另外加跑。')
            db.execute("UPDATE branch_jobs SET state='ready',error=NULL WHERE run_id=? AND state='failed'",(run_id,))
            db.execute("UPDATE branch_sessions SET concurrency=?,status='running',stop=0,error=NULL WHERE id=?",(concurrency,session))
            self.live_sessions.add(session)
        if background:
            thread=threading.Thread(target=self._run,args=(session,key,transport),daemon=True)
            self.threads[session]=thread;thread.start()
        else:self._run(session,key,transport)
        return dict(session_id=session,requested=row['requested'],completed_before=completed,remaining=row['requested']-completed,concurrency=concurrency,notice=f'已接續原輪剩餘{row["requested"]-completed}批，同時最多{concurrency}支；不重送已提交批次。')

    def _plan(self,db,job,session):
        from translation_workspace import BATCH_CHARACTERS
        path=json.loads(job['path']);units=[];finished=[];spawn=[];characters=0;done=False
        refs=self.ws._refs(db,job['run_id'],dict(stack=path))
        while path:
            frame=path[-1];node=frame['node']
            owner=db.execute('SELECT * FROM branch_nodes WHERE run_id=? AND node=?',(job['run_id'],node)).fetchone()
            if owner is not None and owner['owner']!=job['id']:
                if units:break
                # Textless predecessors need no model call. Finish them before
                # parking at an existing owner, otherwise other paths wait forever.
                finish_nodes(db,job['run_id'],job['id'],finished)
                state='context_review' if owner['state']=='done' else 'waiting'
                db.execute('UPDATE branch_jobs SET state=?,wait_node=? WHERE id=?',(state,node,job['id']))
                db.execute('INSERT OR IGNORE INTO branch_merges(run_id,job_id,node,owner,path,state)VALUES(?,?,?,?,?,?)',(job['run_id'],job['id'],node,owner['owner'],json.dumps(path),state))
                return None
            if owner is None:db.execute("INSERT INTO branch_nodes VALUES(?,?,?,'reserved')",(job['run_id'],node,job['id']))
            available=self.ws.by_node[node]
            while frame['cursor']<len(available):
                uid=available[frame['cursor']];size=self.ws.units[uid]['characters']
                if size>BATCH_CHARACTERS:
                    if units:break
                    db.execute("UPDATE branch_jobs SET state='failed',error=? WHERE id=?",('單一完整段落超過一萬字元，未送出模型。',job['id']))
                    return None
                if units and characters+size>BATCH_CHARACTERS:break
                units.append(uid);characters+=size;frame['cursor']+=1
            if frame['cursor']<len(available):break
            finished.append(node)
            children=self.ws.children[node]
            remaining=list(range(frame.get('child',0),len(children)))
            if len(remaining)>1:
                for edge_index in remaining:
                    child_path=deepcopy(path);child_path[-1]['child']=edge_index+1
                    child_path.append(dict(node=children[edge_index]['target'],cursor=0,child=0,entered=False))
                    spawn.append(child_path)
                done=True;break
            if not remaining:done=True;break
            edge_index=remaining[0];frame['child']=edge_index+1
            path.append(dict(node=children[edge_index]['target'],cursor=0,child=0,entered=False))
            if characters>=BATCH_CHARACTERS:break
        advance=dict(path=path,finished=finished,spawn=spawn,done=done)
        if not units:
            finish_nodes(db,job['run_id'],job['id'],finished)
            for child_path in spawn:add_job(db,job['run_id'],child_path)
            db.execute('UPDATE branch_jobs SET path=?,state=? WHERE id=?',(json.dumps(path),'done' if done else 'ready',job['id']))
            return None
        if len(units)!=len(set(units)):raise ValueError('分支排程出現重複來源，未送出。')
        version,dictionary=self.ws._dictionary(db,refs+units)
        ordinal=db.execute('SELECT COALESCE(MAX(ordinal),0)+1 FROM batches WHERE run_id=?',(job['run_id'],)).fetchone()[0]
        cursor=db.execute('INSERT INTO batches(run_id,ordinal,status,units,refs,path,after_state,dict_version,dictionary)VALUES(?,?,?,?,?,?,?,?,?)',(job['run_id'],ordinal,'draft',json.dumps(units),json.dumps(refs),json.dumps([f['node'] for f in path]),json.dumps(dict(stack=path,visited={},merges=[])),version,json.dumps(dictionary)))
        batch_id=cursor.lastrowid
        db.execute('INSERT INTO branch_batches(batch_id,job_id,session_id,advance)VALUES(?,?,?,?)',(batch_id,job['id'],session,json.dumps(advance)))
        db.execute('UPDATE branch_jobs SET batch_id=? WHERE id=?',(batch_id,job['id']))
        return batch_id

    def _claim(self,session):
        with self.lock,self.ws.lock,self.ws.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM branch_sessions WHERE id=?',(session,)).fetchone()
            if row['stop'] or row['status']!='running':return None
            issued=db.execute('SELECT COUNT(*) FROM branch_batches WHERE session_id=?',(session,)).fetchone()[0]
            while True:
                if issued>=row['requested']:
                    # A retry already belongs to this quota; do not block it merely
                    # because all 500 slots were allocated, nor create slot 501.
                    job=db.execute("SELECT j.* FROM branch_jobs j JOIN branch_batches b ON j.batch_id=b.batch_id WHERE j.run_id=? AND j.state='ready' AND b.session_id=? AND b.state='prepared' ORDER BY j.rowid LIMIT 1",(row['run_id'],session)).fetchone()
                else:
                    job=db.execute("SELECT * FROM branch_jobs WHERE run_id=? AND state='ready' ORDER BY (batch_id IS NULL),rowid LIMIT 1",(row['run_id'],)).fetchone()
                if job is None:return None
                if job['batch_id']:
                    db.execute('UPDATE branch_batches SET session_id=? WHERE batch_id=?',(session,job['batch_id']))
                    batch=job['batch_id']
                else:batch=self._plan(db,job,session)
                if batch is not None:
                    db.execute("UPDATE branch_jobs SET state='running' WHERE id=?",(job['id'],))
                    return dict(job_id=job['id'],batch_id=batch)

    def _worker(self,session,key,transport):
        claimed=self._claim(session)
        if not claimed:return False
        try:
            batch=self.ws.batch(claimed['batch_id'])
            if batch['machine_ready']:
                review=self.ws.review_saved_draft(batch['id'],batch['revision'],key,True,transport)
                result=dict(glossary_review=review)
            else:result=self.ws.generate(batch['id'],batch['revision'],self.ws.settings()['revision'],key,True,transport,repair_saved=True)
            if result.get('glossary_review',{}).get('state')=='failed':raise ValueError(result['glossary_review']['error'])
            current=self.ws.batch(batch['id'])
            self.ws.approve(batch['id'],current['revision'],False,automatic=True)
        except Exception as error:
            with self.lock,self.ws.lock,self.ws.db() as db:
                db.execute("UPDATE branch_jobs SET state='failed',error=? WHERE id=?",(str(error),claimed['job_id']))
                # Account-wide/network errors pause all new sends; unrelated content
                # failures stop only this job. In-flight requests are not cancelled.
                text=str(error)
                if re.search(r'(?:HTTP |回應內錯誤狀態 )(?:401|402|403|429|5\d\d)',text) or any(word in text for word in ('帳務預檢暫停','upstream_error','upstream_timeout','連線未完成','遠端可用空間不足')):
                    db.execute('UPDATE branch_sessions SET stop=1,error=? WHERE id=?',(text,session))
        return True

    def _run(self,session,key,transport):
        try:
            with self.ws.db() as db:limit=db.execute('SELECT concurrency FROM branch_sessions WHERE id=?',(session,)).fetchone()[0]
            if transport is None:
                transport=CreditGate(key,limit)
                with self.lock:self.request_gates[session]=transport
            # Each worker performs a single serial model pipeline. Refill only after
            # completed futures; no more than 30 API calls can be active at once.
            from concurrent.futures import wait,FIRST_COMPLETED
            with ThreadPoolExecutor(max_workers=limit) as pool:
                futures={pool.submit(self._worker,session,key,transport) for _ in range(limit)}
                while futures:
                    finished,futures=wait(futures,return_when=FIRST_COMPLETED)
                    results=[f.result() for f in finished]
                    if any(results):
                        # Forks may have appeared while other workers found no ready job.
                        for _ in range(limit-len(futures)):futures.add(pool.submit(self._worker,session,key,transport))
            with self.lock,self.ws.db() as db:
                row=db.execute('SELECT * FROM branch_sessions WHERE id=?',(session,)).fetchone()
                approved=db.execute("SELECT COUNT(*) FROM branch_batches WHERE session_id=? AND state='approved'",(session,)).fetchone()[0]
                failed=db.execute("SELECT COUNT(*) FROM branch_jobs WHERE run_id=? AND state='failed'",(row['run_id'],)).fetchone()[0]
                state='paused' if row['stop'] else 'complete' if approved==row['requested'] else 'blocked' if failed else 'exhausted'
                db.execute('UPDATE branch_sessions SET status=? WHERE id=?',(state,session))
        except Exception as error:
            with self.ws.db() as db:db.execute("UPDATE branch_sessions SET status='failed',error=? WHERE id=?",(str(error),session))
        finally:
            with self.lock:self.live_sessions.discard(session)

    def stop(self,session):
        with self.lock,self.ws.db() as db:
            if not db.execute("SELECT 1 FROM branch_sessions WHERE id=? AND status='running'",(session,)).fetchone():raise ValueError('找不到執行中的分支工作。')
            db.execute('UPDATE branch_sessions SET stop=1 WHERE id=?',(session,))
        return dict(notice='停止啟動新批次，已送出的各分支完成當批後停止。')

    def status(self,run_id):
        with self.lock,self.ws.db() as db:
            session=db.execute('SELECT * FROM branch_sessions WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run_id,)).fetchone()
            if session is None:return dict(enabled=branch_mode(db,run_id),state='idle',jobs=[],counts={})
            result=dict(session);result['session_id']=result.pop('id')
            result['state']=result.pop('status')
            if result['state']=='running' and result['session_id'] not in self.live_sessions:result['state']='unconfirmed'
            rows=list(db.execute('SELECT * FROM branch_jobs WHERE run_id=? ORDER BY rowid',(run_id,)))
            counts={state:sum(r['state']==state for r in rows) for state in ('ready','running','waiting','context_review','failed','done')}
            completed=db.execute("SELECT COUNT(*) FROM branch_batches WHERE session_id=? AND state='approved'",(result['session_id'],)).fetchone()[0]
            result.update(enabled=True,counts=counts,completed=completed,jobs=[dict(id=r['id'],state=r['state'],batch_id=r['batch_id'],node=self.ws.nodes[json.loads(r['path'])[-1]['node']]['name'],error=r['error']) for r in rows if r['state']!='done'][:100])
            if result['session_id'] in self.request_gates:result['request_gate']=self.request_gates[result['session_id']].snapshot()
            for job in result['jobs']:
                if job['state']!='running':continue
                pro=db.execute("SELECT request FROM glossary_reviews WHERE batch_id=? AND state='pending' LIMIT 1",(job['batch_id'],)).fetchone()
                flash=db.execute("SELECT request FROM model_attempts WHERE batch_id=? AND state='pending' ORDER BY id DESC LIMIT 1",(job['batch_id'],)).fetchone()
                review_request=json.loads(pro['request']) if pro else {}
                job['stage']=review_label(review_request.get('request',review_request).get('model')) if pro else ('局部修正' if json.loads(flash['request']).get('repair_round') else 'Flash 翻譯') if flash else '準備／驗證／保存'
            return result
