import json
import unittest
import test_translation as fixtures


class SparseRecoveryTests(unittest.TestCase):
    setUp=fixtures.TranslationTests.setUp
    tearDown=fixtures.TranslationTests.tearDown
    workspace=fixtures.TranslationTests.workspace

    def proposal(self,source='reach his limit'):
        return dict(action='add_term',source=source,sense='達到極限',target='到了極限',usage='He reaches his limit.',term_type='general',existing_term_id=None,unit_ids=['u0'],reason='source evidence')

    def test_missing_metadata_is_asked_not_assumed_empty_and_body_not_resent(self):
        service=self.workspace(['A readable sentence.'],[]);bid=service.prepare(service.new_run('same')['id'])['id'];calls=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(body)
            if len(calls)==1:
                response=fixtures.model_reply([dict(id='u0',target='既有正文XYZ',term_ids=[],note='')]);out=json.loads(response['choices'][0]['message']['content']);out.pop('glossary_proposals');response['choices'][0]['message']['content']=json.dumps(out);return response
            self.assertTrue(body['metadata_missing']);self.assertNotIn('translations',request['response_format']['json_schema']['schema']['properties']);self.assertNotIn('既有正文XYZ',json.dumps(body,ensure_ascii=False))
            self.assertEqual([u['id'] for u in body['source_units']],['u0']);return fixtures.proposal_reply([])
        result=service.generate(bid,0,0,'synthetic-only',True,transport)
        self.assertEqual(len(calls),2);self.assertEqual(result['correction_attempts'],1)
        self.assertEqual(service.batch(bid)['units'][0]['target'],'既有正文XYZ')

    def test_regular_first_verb_form_correction_has_actual_cited_source_and_full_review(self):
        service=self.workspace(['He reaches his limit.'],[]);bid=service.prepare(service.new_run('same')['id'])['id'];tasks=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);tasks.append(body['task_type'])
            if body['task_type']=='translation':return fixtures.model_reply([dict(id='u0',target='他到了極限。',term_ids=[],note='')],[self.proposal()])
            if body['task_type']=='glossary_review':return fixtures.review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='tested') for p in body['proposals']])
            self.assertEqual(body['source_units'],[dict(id='u0',source='He reaches his limit.')]);return fixtures.proposal_reply([self.proposal('reaches his limit')])
        result=service.generate(bid,0,0,'synthetic-only',True,transport)
        self.assertEqual(tasks,['translation','glossary_proposal_correction','glossary_review'])
        self.assertEqual(result['glossary_review']['state'],'complete')
        self.assertFalse(service.term_occurs('reach his limit','He reaches his limit.'))
        self.assertEqual(service.batch(bid)['units'][0]['target'],'他到了極限。')

    def make_retained(self,service,bid):
        def fail(url,request,key):
            body=json.loads(request['messages'][1]['content'])
            if body['task_type']=='translation':return fixtures.model_reply([dict(id='u0',target='他到了極限。',term_ids=[],note='')],[self.proposal()])
            return fixtures.model_reply([],[])
        with self.assertRaises(ValueError):service.generate(bid,0,0,'synthetic-only',True,fail)

    def test_explicit_saved_repair_reuses_original_body_even_if_parallel_dictionary_grew(self):
        service=self.workspace(['He reaches his limit.'],[]);bid=service.prepare(service.new_run('same')['id'])['id'];self.make_retained(service,bid)
        service.save_term(dict(source='limit',sense='界限',target='極限',usage='noun',term_type='general',status='approved'))
        calls=[]
        def repair(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(body['task_type']);self.assertNotEqual(body['task_type'],'translation');return fixtures.proposal_reply([])
        result=service.generate(bid,0,0,'synthetic-only',True,repair,repair_saved=True)
        self.assertTrue(result['reused_translation']);self.assertEqual(calls,['glossary_proposal_correction']);self.assertEqual(service.batch(bid)['units'][0]['target'],'他到了極限。')

    def test_saved_repair_refuses_changed_system_without_spend(self):
        service=self.workspace(['He reaches his limit.'],[]);bid=service.prepare(service.new_run('same')['id'])['id'];self.make_retained(service,bid);service.save_settings('Changed system',0)
        def never(*args):raise AssertionError('no paid call')
        with self.assertRaisesRegex(ValueError,'來源／System／Schema'):service.generate(bid,0,1,'synthetic-only',True,never,repair_saved=True)

    def test_unrelated_phrase_and_unevidenced_citation_remain_rejected(self):
        service=self.workspace(['He reaches his limit.'],[]);batch=service.batch(service.prepare(service.new_run('same')['id'])['id']);plan=service.repair_plan(batch,json.dumps(dict(translations=[dict(id='u0',target='他到了極限。',term_ids=[],note='')],glossary_proposals=[self.proposal()])))
        with self.assertRaises(ValueError):service.merge_correction(batch,json.dumps(dict(glossary_proposals=[self.proposal('unrelated phrase')])),plan)
        bad=self.proposal('reaches his limit');bad['unit_ids']=['unrelated-id'];merged=service.merge_correction(batch,json.dumps(dict(glossary_proposals=[bad])),plan)
        with self.assertRaises(ValueError):service.validate_json(batch,merged,True)

    def test_missing_metadata_feedback_names_bad_source_for_model_to_fix_not_local_stemming(self):
        service=self.workspace(['He starts pumping.'],[]);bid=service.prepare(service.new_run('same')['id'])['id'];calls=[]
        def transport(url,request,key):
            body=json.loads(request['messages'][1]['content']);calls.append(body['task_type'])
            if body['task_type']=='translation':
                response=fixtures.model_reply([dict(id='u0',target='原有正文不變',term_ids=[],note='')]);out=json.loads(response['choices'][0]['message']['content']);out.pop('glossary_proposals');response['choices'][0]['message']['content']=json.dumps(out);return response
            if body['task_type']=='glossary_review':return fixtures.review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='source evidenced') for p in body['proposals']])
            p=self.proposal('pump' if 'format_repair' not in body else 'pumping');p['usage']='He starts pumping.'
            if 'format_repair' in body:self.assertIn('pump',body['format_repair']['error']);self.assertIn('ing/ed',body['format_repair']['error'])
            return fixtures.proposal_reply([p])
        result=service.generate(bid,0,0,'synthetic-only',True,transport)
        self.assertEqual(result['correction_attempts'],2);self.assertEqual(calls.count('translation'),1)
        self.assertEqual(result['glossary_review']['state'],'complete');self.assertEqual(service.batch(bid)['units'][0]['target'],'原有正文不變')
        self.assertFalse(service.term_occurs('pump','He starts pumping.'))


if __name__=='__main__':unittest.main()
