"""Loopback-only story index and isolated original-QSP exploration player."""
import argparse
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import secrets
import hmac
import threading
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, unquote
from extract import read_json


def serve(data, port, player=None):
    graph = read_json(data / 'graph.json')
    nodes = {n['id']: n for n in graph['nodes']}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in graph['edges']:
        outgoing[edge['source']].append(edge)
        if edge['target']:
            incoming[edge['target']].append(edge)
    assets = Path(__file__).parent
    static_cache = {}
    workspace_cache = {}
    workspace_lock = threading.Lock()
    csrf_token = secrets.token_urlsafe(32)
    from bazaarlink import OAuth
    oauth = OAuth(data / 'private-oauth')

    def workspace():
        with workspace_lock:
            if 'instance' not in workspace_cache:
                from translation_workspace import Workspace
                from translation_catalog import Sources
                from workspace_location import workspace_directory
                workspace_cache['instance'] = Workspace(workspace_directory(data),Sources(data))
            return workspace_cache['instance']

    def branches():
        service=workspace()
        with workspace_lock:
            if 'branches' not in workspace_cache:
                from branch_scheduler import BranchScheduler
                workspace_cache['branches']=BranchScheduler(service)
            return workspace_cache['branches']

    class Handler(BaseHTTPRequestHandler):
        def auth_session(self):
            try:
                cookies = SimpleCookie(self.headers.get('Cookie', ''))
                return cookies[OAuth.COOKIE].value if OAuth.COOKIE in cookies else ''
            except CookieError:
                return ''

        def redirect(self, location, cookie=None):
            self.send_response(303)
            self.send_header('Location', location)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', '0')
            if cookie:
                self.send_header('Set-Cookie', cookie)
            self.end_headers()

        def do_GET(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                return self.send_error(403)
            parsed = urlsplit(self.path)
            q = parse_qs(parsed.query)
            if parsed.path == '/oauth/callback':
                try:
                    if set(q) != {'code', 'state'} or any(len(v) != 1 for v in q.values()):
                        raise ValueError('invalid callback')
                    oauth.complete(self.auth_session(), q['state'][0], q['code'][0], 'http://' + self.headers['Host'])
                    return self.redirect('/translate?oauth=success')
                except (ValueError, OSError):
                    return self.redirect('/translate?oauth=failed')
            if parsed.path.startswith('/api/workflow/'):
                try:
                    if parsed.path == '/api/workflow/auth/status':
                        return self.send_json(oauth.status(self.auth_session()))
                    if parsed.path == '/api/workflow/test-round-status':
                        from round_status import test_round_status
                        return self.send_json(test_round_status(data))
                    service = workspace()
                    if parsed.path == '/api/workflow/bootstrap':
                        summary = service.summary()
                        summary['model_connected'] = oauth.status(self.auth_session())['authorized']
                        return self.send_json(dict(csrf=csrf_token, **summary))
                    if parsed.path == '/api/workflow/glossary':
                        return self.send_json(service.glossary())
                    if parsed.path == '/api/workflow/run':
                        return self.send_json(service.progress(q.get('id', [''])[0]))
                    if parsed.path == '/api/workflow/activity':
                        activity=service.activity(q.get('run_id', [''])[0])
                        from round_status import test_round_status
                        control=test_round_status(data)
                        if control.get('isolated') is False:
                            activity['controlled_run']=control
                            if control.get('running') or control['state']=='unconfirmed':
                                activity.update(state='running' if control.get('running') else 'unconfirmed',running=control.get('running',False),blocked=True,stage=control['stage'])
                                activity['current']=dict(batch_id=control.get('current_batch'),ordinal=control.get('current_ordinal'),stage=control['stage'],state='pending',kind='controlled',id=control.get('current_batch'),error=control.get('error'))
                        return self.send_json(activity)
                    if parsed.path == '/api/workflow/branches/status':
                        return self.send_json(branches().status(q.get('run_id',[''])[0]))
                    if parsed.path == '/api/workflow/batch':
                        return self.send_json(service.batch(int(q.get('id', ['0'])[0])))
                    if parsed.path == '/api/workflow/prompt':
                        return self.send_json(service.prompt(int(q.get('id', ['0'])[0]), test=q.get('test') == ['1']))
                    if parsed.path == '/api/workflow/settings':
                        return self.send_json(service.settings())
                    if parsed.path == '/api/workflow/proposals':
                        return self.send_json(service.proposals(int(q.get('batch_id', ['0'])[0])))
                    if parsed.path == '/api/workflow/dictionary-results':
                        return self.send_json(service.dictionary_results(int(q['batch_id'][0]) if q.get('batch_id') else None))
                    if parsed.path == '/api/workflow/review-preview':
                        return self.send_json(service.review_prompt(int(q.get('batch_id', ['0'])[0])))
                    return self.send_error(404)
                except FileNotFoundError:
                    return self.send_json({'error': '翻譯索引尚未建立，請先在遠端執行索引程式。'}, 503)
                except ValueError as error:
                    return self.send_json({'error': str(error)}, 422)
            if parsed.path.startswith('/player/'):
                if player is None:
                    return self.send_error(404)
                relative = unquote(parsed.path[len('/player/'):])
                if not relative:
                    return self.send_bytes((assets / 'player.html').read_bytes(), 'text/html; charset=utf-8', runtime=True)
                path = (player / relative).resolve()
                if not path.is_relative_to(player) or not path.is_file():
                    return self.send_error(404)
                # The explorer is text-only; don't load tens of thousands of media files.
                if '/images/' in '/' + relative or relative.startswith('game/sound/'):
                    return self.send_bytes(b'', 'application/octet-stream', runtime=True)
                if relative == 'assets/index-BuT4OyUC.js':
                    bundle = path.read_bytes()
                    if b'const qspApi$=atom()' not in bundle or b'function getStorage()' not in bundle:
                        return self.send_error(409, 'Unsupported player bridge')
                    return self.send_bytes(bundle + b'\n' + (assets / 'runtime-bridge.js').read_bytes(),
                                           'text/javascript; charset=utf-8', runtime=True)
                compressed = path.suffix.lower() == '.qsp' and 'gzip' in self.headers.get('Accept-Encoding', '') and path.with_suffix('.qsp.gz').is_file()
                served = path.with_suffix('.qsp.gz') if compressed else path
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(served.stat().st_size))
                if compressed:
                    self.send_header('Content-Encoding', 'gzip')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                with served.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        self.wfile.write(chunk)
                return
            if parsed.path == '/api/summary':
                return self.send_json({k: graph[k] for k in ('source', 'summary', 'entry', 'limitations')})
            if parsed.path == '/api/static-graph':
                file = data / 'static-story/graph.json'
                if not file.is_file():
                    return self.send_error(404)
                retained = file.parent / 'branches-acyclic.json'
                stamp = (file.stat().st_mtime_ns, retained.stat().st_mtime_ns if retained.is_file() else None)
                if static_cache.get('stamp') != stamp:
                    content = read_json(file)
                    if retained.is_file():
                        allowed = set(read_json(retained))
                        content['edges'] = [edge for index, edge in enumerate(content['edges']) if index in allowed]
                    static_cache['body'] = json.dumps(content, ensure_ascii=True).encode()
                    static_cache['stamp'] = stamp
                return self.send_bytes(static_cache['body'], 'application/json')
            if parsed.path in ('/download/branches-all.json', '/download/branches-acyclic.json', '/download/branches-cycles-excluded.json'):
                file = data / 'static-story' / parsed.path.rsplit('/', 1)[1]
                if not file.is_file():
                    return self.send_error(404)
                return self.send_bytes(file.read_bytes(), 'application/json')
            if parsed.path == '/api/nodes':
                term = q.get('q', [''])[0].casefold()
                found = [n for n in graph['nodes'] if term in n['name'].casefold()]
                return self.send_json(dict(total=len(found), nodes=found[:100]))
            if parsed.path == '/api/node':
                node_id = q.get('id', [''])[0]
                if node_id not in nodes:
                    return self.send_error(404)
                loc = read_json(data / 'locations' / (node_id + '.json'))
                return self.send_json(dict(location=loc, outgoing=outgoing[node_id], incoming=incoming[node_id]))
            public = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/translate': ('translate.html', 'text/html; charset=utf-8'),
                      '/translate.js': ('translate.js', 'text/javascript; charset=utf-8'),
                      '/translate.css': ('translate.css', 'text/css; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8'),
                      '/download/graph.json': (None, 'application/json')}
            if parsed.path not in public:
                return self.send_error(404)
            file, mime = public[parsed.path]
            self.send_bytes(((assets / file) if file else (data / 'graph.json')).read_bytes(), mime)

        def do_POST(self):
            host = self.headers.get('Host', '')
            if host not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                return self.send_error(403)
            if self.headers.get('Origin') not in (None, 'http://' + host) or not hmac.compare_digest(self.headers.get('X-Atlas-CSRF', ''), csrf_token):
                return self.send_json({'error': '請從本站重新載入後操作。'}, 403)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 2 * 1024 * 1024:
                    return self.send_json({'error': '請求內容太大或為空。'}, 413)
                values = json.loads(self.rfile.read(size))
                if not isinstance(values, dict):
                    raise ValueError('請求格式錯誤')
                service = workspace()
                route = urlsplit(self.path).path
                from round_status import test_round_status
                control=test_round_status(data)
                if control.get('isolated') is False and (control.get('running') or control['state']=='unconfirmed') and route not in ('/api/workflow/auth/start','/api/workflow/auth/disconnect'):
                    return self.send_json({'error':'正式批量工作正在執行；結果會直接保存，暫停其他修改或重送以避免覆寫。'},409)
                with service.db() as db:run=db.execute('SELECT id FROM runs ORDER BY created,id LIMIT 1').fetchone()
                branch_control=branches().status(run['id']) if run else dict(state='idle')
                if branch_control['state'] in ('running','unconfirmed') and route not in ('/api/workflow/branches/stop','/api/workflow/auth/start','/api/workflow/auth/disconnect'):
                    return self.send_json({'error':'分支佇列正在執行或狀態未確認，不能另外修改或重送。'},409)
                if route == '/api/workflow/auth/start':
                    session, url = oauth.start(self.auth_session(), 'http://' + host)
                    return self.send_json(dict(authorize_url=url), cookie=f'{OAuth.COOKIE}={session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000')
                elif route == '/api/workflow/auth/disconnect':
                    result = oauth.disconnect(self.auth_session())
                    return self.send_json(result, cookie=f'{OAuth.COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0')
                elif route == '/api/workflow/branches/start':
                    result=branches().start(values['run_id'],values.get('count'),values.get('concurrency'),oauth.key(self.auth_session()),values.get('confirmed'))
                elif route == '/api/workflow/branches/resume':
                    result=branches().resume(values.get('session_id'),values.get('concurrency'),oauth.key(self.auth_session()),values.get('confirmed'))
                elif route == '/api/workflow/branches/stop':
                    result=branches().stop(values.get('session_id'))
                elif route == '/api/workflow/settings':
                    result = service.save_settings(values.get('system_prompt'), values.get('revision'))
                elif route == '/api/workflow/import':
                    result = service.import_json(int(values['batch_id']), values.get('raw'), int(values['revision']))
                elif route == '/api/workflow/generate':
                    result = service.generate(int(values['batch_id']), int(values['revision']), values.get('settings_revision'), oauth.key(self.auth_session()), values.get('confirmed'))
                elif route == '/api/workflow/generate-test':
                    result = service.generate(int(values['batch_id']), int(values['revision']), values.get('settings_revision'), oauth.key(self.auth_session()), values.get('confirmed'), test=True)
                elif route == '/api/workflow/add-proposal':
                    result = service.add_proposal(int(values['proposal_id']))
                elif route == '/api/workflow/review-dictionary':
                    result = service.review_dictionary(int(values['batch_id']), oauth.key(self.auth_session()), values.get('confirmed'))
                elif route == '/api/workflow/review-saved-draft':
                    result = service.review_saved_draft(int(values['batch_id']),int(values['revision']),oauth.key(self.auth_session()),values.get('confirmed'))
                elif route == '/api/workflow/run':
                    result = service.new_run(values.get('name'))
                elif route == '/api/workflow/prepare':
                    result = service.prepare(values['run_id'])
                elif route == '/api/workflow/save':
                    result = service.save(int(values['batch_id']), values['targets'], int(values['revision']), values.get('decisions'))
                elif route == '/api/workflow/approve':
                    result = service.approve(int(values['batch_id']), int(values['revision']), values.get('reviewed'))
                elif route == '/api/workflow/auto-submit':
                    if values.get('confirmed') is not True or not oauth.key(self.auth_session()):
                        raise ValueError('請先確認批量執行與機器檢查後自動提交；這不是人工審閱。')
                    result = service.approve(int(values['batch_id']),int(values['revision']),False,automatic=True)
                elif route == '/api/workflow/term':
                    result = service.save_term(values)
                elif route == '/api/workflow/refresh-dictionary':
                    result = service.refresh_dictionary(int(values['batch_id']))
                else:
                    return self.send_error(404)
                return self.send_json(result)
            except (ValueError, KeyError, TypeError) as error:
                return self.send_json({'error': str(error)}, 422)
            except FileNotFoundError:
                return self.send_json({'error': '翻譯索引尚未備妥。'}, 503)

        def send_json(self, obj, status=200, cookie=None):
            self.send_bytes(json.dumps(obj, ensure_ascii=True).encode(), 'application/json', status=status, cookie=cookie)

        def send_bytes(self, body, mime, runtime=False, status=200, cookie=None):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            if cookie:
                self.send_header('Set-Cookie', cookie)
            policy = ("default-src 'self' blob: data:; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'" if runtime else "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('Content-Security-Policy', policy)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'Local story workbench: http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(__file__).parent / 'data')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--player', type=Path)
    args = parser.parse_args()
    serve(args.data.resolve(), args.port, args.player.resolve() if args.player else None)
