"""Integration tests for bookapi.server against a live server on an ephemeral port."""

import contextlib
import io
import json
import threading
import unittest
import unittest.mock as mock
import urllib.error
import urllib.request

from bookapi import data
from bookapi import server as server_mod
from bookapi.server import BookRequestHandler, main, make_server

JSON_CT = "application/json; charset=utf-8"


class LiveServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)

    def request(self, method, path, data=None):
        url = "http://127.0.0.1:{}{}".format(self.port, path)
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            try:
                body = err.read()
            finally:
                err.close()
            return err.code, err.headers, body

    def assert_json_headers(self, headers, body):
        self.assertEqual(headers.get("Content-Type"), JSON_CT)
        self.assertEqual(int(headers.get("Content-Length")), len(body))

    def get_json(self, path, expected_status):
        status, headers, body = self.request("GET", path)
        self.assertEqual(status, expected_status, path)
        self.assert_json_headers(headers, body)
        return json.loads(body.decode("utf-8"))

    def assert_error(self, payload):
        self.assertIsInstance(payload, dict)
        self.assertIn("error", payload)
        self.assertIsInstance(payload["error"], str)


class ListAndGetTests(LiveServerTestCase):
    def test_list_books(self):
        payload = self.get_json("/books", 200)
        self.assertIsInstance(payload, list)
        self.assertEqual(payload, data.list_books())

    def test_list_books_ignores_other_params(self):
        self.assertEqual(self.get_json("/books?sort=year", 200), data.list_books())

    def test_get_existing_book(self):
        payload = self.get_json("/books/1", 200)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload, data.get_book(1))

    def test_get_every_book(self):
        for book in data.list_books():
            with self.subTest(id=book["id"]):
                self.assertEqual(self.get_json("/books/{}".format(book["id"]), 200), book)

    def test_get_unicode_book_body_is_utf8(self):
        payload = self.get_json("/books/5", 200)
        self.assertEqual(payload["author"], "Gabriel Garc\u00eda M\u00e1rquez")

    def test_get_ignores_query(self):
        self.assertEqual(self.get_json("/books/2?x=1", 200), data.get_book(2))

    def test_missing_book_404(self):
        payload = self.get_json("/books/99999", 404)
        self.assert_error(payload)
        self.assertEqual(payload["error"], "Book not found")

    def test_zero_id_404(self):
        self.assert_error(self.get_json("/books/0", 404))

    def test_non_integer_id_400(self):
        for path in ("/books/abc", "/books/-1", "/books/1.5", "/books/1a", "/books/%20"):
            with self.subTest(path=path):
                payload = self.get_json(path, 400)
                self.assert_error(payload)
                self.assertEqual(payload["error"], "Invalid book id; must be a non-negative integer")

    def test_non_ascii_digits_400(self):
        # Arabic-Indic digit one, percent-encoded; must not be accepted by int().
        self.assert_error(self.get_json("/books/%D9%A1", 400))


class SearchTests(LiveServerTestCase):
    def expected(self, author):
        return [b for b in data.list_books() if b["author"] == author]

    def test_known_author(self):
        payload = self.get_json("/books?author=George%20Orwell", 200)
        self.assertEqual(payload, self.expected("George Orwell"))
        self.assertEqual(len(payload), 2)

    def test_partial_lowercase(self):
        self.assertEqual(self.get_json("/books?author=orwell", 200),
                         self.expected("George Orwell"))

    def test_partial_uppercase(self):
        self.assertEqual(self.get_json("/books?author=ORWELL", 200),
                         self.expected("George Orwell"))

    def test_no_match_returns_empty_list(self):
        self.assertEqual(self.get_json("/books?author=zzz", 200), [])

    def test_percent_encoded_space(self):
        self.assertEqual(self.get_json("/books?author=jane%20austen", 200),
                         self.expected("Jane Austen"))

    def test_plus_encoded_space(self):
        self.assertEqual(self.get_json("/books?author=jane+austen", 200),
                         self.expected("Jane Austen"))

    def test_unicode(self):
        payload = self.get_json("/books?author=Garc%C3%ADa", 200)
        self.assertEqual(payload, self.expected("Gabriel Garc\u00eda M\u00e1rquez"))
        self.assertEqual(len(payload), 1)

    def test_unicode_case_insensitive(self):
        self.assertEqual(self.get_json("/books?author=GARC%C3%8DA", 200),
                         self.expected("Gabriel Garc\u00eda M\u00e1rquez"))

    def test_empty_author_matches_all(self):
        self.assertEqual(self.get_json("/books?author=", 200), data.list_books())


