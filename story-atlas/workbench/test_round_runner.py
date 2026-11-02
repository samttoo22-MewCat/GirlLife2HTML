"""Disconnected-console and resume tests; mock transport only, no paid calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_isolated_round as runner
import test_translation as fixtures


class ClosedConsole:
    def write(self,*args):raise BrokenPipeError(32,'synthetic disconnected SSH')
    def flush(self):raise BrokenPipeError(32,'synthetic disconnected SSH')


class RunnerTests(unittest.TestCase):
    workspace=fixtures.TranslationTests.workspace

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.project=Path(self.temp.name)
        self.root=self.project/'data/test-rounds/same-round'
        (self.root/'static-story').mkdir(parents=True);(self.root/'translation').mkdir()
        private=self.project/'data/private-oauth';private.mkdir()
        (private/'configured-key.json').write_text(json.dumps(dict(key='synthetic-only')))
        self.service=self.workspace([chr(97+i)*6000 for i in range(10)],[(i,i+1) for i in range(9)])
        self.run_id=self.service.new_run('same')['id']
        (self.root/'round.json').write_text(json.dumps(dict(count=10,completed=[],calls=[],status='created')))
        self.ids=[]

    def tearDown(self):self.temp.cleanup()

    def transport(self,url,request,key):
        self.assertEqual(key,'synthetic-only')
        payload=json.loads(request['messages'][1]['content'])
        units=payload['new_units'];self.ids.extend(u['id'] for u in units)
        return fixtures.model_reply([dict(id=u['id'],target=u['source'],term_ids=[],note='') for u in units])

    def execute(self):
        with patch.object(runner,'__file__',str(self.project/'run_isolated_round.py')),patch('sys.argv',['runner','--directory',str(self.root),'--count','10','--confirmed']),patch.object(runner,'Workspace',return_value=self.service),patch.object(runner,'Sources'),patch.object(runner,'request_json',side_effect=self.transport),patch('sys.stdout',ClosedConsole()):
            runner.main()

    def test_closed_console_completes_and_completed_round_never_resends(self):
        self.execute();self.assertEqual(len(self.ids),10);self.assertEqual(len(set(self.ids)),10)
        state=json.loads((self.root/'round.json').read_text());self.assertEqual(state['status'],'complete')
        self.assertEqual(len(state['completed']),10)
        events=[json.loads(line) for line in (self.root/'runner-events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['event']=='start' for e in events),10)
        self.assertEqual(sum(e['event']=='complete' for e in events),10)
        self.assertEqual(events[-1]['event'],'round_complete')
        self.execute();self.assertEqual(len(self.ids),10)

    def test_commit_before_checkpoint_recovered_without_repaying_or_extra_batch(self):
        approve=self.service.approve
        def interrupt(*args,**kwargs):
            approve(*args,**kwargs)
            raise RuntimeError('synthetic crash after commit')
        with patch.object(self.service,'approve',side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError,'after commit'):self.execute()
        self.assertEqual(self.ids,['u0'])
        state=json.loads((self.root/'round.json').read_text());self.assertEqual(state['status'],'failed')
        self.execute()
        self.assertEqual(len(self.ids),10);self.assertEqual(len(set(self.ids)),10)
        state=json.loads((self.root/'round.json').read_text());self.assertEqual(len(state['completed']),10)
        self.assertTrue(state['completed'][0]['recovered_checkpoint'])
        self.assertEqual(state['failures'][0]['error'],'synthetic crash after commit')

    def test_event_filesystem_error_is_not_ignored(self):
        (self.root/'runner-events.jsonl').mkdir()
        with self.assertRaises(OSError):runner.log_event(self.root,dict(event='start'))


if __name__=='__main__':unittest.main()
