"""Synthetic branch pipelines only; transport is always mocked, no paid calls."""
from collections import Counter
import json
import threading
import unittest

from branch_scheduler import BranchScheduler
import test_translation as fixtures
from test_translation import model_reply, review_reply


class BranchTests(unittest.TestCase):
    setUp=fixtures.TranslationTests.setUp
    tearDown=fixtures.TranslationTests.tearDown
    workspace=fixtures.TranslationTests.workspace
    approve=fixtures.TranslationTests.approve

    def start(self,service,count=100,limit=3,transport=None,background=False):
        run=service.new_run('same progress')['id'];scheduler=BranchScheduler(service)
        scheduler.start(run,count,limit,'synthetic-only',True,transport or self.echo,background)
        return run,scheduler

    @staticmethod
    def echo(url,request,key):
        body=json.loads(request['messages'][1]['content'])
        if body['task_type']=='glossary_review':
            return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='synthetic') for p in body['proposals']])
        return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in body['new_units']])

    def test_common_prefix_commits_before_children_and_refs_never_include_siblings(self):
        service=self.workspace(['common root','left leaf','right leaf'],[(0,1),(0,2)])
        calls=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);ids=[u['id'] for u in body['new_units']];calls.extend(ids)
            if ids!=['u0']:
                self.assertEqual([r['id'] for r in body['references']],['u0'])
                with service.db() as db:self.assertTrue(db.execute("SELECT 1 FROM translations WHERE unit_id='u0'").fetchone())
            return self.echo(url,request,key)
        run,scheduler=self.start(service,transport=transport)
        self.assertEqual(Counter(calls),Counter(['u0','u1','u2']))
        self.assertEqual(scheduler.status(run)['counts']['done'],3)
        with self.assertRaisesRegex(ValueError,'分支佇列'):service.prepare(run)
        self.assertIsNone(service.progress(run)['remaining_batches'])

    def test_schema_setup_is_additive_and_does_not_enable_or_send_any_work(self):
        service=self.workspace(['root'],[]);run=service.new_run('same')['id']
        with service.db() as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
        scheduler=BranchScheduler(service)
        with service.db() as db:
            after={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in tables}
            self.assertEqual(db.execute('SELECT COUNT(*) FROM branch_runs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_attempts').fetchone()[0],0)
        self.assertEqual(before,after)
        self.assertFalse(scheduler.status(run)['enabled'])

    def test_thirty_real_overlapping_mock_calls_and_forty_children_without_duplicates(self):
        service=self.workspace(['shared']+['leaf '+str(i) for i in range(40)],[(0,i) for i in range(1,41)])
        barrier=threading.Barrier(30,timeout=20);lock=threading.Lock();active=0;peak=0;leaves=0;seen=[]
        def transport(url,request,key):
            nonlocal active,peak,leaves
            body=json.loads(request['messages'][1]['content']);ids=[u['id'] for u in body['new_units']]
            if ids==['u0']:return self.echo(url,request,key)
            with lock:
                leaves+=1;position=leaves;active+=1;peak=max(peak,active);seen.extend(ids)
            try:
                if position<=30:barrier.wait()
                return self.echo(url,request,key)
            finally:
                with lock:active-=1
        run,scheduler=self.start(service,count=41,limit=30,transport=transport)
        self.assertEqual(peak,30)
        self.assertEqual(len(seen),40);self.assertEqual(len(set(seen)),40)
        self.assertEqual(scheduler.status(run)['completed'],41)
        self.assertEqual(scheduler.status(run)['state'],'complete')

    def test_requested_batch_budget_and_explicit_resume_do_not_repeat_approved_work(self):
        service=self.workspace(['root']+['leaf '+str(i) for i in range(8)],[(0,i) for i in range(1,9)])
        seen=[]
        def transport(url,request,key):
            seen.extend(u['id'] for u in json.loads(request['messages'][1]['content'])['new_units'])
            return self.echo(url,request,key)
        run,scheduler=self.start(service,count=3,limit=30,transport=transport)
        self.assertEqual(len(seen),3)
        self.assertEqual(scheduler.status(run)['completed'],3)
        for _ in range(3):scheduler.status(run)
        self.assertEqual(len(seen),3)
        scheduler.start(run,2,30,'synthetic-only',True,transport,False)
        self.assertEqual(len(seen),5);self.assertEqual(len(set(seen)),5)

    def test_join_has_one_owner_and_preserves_other_path_as_context_review(self):
        service=self.workspace(['root','left','right','join','tail'],[(0,1),(0,2),(1,3),(2,3),(3,4)])
        seen=[];lock=threading.Lock();barrier=threading.Barrier(2,timeout=10)
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);ids=[u['id'] for u in body['new_units']]
            with lock:seen.extend(ids)
            if ids!=['u0']:barrier.wait()
            return self.echo(url,request,key)
        run,scheduler=self.start(service,limit=2,transport=transport)
        self.assertEqual(Counter(seen),Counter(['u0','u1','u2','u3','u4']))
        self.assertEqual(scheduler.status(run)['counts']['context_review'],1)
        with service.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM branch_nodes WHERE node=3").fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM branch_merges').fetchone()[0],1)

    def test_content_failure_only_stops_one_branch_and_later_retry_is_sparse(self):
        service=self.workspace(['root','left','right'],[(0,1),(0,2)]);seen=[]
        def failing(url,request,key):
            ids=[u['id'] for u in json.loads(request['messages'][1]['content'])['new_units']];seen.extend(ids)
            if ids==['u1']:raise ValueError('synthetic content failure')
            return self.echo(url,request,key)
        run,scheduler=self.start(service,count=3,transport=failing)
        self.assertEqual(scheduler.status(run)['completed'],2)
        self.assertEqual(scheduler.status(run)['counts']['failed'],1)
        retried=[]
        def success(url,request,key):
            retried.extend(u['id'] for u in json.loads(request['messages'][1]['content'])['new_units'])
            return self.echo(url,request,key)
        scheduler.start(run,1,3,'synthetic-only',True,success,False)
        self.assertEqual(retried,['u1'])

    def test_stop_finishes_inflight_and_does_not_issue_another_batch(self):
        service=self.workspace(['root']+['leaf '+str(i) for i in range(6)],[(0,i) for i in range(1,7)])
        two=threading.Event();release=threading.Event();lock=threading.Lock();calls=[]
        def transport(url,request,key):
            ids=[u['id'] for u in json.loads(request['messages'][1]['content'])['new_units']]
            with lock:
                calls.extend(ids)
                if len(calls)==3:two.set()
            if ids!=['u0']:
                if not release.wait(10):raise ValueError('fixture timeout')
            return self.echo(url,request,key)
        run,scheduler=self.start(service,count=7,limit=2,transport=transport,background=True)
        try:
            self.assertTrue(two.wait(10));s=scheduler.status(run)
            self.assertEqual(s['counts']['running'],2)
            scheduler.stop(s['session_id'])
        finally:release.set()
        scheduler.threads[s['session_id']].join(15)
        self.assertEqual(len(calls),3);self.assertEqual(scheduler.status(run)['state'],'paused')

    def test_sequential_checkpoint_conversion_preserves_partial_path_and_legacy_cursor(self):
        service=self.workspace(['r'*6000,'c'*6000,'left','right','outer'],[(0,1),(1,2),(1,3),(0,4)])
        run=service.new_run('single')['id'];self.approve(service,service.prepare(run)['id'])
        with service.db() as db:legacy=db.execute('SELECT state FROM runs WHERE id=?',(run,)).fetchone()[0]
        seen=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);seen.extend(u['id'] for u in body['new_units'])
            for ref in body['references']:
                self.assertNotIn(ref['id'],[u['id'] for u in body['new_units']])
            self.assertLessEqual(sum(len(r['source']) for r in body['references']),10000)
            return self.echo(url,request,key)
        scheduler=BranchScheduler(service);scheduler.start(run,20,4,'synthetic-only',True,transport,False)
        self.assertEqual(Counter(seen),Counter(['u1','u2','u3','u4']))
        with service.db() as db:
            self.assertEqual(db.execute('SELECT state FROM runs WHERE id=?',(run,)).fetchone()[0],legacy)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM translations').fetchone()[0],5)

    def test_unconfirmed_session_and_invalid_limits_never_start_paid_calls(self):
        service=self.workspace(['root'],[]);run=service.new_run('single')['id'];scheduler=BranchScheduler(service)
        for count,limit,confirmed in [(1,31,True),(0,30,True),(1,1,False)]:
            with self.assertRaises(ValueError):scheduler.start(run,count,limit,'synthetic-only',confirmed,self.echo,False)
        with service.db() as db:
            db.execute("INSERT INTO branch_sessions(id,run_id,requested,concurrency,status)VALUES('orphan',?,1,1,'running')",(run,))
        self.assertEqual(scheduler.status(run)['state'],'unconfirmed')
        with self.assertRaises(ValueError):scheduler.start(run,1,1,'synthetic-only',True,self.echo,False)

    def test_oversized_atomic_source_stops_before_model(self):
        service=self.workspace(['x'*10001],[])
        def never(*args):raise AssertionError('must not spend')
        run,scheduler=self.start(service,transport=never)
        self.assertEqual(scheduler.status(run)['completed'],0)
        self.assertEqual(scheduler.status(run)['counts']['failed'],1)

    def test_no_shared_upstream_continues_down_empty_source_node_without_sibling_context(self):
        service=self.workspace(['unused','left','right'],[(0,1),(0,2)])
        service.by_node[0]=[];seen=[]
        def transport(url,request,key):
            payload=json.loads(request['messages'][1]['content'])
            self.assertEqual(payload['references'],[])
            seen.extend(u['id'] for u in payload['new_units'])
            return self.echo(url,request,key)
        run,scheduler=self.start(service,count=2,limit=5,transport=transport)
        self.assertEqual(scheduler.status(run)['completed'],2)
        self.assertEqual(set(seen),{'u1','u2'})

    def test_no_shared_upstream_descends_empty_chain_then_uses_only_own_approved_path(self):
        service=self.workspace(['unused','a'*6000,'b'*6000],[(0,1),(1,2)])
        service.by_node[0]=[];references=[];seen=[]
        def transport(url,request,key):
            payload=json.loads(request['messages'][1]['content']);references.append([u['id'] for u in payload['references']]);seen.extend(u['id'] for u in payload['new_units'])
            return self.echo(url,request,key)
        run,scheduler=self.start(service,count=2,limit=5,transport=transport)
        self.assertEqual(scheduler.status(run)['completed'],2)
        self.assertEqual(seen,['u1','u2']);self.assertEqual(references,[[],['u1']])

    def test_empty_chain_before_existing_merge_releases_waiters_without_model(self):
        from branch_scheduler import add_job
        service=self.workspace(['empty','empty','join'],[(0,1),(1,2)])
        service.by_node[0]=[];service.by_node[1]=[];scheduler=BranchScheduler(service);run=service.new_run('same')['id']
        with service.db() as db:
            scheduler._initialize(db,run)
            db.execute("INSERT INTO branch_nodes VALUES(?,?,?,'done')",(run,2,'prior-owner'))
            add_job(db,run,[dict(node=1,cursor=0,child=0,entered=False)])
            waiter=db.execute('SELECT id FROM branch_jobs ORDER BY rowid DESC LIMIT 1').fetchone()[0]
            db.execute("UPDATE branch_jobs SET state='waiting',wait_node=1 WHERE id=?",(waiter,))
        def never(*args):raise AssertionError('No duplicate model for owned merge')
        scheduler.start(run,1,1,'synthetic-only',True,never,False)
        with service.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM branch_nodes WHERE state='reserved'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state FROM branch_jobs WHERE id=?',(waiter,)).fetchone()[0],'context_review')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_attempts').fetchone()[0],0)

    def test_reconcile_stranded_textless_frontier_never_marks_untranslated_text_done(self):
        from branch_scheduler import add_job,reconcile_empty_frontiers
        service=self.workspace(['empty','real text'],[(0,1)]);service.by_node[0]=[];scheduler=BranchScheduler(service);run=service.new_run('same')['id']
        with service.db() as db:
            scheduler._initialize(db,run);owner=db.execute('SELECT id FROM branch_jobs').fetchone()[0]
            db.execute("UPDATE branch_jobs SET state='context_review' WHERE id=?",(owner,))
            for node in (0,1):db.execute("INSERT INTO branch_nodes VALUES(?,?,?,'reserved')",(run,node,owner))
            repaired=reconcile_empty_frontiers(db,service,run)
            self.assertEqual(repaired,[0]);self.assertEqual(db.execute('SELECT state FROM branch_nodes WHERE node=1').fetchone()[0],'reserved')

    def test_resume_same_hundred_quota_at_five_preserves_fifty_three_approvals(self):
        service=self.workspace(['root']+['leaf '+str(i) for i in range(100)],[(0,i) for i in range(1,101)])
        run=service.new_run('same')['id'];scheduler=BranchScheduler(service);seen=[]
        def pause(url,request,key):
            seen.extend(u['id'] for u in json.loads(request['messages'][1]['content'])['new_units'])
            if len(seen)==53:scheduler.stop(scheduler.status(run)['session_id'])
            return self.echo(url,request,key)
        scheduler.start(run,100,1,'synthetic-only',True,pause,False)
        state=scheduler.status(run);self.assertEqual(state['completed'],53);self.assertEqual(state['state'],'paused')
        before=set(seen);resumed=[]
        def remaining(url,request,key):
            ids=[u['id'] for u in json.loads(request['messages'][1]['content'])['new_units']]
            self.assertFalse(before.intersection(ids));resumed.extend(ids)
            return self.echo(url,request,key)
        result=scheduler.resume(state['session_id'],5,'synthetic-only',True,remaining,False)
        self.assertEqual(result['remaining'],47);self.assertEqual(result['session_id'],state['session_id'])
        final=scheduler.status(run);self.assertEqual(final['concurrency'],5)
        self.assertEqual(final['requested'],100);self.assertEqual(final['completed'],100);self.assertEqual(final['state'],'complete')
        self.assertEqual(len(resumed),47);self.assertEqual(len(set(resumed)),47)
        with self.assertRaises(ValueError):scheduler.resume(state['session_id'],5,'synthetic-only',True,self.echo,False)

    def test_resume_blocks_unconfirmed_pending_and_invalid_limits_without_spend(self):
        service=self.workspace(['root','leaf'],[(0,1)]);run=service.new_run('same')['id'];scheduler=BranchScheduler(service)
        def fail(*args):raise ValueError('HTTP 402 synthetic')
        scheduler.start(run,2,1,'synthetic-only',True,fail,False);session=scheduler.status(run)['session_id']
        for limit,confirmed in ((0,True),(31,True),(5,False)):
            with self.assertRaises(ValueError):scheduler.resume(session,limit,'synthetic-only',confirmed,self.echo,False)
        with service.db() as db:db.execute("UPDATE model_attempts SET state='pending'")
        with self.assertRaisesRegex(ValueError,'未確認'):scheduler.resume(session,5,'synthetic-only',True,self.echo,False)
        self.assertEqual(scheduler.status(run)['state'],'paused')

    def test_same_quota_retry_after_every_slot_allocated_never_creates_extra_batch(self):
        service=self.workspace(['root','left','right'],[(0,1),(0,2)]);run=service.new_run('same')['id'];scheduler=BranchScheduler(service)
        def failure(url,request,key):
            body=json.loads(request['messages'][1]['content'])
            if [u['id'] for u in body['new_units']]==['u1']:raise ValueError('synthetic content error')
            return self.echo(url,request,key)
        scheduler.start(run,3,2,'synthetic-only',True,failure,False);state=scheduler.status(run)
        self.assertEqual(state['completed'],2);self.assertEqual(state['state'],'blocked')
        calls=[]
        def fixed(url,request,key):calls.extend(u['id'] for u in json.loads(request['messages'][1]['content'])['new_units']);return self.echo(url,request,key)
        scheduler.resume(state['session_id'],5,'synthetic-only',True,fixed,False)
        self.assertEqual(calls,['u1']);self.assertEqual(scheduler.status(run)['completed'],3)
        self.assertEqual(scheduler.status(run)['state'],'complete')
        with service.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM batches').fetchone()[0],3)

    def test_account_and_embedded_upstream_errors_pause_all_new_work(self):
        service=self.workspace(['root','left','right'],[(0,1),(0,2)])
        for message in ('HTTP 401','HTTP 402','HTTP 403','HTTP 429','HTTP 503','HTTP 200（回應內錯誤狀態 502） upstream_error'):
            calls=[]
            def fail(*args):calls.append(1);raise ValueError(message)
            run,scheduler=self.start(service,count=3,limit=3,transport=fail)
            self.assertEqual(len(calls),1)
            self.assertEqual(scheduler.status(run)['state'],'paused')
            self.assertEqual(scheduler.status(run)['completed'],0)

    def test_approval_rollback_does_not_spawn_children_or_mark_source_done(self):
        service=self.workspace(['root','left','right'],[(0,1),(0,2)])
        with service.db() as db:
            db.execute("CREATE TRIGGER fail_branch_commit BEFORE INSERT ON events WHEN NEW.kind='auto_submitted' BEGIN SELECT RAISE(ABORT,'fixture commit failure'); END")
        run,scheduler=self.start(service,count=3,limit=2)
        with service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM translations').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM branch_jobs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT state FROM branch_nodes').fetchone()[0],'reserved')
            self.assertEqual(db.execute('SELECT state FROM branch_batches').fetchone()[0],'prepared')
            db.execute('DROP TRIGGER fail_branch_commit')
        def never(*args):raise AssertionError('saved model draft must not be retranslated')
        scheduler.start(run,1,2,'synthetic-only',True,never,False)
        self.assertEqual(scheduler.status(run)['completed'],1)
        with service.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM branch_jobs').fetchone()[0],3)

    def test_parallel_dictionary_conflict_is_rereviewed_without_retranslating_body(self):
        service=self.workspace(['root','class A','class B'],[(0,1),(0,2)])
        barrier=threading.Barrier(2,timeout=15);lock=threading.Lock();reviews=0;translations=0
        def transport(url,request,key):
            nonlocal reviews,translations
            body=json.loads(request['messages'][1]['content'])
            if body['task_type']=='glossary_review':
                with lock:reviews+=1
                if 'format_repair' not in body:barrier.wait()
                else:self.assertTrue(body['related_dictionary'])
                return review_reply([dict(proposal_id=p['proposal_id'],decision='reject' if 'format_repair' in body else 'approve',reason='synthetic conflict review') for p in body['proposals']])
            with lock:translations+=1
            units=body['new_units'];proposal=[]
            if units[0]['id']!='u0':proposal=[dict(action='add_term',source='class',sense='課程',target=units[0]['source'][-1],usage=units[0]['source'],term_type='general',existing_term_id=None,unit_ids=[units[0]['id']],reason='branch-specific')]
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in units],proposal)
        run,scheduler=self.start(service,count=3,limit=2,transport=transport)
        self.assertEqual(scheduler.status(run)['completed'],3)
        self.assertEqual(translations,3);self.assertEqual(reviews,3)
        self.assertEqual(len(service.glossary()['entries']),1)


if __name__=='__main__':unittest.main()