class RoutingErrorTests(LiveServerTestCase):
    def test_unknown_routes_404(self):
        for path in ("/nope", "/", "/books/", "/books/1/", "/books/1/extra", "/bookss"):
            with self.subTest(path=path):
                payload = self.get_json(path, 404)
                self.assert_error(payload)
                self.assertEqual(payload["error"], "Not found")

    def test_methods_not_allowed(self):
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            for path in ("/books", "/books/1", "/nope"):
                with self.subTest(method=method, path=path):
                    body_data = b"{}" if method in ("POST", "PUT", "PATCH") else None
                    status, headers, body = self.request(method, path, data=body_data)
                    self.assertEqual(status, 405)
                    self.assertEqual(headers.get("Allow"), "GET")
                    self.assert_json_headers(headers, body)
                    payload = json.loads(body.decode("utf-8"))
                    self.assert_error(payload)
                    self.assertEqual(payload["error"], "Method not allowed")

    def test_head_405_without_body(self):
        status, headers, body = self.request("HEAD", "/books")
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "GET")
        self.assertEqual(headers.get("Content-Type"), JSON_CT)
        self.assertEqual(body, b"")
        # Content-Length describes the would-be body, identical to the POST 405 body.
        _s, _h, post_body = self.request("POST", "/books", data=b"")
        self.assertEqual(int(headers.get("Content-Length")), len(post_body))

    def test_handler_does_not_use_send_error(self):
        self.assertIs(BookRequestHandler.send_error,
                      server_mod.BaseHTTPRequestHandler.send_error)
        with open(server_mod.__file__, encoding="utf-8") as fh:
            self.assertNotIn("send_error(", fh.read())


class LoggingTests(LiveServerTestCase):
    def test_requests_are_not_logged(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.request("GET", "/books")
            self.request("GET", "/books/99999")
            self.request("GET", "/books/abc")
            self.request("POST", "/books", data=b"")
        self.assertEqual(buf.getvalue(), "")


class _FakeServer:
    def __init__(self, host, port):
        self.server_address = (host, port)
        self.closed = False

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        self.closed = True


class MainTests(unittest.TestCase):
    """main() argument handling, with make_server patched via unittest.mock."""

    def setUp(self):
        self.servers = []

        def fake_make_server(host="127.0.0.1", port=8000):
            srv = _FakeServer(host, port)
            self.servers.append(srv)
            return srv

        patcher = mock.patch("bookapi.server.make_server", side_effect=fake_make_server)
        self.stub = patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(argv)
        return rc, out.getvalue()

    def test_defaults(self):
        rc, out = self.run_main([])
        self.assertEqual(rc, 0)
        self.stub.assert_called_once_with("127.0.0.1", 8000)
        self.assertIn("Serving on http://127.0.0.1:8000", out)
        self.assertTrue(self.servers[0].closed)

    def test_custom_host_and_port(self):
        rc, out = self.run_main(["--host", "0.0.0.0", "--port", "9001"])
        self.assertEqual(rc, 0)
        self.stub.assert_called_once_with("0.0.0.0", 9001)
        self.assertIn("Serving on http://0.0.0.0:9001", out)
        self.assertTrue(self.servers[0].closed)

    def test_invalid_port_rejected(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            main(["--port", "notanumber"])
        self.stub.assert_not_called()

    def test_patch_targets_module_global(self):
        self.assertIs(server_mod.make_server, self.stub)


class MakeServerTests(unittest.TestCase):
    def test_make_server_properties(self):
        srv = make_server("127.0.0.1", 0)
        try:
            self.assertIsInstance(srv, server_mod.ThreadingHTTPServer)
            self.assertTrue(srv.daemon_threads)
            self.assertIs(srv.RequestHandlerClass, BookRequestHandler)
            self.assertEqual(srv.server_address[0], "127.0.0.1")
            self.assertNotEqual(srv.server_address[1], 0)
        finally:
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
