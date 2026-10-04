"""Launcher regressions with fake HTTP only; no server or browser is started."""
import io
from http.cookies import SimpleCookie
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lecture_launcher as launcher


TOKEN = "safe_token_12345678901234567890"
URL = f"http://127.0.0.1:8776/?token={TOKEN}"
IDENTITY = {"app_id": "live-lecture-translation", "schema_version": 1, "cloud_enabled": True}
STATE = {"schema_version": 1, "capture": {"state": "recording"}, "capabilities": {"cloud_enabled": True}}


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url_file = Path(self.temp.name) / "url.txt"
        self.url_file.write_text(URL + "\n")

    def test_verified_identity_and_auth_state_required(self):
        calls = []
        def getter(path, token):
            calls.append((path, token))
            return IDENTITY if path == "/api/identity" else STATE
        self.assertEqual(launcher.verify_existing(self.url_file, getter=getter), (URL, IDENTITY))
        self.assertEqual(calls, [("/api/identity", TOKEN), ("/api/state", TOKEN)])

    def test_malicious_or_ambiguous_urls_never_make_request(self):
        invalid = [URL.replace("127.0.0.1", "evil.example"), URL.replace("http:", "https:"),
                   URL.replace(":8776", ":8767"), URL.replace("/?", "/api/start?"),
                   URL + "#fragment", URL + "&token=", URL + "&other=", URL + "&token=" + TOKEN,
                   URL.replace("127.0.0.1", "x@127.0.0.1"), URL.replace(TOKEN, "short"),
                   URL.replace(TOKEN, "x%0d%0aHeader%3abad"), "x" * 2049, ""]
        for url in invalid:
            with self.subTest(url=url):
                self.url_file.write_text(url)
                with patch.object(launcher, "get_json") as getter:
                    with self.assertRaises(launcher.LauncherError):
                        launcher.verify_existing(self.url_file, getter=getter)
                    getter.assert_not_called()

    def test_missing_url_has_no_secret_in_error(self):
        self.url_file.unlink()
        with self.assertRaises(launcher.LauncherError) as failure:
            launcher.read_saved_url(self.url_file)
        self.assertNotIn(TOKEN, str(failure.exception))

    def test_unknown_identity_does_not_read_state(self):
        for identity in [{}, {**IDENTITY, "app_id": "another-app"}, {**IDENTITY, "schema_version": True}, {**IDENTITY, "cloud_enabled": 1}]:
            with self.subTest(identity=identity):
                with patch.object(launcher, "get_json", return_value=identity) as getter:
                    with self.assertRaises(launcher.LauncherError):
                        launcher.verify_existing(self.url_file, getter=getter)
                    self.assertEqual(getter.call_count, 1)

    def test_state_schema_and_cloud_identity_must_agree(self):
        for state in [{}, {**STATE, "schema_version": True}, {**STATE, "capabilities": []},
                      {**STATE, "capabilities": {"cloud_enabled": False}}, {**STATE, "capture": None}]:
            with self.subTest(state=state):
                with self.assertRaises(launcher.LauncherError):
                    launcher.verify_existing(self.url_file, getter=lambda path, token: IDENTITY if path.endswith("identity") else state)

    def test_http_is_fixed_origin_get_cookie_and_no_redirect(self):
        class FakeConnection:
            def __init__(self, host, port, timeout):
                self.args = (host, port, timeout); self.closed = False
            def request(self, method, path, headers): self.request_args = (method, path, headers)
            def getresponse(self): return self
            status = 302
            def read(self, amount): return json.dumps(IDENTITY).encode()
            def close(self): self.closed = True
        connection = FakeConnection("127.0.0.1", 8776, 4)
        with self.assertRaises(launcher.LauncherError):
            launcher.get_json("/api/identity", TOKEN, connection_factory=lambda *args, **kwargs: connection)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.request_args, ("GET", "/api/identity", {"Cookie": f"lecture_session_8776={TOKEN}", "Accept": "application/json"}))
        connection.status = 200
        self.assertEqual(launcher.get_json("/api/identity", TOKEN, connection_factory=lambda *args, **kwargs: connection), IDENTITY)

    def test_fixed_origin_header_only_authenticates_own_port_cookie(self):
        for accepted_name in ('lecture_session', 'lecture_session_8766', 'lecture_session_8776'):
            with self.subTest(accepted_name=accepted_name):
                class FakeConnection:
                    status = 403
                    def request(self, method, path, headers):
                        cookie = SimpleCookie(headers['Cookie']).get(accepted_name)
                        self.status = 200 if cookie and cookie.value == TOKEN else 403
                    def getresponse(self): return self
                    def read(self, amount): return json.dumps(IDENTITY).encode()
                    def close(self): pass
                factory = unittest.mock.Mock(return_value=FakeConnection())
                if accepted_name == 'lecture_session_8776':
                    self.assertEqual(IDENTITY, launcher.get_json('/api/identity', TOKEN,
                                                               connection_factory=factory))
                else:
                    with self.assertRaises(launcher.LauncherError):
                        launcher.get_json('/api/identity', TOKEN, connection_factory=factory)
                factory.assert_called_once_with('127.0.0.1', 8776, timeout=4)

    def test_auth_failure_or_non_json_response_denied(self):
        for status, body in [(401, b"unauthorized"), (200, b"<html>another app</html>"), (200, b"[]")]:
            class FakeConnection:
                def request(self, *args, **kwargs): pass
                def getresponse(self): return self
                def read(self, amount): return body
                def close(self): pass
            fake = FakeConnection(); fake.status = status
            with self.assertRaises(launcher.LauncherError):
                launcher.get_json("/api/state", TOKEN, connection_factory=lambda *args, **kwargs: fake)

    def test_reopen_only_after_verification(self):
        with patch.object(launcher, "verify_existing", return_value=(URL, IDENTITY)), patch.object(launcher.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(["--url-file", str(self.url_file), "--cloud"]), 0)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/open", URL])

    def test_check_never_opens_browser(self):
        with patch.object(launcher, "verify_existing", return_value=(URL, IDENTITY)), patch.object(launcher.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(["--url-file", str(self.url_file), "--check"]), 0)
            run.assert_not_called()

    def test_failure_never_opens_browser(self):
        with patch.object(launcher, "verify_existing", side_effect=launcher.LauncherError("stale")), patch.object(launcher.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(["--url-file", str(self.url_file)]), 1)
            run.assert_not_called()

    def test_local_reuse_informs_without_upgrade(self):
        out = io.StringIO()
        with patch.object(launcher, "verify_existing", return_value=(URL, {**IDENTITY, "cloud_enabled": False})), patch.object(launcher.subprocess, "run") as run, redirect_stdout(out):
            self.assertEqual(launcher.main(["--url-file", str(self.url_file), "--cloud"]), 0)
            self.assertEqual(run.call_count, 1)
        self.assertIn("既存アプリはローカル構成", out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())


if __name__ == "__main__":
    unittest.main()
