"""Safe synthetic data; tests run on remote SSH host, no model/network egress."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from extract import export, rebuild
from run_static_acyclic import acyclic_dfs
from static_tree import build
from translation_catalog import build_catalog, Sources
from extract import write_json


class WorkflowHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        source = cls.root / 'fixture.qsp'
        source.write_bytes(rebuild(['QSPGAME', 'test', ''], [dict(name='start', description='', code="*pl 'The class starts <<$name>>'\nmsg iif(1,'branch A','branch B')\nact 'Go','images/x.jpg': gt 'room','door'", actions=[]), dict(name='room', description='', code="if $args[0]='door':\n*pl 'A quiet room'\nend", actions=[])]))
        cls.data = cls.root / 'data'
        export(source, cls.data)
        build(cls.data, cls.data / 'static-story')
        graph = json.loads((cls.data / 'static-story/graph.json').read_text())
        kept, _, _ = acyclic_dfs(graph)
        write_json(cls.data / 'static-story/branches-acyclic.json', kept)
        build_catalog(cls.data, cls.data / 'translation')
        cls.process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).parent / 'server.py'), '--data', str(cls.data), '--port', '0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = cls.process.stdout.readline().strip()
        if not line.startswith('Local story workbench: '):
            raise RuntimeError('Fixture server failed')
        cls.base = line.split(': ', 1)[1]

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.process.stdout.close()
        cls.process.stderr.close()
        cls.temp.cleanup()

    def get(self, path):
        with urlopen(self.base + path) as response:
            return json.loads(response.read())

    def post(self, path, data, token='', origin=None):
        request = Request(self.base + path, json.dumps(data).encode(), method='POST', headers={'Content-Type': 'application/json', 'X-Atlas-CSRF': token, 'Origin': origin or self.base})
        with urlopen(request) as response:
            return json.loads(response.read())

    def test_mutations_require_csrf_and_same_origin(self):
        self.assertEqual(self.get('/api/workflow/test-round-status')['state'],'idle')
        token = self.get('/api/workflow/bootstrap')['csrf']
        for csrf, origin in [('', self.base), (token, 'https://evil.invalid')]:
            with self.assertRaises(HTTPError) as error:
                self.post('/api/workflow/run', dict(name='bad'), csrf, origin)
            self.assertEqual(error.exception.code, 403)

    def test_ambiguous_expression_strings_and_action_images_are_not_auto_translated(self):
        catalog = json.loads((self.data / 'translation/catalog.json').read_text())
        sources = Sources(self.data)
        roles = {sources.value(unit): unit['role'] for unit in catalog['units']}
        self.assertEqual(roles['branch A'], 'review')
        self.assertEqual(roles['branch B'], 'review')
        self.assertEqual(roles['images/x.jpg'], 'review')
        self.assertEqual(roles['The class starts <<$name>>'], 'eligible')

    def test_draft_dictionary_prompt_and_approval_http_roundtrip(self):
        token = self.get('/api/workflow/bootstrap')['csrf']
        term = dict(source='class', sense='lesson', target='課', usage='dialogue', status='approved')
        self.post('/api/workflow/term', term, token)
        self.post('/api/workflow/term', dict(term, sense='group', target='班級'), token)
        run = self.post('/api/workflow/run', dict(name='synthetic'), token)['id']
        batch_id = self.post('/api/workflow/prepare', dict(run_id=run), token)['id']
        batch = self.get('/api/workflow/batch?id=' + str(batch_id))
        self.assertTrue(batch['units'])
        self.assertEqual(len(batch['dictionary']), 2)
        self.assertFalse(self.get('/api/workflow/bootstrap')['model_connected'])
        prompt = self.get('/api/workflow/prompt?id=' + str(batch_id))
        self.assertIn('new_units', json.loads(json.loads(prompt['prompt'])['messages'][1]['content']))
        with self.assertRaises(HTTPError):
            self.post('/api/workflow/approve', dict(batch_id=batch_id, revision=0, reviewed=True), token)
        targets = {u['id']: u['source'] for u in batch['units']}
        saved = self.post('/api/workflow/save', dict(batch_id=batch_id, revision=0, targets=targets), token)
        self.post('/api/workflow/approve', dict(batch_id=batch_id, revision=saved['revision'], reviewed=True), token)
        self.assertEqual(self.get('/api/workflow/batch?id=' + str(batch_id))['status'], 'approved')
        activity=self.get('/api/workflow/activity?run_id='+run)
        self.assertFalse(activity['blocked'])
        self.assertEqual(activity['requests'],[])
        self.assertEqual(self.get('/api/workflow/run?id=' + run)['approved_units'], len(targets))

    def test_oauth_start_cookie_callback_rejection_and_settings_http(self):
        token = self.get('/api/workflow/bootstrap')['csrf']
        settings = self.get('/api/workflow/settings')
        saved = self.post('/api/workflow/settings', dict(system_prompt='測試台灣繁體中文風格', revision=settings['revision']), token)
        self.assertEqual(saved['revision'], settings['revision']+1)
        self.assertIn('固定', saved['format_contract'])
        request = Request(self.base+'/api/workflow/auth/start', b'{}', method='POST', headers={'X-Atlas-CSRF':token,'Origin':self.base})
        with urlopen(request) as response:
            cookie = response.headers['Set-Cookie']
            started = json.load(response)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(started['authorize_url']).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['callback_url'], [self.base+'/oauth/callback'])
        self.assertNotIn('code_verifier', query)
        with urlopen(self.base+'/oauth/callback?code=fake&state=wrong') as response:
            self.assertEqual(response.url, self.base+'/translate?oauth=failed')
            self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.assertFalse(self.get('/api/workflow/auth/status')['authorized'])
        with self.assertRaises(HTTPError):
            self.post('/api/workflow/generate',dict(batch_id=1,revision=0,settings_revision=0,confirmed=True),token)

    def test_auto_submit_requires_explicit_confirmation_http(self):
        token=self.get('/api/workflow/bootstrap')['csrf']
        with self.assertRaises(HTTPError) as error:
            self.post('/api/workflow/auto-submit',dict(batch_id=999,revision=0,confirmed=False),token)
        self.assertEqual(error.exception.code,422)

    def test_dictionary_results_read_only_http(self):
        result=self.get('/api/workflow/dictionary-results')
        self.assertIn('approved_entries',result)
        self.assertIn('proposals',result)
        self.assertIn('counts',result)
        self.assertIn('review',result)


if __name__ == '__main__':
    unittest.main()
