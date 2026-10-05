"""Real loopback HTTP tests. Capture, device discovery and inference stay fake."""
from copy import deepcopy
import hashlib
import http.client
import http.cookiejar
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_capture
import lecture_live


class FakeApp:
    def __init__(self):
        self.state = lecture_live.blank_state()
        self.state['lines'] = [{'text': '<script>private transcript</script>'}]
        self.mutations = []
        self.lock = threading.RLock()
        self.result_dir = None

    def snapshot(self):
        return deepcopy(self.state)

    def start(self, payload):
        self.mutations.append(('start', payload))
        return self.snapshot()

    def stop(self):
        self.mutations.append(('stop', None))
        return self.snapshot()

    def retry_analysis(self):
        self.mutations.append(('retry', None))
        return self.snapshot()

    def retry_translation(self):
        self.mutations.append(('retry-translation', None))
        return self.snapshot()


class LectureHTTPTest(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.server, self.url = lecture_live.make_server(self.app, port=0)
        self.addCleanup(self.server.server_close)
        self.port = self.server.server_port
        self.host = f'127.0.0.1:{self.port}'
        self.token = parse_qs(urlsplit(self.url).query)['token'][0]
        self.cookie_name = f'lecture_session_{self.port}'
        self.cookie = f'{self.cookie_name}={self.token}'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, *, method='GET', body=None, authenticated=False, headers=None, host=True):
        supplied = dict(headers or {})
        if host and 'Host' not in supplied:
            supplied['Host'] = self.host
        if authenticated and 'Cookie' not in supplied:
            supplied['Cookie'] = self.cookie
        if body is not None:
            if not isinstance(body, bytes):
                body = body.encode('utf-8')
            supplied.setdefault('Content-Length', str(len(body)))
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in supplied.items():
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def post(self, path, value=None, *, raw=None, authenticated=True, headers=None):
        combined = {'Content-Type': 'application/json', 'Origin': f'http://{self.host}'}
        combined.update(headers or {})
        body = raw if raw is not None else json.dumps({} if value is None else value)
        return self.request(path, method='POST', body=body, authenticated=authenticated, headers=combined)

    def test_loopback_binding_and_token_exchange_security_headers(self):
        self.assertEqual('127.0.0.1', self.server.server_address[0])
        self.assertGreaterEqual(len(self.token), 40)
        status, headers, body = self.request('/?token=' + self.token)
        self.assertEqual(200, status)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn(self.cookie, headers['Set-Cookie'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertEqual('no-referrer', headers['Referrer-Policy'])
        self.assertEqual('nosniff', headers['X-Content-Type-Options'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertNotIn(self.token.encode(), body)
        self.assertEqual([], self.app.mutations)

    def test_authentication_is_required_for_state_assets_and_mutation(self):
        for path in ('/', '/api/state', '/api/analysis-history', '/api/identity', '/api/devices', '/app.js', '/style.css'):
            with self.subTest(path=path):
                self.assertEqual(403, self.request(path)[0])
        self.assertEqual(403, self.request('/?token=wrong')[0])
        self.assertEqual(403, self.request('/api/state?token=' + self.token)[0])
        self.assertEqual(403, self.request('/api/state', headers={'Cookie': f'{self.cookie_name}=wrong'})[0])
        self.assertEqual(403, self.post('/api/start', authenticated=False)[0])
        self.assertEqual(403, self.post('/api/retry-translation', authenticated=False)[0])
        self.assertEqual([], self.app.mutations)

    def test_persisted_history_pages_are_read_only_and_bound_to_current_session(self):
        with tempfile.TemporaryDirectory() as directory:
            self.app.result_dir = Path(directory)
            self.app.state['session'] = {'id': 'synthetic-session'}
            records = [{'through_seconds': i, 'generated_at': i,
                        'headline': {'text': f'Synthetic {i}', 'source_ids': []}}
                       for i in range(75)]
            (self.app.result_dir / 'analysis-history.jsonl').write_text(
                ''.join(json.dumps(row) + '\n' for row in records))
            endpoint = '/api/analysis-history?session_id=synthetic-session&limit=60'
            status, _, body = self.request(endpoint, authenticated=True)
            self.assertEqual(200, status)
            page = json.loads(body)
            self.assertEqual(60, len(page['items']))
            status, _, body = self.request(endpoint + '&cursor=' + page['next_cursor'], authenticated=True)
            self.assertEqual(200, status)
            self.assertEqual(15, len(json.loads(body)['items']))
            self.assertEqual(409, self.request('/api/analysis-history?session_id=other', authenticated=True)[0])
            self.assertEqual(400, self.request(endpoint + '&cursor=bad', authenticated=True)[0])
            self.assertEqual([], self.app.mutations)
            # A CLI-selected saved view can page without setting a writable result_dir.
            self.app.history_result_dir, self.app.result_dir = self.app.result_dir, None
            self.assertEqual(200, self.request(endpoint, authenticated=True)[0])
            self.app.state['session'] = {'id': 'replacement'}
            self.assertEqual(409, self.request(endpoint, authenticated=True)[0])

    def test_history_session_change_during_read_discards_response(self):
        self.app.state['session'] = {'id': 'old'}
        self.app.result_dir = Path('/synthetic-only')
        def switched(*args, **kwargs):
            self.app.state['session'] = {'id': 'new'}
            return {'session_id': 'old', 'items': [{'headline': {'text': 'Never publish'}}]}
        with patch('lecture_history.read_history_page', side_effect=switched):
            status, _, body = self.request('/api/analysis-history?session_id=old', authenticated=True)
        self.assertEqual(409, status)
        self.assertNotIn(b'Never publish', body)

    def test_authenticated_identity_is_read_only_and_marks_our_application(self):
        status, _, raw = self.request('/api/identity', authenticated=True)
        self.assertEqual(200, status)
        data = json.loads(raw)
        self.assertEqual('live-lecture-translation', data['app_id'])
        self.assertEqual(1, data['schema_version'])
        self.assertIsInstance(data['process_id'], int)
        self.assertFalse(data['continuous_translation_enabled'])
        self.assertNotIn('token', data)
        self.assertEqual([], self.app.mutations)

    def test_non_ascii_credentials_are_denied_without_crashing(self):
        for query in ('%E3%81%82', '%C3%A9', '%FF'):
            with self.subTest(query=query):
                self.assertEqual(403, self.request('/?token=' + query)[0])
        for value in ('"é"', '"\\351"', '"broken'):
            cookie = f'{self.cookie_name}={value}'
            with self.subTest(cookie=cookie):
                self.assertEqual(403, self.request('/api/state', headers={'Cookie': cookie})[0])
        self.assertEqual(200, self.request('/api/state', authenticated=True)[0])
        self.assertEqual([], self.app.mutations)

    def test_legacy_or_other_port_cookie_cannot_authenticate(self):
        for name in ('lecture_session', f'lecture_session_{self.port + 1}'):
            with self.subTest(name=name):
                self.assertEqual(403, self.request('/api/state', headers={'Cookie': f'{name}={self.token}'})[0])
        self.assertEqual(200, self.request('/api/state', authenticated=True)[0])

    def test_shared_cookie_jar_keeps_two_servers_authenticated(self):
        other_app = FakeApp()
        other_app.state['lines'] = [{'text': 'second server'}]
        other_server, other_url = lecture_live.make_server(other_app, port=0)
        self.addCleanup(other_server.server_close)
        other_thread = threading.Thread(target=other_server.serve_forever,
                                        kwargs={'poll_interval': .01}, daemon=True)
        other_thread.start()
        def stop_other():
            other_server.shutdown()
            other_thread.join(2)
            self.assertFalse(other_thread.is_alive())
        self.addCleanup(stop_other)
        jar = http.cookiejar.CookieJar()
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(jar))
        for url in (self.url, other_url):
            with opener.open(url, timeout=3) as response:
                self.assertEqual(200, response.status)
                response.read()
        self.assertEqual({self.cookie_name, f'lecture_session_{other_server.server_port}'},
                         {cookie.name for cookie in jar})
        # Cookies are sent to both ports, so each server must select its own.
        for url, app in ((self.url, self.app), (other_url, other_app), (self.url, self.app)):
            origin = urlsplit(url)
            with opener.open(f'{origin.scheme}://{origin.netloc}/api/state', timeout=3) as response:
                self.assertEqual(app.snapshot(), json.load(response))
        self.assertEqual([], self.app.mutations)
        self.assertEqual([], other_app.mutations)

    def test_exact_host_required_even_with_valid_cookie_or_token(self):
        for host in ('localhost:' + str(self.port), '127.0.0.1:' + str(self.port + 1), 'evil.example', '127.0.0.1'):
            with self.subTest(host=host):
                self.assertEqual(403, self.request('/api/state', authenticated=True, headers={'Host': host})[0])
                self.assertEqual(403, self.request('/?token=' + self.token, headers={'Host': host})[0])
        self.assertEqual(403, self.request('/api/state', authenticated=True, host=False)[0])
        self.assertEqual([], self.app.mutations)

    def test_status_get_is_read_only_and_does_not_invoke_device_discovery(self):
        before = deepcopy(self.app.state)
        with patch.object(lecture_capture, 'list_devices', side_effect=AssertionError('unexpected device discovery')):
            for _ in range(3):
                status, headers, body = self.request('/api/state', authenticated=True)
                self.assertEqual(200, status)
                self.assertIn('application/json', headers['Content-Type'])
                self.assertEqual(before, json.loads(body))
                self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(before, self.app.state)
        self.assertEqual([], self.app.mutations)

    def test_device_list_uses_read_only_discovery_and_reports_failure(self):
        with patch.object(lecture_capture, 'list_devices', return_value=[{'id': '1', 'name': 'Synthetic microphone'}]) as discover:
            status, _, body = self.request('/api/devices', authenticated=True)
            self.assertEqual(200, status)
            self.assertEqual('1', json.loads(body)['devices'][0]['id'])
            discover.assert_called_once_with()
        with patch.object(lecture_capture, 'list_devices', side_effect=RuntimeError('synthetic unavailable')):
            status, _, body = self.request('/api/devices', authenticated=True)
            self.assertEqual(200, status)
            self.assertEqual([], json.loads(body)['devices'])
            self.assertIn('unavailable', json.loads(body)['error'])
        self.assertEqual([], self.app.mutations)

    def test_static_allowlist_refuses_traversal_and_get_mutations(self):
        for path in ('/../CLAUDE.md', '/%2e%2e/CLAUDE.md', '/app.js/../CLAUDE.md',
                     '/data/event-audio/raw.pcm', '/api/start', '/api/stop', '/api/retry-analysis', '/api/retry-translation'):
            with self.subTest(path=path):
                status, _, body = self.request(path, authenticated=True)
                self.assertEqual(404, status)
                self.assertEqual({'error': 'Not found'}, json.loads(body))
        for path in ('/app.js', '/style.css'):
            self.assertEqual(200, self.request(path, authenticated=True)[0])
        self.assertEqual([], self.app.mutations)

    def test_origin_and_json_content_type_are_required_for_browser_posts(self):
        for origin in ('https://evil.example', 'http://localhost:' + str(self.port), 'null'):
            with self.subTest(origin=origin):
                self.assertEqual(403, self.post('/api/stop', headers={'Origin': origin})[0])
        for content_type in ('text/plain', 'application/x-www-form-urlencoded', ''):
            with self.subTest(content_type=content_type):
                self.assertEqual(415, self.post('/api/stop', headers={'Content-Type': content_type})[0])
        self.assertEqual([], self.app.mutations)
        # Non-browser local clients may omit Origin but still require the token.
        status, _, _ = self.request('/api/stop', method='POST', body='{}', authenticated=True,
                                    headers={'Content-Type': 'application/json'})
        self.assertEqual(200, status)
        self.assertEqual([('stop', None)], self.app.mutations)

    def test_malformed_or_non_object_json_never_performs_action(self):
        for path in ('/api/start', '/api/stop', '/api/retry-analysis', '/api/retry-translation'):
            for body in ('{', '[]', 'null', 'true', '1', '"text"'):
                with self.subTest(path=path, body=body):
                    self.assertEqual(400, self.post(path, raw=body)[0])
        for length in ('-1', '0', '4097', 'not-an-integer'):
            with self.subTest(length=length):
                self.assertEqual(400, self.post('/api/stop', headers={'Content-Length': length})[0])
        self.assertEqual([], self.app.mutations)

    def test_valid_post_routes_dispatch_exactly_once_and_unknown_route_is_safe(self):
        self.assertEqual(200, self.post('/api/start', {'language': 'en', 'provider': 'off'})[0])
        self.assertEqual(200, self.post('/api/stop')[0])
        self.assertEqual(200, self.post('/api/retry-analysis')[0])
        self.assertEqual(200, self.post('/api/retry-translation')[0])
        self.assertEqual([('start', {'language': 'en', 'provider': 'off'}), ('stop', None), ('retry', None), ('retry-translation', None)],
                         self.app.mutations)
        self.assertEqual(404, self.post('/api/start?extra=1')[0])
        self.assertEqual(404, self.post('/unknown')[0])
        self.assertEqual(4, len(self.app.mutations))

    def test_application_failures_have_explicit_status_without_exception_details(self):
        for exception, status in ((ValueError('invalid input'), 400),
                                  (RuntimeError('already processing'), 409),
                                  (OSError('private /sensitive/path'), 500)):
            with self.subTest(exception=type(exception).__name__):
                with patch.object(self.app, 'start', side_effect=exception):
                    code, _, body = self.post('/api/start')
                self.assertEqual(status, code)
                if status == 500:
                    self.assertNotIn(b'/sensitive/path', body)

    def test_archived_state_get_stop_and_retry_do_not_rewrite_saved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            saved_path = Path(directory) / 'state.json'
            saved = lecture_live.blank_state()
            saved['capture']['state'] = 'completed'
            saved['analysis'].update(state='completed', provider='local')
            saved['lines'] = [{'id': 'saved-1', 'text': 'Synthetic archived text',
                               'start_seconds': 0, 'end_seconds': 1}]
            saved_path.write_text(json.dumps(saved))
            before = hashlib.sha256(saved_path.read_bytes()).hexdigest()
            archived = lecture_live.LectureApp(data_root=Path(directory) / 'data',
                                             results_root=Path(directory) / 'results')
            archived.state = deepcopy(saved)
            # The Handler closure retains this app, so redirect the stub methods
            # to the real read-only archive behavior without opening a new server.
            with patch.object(self.app, 'snapshot', side_effect=archived.snapshot), \
                    patch.object(self.app, 'stop', side_effect=archived.stop), \
                    patch.object(self.app, 'retry_analysis', side_effect=archived.retry_analysis), \
                    patch.object(self.app, 'retry_translation', side_effect=archived.retry_translation):
                self.assertEqual(200, self.request('/api/state', authenticated=True)[0])
                self.assertEqual(200, self.post('/api/stop')[0])
                self.assertEqual(400, self.post('/api/retry-analysis')[0])
                self.assertEqual(400, self.post('/api/retry-translation')[0])
            self.assertEqual(before, hashlib.sha256(saved_path.read_bytes()).hexdigest())
            self.assertFalse((Path(directory) / 'data').exists())
            self.assertFalse((Path(directory) / 'results').exists())
            self.assertIsNone(archived.worker)
            self.assertIsNone(archived.recorder)


if __name__ == '__main__':
    unittest.main()
