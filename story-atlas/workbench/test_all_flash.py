import json
import unittest
import test_translation as fixtures
from bazaarlink import MODEL,REVIEW_MODEL


class AllFlashTests(unittest.TestCase):
    setUp=fixtures.TranslationTests.setUp
    tearDown=fixtures.TranslationTests.tearDown
    workspace=fixtures.TranslationTests.workspace

    def test_translation_sparse_repair_review_and_review_format_repair_all_use_flash(self):
        service=self.workspace(['A class starts.'],[]);bid=service.prepare(service.new_run('same')['id'])['id'];calls=[];system=None;reviews=0
        p=dict(action='add_term',source='class',sense='課程',target='課堂',usage='A class starts.',term_type='general',existing_term_id=None,unit_ids=['u0'],reason='evidenced')
        def transport(url,request,key):
            nonlocal system,reviews
            self.assertEqual(request['model'],MODEL);self.assertEqual(MODEL,REVIEW_MODEL)
            self.assertTrue(request['response_format']['json_schema']['strict'])
            if system is None:system=request['messages'][0]
            self.assertEqual(request['messages'][0],system)
            body=json.loads(request['messages'][1]['content']);calls.append(body['task_type'])
            if body['task_type']=='translation':
                bad=dict(p,unit_ids=['wrong-id']);return fixtures.model_reply([dict(id='u0',target='課程開始了。',term_ids=[],note='')],[bad])
            if body['task_type']=='glossary_proposal_correction':return fixtures.proposal_reply([p])
            reviews+=1;decisions=[dict(proposal_id=x['proposal_id'],decision='approve',reason='evidenced') for x in body['proposals']]
            if reviews==1:return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(decisions)))])
            self.assertIn('format_repair',body);return fixtures.review_reply(decisions)
        result=service.generate(bid,0,0,'synthetic-only',True,transport)
        self.assertEqual(calls,['translation','glossary_proposal_correction','glossary_review','glossary_review'])
        self.assertEqual(result['glossary_review']['model'],MODEL);self.assertEqual(result['glossary_review']['state'],'complete')

    def test_historical_pro_review_keeps_actual_model_label_without_rewriting_audit(self):
        service=self.workspace(['A class starts.'],[]);run=service.new_run('same')['id'];bid=service.prepare(run)['id'];old='deepseek/deepseek-v4-pro-0813'
        request=dict(request=dict(model=old,messages=[]));response=dict(model=old,usage=dict(total_tokens=12))
        with service.db() as db:
            rid=db.execute("INSERT INTO glossary_reviews(batch_id,state,request,response)VALUES(?,'complete',?,?)",(bid,json.dumps(request),json.dumps(response))).lastrowid
            before=tuple(db.execute('SELECT * FROM glossary_reviews WHERE id=?',(rid,)).fetchone())
        results=service.dictionary_results(bid);self.assertEqual(results['review']['model'],old)
        self.assertEqual(results['review']['requested_model'],old);self.assertEqual(service.activity(run)['stage'],'Pro 審詞')
        self.assertEqual(service.settings()['review_model'],MODEL)
        with service.db() as db:self.assertEqual(tuple(db.execute('SELECT * FROM glossary_reviews WHERE id=?',(rid,)).fetchone()),before)


if __name__=='__main__':unittest.main()
