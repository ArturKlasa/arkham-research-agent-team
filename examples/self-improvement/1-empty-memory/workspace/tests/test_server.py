"""Integration tests: a real server on port 0 exercised via urllib.request."""

import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from books import server as server_module
from books.data import BOOKS, BookRepository
from books.server import create_server, main, parse_args


def _non_daemon_threads():
    main_thread = threading.main_thread()
    return [t for t in threading.enumerate()
            if t is not main_thread and not t.daemon and t.is_alive()]


class _ServerTestCase(unittest.TestCase):
    repository = None

    @classmethod
    def setUpClass(cls):
        cls.server = create_server(port=0, repository=cls.repository)
        host, port = cls.server.server_address[:2]
        cls.base_url = "http://{}:{}".format(host, port)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={"poll_interval": 0.05},
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        assert not cls.thread.is_alive(), "server thread did not stop"

    def request(self, method, path, data=None):
        req = urllib.request.Request(self.base_url + path, data=data,
                                     method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            try:
                return err.code, err.headers, err.read()
            finally:
                err.close()

    def get_json(self, path):
        status, headers, body = self.request("GET", path)
        return status, headers, json.loads(body.decode("utf-8"))

    def assertJsonResponse(self, headers, body):
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertFalse(body.lstrip().startswith(b"<"))
        json.loads(body.decode("utf-8"))

    def assertJsonError(self, status, headers, body, expected_status):
        self.assertEqual(status, expected_status)
        self.assertJsonResponse(headers, body)
        payload = json.loads(body.decode("utf-8"))
        self.assertIsInstance(payload, dict)
        self.assertIn("error", payload)


class BookServerTests(_ServerTestCase):

    def ids(self, books):
        return [b["id"] for b in books]

    # -- R4 ---------------------------------------------------------------
    def test_list_all_books(self):
        status, headers, body = self.request("GET", "/books")
        self.assertEqual(status, 200)
        self.assertJsonResponse(headers, body)
        self.assertEqual(json.loads(body.decode("utf-8")), BOOKS)

    def test_list_all_books_trailing_slash(self):
        status, headers, body = self.request("GET", "/books/")
        self.assertEqual(status, 200)
        self.assertJsonResponse(headers, body)
        self.assertEqual(json.loads(body.decode("utf-8")), BOOKS)

    # -- R5 ---------------------------------------------------------------
    def test_get_book_by_id(self):
        status, headers, body = self.request("GET", "/books/1")
        self.assertEqual(status, 200)
        self.assertJsonResponse(headers, body)
        self.assertEqual(json.loads(body.decode("utf-8")), BOOKS[0])

    def test_get_book_ignores_query_string(self):
        status, _, payload = self.get_json("/books/1?x=y")
        self.assertEqual(status, 200)
        self.assertEqual(payload, BOOKS[0])

    # -- R6 ---------------------------------------------------------------
    def test_missing_book_returns_404(self):
        status, headers, body = self.request("GET", "/books/99999")
        self.assertJsonError(status, headers, body, 404)

    def test_huge_numeric_id_returns_404(self):
        status, headers, body = self.request("GET", "/books/" + "9" * 5000)
        self.assertJsonError(status, headers, body, 404)

    # -- R7 ---------------------------------------------------------------
    def test_non_integer_id_returns_400(self):
        for path in ("/books/abc", "/books/-1", "/books/1.5", "/books/%31a"):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertJsonError(status, headers, body, 400)

    # -- R8 ---------------------------------------------------------------
    def test_search_by_author(self):
        for query in ("J.R.R.%20Tolkien", "TOLKIEN", "olk"):
            with self.subTest(query=query):
                status, headers, body = self.request(
                    "GET", "/books?author=" + query)
                self.assertEqual(status, 200)
                self.assertJsonResponse(headers, body)
                self.assertEqual(self.ids(json.loads(body.decode("utf-8"))),
                                 [1, 2])

    def test_search_no_match_returns_empty_list(self):
        status, headers, body = self.request("GET", "/books?author=zzz")
        self.assertEqual(status, 200)
        self.assertJsonResponse(headers, body)
        self.assertEqual(json.loads(body.decode("utf-8")), [])

    # -- R9 ---------------------------------------------------------------
    def test_unknown_routes_return_404(self):
        for path in ("/nope", "/books/1/extra", "/"):
            with self.subTest(path=path):
                status, headers, body = self.request("GET", path)
                self.assertJsonError(status, headers, body, 404)

    def test_non_get_methods_return_405(self):
        cases = [("POST", b'{"title": "x"}'), ("PUT", None), ("DELETE", None),
                 ("PATCH", None), ("OPTIONS", None)]
        for method, data in cases:
            with self.subTest(method=method):
                status, headers, body = self.request(method, "/books", data)
                self.assertJsonError(status, headers, body, 405)
                self.assertEqual(headers["Allow"], "GET")

    def test_head_returns_405_without_body(self):
        status, headers, body = self.request("HEAD", "/books")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Allow"], "GET")
        self.assertEqual(body, b"")

    # -- R10/R11 -----------------------------------------------------------
    def test_unsupported_method_framework_error_is_json(self):
        status, headers, body = self.request("BREW", "/books")
        self.assertJsonError(status, headers, body, 501)

    def test_crlf_in_author_is_not_injected_into_headers(self):
        status, headers, body = self.request(
            "GET", "/books?author=%0D%0AX-Injected:%20yes")
        self.assertEqual(status, 200)
        self.assertJsonResponse(headers, body)
        self.assertEqual(json.loads(body.decode("utf-8")), [])
        self.assertIsNone(headers.get("X-Injected"))

    def test_no_request_logging_on_stderr(self):
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            self.request("GET", "/books")
            self.request("GET", "/books/99999")
            self.request("GET", "/books/abc")
            self.request("POST", "/books", b"{}")
        self.assertEqual(captured.getvalue(), "")

    def test_handler_reads_repository_from_server(self):
        self.assertIsInstance(self.server.repository, BookRepository)
        self.assertTrue(self.server.daemon_threads)


class InjectedRepositoryTests(_ServerTestCase):
    custom_book = {"id": 42, "title": "Custom", "author": "Someone", "year": 2000}
    repository = BookRepository([custom_book])

    def test_list_uses_injected_repository(self):
        status, _, payload = self.get_json("/books")
        self.assertEqual(status, 200)
        self.assertEqual(payload, [self.custom_book])

    def test_get_uses_injected_repository(self):
        self.assertEqual(self.get_json("/books/42")[2], self.custom_book)
        self.assertEqual(self.request("GET", "/books/1")[0], 404)


class FactoryAndCliTests(unittest.TestCase):

    def test_create_server_binds_localhost_by_default(self):
        srv = create_server(port=0)
        try:
            self.assertEqual(srv.server_address[0], "127.0.0.1")
            self.assertNotEqual(srv.server_address[1], 0)
            self.assertTrue(srv.daemon_threads)
            self.assertIsInstance(srv.repository, BookRepository)
        finally:
            srv.server_close()

    def test_parse_args(self):
        args = parse_args(["--port", "9001"])
        self.assertEqual(args.port, 9001)
        self.assertEqual(args.host, "127.0.0.1")
        defaults = parse_args([])
        self.assertEqual(defaults.port, 8000)

    def test_main_prints_address_and_exits_on_keyboard_interrupt(self):
        created = []
        real_create = server_module.create_server

        def fake_create(host, port):
            srv = real_create(host, port)
            srv.serve_forever = mock.Mock(side_effect=KeyboardInterrupt)
            srv.server_close = mock.Mock(wraps=srv.server_close)
            created.append(srv)
            return srv

        out = io.StringIO()
        with mock.patch.object(server_module, "create_server", fake_create), \
                contextlib.redirect_stdout(out):
            main(["--port", "0"])
        self.assertEqual(len(created), 1)
        port = created[0].server_address[1]
        self.assertIn("Serving on http://127.0.0.1:{}".format(port),
                      out.getvalue())
        created[0].server_close.assert_called_once_with()


def tearDownModule():
    leftover = _non_daemon_threads()
    if leftover:
        raise AssertionError("non-daemon threads still running: %r" % leftover)


class NoLeakedThreadsTests(unittest.TestCase):
    def test_no_non_daemon_threads(self):
        self.assertEqual(_non_daemon_threads(), [])
