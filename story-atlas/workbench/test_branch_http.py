"""HTTP tests use fake credentials and a fixture which forbids outbound HTTP."""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen


class BranchHTTPTests(unittest.TestCase):
    def test_start_refresh_duplicate_guard_stop_and_resume(self):
        process=subprocess.Popen([sys.executable,'-u',str(Path(__file__).parent/'serve_branch_fixture.py'),'--delay','.15'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=process.stdout.readline().strip();self.assertTrue(line.startswith('Local story workbench: '),line)
            base=line.split(': ',1)[1]
            def get(path):
                with urlopen(base+'/api/workflow/'+path,timeout=10) as response:return json.load(response)
            csrf=get('bootstrap')['csrf']
            def post(path,value):
                request=Request(base+'/api/workflow/'+path,json.dumps(value).encode(),headers={'Content-Type':'application/json','Origin':base,'X-Atlas-CSRF':csrf})
                with urlopen(request,timeout=10) as response:return json.load(response)
            run=post('run',{})['id']
            before=get('branches/status?run_id='+run);self.assertEqual(before['state'],'idle')
            with self.assertRaises(HTTPError):post('branches/start',dict(run_id=run,count=5,concurrency=31,confirmed=True))
            started=post('branches/start',dict(run_id=run,count=20,concurrency=3,confirmed=True))
            self.assertEqual(get('branches/status?run_id='+run)['state'],'running')
            # A page reload is GET-only and cannot start another pipeline.
            get('bootstrap');get('branches/status?run_id='+run)
            with self.assertRaises(HTTPError) as caught:post('prepare',dict(run_id=run))
            self.assertEqual(caught.exception.code,409)
            post('branches/stop',dict(session_id=started['session_id']))
            deadline=time.monotonic()+10
            while True:
                stopped=get('branches/status?run_id='+run)
                if stopped['state']!='running':break
                if time.monotonic()>deadline:self.fail('Fixture stop timed out')
                time.sleep(.03)
            self.assertEqual(stopped['state'],'paused');self.assertGreaterEqual(stopped['completed'],1)
            self.assertLessEqual(stopped['completed'],4)
            self.assertEqual(get('branches/status?run_id='+run)['completed'],stopped['completed'])
            resumed=post('branches/resume',dict(session_id=started['session_id'],concurrency=5,confirmed=True))
            self.assertEqual(resumed['session_id'],started['session_id']);self.assertEqual(resumed['requested'],20)
            self.assertEqual(get('branches/status?run_id='+run)['concurrency'],5)
            post('branches/stop',dict(session_id=started['session_id']))
            deadline=time.monotonic()+10
            while get('branches/status?run_id='+run)['state']=='running':
                if time.monotonic()>deadline:self.fail('Resumed stop timed out')
                time.sleep(.03)
            post('branches/start',dict(run_id=run,count=2,concurrency=3,confirmed=True))
            deadline=time.monotonic()+10
            while True:
                done=get('branches/status?run_id='+run)
                if done['state']!='running':break
                if time.monotonic()>deadline:self.fail('Fixture completion timed out')
                time.sleep(.03)
            self.assertEqual(done['state'],'complete');self.assertEqual(done['completed'],2)
        finally:
            process.terminate();process.wait(timeout=10);process.stdout.close();process.stderr.close()


if __name__=='__main__':unittest.main()
