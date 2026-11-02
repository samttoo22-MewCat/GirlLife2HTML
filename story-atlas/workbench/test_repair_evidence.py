import json
import unittest
import test_translation as fixtures


class RepairEvidenceTests(unittest.TestCase):
    setUp=fixtures.TranslationTests.setUp
    tearDown=fixtures.TranslationTests.tearDown
    workspace=fixtures.TranslationTests.workspace

    def proposal(self,source,ids):
        return dict(action='add_term',source=source,sense='測試詞義',target='測試譯法',usage='合成語境',term_type='general',existing_term_id=None,unit_ids=ids,reason='原文依據')

    def test_invalid_proposal_can_split_into_exact_terms_from_its_cited_sources(self):
        ws=self.workspace(['Eat diet food.','The diet meal is ready.','Unrelated music.'],[(0,1),(1,2)])
        bid=ws.prepare(ws.new_run('same')['id'])['id'];batch=ws.batch(bid)
        rows=[dict(id=u['id'],target='合成正文',term_ids=[],note='') for u in batch['units']]
        bad=self.proposal('diet food',['u0','u1']);plan=ws.repair_plan(batch,json.dumps(dict(translations=rows,glossary_proposals=[bad])))
        fixed=[self.proposal('diet food',['u0']),self.proposal('diet meal',['u1'])]
        merged=ws.merge_correction(batch,json.dumps(dict(glossary_proposals=fixed)),plan)
        ws.validate_json(batch,merged,True);self.assertEqual(json.loads(merged)['translations'],rows)
        with self.assertRaises(ValueError):ws.merge_correction(batch,json.dumps(dict(glossary_proposals=[self.proposal('music',['u2'])])),plan)
        with self.assertRaises(ValueError):ws.merge_correction(batch,json.dumps(dict(glossary_proposals=[self.proposal('diet meal',['sibling'])])),plan)
        with self.assertRaises(ValueError):ws.merge_correction(batch,json.dumps(dict(glossary_proposals=[self.proposal('diet meal',['u0'])])),plan)

    def test_exact_surface_phrase_not_invented_inflection(self):
        ws=self.workspace(['She hands you over to Alice.'],[]);bid=ws.prepare(ws.new_run('same')['id'])['id'];batch=ws.batch(bid)
        row=dict(id='u0',target='Alice 接手了。',term_ids=[],note='')
        plan=ws.repair_plan(batch,json.dumps(dict(translations=[row],glossary_proposals=[self.proposal('hand over',['u0'])])))
        merged=ws.merge_correction(batch,json.dumps(dict(glossary_proposals=[self.proposal('hands you over',['u0'])])),plan)
        ws.validate_json(batch,merged,True);self.assertEqual(json.loads(merged)['translations'],[row])

    def test_saved_draft_new_name_guard_repairs_only_bad_row_not_whole_batch(self):
        ws=self.workspace(['Welcome to assbook.','A valid sentence.'],[(0,1)]);bid=ws.prepare(ws.new_run('same')['id'])['id']
        original=[dict(id='u0',target='歡迎使用 Assbook。',term_ids=[],note='原備註'),dict(id='u1',target='原合法正文保留。',term_ids=[],note='不變')]
        ws.generate(bid,0,0,'synthetic',True,lambda *args:fixtures.model_reply(original,[]))
        ws.save_term(dict(source='Assbook',target='Assbook',sense='平台名稱',usage='合成',term_type='name',status='approved'))
        calls=[]
        def transport(url,req,key):
            body=json.loads(req['messages'][1]['content']);calls.append(body['task_type'])
            self.assertEqual(body['task_type'],'translation_correction')
            self.assertEqual([u['id'] for u in body['invalid_translations']],['u0'])
            return fixtures.model_reply([dict(original[0],target='歡迎使用 assbook。')],[])
        ws.review_saved_draft(bid,1,'synthetic',True,transport)
        batch=ws.batch(bid);self.assertEqual(batch['units'][1]['target'],original[1]['target']);self.assertEqual(batch['decisions']['u1']['note'],'不變')
        ws.approve(bid,batch['revision'],False,automatic=True)
        self.assertEqual(calls,['translation_correction'])


if __name__=='__main__':unittest.main()
