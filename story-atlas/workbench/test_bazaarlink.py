"""OAuth success/error contracts tested via mock; no real keys or paid calls."""
import base64
import hashlib
import json
import io
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from unittest.mock import patch, Mock, MagicMock

from bazaarlink import OAuth, ProviderError, strict_json, request_json


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'private'
        self.now = 1000
        self.calls = []
        def exchange(url, payload):
            self.calls.append((url, payload))
            return dict(key='sk-bl-synthetic-not-real', user_id='synthetic')
        self.auth = OAuth(self.root, exchange=exchange, clock=lambda:self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_s256_binding_single_use_private_persistence_and_disconnect(self):
        sid, url = self.auth.start('', 'http://127.0.0.1:33325')
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(len(query['code_challenge'][0]), 43)
        self.assertNotIn('code_verifier', query)
        for session, state, origin in [('',query['state'][0],'http://127.0.0.1:33325'),(sid,'wrong','http://127.0.0.1:33325'),(sid,query['state'][0],'http://localhost:33325')]:
            with self.assertRaises(ValueError):
                self.auth.complete(session,state,'fake-code',origin)
        self.assertEqual(self.calls, [])
        self.auth.complete(sid,query['state'][0],'fake-code','http://127.0.0.1:33325')
        payload = self.calls[0][1]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(payload['code_verifier'].encode()).digest()).decode().rstrip('=')
        self.assertEqual(challenge,query['code_challenge'][0])
        self.assertEqual(payload['code_challenge_method'],'S256')
        self.assertEqual(self.root.stat().st_mode&0o777,0o700)
        self.assertEqual(next(self.root.iterdir()).stat().st_mode&0o777,0o600)
        self.assertNotIn('sk-bl',json.dumps(self.auth.status(sid)))
        with self.assertRaises(ValueError):
            self.auth.complete(sid,query['state'][0],'fake-code','http://127.0.0.1:33325')
        self.assertEqual(len(self.calls),1)
        restored=OAuth(self.root)
        self.assertTrue(restored.status(sid)['authorized'])
        self.assertFalse(restored.status('')['authorized'])
        restored.disconnect(sid)
        self.assertFalse(restored.status(sid)['authorized'])

    def test_expiry_reauth_and_failed_exchange_are_not_retried(self):
        sid,url=self.auth.start('', 'http://localhost:33325')
        state=parse_qs(urlsplit(url).query)['state'][0]
        self.now+=601
        with self.assertRaises(ValueError):self.auth.complete(sid,state,'code','http://localhost:33325')
        self.assertEqual(self.calls,[])
        sid,url=self.auth.start(sid,'http://localhost:33325')
        def fail(*args):raise ValueError('synthetic upstream failure')
        self.auth.exchange=fail
        state=parse_qs(urlsplit(url).query)['state'][0]
        with self.assertRaises(ValueError):self.auth.complete(sid,state,'code','http://localhost:33325')
        with self.assertRaises(ValueError):self.auth.complete(sid,state,'code','http://localhost:33325')
        self.assertFalse(self.auth.status(sid)['authorized'])

    def test_json_duplicate_keys_and_non_json_values_rejected(self):
        for text in ['{"x":1,"x":2}','{"x":NaN}','```json\n{}\n```']:
            with self.assertRaises(ValueError):strict_json(text)

    def test_402_is_credit_issue_not_oauth_failure_and_no_retry(self):
        opener=Mock()
        opener.open.side_effect=HTTPError('https://api.bazaarlink.ai/v1/chat/completions',402,'Payment Required',{},None)
        with patch('bazaarlink.build_opener',return_value=opener):
            with self.assertRaises(ValueError) as error:
                request_json('https://api.bazaarlink.ai/v1/chat/completions',{},'sk-bl-synthetic')
        message=str(error.exception)
        self.assertIn('餘額不足',message)
        self.assertIn('不需因此重新授權',message)
        self.assertNotIn('授權碼失效',message)
        self.assertEqual(opener.open.call_count,1)

    def test_exchange_403_and_inference_403_have_different_guidance(self):
        for key in (None,'sk-bl-synthetic'):
            opener=Mock()
            opener.open.side_effect=HTTPError('https://bazaarlink.ai',403,'Forbidden',{},None)
            with patch('bazaarlink.build_opener',return_value=opener):
                with self.assertRaises(ValueError) as error:request_json('https://bazaarlink.ai',{},key)
            self.assertEqual('一次性授權碼' in str(error.exception),key is None)

    def test_explicit_private_project_key_overrides_personal_oauth_without_leak(self):
        self.root.mkdir(mode=0o700)
        file=self.root/'configured-key.json'
        file.write_text(json.dumps(dict(key='sk-bl-synthetic-project-only')))
        file.chmod(0o600)
        self.assertEqual(self.auth.key(''),'sk-bl-synthetic-project-only')
        status=self.auth.status('')
        self.assertTrue(status['authorized'])
        self.assertEqual(status['credential_source'],'configured_api_key')
        self.assertNotIn('sk-bl-',json.dumps(status))
        self.assertTrue(self.auth.disconnect('')['authorized'])
        self.assertTrue(file.exists())

    def test_http_200_error_envelope_keeps_code_message_request_id_and_redacts_key(self):
        key='sk-bl-synthetic-secret'
        body=dict(object='error',http_status=502,error=dict(type='upstream_error',code='upstream_timeout',message='Upstream timed out '+key),id='body-id')
        opener=MagicMock();response=opener.open.return_value.__enter__.return_value
        response.status=200;response.headers={'x-request-id':'request-17'}
        response.read.return_value=b' \n'+json.dumps(body).encode()
        with patch('bazaarlink.build_opener',return_value=opener):
            with self.assertRaises(ProviderError) as caught:request_json('https://api.bazaarlink.ai/v1/chat/completions',{},key)
        error=caught.exception
        self.assertIn('HTTP 200',str(error));self.assertIn('502',str(error))
        self.assertIn('upstream_timeout',str(error));self.assertIn('request-17',str(error))
        self.assertNotIn(key,str(error)+json.dumps(error.response))
        self.assertEqual(error.response['transport_http_status'],200)
        self.assertEqual(error.response['http_status'],502)
        self.assertEqual(opener.open.call_count,1)

    def test_http_error_body_is_preserved_without_echoing_request_payload(self):
        opener=Mock()
        body=dict(error=dict(message='max_tokens is not supported',code='unsupported_parameter',param='max_tokens'),request=dict(api_key='not-for-audit'))
        opener.open.side_effect=HTTPError('https://api.bazaarlink.ai/v1/chat/completions',400,'Bad Request',{'x-request-id':'req-bad'},io.BytesIO(json.dumps(body).encode()))
        with patch('bazaarlink.build_opener',return_value=opener):
            with self.assertRaises(ProviderError) as caught:request_json('https://api.bazaarlink.ai/v1/chat/completions',{},'sk-bl-synthetic')
        self.assertIn('unsupported_parameter',str(caught.exception))
        self.assertEqual(caught.exception.response['error']['param'],'max_tokens')
        self.assertNotIn('not-for-audit',json.dumps(caught.exception.response))
        self.assertEqual(opener.open.call_count,1)


if __name__ == '__main__':
    unittest.main()
