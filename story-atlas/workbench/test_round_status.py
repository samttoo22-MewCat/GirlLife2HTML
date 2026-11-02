import fcntl
import json
from pathlib import Path
import tempfile
import unittest

from round_status import test_round_status as status


class RoundStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.folder=self.root/'test-rounds'/'round-50';self.folder.mkdir(parents=True)
        (self.folder/'runner.lock').touch()
        (self.folder.parent/'active-round.json').write_text(json.dumps(dict(directory='round-50')))
        self.state=dict(status='running',count=50,current_ordinal=30,completed=[],calls=[dict(task='translation',state='received',usage=dict(cost=.01))],secret='sk-bl-not-for-frontend')
    def tearDown(self):self.tmp.cleanup()
    def write(self):
        (self.folder/'round.json').write_text(json.dumps(self.state))
    def test_live_lock_and_progress_are_read_only_and_hide_private_fields(self):
        self.write();before=(self.folder/'round.json').read_bytes()
        with (self.folder/'runner.lock').open('r') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=status(self.root)
            self.assertTrue(result['running']);self.assertEqual(result['state'],'running')
        self.assertEqual(result['count'],50);self.assertEqual(result['reported_cost'],.01)
        self.assertNotIn('not-for-frontend',json.dumps(result))
        self.assertEqual((self.folder/'round.json').read_bytes(),before)
    def test_dead_runner_is_not_reported_running_and_terminal_error_is_visible(self):
        self.write();self.assertEqual(status(self.root)['state'],'unconfirmed')
        self.state.update(status='failed',error='failed sk-bl-secret');self.write()
        result=status(self.root);self.assertEqual(result['state'],'failed')
        self.assertNotIn('sk-bl-secret',result['error'])
    def test_progress_and_completion_are_read_from_latest_state(self):
        self.state.update(status='complete',completed=[dict(batch_id=i) for i in range(50)]);self.write()
        result=status(self.root);self.assertEqual(result['completed'],50)
        self.assertEqual(result['state'],'complete');self.assertFalse(result['running'])
    def test_path_escape_and_invalid_counts_are_rejected(self):
        (self.folder.parent/'active-round.json').write_text(json.dumps(dict(directory='../private-oauth')))
        with self.assertRaises(ValueError):status(self.root)
    def test_no_round_is_idle_without_creating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(status(tmp)['state'],'idle')
            self.assertEqual(list(Path(tmp).iterdir()),[])


if __name__=='__main__':unittest.main()
