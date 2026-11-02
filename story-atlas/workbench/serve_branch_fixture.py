"""Synthetic fork UI/API fixture. All outbound HTTP is forbidden in this process."""
import argparse
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from extract import digest,write_json
from test_translation import FakeSources,model_reply,review_reply
from server import serve


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=0);parser.add_argument('--delay',type=float,default=.1);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='branch-ui-only-') as tmp:
        data=Path(tmp);(data/'static-story').mkdir();(data/'translation').mkdir();(data/'private-oauth').mkdir(mode=0o700)
        values={f'u{i}':('Common introduction' if i==0 else 'Independent branch '+str(i)) for i in range(41)}
        graph=dict(root=0,nodes=[dict(id=i,name='Common start' if i==0 else 'Branch '+str(i)) for i in range(41)],edges=[dict(source=0,target=i,resolution='static',kind='choice',option='Option '+str(i)) for i in range(1,41)])
        write_json(data/'graph.json',graph);write_json(data/'static-story/graph.json',graph)
        write_json(data/'static-story/branches-acyclic.json',list(range(40)))
        write_json(data/'translation/catalog.json',dict(graph_hash=digest((data/'static-story/graph.json').read_bytes()),summary=dict(total=41,eligible=41,review=0),units=[dict(id=f'u{i}',node=i,characters=len(values[f'u{i}'].replace(' ','')),role='eligible') for i in range(41)]))
        key='sk-bl-synthetic-branches-only';private=data/'private-oauth/configured-key.json';private.write_text(json.dumps(dict(key=key)));private.chmod(0o600)
        def mock(url,request,credential):
            if credential!=key:raise AssertionError('Unexpected fixture key')
            time.sleep(args.delay)
            payload=json.loads(request['messages'][1]['content'])
            if payload['task_type']=='glossary_review':return review_reply([dict(proposal_id=p['proposal_id'],decision='approve',reason='fixture') for p in payload['proposals']])
            return model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='fixture') for u in payload['new_units']])
        class FixtureGate:
            def __init__(self,key,maximum):self.maximum=maximum
            def __call__(self,url,request,key):return mock(url,request,key)
            def snapshot(self):return dict(active_requests=0,queued_requests=0,effective_concurrency=self.maximum,rpm=200,balance=100,paused=False,estimate_only=True)
        with patch('translation_catalog.Sources',return_value=FakeSources(values)),patch('translation_workspace.request_json',side_effect=mock),patch('bazaarlink.request_json',side_effect=mock),patch('branch_scheduler.CreditGate',FixtureGate),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Fixture prohibits network egress')):
            serve(data,args.port)


if __name__=='__main__':main()
