from pathlib import Path
import tempfile
import unittest

from extract import write_json, digest
from translation_catalog import protect, validate_target
from translation_workspace import Workspace


def model_reply(items, proposals=None):
    import json
    return dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(dict(translations=items, glossary_proposals=proposals or []))))], usage=dict(total_tokens=12))


def review_reply(items):
    import json
    return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(glossary_reviews=items))))],usage=dict(total_tokens=8))


def proposal_reply(items):
    import json
    return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(glossary_proposals=items))))],usage=dict(total_tokens=3))


class FakeSources:
    def __init__(self, values):
        self.values = values

    def value(self, unit):
        return self.values[unit['id']]

    def conditions(self, unit):
        return []


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'static-story').mkdir()
        (self.root / 'translation').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def workspace(self, texts, edges):
        nodes = [dict(id=i, name=f'node-{i}') for i in range(len(texts))]
        graph = dict(root=0, nodes=nodes, edges=[dict(source=a, target=b, resolution='static', option=f'option-{b}') for a, b in edges])
        write_json(self.root / 'static-story/graph.json', graph)
        raw = (self.root / 'static-story/graph.json').read_bytes()
        write_json(self.root / 'static-story/branches-acyclic.json', list(range(len(edges))))
        units = [dict(id=f'u{i}', node=i, characters=len(t.replace(' ', '')), role='eligible') for i, t in enumerate(texts)]
        write_json(self.root / 'translation/catalog.json', dict(graph_hash=digest(raw), summary=dict(total=len(units), eligible=len(units), review=0), units=units))
        return Workspace(self.root, FakeSources({f'u{i}': t for i, t in enumerate(texts)}))

    def approve(self, service, batch_id):
        batch = service.batch(batch_id)
        targets = {u['id']: u['source'] for u in batch['units']}
        saved = service.save(batch_id, targets, batch['revision'])
        service.approve(batch_id, saved['revision'], True)

    def assert_draft_content_unchanged(self,service,bid,before):
        current=service.batch(bid)
        self.assertEqual({k:v for k,v in current.items() if k!='retained_results'},
                         {k:v for k,v in before.items() if k!='retained_results'})

    def test_nearest_fork_reference_excludes_finished_sibling(self):
        service = self.workspace(['common root', 'a'*6000, 'b'*5000, 'shared'], [(0, 1), (0, 2), (1, 3), (2, 3)])
        run = service.new_run('test')['id']
        first = service.prepare(run)['id']
        self.assertEqual([i['id'] for i in service.batch(first)['units']], ['u0', 'u1', 'u3'])
        self.approve(service, first)
        second = service.prepare(run)['id']
        batch = service.batch(second)
        self.assertEqual([i['id'] for i in batch['units']], ['u2'])
        self.assertEqual([i['id'] for i in batch['references']], ['u0'])
        self.assertTrue(set(i['id'] for i in batch['units']).isdisjoint(i['id'] for i in batch['references']))
        self.approve(service, second)
        self.assertEqual(service.prepare(run)['status'], 'context_review')

    def test_source_budget_reference_budget_and_repeated_prepare_idempotent(self):
        service = self.workspace([chr(97+i)*6000 for i in range(7)], [(i, i+1) for i in range(6)])
        run = service.new_run('test')['id']
        for expected in [[f'u{i}'] for i in range(7)]:
            batch_id = service.prepare(run)['id']
            self.assertEqual(service.prepare(run)['id'], batch_id)
            batch = service.batch(batch_id)
            self.assertEqual([i['id'] for i in batch['units']], expected)
            self.assertLessEqual(batch['source_characters'], 10000)
            self.assertLessEqual(batch['reference_characters'], 10000)
            if expected == ['u6']:
                self.assertEqual([i['id'] for i in batch['references']], ['u5'])
            self.approve(service, batch_id)
        self.assertEqual(service.prepare(run)['status'], 'eligible_units_finished')

    def assert_proposal_reference_repair(self,test):
        import json
        from copy import deepcopy
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        before=service.batch(bid)
        items=[dict(id='u0',target='Alice 到了。',term_ids=[],note='')]
        proposal=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='稱呼',term_type='name',existing_term_id=None,unit_ids=['wrong-branch-id'],reason='保留拼寫')
        calls=[]
        def transport(url,request,key):
            calls.append(deepcopy(request))
            if len(calls)==1:return model_reply(items,[proposal])
            if len(calls)==2:
                feedback=json.loads(request['messages'][-1]['content'])
                self.assertEqual([u['id'] for u in feedback['source_units']],['u0'])
                self.assertIn('wrong-branch-id',feedback['invalid_proposals'][0]['error'])
                self.assertNotIn('Alice 到了。',request['messages'][1]['content'])
                self.assertEqual(set(request['response_format']['json_schema']['schema']['properties']),{'glossary_proposals'})
                self.assertEqual(request['messages'][0],calls[0]['messages'][0])
                self.assertEqual(request['model'],calls[0]['model'])
                return proposal_reply([dict(proposal,unit_ids=['u0'])])
            payload=json.loads(request['messages'][1]['content'])
            return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='核准') for p in payload['proposals']])
        result=service.generate(bid,0,0,'fake',True,transport,test=test)
        self.assertEqual(len(calls),3)
        self.assertEqual(result['correction_attempts'],1)
        self.assertEqual(result['correction_usage'],[dict(total_tokens=3)])
        self.assertEqual(result['glossary_review']['approved'],1)
        table='model_tests' if test else 'model_attempts'
        with service.db() as db:
            rows=db.execute(f'SELECT id,state,request,response FROM {table} ORDER BY id').fetchall()
            self.assertEqual([r['state'] for r in rows],['needs_review','test_complete' if test else 'saved_draft'])
            self.assertEqual(json.loads(rows[1]['request'])['correction_of'],rows[0]['id'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM glossary_proposals').fetchone()[0],1)
        if test:self.assertEqual(service.batch(bid),before)
        else:self.assertEqual(service.batch(bid)['units'][0]['target'],'Alice 到了。')

    def test_formal_proposal_reference_is_repaired_once_then_reviewed(self):
        self.assert_proposal_reference_repair(False)

    def test_test_proposal_reference_is_repaired_without_changing_draft(self):
        self.assert_proposal_reference_repair(True)

    def test_bad_proposal_reference_stops_after_three_corrections(self):
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        before=service.batch(bid)
        proposal=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='稱呼',term_type='name',existing_term_id=None,unit_ids=['wrong'],reason='拼寫')
        calls=[]
        def transport(*args):
            calls.append(1)
            return model_reply([dict(id='u0',target='Alice 到了。',term_ids=[],note='')],[proposal]) if len(calls)==1 else proposal_reply([proposal])
        with self.assertRaisesRegex(ValueError,'局部修正3次'):
            service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),4)
        self.assert_draft_content_unchanged(service,bid,before)
        self.assertEqual(service.batch(bid)['retained_results'][0]['repair_attempts'],3)
        with service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM glossary_proposals').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM glossary_reviews').fetchone()[0],0)
            self.assertEqual([r[0] for r in db.execute('SELECT state FROM model_attempts')],['needs_review']*4)

    def test_non_reference_errors_never_start_correction(self):
        service=self.workspace(['Hello'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        calls=[]
        def transport(*args):
            calls.append(1)
            raise ValueError('HTTP 402')
        with self.assertRaisesRegex(ValueError,'402'):
            service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),1)

    def test_reuse_failed_translation_only_resends_bad_proposal(self):
        import json
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        proposal=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='稱呼',term_type='name',existing_term_id=None,unit_ids=['wrong'],reason='拼寫')
        original=model_reply([dict(id='u0',target='Alice 到了。',term_ids=[],note='')],[proposal])
        prepared=service.prompt(bid)
        # Simulate the real failed request from before minItems was added.
        prepared['request']['response_format']['json_schema']['schema']['properties']['glossary_proposals']['items']['properties']['unit_ids'].pop('minItems')
        with service.db() as db:
            original_id=db.execute("INSERT INTO model_attempts(batch_id,revision,state,request,response,error)VALUES(?,0,'needs_review',?,?,?)",(bid,json.dumps(prepared),json.dumps(original),'old error')).lastrowid
        calls=[]
        def transport(url,request,key):
            payload=json.loads(request['messages'][1]['content']);calls.append(payload['task_type'])
            if payload['task_type']=='glossary_proposal_correction':
                return proposal_reply([dict(proposal,unit_ids=['u0'])])
            self.assertEqual(payload['task_type'],'glossary_review')
            return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='核准') for p in payload['proposals']])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(calls,['glossary_proposal_correction','glossary_review'])
        self.assertTrue(result['reused_translation'])
        self.assertIsNone(result['usage'])
        with service.db() as db:
            old=db.execute('SELECT error,response FROM model_attempts WHERE id=?',(original_id,)).fetchone()
            self.assertEqual(old['error'],'old error')
            self.assertEqual(json.loads(old['response']),original)
        self.assertEqual(service.batch(bid)['units'][0]['target'],'Alice 到了。')

    def test_active_request_blocks_duplicate_test_and_normal_calls(self):
        service=self.workspace(['Hello'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        calls=[]
        def transport(*args):
            calls.append(1)
            for test in (True,False):
                with self.assertRaisesRegex(ValueError,'處理中'):
                    service.generate(bid,0,0,'fake',True,transport,test=test)
            return model_reply([dict(id='u0',target='你好',term_ids=[],note='')])
        service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),1)
        self.assertEqual(service.active_batches,set())

    def test_saved_pending_request_blocks_new_spending(self):
        service=self.workspace(['Hello'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        with service.db() as db:db.execute("INSERT INTO model_attempts(batch_id,revision,state,request)VALUES(?,0,'pending','{}')",(bid,))
        with self.assertRaisesRegex(ValueError,'處理中'):
            service.generate(bid,0,0,'fake',True,lambda *args:self.fail('duplicate transport'))

    def test_invalid_translation_repair_is_bounded(self):
        service=self.workspace(['Hello'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        calls=[]
        def transport(*args):
            calls.append(1)
            return model_reply([dict(id='wrong',target='你好',term_ids=[],note='')])
        with self.assertRaisesRegex(ValueError,'局部修正'):
            service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),4)

    def test_repair_only_sends_invalid_terms_and_matching_sources(self):
        import json
        service=self.workspace(['Alice arrives.','Bob arrives.'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id']
        alice=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='稱呼',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='拼寫')
        bob=dict(alice,source='Bob',target='鮑勃',unit_ids=['wrong'])
        items=[dict(id='u0',target='Alice 到了。',term_ids=[],note=''),dict(id='u1',target='Bob 到了。',term_ids=[],note='')]
        calls=[]
        def transport(url,request,key):
            calls.append(request)
            if len(calls)==1:return model_reply(items,[alice,bob])
            payload=json.loads(request['messages'][1]['content'])
            if len(calls)==2:
                self.assertEqual([u['id'] for u in payload['source_units']],['u1'])
                self.assertEqual([p['proposal']['source'] for p in payload['invalid_proposals']],['Bob'])
                self.assertEqual(request['response_format']['json_schema']['schema']['properties']['glossary_proposals']['items']['properties']['unit_ids']['items']['enum'],['u1'])
                return proposal_reply([dict(bob,target='Bob',unit_ids=['u1'])])
            return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='核准') for p in payload['proposals']])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(result['glossary_review']['approved'],2)
        self.assertEqual({u['id']:u['target'] for u in service.batch(bid)['units']},{i['id']:i['target'] for i in items})

    def test_changed_batch_stops_before_correction_spending(self):
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='稱呼',term_type='name',existing_term_id=None,unit_ids=['wrong'],reason='拼寫')
        calls=[]
        def transport(*args):
            calls.append(1)
            service.save(bid,{},0)
            return model_reply([dict(id='u0',target='Alice 到了。',term_ids=[],note='')],[p])
        with self.assertRaisesRegex(ValueError,'批次已變更'):
            service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),1)

    def test_targeted_repair_matrix_preserves_good_units_and_reuses_failed_output(self):
        import json
        from copy import deepcopy
        original_root=self.root
        for kind in ('name','tokens','empty','html','terms','note','missing','duplicate','extra'):
            with self.subTest(kind=kind):
                self.root=original_root/kind
                (self.root/'static-story').mkdir(parents=True)
                (self.root/'translation').mkdir()
                service=self.workspace(['Niko waits <<$hour>>.','Keep original.'],[(0,1)])
                service.save_term(dict(source='Niko',sense='人物姓名',target='Niko',usage='人名',status='approved',term_type='name'))
                bid=service.prepare(service.new_run('test')['id'])['id']
                fixed=dict(id='u0',target=protect('Niko waits <<$hour>>.')[0],term_ids=[],note='')
                good=dict(id='u1',target='這段合法譯文不要重傳',term_ids=[],note='原有備註')
                items=[deepcopy(fixed),deepcopy(good)]
                if kind=='name':items[0]['target']=items[0]['target'].replace('Niko','尼可')
                if kind=='tokens':items[0]['target']='Niko waits.'
                if kind=='empty':items[0]['target']=''
                if kind=='html':items[0]['target']+='<script>bad</script>'
                if kind=='terms':items[0]['term_ids']=[999999]
                if kind=='note':items[0].pop('note')
                if kind=='missing':items=items[1:]
                if kind=='duplicate':items.append(deepcopy(items[0]))
                if kind=='extra':items.append(dict(fixed,id='other-branch'))
                prepared=service.prompt(bid)
                with service.db() as db:
                    db.execute("INSERT INTO model_attempts(batch_id,revision,state,request,response,error)VALUES(?,0,'needs_review',?,?,?)",(bid,json.dumps(prepared),json.dumps(model_reply(items)),'old validation error'))
                calls=[]
                def transport(url,request,key):
                    payload=json.loads(request['messages'][1]['content']);calls.append(payload['task_type'])
                    self.assertEqual(payload['task_type'],'translation_correction')
                    self.assertNotIn(good['target'],request['messages'][1]['content'])
                    ids=[p['id'] for p in payload['invalid_translations']]
                    self.assertEqual(ids,[] if kind=='extra' else ['u0'])
                    self.assertEqual([u['id'] for u in payload['source_units']],[] if kind=='extra' else ['u0'])
                    return model_reply([fixed] if ids else [])
                result=service.generate(bid,0,0,'fake',True,transport)
                self.assertEqual(calls,['translation_correction'])
                self.assertTrue(result['reused_translation'])
                final=service.batch(bid)
                self.assertEqual(final['units'][0]['target'],fixed['target'])
                self.assertEqual(final['units'][1]['target'],good['target'])
                self.assertEqual(final['decisions']['u1']['note'],good['note'])
        self.root=original_root

    def test_name_boundary_accepts_chinese_context_but_rejects_longer_english_name(self):
        service=self.workspace(['Niko waits.'],[])
        dictionary=[dict(source='Niko',target='Niko',term_type='name')]
        service.validate_names('u0','Niko正在等待。',dictionary)
        for wrong in ('Nikolas正在等待。','NIKO正在等待。','尼可正在等待。'):
            with self.subTest(target=wrong),self.assertRaisesRegex(ValueError,'Niko'):
                service.validate_names('u0',wrong,dictionary)

    def test_first_seen_name_proposal_also_repairs_translated_name(self):
        import json
        service=self.workspace(['Niko waits.','Other text.'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Niko',sense='人物姓名',target='Niko',usage='人名',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='保留原拼寫')
        calls=[]
        def transport(url,request,key):
            payload=json.loads(request['messages'][1]['content']);calls.append(payload['task_type'])
            if len(calls)==1:return model_reply([dict(id='u0',target='尼可等待。',term_ids=[],note=''),dict(id='u1',target='其他文字。',term_ids=[],note='')],[p])
            if len(calls)==2:
                self.assertEqual([i['id'] for i in payload['invalid_translations']],['u0'])
                self.assertEqual(payload['preserved_names'],['Niko'])
                return model_reply([dict(id='u0',target='Niko等待。',term_ids=[],note='')])
            return review_reply([dict(proposal_id=q['proposal_id'],decision='approve',reason='保留原名') for q in payload['proposals']])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(result['glossary_review']['approved'],1)
        self.assertEqual(service.batch(bid)['units'][0]['target'],'Niko等待。')
        self.assertEqual(service.batch(bid)['units'][1]['target'],'其他文字。')
        self.assertEqual(len(calls),3)

    def test_name_and_proposal_errors_are_fixed_in_one_sparse_call(self):
        import json
        service=self.workspace(['Niko waits.','Other text.'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Niko',sense='人物姓名',target='尼可',usage='人名',term_type='name',existing_term_id=None,unit_ids=['wrong'],reason='name')
        calls=[]
        def transport(url,request,key):
            payload=json.loads(request['messages'][1]['content']);calls.append(payload['task_type'])
            if len(calls)==1:return model_reply([dict(id='u0',target='尼可等待。',term_ids=[],note=''),dict(id='u1',target='這是已通過驗證的第二段文字',term_ids=[],note='')],[p])
            if len(calls)==2:
                self.assertEqual(len(payload['invalid_translations']),1)
                self.assertEqual(len(payload['invalid_proposals']),1)
                self.assertNotIn('這是已通過驗證的第二段文字',request['messages'][1]['content'])
                return model_reply([dict(id='u0',target='Niko等待。',term_ids=[],note='')],[dict(p,target='Niko',unit_ids=['u0'])])
            return review_reply([dict(proposal_id=q['proposal_id'],decision='approve',reason='核准') for q in payload['proposals']])
        service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),3)
        self.assertEqual(service.batch(bid)['units'][1]['target'],'這是已通過驗證的第二段文字')

    def test_persistent_name_error_stops_after_three_sparse_repairs(self):
        service=self.workspace(['Niko waits.'],[])
        service.save_term(dict(source='Niko',sense='人名',target='Niko',usage='稱呼',status='approved',term_type='name'))
        bid=service.prepare(service.new_run('test')['id'])['id']
        before=service.batch(bid);calls=[]
        def transport(*args):
            calls.append(1)
            return model_reply([dict(id='u0',target='尼可等待。',term_ids=[],note='')])
        with self.assertRaisesRegex(ValueError,'局部修正3次'):
            service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),4)
        self.assert_draft_content_unchanged(service,bid,before)

    def test_malformed_json_is_not_blindly_retranslated(self):
        service=self.workspace(['Hello'],[])
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def transport(*args):
            calls.append(1)
            return dict(choices=[dict(finish_reason='stop',message=dict(content='not JSON'))])
        with self.assertRaises(ValueError):service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),1)

    def test_repair_cannot_overwrite_already_valid_unit(self):
        service=self.workspace(['Niko waits.','Other text.'],[(0,1)])
        service.save_term(dict(source='Niko',sense='人名',target='Niko',usage='稱呼',status='approved',term_type='name'))
        bid=service.prepare(service.new_run('test')['id'])['id'];before=service.batch(bid);calls=[]
        def transport(*args):
            calls.append(1)
            return model_reply([dict(id='u0',target='尼可等待。' if len(calls)==1 else 'Niko等待。',term_ids=[],note=''),dict(id='u1',target='合法譯文' if len(calls)==1 else '偷偷覆寫',term_ids=[],note='')])
        with self.assertRaisesRegex(ValueError,'不得改動已合法譯文'):
            service.generate(bid,0,0,'fake',True,transport)
        self.assert_draft_content_unchanged(service,bid,before)
        self.assertEqual(len(calls),4)

    def test_batch_count_matches_actual_scheduler_and_never_writes(self):
        service=self.workspace(['root','first','second','third'],[(0,1),(0,2),(0,3)])
        run=service.new_run('test')['id']
        with service.db() as db:before=list(db.iterdump())
        self.assertEqual(service.batch_totals(run)['total_batches'],3)
        with service.db() as db:self.assertEqual(list(db.iterdump()),before)
        for index in range(3):
            bid=service.prepare(run)['id']
            totals=service.batch_totals(run)
            self.assertEqual(totals['total_batches'],3)
            self.assertEqual(totals['current_batch'],index+1)
            self.assertEqual(totals['approved_batches'],index)
            self.approve(service,bid)
        self.assertIsNone(service.prepare(run)['id'])
        self.assertEqual(service.batch_totals(run)['approved_batches'],3)
        self.assertEqual(service.batch_totals(run)['remaining_batches'],0)

    def test_batch_count_honors_fourty_thousand_and_convergence(self):
        service=self.workspace(['a'*5000,'b'*5000,'c'*5000,'d'*5000],[(0,1),(0,2),(1,3),(2,3)])
        run=service.new_run('test')['id']
        total=service.batch_totals(run)['total_batches']
        seen=[]
        while True:
            bid=service.prepare(run)['id']
            if not bid:break
            seen.append(bid)
            self.assertEqual(service.batch_totals(run)['total_batches'],total)
            self.approve(service,bid)
        self.assertEqual(total,len(seen))
        self.assertEqual(total,3)

    def test_three_repairs_keep_successes_mark_failures_and_resume_only_remaining(self):
        import json
        service=self.workspace(['Niko waits.','Alice waits.','Other text.'],[(0,1),(1,2)])
        for name in ('Niko','Alice'):service.save_term(dict(source=name,sense='人名',target=name,usage='稱呼',status='approved',term_type='name'))
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def item(uid,target):return dict(id=uid,target=target,term_ids=[],note='')
        def failing(url,request,key):
            data=json.loads(request['messages'][1]['content']);calls.append(data['task_type'])
            if len(calls)==1:return model_reply([item('u0','尼可等待。'),item('u1','愛麗絲等待。'),item('u2','完整保留的合法譯文')])
            ids=[i['id'] for i in data['invalid_translations']]
            self.assertEqual(ids,['u0','u1'] if len(calls)==2 else ['u1'])
            if len(calls)>2:self.assertNotIn('Niko等待。',request['messages'][1]['content'])
            return model_reply([item(uid,'Niko等待。' if uid=='u0' else '愛麗絲等待。') for uid in ids])
        with self.assertRaisesRegex(ValueError,'局部修正3次'):
            service.generate(bid,0,0,'fake',True,failing)
        self.assertEqual(len(calls),4)
        retained=service.batch(bid)['retained_results'][0]
        self.assertEqual(retained['repair_attempts'],3)
        self.assertEqual([i['id'] for i in retained['issues']['units']],['u1'])
        self.assertEqual({i['id']:i['target'] for i in retained['output']['translations']}['u0'],'Niko等待。')
        with self.assertRaisesRegex(ValueError,'未解決'):
            service.approve(bid,0,True)
        restored=Workspace(self.root,FakeSources({'u0':'Niko waits.','u1':'Alice waits.','u2':'Other text.'}))
        self.assertEqual(restored.batch(bid)['retained_results'],service.batch(bid)['retained_results'])
        def finish(url,request,key):
            data=json.loads(request['messages'][1]['content']);calls.append(data['task_type'])
            self.assertEqual([i['id'] for i in data['invalid_translations']],['u1'])
            self.assertNotIn('完整保留的合法譯文',request['messages'][1]['content'])
            self.assertNotIn('Niko等待。',request['messages'][1]['content'])
            return model_reply([item('u1','Alice等待。')])
        result=restored.generate(bid,0,0,'fake',True,finish)
        self.assertTrue(result['reused_translation'])
        self.assertEqual(len(calls),5)
        final=restored.batch(bid)
        self.assertEqual(final['retained_results'],[])
        self.assertEqual([u['target'] for u in final['units']],['Niko等待。','Alice等待。','完整保留的合法譯文'])

    def test_third_sparse_repair_can_succeed_without_a_fourth(self):
        service=self.workspace(['Niko waits.'],[])
        service.save_term(dict(source='Niko',sense='人名',target='Niko',usage='稱呼',status='approved',term_type='name'))
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def transport(*args):
            calls.append(1)
            return model_reply([dict(id='u0',target='Niko等待。' if len(calls)==4 else '尼可等待。',term_ids=[],note='')])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),4)
        self.assertEqual(result['correction_attempts'],3)
        self.assertEqual(service.batch(bid)['retained_results'],[])
        with service.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM model_attempts WHERE state='pending'").fetchone()[0],0)

    def test_failed_test_is_retained_but_does_not_block_formal_approval(self):
        service=self.workspace(['Niko waits.'],[])
        service.save_term(dict(source='Niko',sense='人名',target='Niko',usage='稱呼',status='approved',term_type='name'))
        bid=service.prepare(service.new_run('test')['id'])['id']
        with self.assertRaisesRegex(ValueError,'局部修正3次'):
            service.generate(bid,0,0,'fake',True,lambda *args:model_reply([dict(id='u0',target='尼可等待。',term_ids=[],note='')]),test=True)
        retained=service.batch(bid)['retained_results'][0]
        self.assertEqual(retained['test'],1)
        self.assertEqual(service.batch(bid)['units'][0]['target'],'')
        self.approve(service,bid)
        self.assertEqual(service.batch(bid)['status'],'approved')

    def test_nested_nearest_unfinished_fork_survives_restart_and_batch_splits(self):
        texts=['root','A'*5000,'outer sibling','B'*5000,'inner sibling','deep leaf','outer leaf more text']
        edges=[(0,1),(0,2),(1,3),(1,4),(3,5),(2,6),(2,5)]
        service=self.workspace(texts,edges);run=service.new_run('test')['id']
        expected=[['u0','u1'],['u3','u5'],['u4'],['u2','u6']]
        seen=[]
        for index,ids in enumerate(expected):
            bid=service.prepare(run)['id']
            service=Workspace(self.root,FakeSources({f'u{i}':t for i,t in enumerate(texts)}))
            self.assertEqual(service.prepare(run)['id'],bid)
            batch=service.batch(bid)
            self.assertEqual([u['id'] for u in batch['units']],ids)
            if index==2:self.assertEqual(batch['route'],['node-0','node-1','node-4'])
            if index==3:self.assertEqual(batch['route'],['node-0','node-2'])
            self.assertEqual(service.batch_totals(run)['total_batches'],4)
            seen.extend(ids);self.approve(service,bid)
        self.assertEqual(len(seen),len(set(seen)))
        self.assertEqual(set(seen),{f'u{i}' for i in range(len(texts))})
        self.assertEqual(service.prepare(run)['status'],'context_review')
        self.assertEqual(service.progress(run)['approved_batches'],4)

    def test_automatic_submission_is_distinct_and_requires_fresh_model_output(self):
        service=self.workspace(['root','first','second'],[(0,1),(0,2)])
        run=service.new_run('test')['id']
        for index in range(2):
            bid=service.prepare(run)['id'];batch=service.batch(bid)
            with self.assertRaisesRegex(ValueError,'剛完成'):service.approve(bid,0,False,automatic=True)
            result=service.generate(bid,0,0,'fake',True,lambda *args:model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in batch['units']]))
            ready=service.batch(bid);self.assertTrue(ready['machine_ready'])
            service.approve(bid,ready['revision'],False,automatic=True)
            self.assertEqual(service.batch(bid)['approval_mode'],'automatic')
            self.assertEqual(service.progress(run)['approved_batches'],index+1)
        with service.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='auto_submitted'").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='approved'").fetchone()[0],0)

    def test_saved_draft_resume_reviews_only_with_original_system_snapshot(self):
        import json
        service=self.workspace(['Hello friend.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id'];systems=[]
        p=dict(action='add_term',source='Hello',sense='問候',target='你好',usage='招呼',term_type='general',existing_term_id=None,unit_ids=['u0'],reason='test')
        def initial(url,request,key):
            payload=json.loads(request['messages'][1]['content'])
            if payload['task_type']=='translation':
                systems.append(request['messages'][0])
                return model_reply([dict(id='u0',target='你好，朋友。',term_ids=[],note='')],[p])
            raise ValueError('synthetic Pro failure')
        result=service.generate(bid,0,0,'fake',True,initial)
        self.assertEqual(result['glossary_review']['state'],'failed')
        ready=service.batch(bid);self.assertTrue(ready['machine_ready'])
        with self.assertRaisesRegex(ValueError,'待審用詞'):service.approve(bid,ready['revision'],False,automatic=True)
        service.save_settings('changed after Flash',0)
        calls=[]
        def review(url,request,key):
            data=json.loads(request['messages'][1]['content']);calls.append(data['task_type'])
            self.assertEqual(request['messages'][0],systems[0])
            with self.assertRaisesRegex(ValueError,'處理中'):service.review_saved_draft(bid,ready['revision'],'fake',True,review)
            return review_reply([dict(proposal_id=q['proposal_id'],decision='approve',reason='核准') for q in data['proposals']])
        service.review_saved_draft(bid,ready['revision'],'fake',True,review)
        self.assertEqual(calls,['glossary_review'])
        service.approve(bid,ready['revision'],False,automatic=True)

    def test_manual_edits_are_not_silently_automatically_submitted(self):
        service=self.workspace(['Hello'],[]);bid=service.prepare(service.new_run('test')['id'])['id']
        service.generate(bid,0,0,'fake',True,lambda *args:model_reply([dict(id='u0',target='你好',term_ids=[],note='')]))
        service.save(bid,{'u0':'手動修訂'},1)
        self.assertFalse(service.batch(bid)['machine_ready'])
        with self.assertRaisesRegex(ValueError,'剛完成'):service.approve(bid,2,False,automatic=True)

    def test_adaptive_parts_are_ordered_and_committed_only_as_one_batch(self):
        import json
        from adaptive_translation import budget_profile
        texts=[str(i)+'x'*1000 for i in range(7)]
        service=self.workspace(texts,[(i,i+1) for i in range(6)])
        service.adaptive_enabled=True  # archived split mode remains testable
        service.graph['edges'][0].update(kind='choice',option='Knock on the door')
        service.token_profile=lambda:dict(budget_profile([dict(characters=39928,usage=dict(completion_tokens=65536,completion_tokens_details=dict(reasoning_tokens=32925)))]),output_target=44000)
        bid=service.prepare(service.new_run('test')['id'])['id'];before=service.batch(bid)
        self.assertGreater(before['budget_plan']['parts'],1)
        seen=[]
        def transport(url,request,key):
            payload=json.loads(request['messages'][1]['content']);ids=[u['id'] for u in payload['new_units']];seen.extend(ids)
            self.assertLess(len(ids),len(texts))
            self.assertEqual(payload['route_context']['steps'][0]['option'],'Knock on the door')
            self.assertEqual(request['max_tokens'],65536)
            with service.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM translations').fetchone()[0],0)
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in payload['new_units']])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(seen,[u['id'] for u in before['units']])
        self.assertGreater(result['adaptive_summary']['requests'],1)
        self.assertEqual(service.batch(bid)['status'],'draft')
        self.assertEqual([u['target'] for u in service.batch(bid)['units']],texts)

    def test_single_request_uses_published_maximum_without_splitting(self):
        import json
        service=self.workspace(['first','second'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def transport(url,request,key):
            calls.append(request)
            self.assertEqual(request['max_tokens'],384000)
            body=json.loads(request['messages'][1]['content'])
            self.assertEqual(len(body['new_units']),2)
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in body['new_units']])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),1)
        self.assertIsNone(result['adaptive_summary'])
        self.assertFalse(service.batch(bid)['budget_plan']['adaptive_enabled'])

    def test_single_request_length_stops_without_adaptive_retry(self):
        service=self.workspace(['first','second'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def transport(url,request,key):
            calls.append(request)
            return dict(choices=[dict(finish_reason='length',message=dict(content='{"translations":['))])
        with self.assertRaisesRegex(ValueError,'拆包暫停'):
            service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),1)

    def test_provider_error_diagnostic_persists_without_draft_or_progress_writes(self):
        import json
        from bazaarlink import ProviderError
        service=self.workspace(['first','second'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id'];before=service.batch(bid);calls=[]
        diagnostic=dict(object='error',transport_http_status=200,http_status=502,error=dict(code='upstream_error',message='Synthetic upstream failed'),id='synthetic-request-17')
        def transport(url,request,key):
            calls.append(request)
            raise ProviderError('BazaarLink HTTP 200 / 502 upstream_error synthetic-request-17',diagnostic)
        with self.assertRaisesRegex(ValueError,'synthetic-request-17'):
            service.generate(bid,0,0,'fake',True,transport)
        after=service.batch(bid)
        self.assertEqual((after['revision'],after['status'],after['units']),(before['revision'],before['status'],before['units']))
        with service.db() as db:
            row=db.execute('SELECT response,error,state FROM model_attempts ORDER BY id DESC LIMIT 1').fetchone()
            self.assertEqual(json.loads(row['response']),diagnostic)
            self.assertEqual(row['state'],'needs_review')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM glossary_reviews').fetchone()[0],0)
        self.assertEqual(len(calls),1)

    def test_activity_observes_running_request_then_completion_without_resending(self):
        import threading,json
        service=self.workspace(['first','second'],[(0,1)])
        run=service.new_run('test')['id'];bid=service.prepare(run)['id']
        started=threading.Event();release=threading.Event();errors=[];calls=[]
        def transport(url,request,key):
            calls.append(request);started.set()
            if not release.wait(10):raise ValueError('fixture wait timed out')
            units=json.loads(request['messages'][1]['content'])['new_units']
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in units])
        def worker():
            try:service.generate(bid,0,0,'fake',True,transport)
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=worker);thread.start()
        try:
            self.assertTrue(started.wait(5))
            for _ in range(3):
                result=service.activity(run)
                self.assertTrue(result['running']);self.assertTrue(result['blocked'])
                self.assertEqual(result['stage'],'Flash 翻譯')
                self.assertEqual(result['current']['ordinal'],1)
                self.assertNotIn('messages',str(result));self.assertNotIn('fake',str(result))
            # A new server process may read persisted pending, but must not call it running.
            service.active_batches.discard(bid)
            unknown=service.activity(run)
            self.assertEqual(unknown['state'],'unconfirmed');self.assertTrue(unknown['blocked'])
            service.active_batches.add(bid)
            self.assertEqual(len(calls),1)
        finally:release.set();thread.join(10)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        result=service.activity(run)
        self.assertFalse(result['blocked']);self.assertEqual(result['state'],'idle')
        self.assertEqual(result['current']['state'],'saved_draft')
        self.assertEqual(len(calls),1)

    def test_activity_displays_repair_pro_and_terminal_error_without_writes(self):
        import json
        service=self.workspace(['first'],[]);run=service.new_run('test')['id'];bid=service.prepare(run)['id']
        with service.db() as db:
            db.execute("INSERT INTO model_attempts(batch_id,revision,state,request)VALUES(?,0,'pending',?)",(bid,json.dumps(dict(repair_round=2,request=dict(model='flash')))))
        service.active_batches.add(bid)
        self.assertEqual(service.activity(run)['stage'],'局部修正（第2輪）')
        with service.db() as db:
            db.execute("UPDATE model_attempts SET state='saved_draft'")
            db.execute("INSERT INTO glossary_reviews(batch_id,state,request)VALUES(?,'pending',?)",(bid,json.dumps(dict(request=dict(model='pro')))))
        self.assertEqual(service.activity(run)['stage'],'Pro 審詞')
        with service.db() as db:db.execute("UPDATE glossary_reviews SET state='failed',error='synthetic upstream error'")
        service.active_batches.discard(bid)
        before=service.batch(bid)
        result=service.activity(run)
        self.assertEqual(result['state'],'failed')
        self.assertEqual(result['current']['error'],'synthetic upstream error')
        self.assertEqual(service.batch(bid),before)

    def test_adaptive_network_failure_resumes_completed_parts_without_resending(self):
        import json
        from adaptive_translation import budget_profile
        texts=[str(i)+'x'*1000 for i in range(6)]
        service=self.workspace(texts,[(i,i+1) for i in range(5)])
        service.adaptive_enabled=True
        service.token_profile=lambda:dict(budget_profile([dict(characters=39928,usage=dict(completion_tokens=65536,completion_tokens_details=dict(reasoning_tokens=32925)))]),output_target=44000)
        bid=service.prepare(service.new_run('test')['id'])['id'];first=[];calls=[]
        def broken(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(1)
            if len(calls)==2:raise ValueError('HTTP 402')
            first.extend(u['id'] for u in body['new_units'])
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in body['new_units']])
        with self.assertRaisesRegex(ValueError,'402'):service.generate(bid,0,0,'fake',True,broken)
        retained=service.batch(bid)['retained_results'][0]
        self.assertEqual(len(retained['output']['translations']),len(first))
        self.assertTrue(retained['issues']['adaptive']['failed'])
        def finish(url,request,key):
            body=json.loads(request['messages'][1]['content'])
            self.assertTrue(set(first).isdisjoint(u['id'] for u in body['new_units']))
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in body['new_units']])
        result=service.generate(bid,0,0,'fake',True,finish)
        self.assertEqual(result['adaptive_summary']['resumed_units'],len(first))
        self.assertEqual(service.batch(bid)['retained_results'],[])

    def test_existing_truncated_full_reply_is_salvaged_and_only_tail_translated(self):
        import json
        service=self.workspace(['first','second','third','fourth'],[(0,1),(1,2),(2,3)])
        service.adaptive_enabled=True

        bid=service.prepare(service.new_run('test')['id'])['id'];prepared=service.prompt(bid)
        old_items=[dict(id='u0',target='第一段保留',term_ids=[],note=''),dict(id='u1',target='第二段保留',term_ids=[],note='')]
        cut='{"translations":['+','.join(json.dumps(i) for i in old_items)+',{"id":"u2","target":"cut'
        old=dict(choices=[dict(finish_reason='length',message=dict(content=cut))],usage=dict(total_tokens=100))
        with service.db() as db:
            db.execute("INSERT INTO model_attempts(batch_id,revision,state,request,response,error)VALUES(?,0,'needs_review',?,?,?)",(bid,json.dumps(prepared),json.dumps(old),'length'))
        calls=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(body['task_type'])
            if body['task_type']=='glossary_proposal_recovery':return proposal_reply([])
            self.assertEqual([u['id'] for u in body['new_units']],['u2','u3'])
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in body['new_units']])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(calls,['glossary_proposal_recovery','translation'])
        self.assertEqual(result['adaptive_summary']['resumed_units'],2)
        self.assertEqual([u['target'] for u in service.batch(bid)['units']][:2],['第一段保留','第二段保留'])

    def test_only_one_run_reuses_progress_and_ignores_names(self):
        service=self.workspace(['Hello'],[])
        first=service.new_run('first')['id']
        batch=service.prepare(first)['id']
        before=service.batch(batch)
        self.assertEqual(service.new_run('second')['id'],first)
        restored=Workspace(self.root,FakeSources({'u0':'Hello'}))
        self.assertEqual(restored.new_run('third')['id'],first)
        self.assertEqual(restored.batch(batch),before)
        self.assertEqual(len(restored.summary()['runs']),1)

    def test_ten_thousand_boundary_and_oversized_unit(self):
        service=self.workspace(['a'*5000,'b'*5000,'c'*10001],[(0,1),(1,2)])
        run=service.new_run('budget')['id']
        first=service.prepare(run)['id']
        self.assertEqual(service.batch(first)['source_characters'],10000)
        self.assertFalse(service.batch(first)['oversized'])
        self.approve(service,first)
        second=service.batch(service.prepare(run)['id'])
        self.assertEqual(second['source_characters'],10001)
        self.assertTrue(second['oversized'])
        self.assertEqual([u['id'] for u in second['units']],['u2'])

    def test_body_repair_can_propose_evidenced_new_sense_without_bad_initial_proposals(self):
        import json
        service=self.workspace(['Roman style art.','An unchanged line.'],[(0,1)])
        term=service.save_term(dict(source='Roman',sense='人名',target='Roman',usage='人物',term_type='name',status='approved'))['id']
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(body['task_type'])
            if body['task_type']=='glossary_review':return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='有原文依據') for p in body['proposals']])
            if body['task_type']=='translation':return model_reply([dict(id='u0',target='羅馬風格的藝術。',term_ids=[],note=''),dict(id='u1',target='完全不變的一段。',term_ids=[],note='')])
            self.assertEqual(body['invalid_proposals'],[])
            self.assertEqual([u['id'] for u in body['source_units']],['u0'])
            proposal=dict(action='add_sense',source='Roman',sense='古羅馬風格',target='Roman',usage='Roman style art，形容詞而非人名',term_type='general',existing_term_id=term,unit_ids=['u0'],reason='補足不同詞義')
            return model_reply([dict(id='u0',target='Roman 風格的藝術。',term_ids=[],note='')],[proposal])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(calls,['translation','translation_correction','glossary_review'])
        self.assertEqual(result['correction_attempts'],1)
        self.assertEqual(service.batch(bid)['units'][1]['target'],'完全不變的一段。')
        self.assertEqual(result['glossary_review']['approved'],1)

    def test_body_repair_new_terms_cannot_cite_unmodified_units(self):
        import json
        service=self.workspace(['Roman style art.','A coffee cup.'],[(0,1)])
        service.save_term(dict(source='Roman',sense='人名',target='Roman',usage='人物',term_type='name',status='approved'))
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(1)
            if body['task_type']=='translation':return model_reply([dict(id='u0',target='羅馬風格。',term_ids=[],note=''),dict(id='u1',target='咖啡杯。',term_ids=[],note='')])
            p=dict(action='add_term',source='coffee',sense='飲品',target='咖啡',usage='未修正段落',term_type='general',existing_term_id=None,unit_ids=['u1'],reason='不在待修範圍')
            return model_reply([dict(id='u0',target='Roman 風格。',term_ids=[],note='')],[p])
        with self.assertRaisesRegex(ValueError,'不接受無關提案'):service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),4) # bounded feedback retries, never accepts unrelated source
        self.assertEqual(service.proposals(bid)['entries'],[])

    def test_regular_plural_matching_is_shared_and_never_stems_names(self):
        for term,text in [('loser','Later losers.'),('class','Two classes'),('city','Many cities'),('bus','Two buses'),('box','boxes'),('private room','private rooms'),('small city','small cities')]:
            self.assertTrue(Workspace.term_occurs(term,text))
        for term,text in [('art','party'),('loser','encloser'),('city','cityscape')]:
            self.assertFalse(Workspace.term_occurs(term,text))
        self.assertFalse(Workspace.term_occurs('Roman','Romans','name'))

    def test_extra_translation_field_repair_can_propose_singular_for_plural_source(self):
        import json
        service=self.workspace(['Later losers.','A valid line.'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(body['task_type'])
            if body['task_type']=='glossary_review':return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='規則複數來源') for p in body['proposals']])
            if body['task_type']=='translation':return model_reply([dict(id='u0',target='掰啦，魯蛇們。',term_ids=[],note='',note2=None),dict(id='u1',target='合法段落保持。',term_ids=[],note='')])
            p=dict(action='add_term',source='loser',sense='調侃稱呼',target='魯蛇',usage='Later losers',term_type='general',existing_term_id=None,unit_ids=['u0'],reason='原文複數')
            return model_reply([dict(id='u0',target='掰啦，魯蛇們。',term_ids=[],note='')],[p])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(calls,['translation','translation_correction','glossary_review'])
        self.assertEqual(service.batch(bid)['units'][1]['target'],'合法段落保持。')
        self.assertEqual(result['glossary_review']['approved'],1)
        self.assertIn('loser',[t['source'] for t in service.search_dictionary(service.batch(bid))['entries']])

    def test_proposal_only_correction_format_feedback_preserves_body_and_accepts_plural_phrase(self):
        import json
        service=self.workspace(['Two private rooms.','A valid line.'],[(0,1)])
        bid=service.prepare(service.new_run('test')['id'])['id'];calls=[];repairs=[]
        proposal=dict(action='add_term',source='private room',sense='獨立包廂',target='私人包廂',usage='俱樂部',term_type='general',existing_term_id=None,unit_ids=['bad-id'],reason='詞義')
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(body['task_type'])
            if body['task_type']=='translation':return model_reply([dict(id='u0',target='兩間私人包廂。',term_ids=[],note=''),dict(id='u1',target='合法段落保持。',term_ids=[],note='')],[proposal])
            if body['task_type']=='glossary_review':return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='有原文依據') for p in body['proposals']])
            repairs.append(request)
            self.assertNotIn('translations',request['response_format']['json_schema']['schema']['properties'])
            corrected=dict(proposal,source='private rooms',unit_ids=['u0'])
            if len(repairs)==1:return model_reply([],[corrected]) # unwanted translations:[] field
            self.assertIn('format_repair',body)
            self.assertEqual(json.loads(body['format_repair']['previous_response'])['translations'],[])
            self.assertEqual(request['response_format'],repairs[0]['response_format'])
            return proposal_reply([corrected])
        result=service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(calls,['translation','glossary_proposal_correction','glossary_proposal_correction','glossary_review'])
        self.assertEqual(result['correction_attempts'],2)
        self.assertEqual([u['target'] for u in service.batch(bid)['units']],['兩間私人包廂。','合法段落保持。'])

    def test_proposal_correction_extra_field_stops_at_shared_three_round_cap(self):
        import json
        service=self.workspace(['A private room.'],[]);bid=service.prepare(service.new_run('test')['id'])['id'];calls=[]
        p=dict(action='add_term',source='private room',sense='包廂',target='私人包廂',usage='俱樂部',term_type='general',existing_term_id=None,unit_ids=['outside'],reason='來源錯誤')
        def transport(url,request,key):
            task=json.loads(request['messages'][1]['content'])['task_type'];calls.append(task)
            if task=='translation':return model_reply([dict(id='u0',target='一間私人包廂。',term_ids=[],note='')],[p])
            return model_reply([],[dict(p,unit_ids=['u0'])])
        with self.assertRaisesRegex(ValueError,'修正3次'):service.generate(bid,0,0,'fake',True,transport)
        self.assertEqual(len(calls),4)
        self.assertEqual(service.batch(bid)['retained_results'][0]['output']['translations'][0]['target'],'一間私人包廂。')

    def test_protection_roundtrip_and_injection_rejection(self):
        source = 'Hello <<$name>> <b>class</b>'
        protected, _ = protect(source)
        self.assertEqual(validate_target(source, protected), source)
        with self.assertRaises(ValueError):
            validate_target(source, 'missing tokens')
        with self.assertRaises(ValueError):
            validate_target(source, protected + '<script>bad</script>')
        with self.assertRaises(ValueError):
            protect('broken <<$name')
        value, pieces = protect('<script>bad()</script>safe')
        self.assertEqual(value, '⟪P0⟫safe')
        self.assertEqual(len(pieces), 1)

    def test_invalid_translation_never_advances_and_stale_draft_is_rejected(self):
        service = self.workspace(['Hello <<$name>>'], [])
        run = service.new_run('test')['id']
        batch_id = service.prepare(run)['id']
        service.save(batch_id, {'u0': 'bad'}, 0)
        with self.assertRaises(ValueError):
            service.approve(batch_id, 1, True)
        self.assertEqual(service.prepare(run)['id'], batch_id)
        with self.assertRaises(ValueError):
            service.save(batch_id, {'u0': 'old overwrite'}, 0)
        with self.assertRaises(ValueError):
            service.save(batch_id, {'outside': 'no'}, 1)

    def test_dictionary_polysemy_is_pinned_until_explicit_refresh(self):
        service = self.workspace(['The class starts'], [])
        service.save_term(dict(source='class', sense='lesson', target='課', usage='dialogue', status='approved'))
        service.save_term(dict(source='class', sense='group', target='班級', usage='student group', status='approved'))
        run = service.new_run('test')['id']
        batch_id = service.prepare(run)['id']
        self.assertEqual(service.batch(batch_id)['dictionary_version'], 2)
        self.assertEqual(len(service.batch(batch_id)['dictionary']), 2)
        service.save_term(dict(source='class', sense='lesson', target='課程', usage='formal', status='approved'))
        self.assertEqual(service.batch(batch_id)['dictionary_version'], 2)
        service.refresh_dictionary(batch_id)
        self.assertEqual(service.batch(batch_id)['dictionary_version'], 3)
        self.assertEqual(len(service.batch(batch_id)['dictionary']), 3)

    def test_restart_recovers_draft_and_run_progress(self):
        service = self.workspace(['Hello'], [])
        run = service.new_run('test')['id']
        batch_id = service.prepare(run)['id']
        service.save(batch_id, {'u0': '你好'}, 0)
        restored = Workspace(self.root, FakeSources({'u0': 'Hello'}))
        self.assertEqual(restored.prepare(run)['id'], batch_id)
        self.assertEqual(restored.batch(batch_id)['units'][0]['target'], '你好')

    def test_term_decisions_are_bound_to_snapshot_and_clear_on_rule_refresh(self):
        service = self.workspace(['The class starts'], [])
        service.save_term(dict(source='class', sense='lesson', target='課', usage='dialogue', status='approved'))
        term_id = service.glossary()['entries'][0]['id']
        run = service.new_run('test')['id']
        batch_id = service.prepare(run)['id']
        service.save(batch_id, {'u0': '課開始了'}, 0, {'u0': dict(term_ids=[term_id], note='教學活動')})
        self.assertEqual(service.batch(batch_id)['decisions']['u0']['term_ids'], [term_id])
        with self.assertRaises(ValueError):
            service.save(batch_id, {}, 1, {'u0': dict(term_ids=[999999])})
        service.refresh_dictionary(batch_id)
        self.assertEqual(service.batch(batch_id)['decisions'], {})

    def test_prompt_separates_reference_and_new_units_and_does_not_call_models(self):
        service = self.workspace(['class '*2000, 'b'*30001], [(0, 1)])
        run = service.new_run('test')['id']
        first = service.prepare(run)['id']
        self.approve(service, first)
        second = service.prepare(run)['id']
        import json
        request = json.loads(service.prompt(second)['prompt'])
        payload = json.loads(request['messages'][1]['content'])
        self.assertEqual([i['id'] for i in payload['references']], ['u0'])
        self.assertEqual([i['id'] for i in payload['new_units']], ['u1'])
        self.assertFalse(service.summary()['model_connected'])

    def test_system_prompt_persists_with_revision_and_fixed_contract(self):
        service = self.workspace(['Hello'], [])
        service.save_settings('請使用自然的台灣口語。', 0)
        with self.assertRaises(ValueError):
            service.save_settings('舊頁面覆蓋', 0)
        run = service.new_run('test')['id']
        batch_id = service.prepare(run)['id']
        request = service.prompt(batch_id)['request']
        self.assertIn('台灣口語', request['messages'][0]['content'])
        self.assertIn('固定', request['messages'][0]['content'])
        self.assertEqual(request['response_format']['type'], 'json_schema')
        self.assertTrue(request['response_format']['json_schema']['strict'])
        restored = Workspace(self.root, FakeSources({'u0': 'Hello'}))
        self.assertEqual(restored.settings()['revision'], 1)
        self.assertEqual(restored.settings()['system_prompt'], '請使用自然的台灣口語。')

    def test_fixed_json_import_rejects_nonconforming_and_never_advances(self):
        import json
        service = self.workspace(['Hello <<$name>>', 'new'], [(0, 1)])
        batch_id = service.prepare(service.new_run('test')['id'])['id']
        valid = dict(translations=[dict(id=u['id'], target=u['source'], term_ids=[], note='') for u in service.batch(batch_id)['units']])
        raw = json.dumps(valid)
        bad = ['```json\n'+raw+'\n```', '{"translations":[],"extra":1}', '{"translations":[],"translations":[]}',
               json.dumps(dict(translations=valid['translations'][:1])),
               json.dumps(dict(translations=[dict(id='u0', target='你好')])),
               json.dumps(dict(translations=valid['translations']*2)),
               json.dumps(dict(translations=[dict(u, term_ids=[True]) for u in valid['translations']])),
               json.dumps(dict(translations=[dict(u, target='丟失保留片段') for u in valid['translations']]))]
        for text in bad:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    service.import_json(batch_id, text, 0)
                self.assertEqual(service.batch(batch_id)['revision'], 0)
        service.import_json(batch_id, raw, 0)
        self.assertEqual(service.batch(batch_id)['status'], 'draft')

    def test_explicit_generation_pins_request_and_never_auto_approves_or_retries(self):
        import json
        service = self.workspace(['Hello'], [])
        batch_id = service.prepare(service.new_run('test')['id'])['id']
        calls = []
        def fake(url, request, key):
            calls.append(request)
            self.assertEqual(key, 'test-only')
            return model_reply([dict(id='u0',target='你好',term_ids=[],note='')])
        for key, confirmed in [(None, True), ('test-only', False)]:
            with self.assertRaises(ValueError):
                service.generate(batch_id, 0, 0, key, confirmed, fake)
        self.assertEqual(calls, [])
        service.generate(batch_id, 0, 0, 'test-only', True, fake)
        self.assertEqual(service.batch(batch_id)['status'], 'draft')
        self.assertEqual(service.batch(batch_id)['units'][0]['target'], '你好')
        self.assertTrue(calls[0]['response_format']['json_schema']['strict'])
        # Old revision is rejected even if browser retries after network interruption.
        with self.assertRaises(ValueError):
            service.generate(batch_id, 0, 0, 'test-only', True, fake)
        self.assertEqual(len(calls), 1)

    def test_generation_failure_preserves_draft_and_allows_manual_resend(self):
        service = self.workspace(['Hello'], [])
        batch_id = service.prepare(service.new_run('test')['id'])['id']
        calls = []
        def fake(*args):
            calls.append(1)
            return dict(choices=[dict(finish_reason='stop', message=dict(content='not json'))])
        for _ in range(2):
            with self.assertRaises(ValueError):
                service.generate(batch_id, 0, 0, 'test-only', True, fake)
        self.assertEqual(len(calls), 2)
        self.assertEqual(service.batch(batch_id)['revision'], 0)
        self.assertEqual(service.batch(batch_id)['units'][0]['target'], '')

    def test_small_test_caps_graph_nodes_not_segments_and_leaves_draft_untouched(self):
        import json
        service = self.workspace(['Hello']*15, [(i,i+1) for i in range(14)])
        # Two source segments belong to the same graph node, count it only once.
        service.units['u1']['node'] = 0
        service.by_node[0].append('u1')
        service.by_node[1].remove('u1')
        run = service.new_run('test')['id']
        batch_id = service.prepare(run)['id']
        before = service.batch(batch_id)
        progress = service.progress(run)
        preview = service.prompt(batch_id, test=True)
        self.assertEqual(preview['source_nodes'], 10)
        self.assertEqual(len(preview['unit_ids']), 11)
        self.assertIn('u10', preview['unit_ids'])
        self.assertNotIn('u11', preview['unit_ids'])
        calls=[]
        def fake(url, request, key):
            calls.append(request)
            payload=json.loads(request['messages'][1]['content'])
            self.assertEqual(payload['references'], [])
            schema_ids=request['response_format']['json_schema']['schema']['properties']['translations']['items']['properties']['id']['enum']
            self.assertEqual(schema_ids,preview['unit_ids'])
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in payload['new_units']])
        result=service.generate(batch_id,0,0,'fake',True,fake,test=True)
        self.assertTrue(result['test'])
        self.assertEqual(result['source_nodes'],10)
        self.assertEqual(service.batch(batch_id),before)
        self.assertEqual(service.progress(run),progress)
        with service.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_attempts').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state FROM model_tests').fetchone()[0],'test_complete')
        service.generate(batch_id,0,0,'fake',True,fake,test=True)
        self.assertEqual(len(calls),2)
        self.assertEqual(service.batch(batch_id),before)
        # Test attempt does not prevent intentional full-batch translation.
        service.generate(batch_id,0,0,'fake',True,lambda *args:model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in before['units']]))

    def test_small_test_does_not_send_common_ancestor_reference(self):
        import json
        service=self.workspace(['a'*2500]*4+['Hello']*12,[(i,i+1) for i in range(15)])
        run=service.new_run('test')['id']
        self.approve(service,service.prepare(run)['id'])
        batch_id=service.prepare(run)['id']
        self.assertTrue(service.batch(batch_id)['references'])
        preview=service.prompt(batch_id,test=True)
        payload=json.loads(preview['request']['messages'][1]['content'])
        self.assertEqual(payload['references'],[])
        self.assertEqual(preview['source_nodes'],10)
        self.assertNotIn('u0',preview['unit_ids'])

    def test_prompt_has_no_local_content_keyword_gate(self):
        import json
        service=self.workspace(['Documentation labels: sexual health, porn category, anatomy.'],[])
        batch_id=service.prepare(service.new_run('test')['id'])['id']
        for test in (False,True):
            request=service.prompt(batch_id,test=test)['request']
            payload=json.loads(request['messages'][1]['content'])
            self.assertEqual(payload['new_units'][0]['source'],'Documentation labels: sexual health, porn category, anatomy.')
            self.assertTrue(request['response_format']['json_schema']['strict'])

    def test_successful_translation_can_be_sent_again_without_extra_save(self):
        import json
        service=self.workspace(['Hello'],[])
        batch_id=service.prepare(service.new_run('test')['id'])['id']
        calls=[]
        def fake(*args):
            calls.append(1)
            return model_reply([dict(id='u0',target='你好',term_ids=[],note='')])
        for revision in range(3):
            service.generate(batch_id,revision,0,'fake',True,fake)
        self.assertEqual(len(calls),3)
        self.assertEqual(service.batch(batch_id)['revision'],3)
        self.assertEqual(service.batch(batch_id)['status'],'draft')

    def test_legacy_unique_cap_migration_preserves_audit_and_allows_repeat(self):
        service=self.workspace(['Hello'],[])
        with service.db() as db:
            for table in ('model_attempts','model_tests'):
                db.execute(f'DROP TABLE {table}')
                db.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,request TEXT NOT NULL,response TEXT,error TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(batch_id,revision))')
                db.execute(f'INSERT INTO {table}(id,batch_id,revision,state,request,response,error,created)VALUES(?,?,?,?,?,?,?,?)',(9,1,0,'needs_review','原請求','原回覆','原錯誤','2026-11-01'))
            db.execute('CREATE INDEX test_audit_state ON model_tests(state)')
        restored=Workspace(self.root,FakeSources({'u0':'Hello'}))
        with restored.db() as db:
            for table in ('model_attempts','model_tests'):
                self.assertEqual(tuple(db.execute(f'SELECT * FROM {table} WHERE id=9').fetchone()),(9,1,0,'needs_review','原請求','原回覆','原錯誤','2026-11-01'))
                for _ in range(2):db.execute(f'INSERT INTO {table}(batch_id,revision,state,request)VALUES(?,?,?,?)',(1,0,'pending','new'))
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE batch_id=1 AND revision=0').fetchone()[0],3)
            self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='test_audit_state'").fetchone())
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
        Workspace(self.root,FakeSources({'u0':'Hello'}))

    def test_live_dictionary_search_before_test_and_proposals_remain_separate(self):
        import json
        service=self.workspace(['Tatiana takes a class near the river.'],[])
        batch_id=service.prepare(service.new_run('test')['id'])['id']
        self.assertEqual(service.batch(batch_id)['dictionary'],[])
        name=service.save_term(dict(source='Tatiana',sense='人物姓名',target='Tatiana',usage='人名',status='approved',term_type='name'))['id']
        lesson=service.save_term(dict(source='class',sense='課程',target='課',usage='上課',status='approved'))['id']
        service.save_term(dict(source='class',sense='群體',target='班級',usage='學生群體',status='approved'))
        service.save_term(dict(source='unrelated',sense='無關',target='無關',usage='不在本批',status='approved'))
        proposal=dict(action='add_variant',source='class',sense='課程',target='課堂',usage='自然敘事',term_type='general',existing_term_id=lesson,unit_ids=['u0'],reason='提供另一種課程用語')
        def fake(url,request,key):
            payload=json.loads(request['messages'][1]['content'])
            self.assertEqual(len(payload['dictionary']),3)
            self.assertNotIn('unrelated',[t['source'] for t in payload['dictionary']])
            self.assertEqual(payload['dictionary_search']['matched_entries'],3)
            self.assertIn('Tatiana',payload['preserved_names'])
            self.assertIn('人物姓名一律保留原文',request['messages'][0]['content'])
            schema=request['response_format']['json_schema']['schema']
            self.assertIn('glossary_proposals',schema['required'])
            return model_reply([dict(id='u0',target='Tatiana 在河邊上課。',term_ids=[name,lesson],note='')],[proposal])
        result=service.generate(batch_id,0,0,'fake',True,fake,test=True)
        self.assertEqual(result['dictionary_search']['matched_entries'],3)
        self.assertEqual(service.batch(batch_id)['revision'],0)
        self.assertEqual(service.batch(batch_id)['dictionary'],[])
        self.assertEqual(service.batch(batch_id)['units'][0]['target'],'')
        self.assertEqual(len(service.glossary()['entries']),4)
        proposal_id=result['proposal_ids'][0]
        added=service.add_proposal(proposal_id)
        self.assertEqual(service.add_proposal(proposal_id)['id'],added['id'])
        candidate=[t for t in service.glossary()['entries'] if t['id']==added['id']][0]
        self.assertEqual(candidate['status'],'candidate')
        self.assertEqual(service.prompt(batch_id,test=True)['dictionary_snapshot']['search']['matched_entries'],3)
        service.save_term(dict(candidate,status='approved'))
        self.assertEqual(service.prompt(batch_id,test=True)['dictionary_snapshot']['search']['matched_entries'],4)

    def test_names_are_preserved_and_proposal_names_cannot_be_translated(self):
        import json
        service=self.workspace(['Tatiana meets Tatiana.'],[])
        service.save_term(dict(source='Tatiana',sense='人名',target='Tatiana',usage='角色',status='approved',term_type='name'))
        batch_id=service.prepare(service.new_run('test')['id'])['id']
        for target in ('塔蒂亞娜遇見塔蒂亞娜。','Tatiana 遇見她。'):
            with self.assertRaises(ValueError):service.import_json(batch_id,json.dumps(dict(translations=[dict(id='u0',target=target,term_ids=[],note='')],glossary_proposals=[])),0)
        with self.assertRaises(ValueError):
            service.save_term(dict(source='Tatiana',sense='人名',target='塔蒂亞娜',usage='角色',status='approved',term_type='name'))
        service.import_json(batch_id,json.dumps(dict(translations=[dict(id='u0',target='Tatiana 遇見 Tatiana。',term_ids=[],note='')],glossary_proposals=[])),0)
        self.assertEqual(service.batch(batch_id)['units'][0]['target'],'Tatiana 遇見 Tatiana。')

    def test_proposal_validation_and_model_requires_new_top_level_format(self):
        import json
        service=self.workspace(['Alice takes class.'],[])
        batch_id=service.prepare(service.new_run('test')['id'])['id']
        batch=service.batch(batch_id)
        item=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='角色名',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='首次出現的人名')
        service.validate_proposals([item],batch)
        for bad in (dict(item,target='愛麗絲'),dict(item,unit_ids=['outside']),dict(item,action='add_sense',existing_term_id=999),dict(item,source='not in text'),dict(item,existing_term_id=1)):
            with self.assertRaises(ValueError):service.validate_proposals([bad],batch)
        raw=json.dumps(dict(translations=[dict(id='u0',target='Alice 上課。',term_ids=[],note='')]))
        with self.assertRaises(ValueError):service.validate_json(batch,raw,require_proposals=True)

    def test_successful_normal_generation_saves_actual_latest_dictionary_snapshot(self):
        service=self.workspace(['Alice takes class.'],[])
        batch_id=service.prepare(service.new_run('test')['id'])['id']
        term=service.save_term(dict(source='class',sense='課程',target='課',usage='敘事',status='approved'))['id']
        response=model_reply([dict(id='u0',target='Alice 上課。',term_ids=[term],note='')])
        service.generate(batch_id,0,0,'fake',True,lambda *args:response)
        batch=service.batch(batch_id)
        self.assertEqual(batch['dictionary_version'],1)
        self.assertEqual([t['id'] for t in batch['dictionary']],[term])
        self.assertEqual(batch['decisions']['u0']['term_ids'],[term])

    def test_test_dictionary_search_does_not_include_later_nodes(self):
        service=self.workspace(['hello']*10+['unicorn'],[(i,i+1) for i in range(10)])
        batch_id=service.prepare(service.new_run('test')['id'])['id']
        service.save_term(dict(source='unicorn',sense='生物',target='獨角獸',usage='敘事',status='approved'))
        self.assertEqual(service.prompt(batch_id,test=True)['dictionary_snapshot']['entries'],[])
        self.assertEqual(len(service.prompt(batch_id)['dictionary_snapshot']['entries']),1)

    def test_auto_pro_reviews_all_new_words_once_with_old_terms_and_same_system(self):
        import json
        service=self.workspace(['Alice takes class at harbor.'],[])
        lesson=service.save_term(dict(source='class',sense='課程',target='課',usage='上課',status='approved'))['id']
        service.save_term(dict(source='class',sense='群體',target='班級',usage='學生群體',status='approved'))
        harbor=service.save_term(dict(source='harbor',sense='港口',target='港',usage='地點',status='approved'))['id']
        run=service.new_run('test')['id'];bid=service.prepare(run)['id'];before=service.batch(bid)
        proposals=[dict(action='add_variant',source='class',sense='課程',target='課堂',usage='敘事',term_type='general',existing_term_id=lesson,unit_ids=['u0'],reason='敘事用詞'),
                   dict(action='add_term',source='Alice',sense='人物姓名',target='Alice',usage='角色稱呼',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='首次出現'),
                   dict(action='add_variant',source='harbor',sense='港口',target='海港',usage='地點',term_type='general',existing_term_id=harbor,unit_ids=['u0'],reason='另一譯法')]
        calls=[]
        def fake(url,request,key):
            calls.append(request)
            payload=json.loads(request['messages'][1]['content'])
            if payload['task_type']=='translation':
                service.save_settings('送出後另改的設定，不應影響已在途批次',0)
                return model_reply([dict(id='u0',target='Alice 在港邊上課。',term_ids=[lesson,harbor],note='')],proposals)
            self.assertEqual(request['model'],'deepseek/deepseek-v4.1-flash')
            self.assertEqual(len(payload['proposals']),3)
            self.assertEqual(len([t for t in payload['related_dictionary'] if t['source']=='class']),2)
            self.assertTrue(all('sense' in t and 'usage' in t and 'status' in t for t in payload['related_dictionary']))
            self.assertEqual(request['messages'][0],calls[0]['messages'][0])
            return review_reply([dict(proposal_id=p['proposal_id'],decision='reject' if p['source']=='harbor' else 'approve',reason='語境用詞決策') for p in payload['proposals']])
        result=service.generate(bid,0,0,'fake',True,fake,test=True)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['glossary_review']['approved'],2)
        self.assertEqual(result['glossary_review']['rejected'],1)
        self.assertEqual(service.batch(bid),before)
        self.assertEqual(len(service.glossary()['entries']),5)
        self.assertEqual({p['status'] for p in service.proposals(bid)['entries']},{'approved','rejected'})
        with service.db() as db:
            prepared=json.loads(db.execute('SELECT request FROM glossary_reviews').fetchone()[0])
            self.assertEqual(prepared['settings_revision'],0)

    def test_pro_failure_preserves_flash_result_and_retry_only_calls_pro(self):
        import json
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='角色',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='人名')
        calls=[]
        def fail_review(url,request,key):
            calls.append(request['model'])
            if json.loads(request['messages'][1]['content'])['task_type']=='glossary_review':raise ValueError('synthetic reviewer failure')
            return model_reply([dict(id='u0',target='Alice 到了。',term_ids=[],note='')],[p])
        result=service.generate(bid,0,0,'fake',True,fail_review)
        self.assertEqual(result['glossary_review']['state'],'failed')
        self.assertEqual(service.batch(bid)['units'][0]['target'],'Alice 到了。')
        self.assertEqual(service.glossary()['entries'],[])
        def retry(url,request,key):
            self.assertEqual(request['model'],'deepseek/deepseek-v4.1-flash')
            payload=json.loads(request['messages'][1]['content'])
            return review_reply([dict(proposal_id=x['proposal_id'],decision='approve',reason='符合原文') for x in payload['proposals']])
        done=service.review_dictionary(bid,'fake',True,retry)
        self.assertEqual(done['approved'],1)
        self.assertEqual(service.glossary()['entries'][0]['status'],'approved')
        self.assertEqual(service.batch(bid)['revision'],1)
        self.assertEqual(len(calls),2)

    def test_invalid_pro_reply_does_not_partially_approve_and_can_be_resent(self):
        import json
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='角色',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='人名')
        with service.db() as db:ids=service._record_proposals(db,bid,'test',None,[p,p])
        with self.assertRaises(ValueError):
            service.review_dictionary(bid,'fake',True,lambda *args:review_reply([dict(proposal_id=ids[0],decision='approve',reason='好')]))
        self.assertEqual(service.glossary()['entries'],[])
        self.assertTrue(all(x['status']=='candidate' for x in service.proposals(bid)['entries']))
        result=service.review_dictionary(bid,'fake',True,lambda *args:review_reply([dict(proposal_id=i,decision='approve',reason='保留人名') for i in ids]))
        self.assertEqual(result['approved'],2)
        self.assertEqual(len(service.glossary()['entries']),1) # exact duplicates share approved entry

    def review_fixture(self):
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='角色',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='人名')
        with service.db() as db:pid=service._record_proposals(db,bid,'test',None,[p])[0]
        return service,bid,pid

    def test_pro_array_wrapper_is_repaired_by_same_model_schema_and_system(self):
        import json
        service,bid,pid=self.review_fixture();prepared=service.review_prompt(bid);calls=[]
        decision=dict(proposal_id=pid,decision='approve',reason='保留人名')
        def transport(url,request,key):
            calls.append(request)
            self.assertEqual(request['model'],'deepseek/deepseek-v4.1-flash')
            self.assertEqual(request['messages'][0],prepared['request']['messages'][0])
            self.assertEqual(request['response_format'],prepared['request']['response_format'])
            body=json.loads(request['messages'][1]['content'])
            self.assertNotIn('new_units',body)
            self.assertNotIn('translations',body)
            if len(calls)==1:
                return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps([decision])))])
            self.assertIn('不可直接回傳陣列',body['format_repair']['error'])
            self.assertEqual(json.loads(body['format_repair']['previous_response']),[decision])
            return review_reply([decision])
        done=service.review_dictionary(bid,'fake',True,transport,prepared)
        self.assertEqual(done['repair_attempts'],1);self.assertEqual(len(calls),2)
        self.assertEqual(done['approved'],1)
        with service.db() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT state FROM glossary_reviews ORDER BY id')],['failed','complete'])

    def test_pro_format_repair_stops_after_three_and_keeps_all_failed_audits(self):
        import json
        service,bid,pid=self.review_fixture();calls=[]
        def transport(*args):
            calls.append(1)
            return review_reply([dict(proposal_id=pid+100,decision='approve',reason='wrong ID')])
        with self.assertRaisesRegex(ValueError,'修正3次'):
            service.review_dictionary(bid,'fake',True,transport)
        self.assertEqual(len(calls),4);self.assertEqual(service.glossary()['entries'],[])
        self.assertEqual(service.proposals(bid)['entries'][0]['status'],'candidate')
        with service.db() as db:
            rows=list(db.execute('SELECT state,request,response FROM glossary_reviews ORDER BY id'))
            self.assertEqual(len(rows),4)
            self.assertTrue(all(r['state']=='failed' and r['response'] for r in rows))
            self.assertEqual(json.loads(rows[-1]['request'])['review_repair_round'],3)

    def test_pro_http_or_truncated_response_does_not_trigger_format_retry(self):
        for mode in ('network','length','refusal'):
            service,bid,pid=self.review_fixture();calls=[]
            def transport(*args):
                calls.append(1)
                if mode=='network':raise ValueError('HTTP 502 synthetic')
                return dict(choices=[dict(finish_reason='length' if mode=='length' else 'stop',message=dict(content='[]',refusal='refused' if mode=='refusal' else None))])
            with self.assertRaises(ValueError):service.review_dictionary(bid,'fake',True,transport)
            self.assertEqual(len(calls),1)

    def test_explicit_pro_retry_reuses_old_failed_array_and_only_sends_correction(self):
        import json
        service,bid,pid=self.review_fixture();prepared=service.review_prompt(bid)
        decision=dict(proposal_id=pid,decision='approve',reason='保留人名')
        old=dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps([decision])))])
        with service.db() as db:
            rid=db.execute("INSERT INTO glossary_reviews(batch_id,state,request,response,error)VALUES(?,'failed',?,?,'old format error')",(bid,json.dumps(prepared),json.dumps(old))).lastrowid
        calls=[]
        def transport(url,request,key):
            calls.append(request)
            self.assertIn('format_repair',json.loads(request['messages'][1]['content']))
            return review_reply([decision])
        done=service.review_dictionary(bid,'fake',True,transport,prepared)
        self.assertTrue(done['reused_failed_review']);self.assertEqual(len(calls),1)
        self.assertIsNone(done['usage'])
        with service.db() as db:
            row=db.execute('SELECT error,response FROM glossary_reviews WHERE id=?',(rid,)).fetchone()
            self.assertEqual(row['error'],'old format error');self.assertEqual(json.loads(row['response']),old)

    def test_no_new_words_skips_pro_without_a_paid_request(self):
        service=self.workspace(['Hello'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        def never(*args):raise AssertionError('must not call Pro')
        result=service.review_dictionary(bid,'fake',True,never)
        self.assertEqual(result['state'],'skipped')

    def test_legacy_candidate_is_automatically_promoted_without_duplicate(self):
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Alice',sense='人名',target='Alice',usage='角色',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='人名')
        with service.db() as db:pid=service._record_proposals(db,bid,'test',None,[p])[0]
        old=service.add_proposal(pid)['id']
        service.review_dictionary(bid,'fake',True,lambda *args:review_reply([dict(proposal_id=pid,decision='approve',reason='保留原文')]))
        self.assertEqual(len(service.glossary()['entries']),1)
        self.assertEqual(service.glossary()['entries'][0]['id'],old)
        self.assertEqual(service.glossary()['entries'][0]['status'],'approved')

    def test_dictionary_results_are_readable_persistent_and_never_start_models(self):
        service=self.workspace(['Alice arrives.'],[])
        bid=service.prepare(service.new_run('test')['id'])['id']
        p=dict(action='add_term',source='Alice',sense='人物姓名',target='Alice',usage='角色稱呼',term_type='name',existing_term_id=None,unit_ids=['u0'],reason='原文人名')
        with service.db() as db:pid=service._record_proposals(db,bid,'test',None,[p])[0]
        pending=service.dictionary_results(bid)
        self.assertEqual(pending['counts']['candidate'],1)
        self.assertIsNone(pending['review'])
        service.review_dictionary(bid,'fake',True,lambda *args:review_reply([dict(proposal_id=pid,decision='approve',reason='保留原文拼寫')]))
        before=service.batch(bid)
        restored=Workspace(self.root,FakeSources({'u0':'Alice arrives.'}))
        for result in (restored.dictionary_results(bid),restored.dictionary_results()):
            self.assertEqual(result['approved_entries'][0]['source'],'Alice')
            self.assertEqual(result['approved_entries'][0]['target'],'Alice')
            self.assertEqual(result['counts']['approved'],1)
            self.assertEqual(result['proposals'][0]['review_reason'],'保留原文拼寫')
            self.assertEqual(result['review']['state'],'complete')
        self.assertEqual(restored.batch(bid),before)
        with restored.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM glossary_reviews').fetchone()[0],1)

    def test_empty_dictionary_results_dont_create_a_trial_or_review(self):
        service=self.workspace(['Hello'],[])
        result=service.dictionary_results()
        self.assertEqual(result['approved_entries'],[])
        self.assertEqual(result['proposals'],[])
        self.assertEqual(service.summary()['runs'],[])
        with service.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM glossary_reviews').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
