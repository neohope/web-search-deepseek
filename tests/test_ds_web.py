"""Behavioral tests for scripts/ds_web.py against a local mock of the Search and Fetch APIs."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

# Import the CLI from its script location before the module under test is loaded.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import ds_web  # noqa: E402

STATE = {'limited': 0, 'unstable': 0}


def _result(**overrides):
    base = {
        'rerank_score': 2.4,
        'title': 'DeepSeek V4',
        'url': 'https://example.com/v4',
        'snippet': 'short snippet',
        'summary': 'long summary',
        'content': '',
        'site_name': 'Example News',
        'site_icon': None,
        'date': '2026-06-01T00:00:00+00:00',
        'authority': 3,
    }
    base.update(overrides)
    return base


class _Handler(BaseHTTPRequestHandler):
    """Mimic both endpoints; behaviors are selected by words inside the query or URL."""

    def log_message(self, *args):
        pass

    def _send(self, status, payload, headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message, code='invalid_request_error', headers=None):
        envelope = {'error': {'message': message, 'type': code, 'param': None, 'code': code}}
        self._send(status, envelope, headers)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(length)
        if self.headers.get('Authorization') != 'Bearer test-key':
            return self._error(401, 'invalid api key', 'authentication_error')
        payload = json.loads(raw or b'{}')
        if self.path == '/v1/tools/search':
            return self._search(payload)
        if self.path == '/v1/tools/fetch':
            return self._fetch(payload)
        return self._error(404, 'unknown path', 'not_found')

    def _search(self, payload):
        query = str(payload.get('query', ''))
        if 'empty' in query:
            return self._send(200, {'results': []})
        if 'balance' in query:
            return self._error(402, 'insufficient balance', 'insufficient_balance')
        if 'limited' in query:
            STATE['limited'] += 1
            if STATE['limited'] == 1:
                return self._error(429, 'rate limited', 'rate_limit_exceeded', {'Retry-After': '0'})
        if 'unstable' in query:
            STATE['unstable'] += 1
            if STATE['unstable'] == 1:
                return self._error(503, 'service unavailable', 'server_error')
        if 'badjson' in query:
            return self._send(200, b'<html>not json</html>')
        if 'echo' in query:
            echoed = _result(
                title='echo',
                summary=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                rerank_score=1.0,
                site_name=None,
                date=None,
                authority=-1,
            )
            return self._send(200, {'results': [echoed]})
        body = '正文' * 200 if payload.get('need_content') else ''
        return self._send(200, {'results': [
            _result(content=body),
            _result(
                rerank_score=0.8,
                title='Forum post',
                url='https://forum.example/1',
                summary='forum summary',
                site_name=None,
                date=None,
                authority=-1,
            ),
        ]})

    def _fetch(self, payload):
        url = str(payload.get('url', ''))
        if 'missing' in url:
            return self._error(404, 'no extractable page content', 'not_found')
        if 'long' in url:
            return self._send(200, {'title': 'Long page', 'content': 'x' * 5000})
        return self._send(200, {'title': 'Example article', 'content': '# Example\n\nHello 世界。'})


def run_cli(*argv):
    """Run the CLI in-process and capture its streams instead of shelling out."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = ds_web.main(list(argv))
        except SystemExit as exit_error:
            code = exit_error.code if isinstance(exit_error.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    @property
    def base(self):
        return ['--api-key', 'test-key', '--base-url', f'http://127.0.0.1:{self.port}']

    def test_search_prints_ranked_fields(self):
        code, out, err = run_cli('search', 'plain', *self.base)
        self.assertEqual(code, 0, err)
        self.assertIn('results: 2', out)
        self.assertIn('score: 2.40', out)
        self.assertIn('authority: 3', out)
        self.assertIn('date: 2026-06-01', out)
        self.assertIn('site: Example News', out)
        self.assertIn('summary: long summary', out)
        self.assertIn('url: https://example.com/v4', out)

    def test_empty_results_exit_zero_with_notice(self):
        code, out, _ = run_cli('search', 'empty', *self.base)
        self.assertEqual(code, 0)
        self.assertIn('results: 0', out)
        self.assertIn('no results matched', out)

    def test_json_mode_returns_the_api_payload(self):
        code, out, _ = run_cli('search', 'plain', '--json', *self.base)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(len(payload['results']), 2)
        self.assertEqual(payload['results'][0]['url'], 'https://example.com/v4')

    def test_content_is_truncated_at_content_chars(self):
        code, out, _ = run_cli('search', 'plain', '--content', '--content-chars', '10', *self.base)
        self.assertEqual(code, 0)
        self.assertIn('content: 正文正文正文正文正文', out)
        self.assertIn('truncated 390 characters', out)

    def test_ori_question_and_localization_reach_the_api(self):
        code, out, _ = run_cli(
            'search', 'echo', '--count', '3', '--ori-question', '原始问题', '--localization', '1.2.3.4', *self.base,
        )
        self.assertEqual(code, 0)
        self.assertIn('"ori_question": "原始问题"', out)
        self.assertIn('"localization": "1.2.3.4"', out)
        self.assertIn('"count": 3', out)

    def test_parse_env_file_skips_comments_and_strips_quotes(self):
        text = '# comment\nSIMPLE=value\nQUOTED="quoted value"\nexport EXPORTED=https://api.example\n\n'
        with mock.patch.object(Path, 'read_text', return_value=text):
            values = ds_web.parse_env_file(Path('.env'))
        self.assertEqual(values['SIMPLE'], 'value')
        self.assertEqual(values['QUOTED'], 'quoted value')
        self.assertEqual(values['EXPORTED'], 'https://api.example')

    def test_missing_key_exits_two_before_requesting(self):
        with mock.patch.object(ds_web, 'parse_env_file', return_value={}), \
                mock.patch.object(Path, 'is_file', return_value=False), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DEEPSEEK_API_KEY', None)
            code, _, err = run_cli('search', 'plain')
        self.assertEqual(code, 2)
        self.assertIn('no API key', err)

    def test_env_file_supplies_key_and_normalizes_base_url(self):
        dotenv = {
            'DEEPSEEK_API_KEY': 'test-key',
            'DEEPSEEK_BASE_URL': f'http://127.0.0.1:{self.port}/v1/',
        }
        with mock.patch.object(ds_web, 'parse_env_file', return_value=dotenv), \
                mock.patch.object(Path, 'is_file', return_value=True), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DEEPSEEK_API_KEY', None)
            os.environ.pop('DEEPSEEK_BASE_URL', None)
            code, out, err = run_cli('search', 'plain')
        self.assertEqual(code, 0, err)
        self.assertIn('results: 2', out)

    def test_402_reports_status_and_exits_one(self):
        code, _, err = run_cli('search', 'balance', *self.base)
        self.assertEqual(code, 1)
        self.assertIn('HTTP 402', err)
        self.assertIn('insufficient balance', err)

    def test_401_is_not_retried(self):
        code, _, err = run_cli(
            'search', 'plain', '--api-key', 'wrong-key', '--base-url', f'http://127.0.0.1:{self.port}',
        )
        self.assertEqual(code, 1)
        self.assertIn('HTTP 401', err)

    def test_429_is_retried_then_succeeds(self):
        STATE['limited'] = 0
        with mock.patch.object(ds_web, 'retry_delay', return_value=0.0):
            code, out, err = run_cli('search', 'limited', *self.base)
        self.assertEqual(code, 0, err)
        self.assertEqual(STATE['limited'], 2)
        self.assertIn('results: 2', out)

    def test_503_is_retried_then_succeeds(self):
        STATE['unstable'] = 0
        with mock.patch.object(ds_web, 'retry_delay', return_value=0.0):
            code, _, err = run_cli('search', 'unstable', *self.base)
        self.assertEqual(code, 0, err)
        self.assertEqual(STATE['unstable'], 2)

    def test_unparsable_body_is_reported(self):
        code, _, err = run_cli('search', 'badjson', *self.base)
        self.assertEqual(code, 1)
        self.assertIn('unreadable response', err)

    def test_fetch_returns_title_and_markdown(self):
        code, out, _ = run_cli('fetch', 'https://example.com/article', *self.base)
        self.assertEqual(code, 0)
        self.assertIn('title: Example article', out)
        self.assertIn('# Example', out)

    def test_fetch_multiple_urls_prints_one_block_each(self):
        code, out, _ = run_cli('fetch', 'https://a.example/1', 'https://b.example/2', *self.base)
        self.assertEqual(code, 0)
        self.assertEqual(out.count('url: '), 2)

    def test_fetch_404_is_reported(self):
        code, _, err = run_cli('fetch', 'https://example.com/missing', *self.base)
        self.assertEqual(code, 1)
        self.assertIn('HTTP 404', err)

    def test_fetch_truncates_at_max_chars(self):
        code, out, _ = run_cli('fetch', 'https://example.com/long', '--max-chars', '10', *self.base)
        self.assertEqual(code, 0)
        self.assertIn('truncated 4990 characters', out)

    def test_fetch_json_carries_the_url(self):
        code, out, _ = run_cli('fetch', 'https://example.com/article', '--json', *self.base)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload['url'], 'https://example.com/article')
        self.assertEqual(payload['title'], 'Example article')

    def test_invalid_arguments_exit_two(self):
        url = f'http://127.0.0.1:{self.port}'
        common = ['--api-key', 'test-key', '--base-url', url]
        cases = {
            'count above 50': ('search', 'plain', '--count', '51', *common),
            'count below 1': ('search', 'plain', '--count', '0', *common),
            'blank query': ('search', '   ', *common),
            'query over 128 characters': ('search', 'a' * 129, *common),
            'non-http scheme': ('fetch', 'ftp://example.com/x', *common),
            'credentials in url': ('fetch', 'https://user:pw@example.com/x', *common),
            'negative retries': ('search', 'plain', '--retries', '-1', *common),
        }
        for name, argv in cases.items():
            with self.subTest(case=name):
                code, _, _ = run_cli(*argv)
                self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
