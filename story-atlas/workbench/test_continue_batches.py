import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import continue_batches as supervisor
from continue_batches import decision


class ContinuationTests(unittest.TestCase):
    def status(self,**changes):
        result=dict(session_id='expected',state='complete',completed=500,requested=500,counts=dict(failed=0,running=0,waiting=0),error=None);result.update(changes);return result

    def test_running_waits_and_only_exact_completion_can_be_audited(self):
        self.assertEqual(decision(self.status(state='running',completed=100),'expected'),'wait')
        self.assertEqual(decision(self.status(),'expected'),'audit')
        self.assertEqual(decision(self.status(completed=499),'expected'),'needs_repair')

    def test_errors_unconfirmed_or_other_session_never_start_more_paid_work(self):
        for state in ('paused','blocked','failed','unconfirmed','exhausted'):
            self.assertEqual(decision(self.status(state=state),'expected'),'needs_repair')
        for key in ('failed','running','waiting'):
            self.assertEqual(decision(self.status(counts={key:1}),'expected'),'needs_repair')
        self.assertEqual(decision(self.status(error='HTTP 402'),'expected'),'needs_repair')
        self.assertEqual(decision(self.status(session_id='other'),'expected'),'needs_review')

    def test_supervisor_failed_completion_persists_notification_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data/translation').mkdir(parents=True);folder=root/'supervisor'
            calls=[]
            class Response:
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self):return json.dumps(dict(session_id='expected',state='blocked',completed=499,requested=500,counts=dict(failed=1),error=None)).encode()
            def read_only(request,**kwargs):
                self.assertIsInstance(request,str);calls.append(request);return Response()
            with patch.object(supervisor,'__file__',str(root/'continue_batches.py')),patch('sys.argv',['supervisor','--directory',str(folder),'--run-id','run','--after-session','expected','--confirmed']),patch.object(supervisor,'urlopen',side_effect=read_only):
                supervisor.main()
            saved=json.loads((folder/'status.json').read_text());self.assertEqual(saved['state'],'needs_repair');self.assertNotIn('start_post_attempted',saved);self.assertEqual(len(calls),1)

    def test_previously_attempted_post_cannot_be_replayed_even_after_lost_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data/translation').mkdir(parents=True);folder=root/'supervisor';folder.mkdir();(folder/'status.json').write_text(json.dumps(dict(start_post_attempted=True,state='needs_review')))
            with patch.object(supervisor,'__file__',str(root/'continue_batches.py')),patch('sys.argv',['supervisor','--directory',str(folder),'--run-id','run','--after-session','expected','--confirmed']),patch.object(supervisor,'urlopen',side_effect=AssertionError('Never resend')):
                with self.assertRaisesRegex(ValueError,'already attempted'):supervisor.main()


if __name__=='__main__':unittest.main()
