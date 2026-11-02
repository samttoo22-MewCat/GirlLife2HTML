"""Isolated UI fixture ONLY: fake OAuth exchange/inference, never loads real data.

Run remotely: python3 -u serve_translation_fixture.py --port 33326
"""
import argparse
import json

import bazaarlink
from test_translation_http import WorkflowHTTPTests
from server import serve

FAIL_PROPOSALS = False
TRUNCATE_FIRST = False
TRUNCATED = False


def mock_request(url, payload, key=None):
    global TRUNCATED
    if url == 'https://bazaarlink.ai/api/v1/auth/keys':
        if payload['code'] != 'synthetic-code' or payload['code_challenge_method'] != 'S256':
            raise ValueError('invalid synthetic code')
        return dict(key='sk-bl-synthetic-ui-only')
    if url == 'https://api.bazaarlink.ai/v1/chat/completions':
        if key != 'sk-bl-synthetic-ui-only' or not payload['response_format']['json_schema']['strict']:
            raise ValueError('invalid synthetic request')
        data = json.loads(payload['messages'][1]['content'])
        if data.get('task_type')=='glossary_proposal_recovery':
            related=next((u for u in data['source_units'] if 'class' in u['source']),None)
            proposals=[] if not related else [dict(action='add_term',source='class',sense='教學活動',target='課堂',usage='合成恢復用詞',term_type='general',existing_term_id=None,unit_ids=[related['id']],reason='完整譯文已保留，只補回被截斷的提案')]
            return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(dict(glossary_proposals=proposals))))],usage=dict(total_tokens=0,cost=0))
        if data.get('task_type') in ('translation_correction','glossary_proposal_correction'):
            proposals=[]
            for invalid in data['invalid_proposals']:
                p=invalid['proposal']
                if not isinstance(p,dict) or not isinstance(p.get('source'),str):continue
                refs=[u['id'] for u in data['source_units'] if p['source'].casefold() in u['source'].casefold()]
                if refs:proposals.append(dict(p,unit_ids=['wrong-fixture-reference'] if FAIL_PROPOSALS else refs))
            content=dict(glossary_proposals=proposals)
            if data['task_type']=='translation_correction':
                sources={u['id']:u['source'] for u in data['source_units']}
                content['translations']=[dict(id=i['id'],target=sources[i['id']],term_ids=[],note='合成局部修正') for i in data['invalid_translations']]
            return dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps(content)))],usage=dict(total_tokens=0,cost=0))
        if data.get('task_type')=='glossary_review':
            if payload['model']!=bazaarlink.REVIEW_MODEL:
                raise ValueError('unexpected review model')
            content=json.dumps(dict(glossary_reviews=[dict(proposal_id=p['proposal_id'],decision='approve',reason='合成 Flash 審詞核准') for p in data['proposals']]))
            return dict(choices=[dict(finish_reason='stop',message=dict(content=content))],usage=dict(total_tokens=0,cost=0))
        related=next((u for u in data['new_units'] if 'class' in u['source']),None)
        existing=next((t for t in data['dictionary'] if t['source']=='class'),None)
        proposals=[] if not related else [dict(action='add_variant' if existing else 'add_term',source='class',sense='教學活動',target='課堂',usage='合成測試用語',term_type='general',existing_term_id=existing['id'] if existing else None,unit_ids=[related['id']],reason='合成字典提案驗收')]
        if FAIL_PROPOSALS:
            for proposal in proposals:proposal['unit_ids']=['wrong-fixture-reference']
        if TRUNCATE_FIRST and not TRUNCATED and len(data['new_units'])>=2:
            TRUNCATED=True
            complete=data['new_units'][:max(1,len(data['new_units'])//2)]
            items=[dict(id=u['id'],target=u['source'],term_ids=[],note='完整合成段落') for u in complete]
            cut='{"translations":['+','.join(json.dumps(i) for i in items)+',{"id":"incomplete","target":"cut'
            return dict(choices=[dict(finish_reason='length',message=dict(content=cut))],usage=dict(total_tokens=0,cost=0))
        content = json.dumps(dict(translations=[dict(id=u['id'], target=u['source'], term_ids=[], note='合成 UI 驗收') for u in data['new_units']],glossary_proposals=proposals))
        return dict(choices=[dict(finish_reason='stop', message=dict(content=content))], usage=dict(total_tokens=0, cost=0))
    raise ValueError('fixture prohibits real HTTP')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=33326)
    parser.add_argument('--fail-proposals',action='store_true',help='Mock persistent proposal failure for retained-result UI tests')
    parser.add_argument('--truncate-first',action='store_true',help='Mock one length-limited JSON prefix; never contacts provider')
    args = parser.parse_args()
    FAIL_PROPOSALS=args.fail_proposals
    TRUNCATE_FIRST=args.truncate_first
    WorkflowHTTPTests.setUpClass()
    WorkflowHTTPTests.process.terminate()
    WorkflowHTTPTests.process.wait(timeout=10)
    WorkflowHTTPTests.process.stdout.close()
    WorkflowHTTPTests.process.stderr.close()
    bazaarlink.request_json = mock_request
    import translation_workspace
    translation_workspace.request_json = mock_request
    try:
        print('SYNTHETIC ONLY ' + str(WorkflowHTTPTests.data), flush=True)
        serve(WorkflowHTTPTests.data, args.port)
    finally:
        WorkflowHTTPTests.temp.cleanup()
