import json
import unittest
from copy import deepcopy

from adaptive_translation import AdaptiveFailure, budget_profile, complete_prefix, estimate, groups, run_parts
from bazaarlink import response_format


def reply(data,reason='stop'):
    content=data if isinstance(data,str) else json.dumps(data)
    return dict(choices=[dict(finish_reason=reason,message=dict(content=content))],usage=dict(prompt_tokens=5,completion_tokens=7,total_tokens=12))


class AdaptiveTests(unittest.TestCase):
    def request(self,count=4,size=20):
        units=[dict(id=f'u{i}',source=f'scene {i} '+('x'*size),condition_evidence=['condition'],scene=i) for i in range(count)]
        payload=dict(task_type='translation',new_units=units,references=[],dictionary=[],route_context=dict(steps=[dict(kind='choice',option='Knock on the door')]))
        return dict(model='same-model',messages=[dict(role='system',content='same-system'),dict(role='user',content=json.dumps(payload))],response_format=response_format([u['id'] for u in units]),stream=False)

    def row(self,unit):return dict(id=unit['id'],target=unit['source'],term_ids=[],note='')

    def execute(self,request,call,cache=None,seed=None,profile=None):
        saved=[]
        def stash(ids,output,complete):saved.append(dict(unit_ids=ids,output=deepcopy(output),metadata_complete=complete))
        result=run_parts(request,profile or budget_profile([]),call,stash,cache or [],lambda item,props:'target' in item,seed)
        return result,saved

    def test_profile_includes_reasoning_and_splits_before_sending(self):
        profile=budget_profile([dict(characters=39928,usage=dict(completion_tokens=65536,completion_tokens_details=dict(reasoning_tokens=32925)))])
        units=json.loads(self.request(100,400)['messages'][1]['content'])['new_units']
        parts=groups(units,profile)
        self.assertGreater(len(parts),1)
        self.assertGreaterEqual(profile['reasoning_reserve'],32925)
        self.assertEqual([u['id'] for p in parts for u in p],[u['id'] for u in units])
        self.assertTrue(all(estimate(p,profile)['estimated_output_tokens']<=profile['output_target'] for p in parts))

    def test_parts_preserve_order_choice_context_and_real_usage(self):
        request=self.request(100,400);payload=json.loads(request['messages'][1]['content']);payload['references']=[dict(id='old',source='r'*8100,approved_target='old context')];request['messages'][1]['content']=json.dumps(payload)
        profile=budget_profile([dict(characters=39928,usage=dict(completion_tokens=65536,completion_tokens_details=dict(reasoning_tokens=32925)))])
        seen=[]
        def call(kind,ids,wire,budget):
            body=json.loads(wire['messages'][1]['content']);seen.extend(ids)
            self.assertEqual(wire['messages'][0],request['messages'][0])
            self.assertEqual(wire['max_tokens'],65536)
            self.assertEqual(body['route_context']['steps'][0]['option'],'Knock on the door')
            self.assertLessEqual(sum(len(u['source']) for u in body['references']+body.get('preceding_chunks',[])),10000)
            self.assertLessEqual(budget['estimated_output_tokens'],budget['output_target'])
            return reply(dict(translations=[self.row(u) for u in body['new_units']],glossary_proposals=[]))
        result,saved=self.execute(request,call,profile=profile)
        self.assertEqual(seen,[u['id'] for u in payload['new_units']])
        self.assertEqual(result['usage']['total_tokens'],len(saved)*12)

    def cut(self,request):
        units=json.loads(request['messages'][1]['content'])['new_units']
        text='{"translations":['+','.join(json.dumps(self.row(u)) for u in units[:2])+',{"id":"u2","target":"cut \\" inside'
        return reply(text,'length')

    def test_truncated_prefix_discards_half_row_and_recovers_metadata(self):
        request=self.request();calls=[]
        def call(kind,ids,wire,budget):
            calls.append((kind,ids));body=json.loads(wire['messages'][1]['content'])
            if len(calls)==1:return self.cut(request)
            if kind=='metadata':return reply(dict(glossary_proposals=[]))
            self.assertTrue(set(ids).isdisjoint({'u0','u1'}))
            return reply(dict(translations=[self.row(u) for u in body['new_units']],glossary_proposals=[]))
        result,saved=self.execute(request,call)
        output=json.loads(result['choices'][0]['message']['content'])
        self.assertEqual([i['id'] for i in output['translations']],['u0','u1','u2','u3'])
        self.assertEqual(sum(kind=='metadata' for kind,ids in calls),1)
        self.assertEqual(result['adaptive_summary']['truncations'],1)

    def test_completed_glossary_before_cut_body_needs_no_metadata_replay(self):
        request=self.request();units=json.loads(request['messages'][1]['content'])['new_units'];calls=[]
        cut='{"glossary_proposals":[],"translations":['+','.join(json.dumps(self.row(u)) for u in units[:2])+',{"id":"u2","target":"cut'
        def call(kind,ids,wire,budget):
            calls.append(kind)
            if len(calls)==1:return reply(cut,'length')
            self.assertEqual(kind,'translation')
            body=json.loads(wire['messages'][1]['content'])
            return reply(dict(translations=[self.row(u) for u in body['new_units']],glossary_proposals=[]))
        result,_=self.execute(request,call)
        self.assertNotIn('metadata',calls)
        self.assertEqual(len(json.loads(result['choices'][0]['message']['content'])['translations']),4)

    def test_old_length_seed_is_never_requested_in_full_again(self):
        request=self.request();calls=[]
        def call(kind,ids,wire,budget):
            calls.append((kind,ids));body=json.loads(wire['messages'][1]['content'])
            if kind=='metadata':return reply(dict(glossary_proposals=[]))
            self.assertEqual(ids,['u2','u3'])
            return reply(dict(translations=[self.row(u) for u in body['new_units']],glossary_proposals=[]))
        result,_=self.execute(request,call,seed=self.cut(request))
        self.assertEqual(result['adaptive_summary']['resumed_units'],2)
        self.assertEqual(len(calls),2)

    def test_metadata_failure_keeps_prefix_and_resume_does_not_retranslate_it(self):
        request=self.request();saved=[]
        def stash(ids,output,complete):saved.append(dict(unit_ids=ids,output=deepcopy(output),metadata_complete=complete))
        with self.assertRaises(AdaptiveFailure) as caught:
            run_parts(request,budget_profile([]),lambda *args:(_ for _ in ()).throw(ValueError('HTTP 402')),stash,[],lambda *args:True,self.cut(request))
        self.assertEqual(len(caught.exception.output['translations']),2)
        self.assertTrue(caught.exception.summary['metadata_incomplete'])
        calls=[]
        def call(kind,ids,wire,budget):
            calls.append((kind,ids));body=json.loads(wire['messages'][1]['content'])
            if kind=='metadata':return reply(dict(glossary_proposals=[]))
            self.assertEqual(ids,['u2','u3'])
            return reply(dict(translations=[self.row(u) for u in body['new_units']],glossary_proposals=[]))
        result,_=self.execute(request,call,cache=saved)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['adaptive_summary']['resumed_units'],2)

    def test_refusal_is_not_treated_as_output_length(self):
        calls=[]
        def call(*args):calls.append(1);return reply('',reason='content_filter')
        with self.assertRaisesRegex(AdaptiveFailure,'拒絕'):self.execute(self.request(),call)
        self.assertEqual(len(calls),1)

    def test_atomic_oversized_unit_is_rejected_before_spending(self):
        calls=[]
        with self.assertRaisesRegex(ValueError,'單一完整段落'):
            self.execute(self.request(1,100000),lambda *args:calls.append(1))
        self.assertEqual(calls,[])

    def test_prefix_parser_handles_quotes_and_never_invents_missing_fields(self):
        text='{"translations":[{"id":"u0","target":"brace } and \\" quote","term_ids":[],"note":""},{"id":"u1","target":"unfinished'
        data,closed=complete_prefix(text)
        self.assertEqual([i['id'] for i in data['translations']],['u0'])
        self.assertNotIn('glossary_proposals',closed)
        with self.assertRaises(ValueError):complete_prefix('{"translations":[],"translations":[')

    def test_actual_usage_replans_the_next_parts_in_the_same_batch(self):
        request=self.request(200,400);sizes=[]
        def call(kind,ids,wire,budget):
            sizes.append(len(ids));body=json.loads(wire['messages'][1]['content'])
            self.assertLessEqual(budget['estimated_output_tokens'],budget['output_target'])
            response=reply(dict(translations=[self.row(u) for u in body['new_units']],glossary_proposals=[]))
            response['usage']=dict(prompt_tokens=100,completion_tokens=40000,total_tokens=40100,completion_tokens_details=dict(reasoning_tokens=36000))
            return response
        result,_=self.execute(request,call)
        self.assertGreater(len(sizes),result['adaptive_summary']['planned_parts'])
        self.assertLess(max(sizes[1:]),sizes[0])
        self.assertEqual(sum(sizes),200)

    def test_gaps_in_restored_units_are_not_joined_as_adjacent_story(self):
        units=[dict(id='a',source='one',sequence=0),dict(id='b',source='two',sequence=5)]
        self.assertEqual(len(groups(units,budget_profile([]))),2)

    def test_duplicate_rows_are_not_silently_removed(self):
        request=self.request(2);units=json.loads(request['messages'][1]['content'])['new_units']
        result,_=self.execute(request,lambda *args:reply(dict(translations=[self.row(units[0]),self.row(units[0])],glossary_proposals=[])))
        rows=json.loads(result['choices'][0]['message']['content'])['translations']
        self.assertEqual([r['id'] for r in rows],['u0','u0'])


if __name__=='__main__':unittest.main()
