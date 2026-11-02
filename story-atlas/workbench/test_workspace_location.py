import json
from pathlib import Path
import tempfile
import unittest

from workspace_location import workspace_directory
from round_status import test_round_status as round_status


class WorkspaceLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'translation').mkdir();self.config=self.root/'translation/active-workspace.json'
        self.folder=self.root/'test-rounds/round-50';(self.folder/'translation').mkdir(parents=True)
        (self.folder/'translation/workspace.sqlite3').touch()
    def tearDown(self):self.tmp.cleanup()
    def test_default_and_explicit_promotion_select_only_one_authoritative_database(self):
        self.assertEqual(workspace_directory(self.root),self.root)
        self.config.write_text(json.dumps(dict(directory='round-50',promoted=True)))
        self.assertEqual(workspace_directory(self.root),self.folder)
        self.assertTrue((self.folder/'translation/workspace.sqlite3').exists())
    def test_path_escape_and_unconfirmed_promotion_are_rejected(self):
        for value in (dict(directory='../private-oauth',promoted=True),dict(directory='round-50',promoted=False),dict(directory='missing',promoted=True)):
            self.config.write_text(json.dumps(value))
            with self.assertRaises(ValueError):workspace_directory(self.root)
    def test_monitor_labels_authoritative_worker_as_formal_without_changing_results(self):
        self.config.write_text(json.dumps(dict(directory='round-50',promoted=True)))
        (self.folder/'runner.lock').touch()
        state=dict(status='complete',count=50,completed=[dict(batch_id=i) for i in range(50)],calls=[])
        raw=json.dumps(state);(self.folder/'round.json').write_text(raw)
        (self.root/'test-rounds/active-round.json').write_text(json.dumps(dict(directory='round-50')))
        result=round_status(self.root)
        self.assertFalse(result['isolated'])
        self.assertEqual((self.folder/'round.json').read_text(),raw)


if __name__=='__main__':unittest.main()
