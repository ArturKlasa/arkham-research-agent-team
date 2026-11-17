"""HTTP-level tests: a real server on port 0 in a background thread,
plus a subprocess test of the `python -m bookapi` entry point."""

import json
import pathlib
import re
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from bookapi.data import BOOKS, BookStore
from bookapi.server import CONTENT_TYPE, NOT_FOUND, make_server, parse_args

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ALL_IDS = [b["id"] for b in BOOKS]


def _http_get(url):
    """Return (status, headers, body_bytes); HTTP errors are not raised."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.headers, e.read()
        finally:
            e.close()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(port=0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def get(self, path):
        return _http_get(self.base + path)

    def assertJsonHeaders(self, headers, body):
        self.assertEqual(headers["Content-Type"], CONTENT_TYPE)
        self.assertIsNotNone(headers["Content-Length"])
        self.assertEqual(int(headers["Content-Length"]), len(body))

    def get_json(self, path, expected_status):
        status, headers, body = self.get(path)
        self.assertEqual(status, expected_status, path)
        self.assertJsonHeaders(headers, body)
        return json.loads(body.decode("utf-8"))

    # --- list / get ------------------------------------------------------
    def test_list_books(self):
        data = self.get_json("/books", 200)
        self.assertIsInstance(data, list)
        self.assertEqual(data, BOOKS)

    def test_get_book_1(self):
        data = self.get_json("/books/1", 200)
        expected = next(b for b in BOOKS if b["id"] == 1)
        self.assertEqual(data, expected)

    def test_get_book_5_utf8(self):
        status, headers, body = self.get("/books/5")
        self.assertEqual(status, 200)
        self.assertJsonHeaders(headers, body)
        data = json.loads(body.decode("utf-8"))
        expected = next(b for b in BOOKS if b["id"] == 5)
        self.assertEqual(data, expected)
        self.assertEqual(data["title"], "Cien a\u00f1os de soledad")
        self.assertEqual(data["author"], "Gabriel Garc\u00eda M\u00e1rquez")
        # Body is raw UTF-8 (not ASCII escapes), so bytes > characters.
        self.assertIn("a\u00f1os".encode("utf-8"), body)
        self.assertGreater(len(body), len(body.decode("utf-8")))

    # --- 404s ------------------------------------------------------------
    def test_not_found_paths(self):
        for path in ("/books/99999", "/books/abc", "/books/", "/nope"):
            with self.subTest(path=path):
                data = self.get_json(path, 404)
                self.assertEqual(data, {"error": "not found"})
                self.assertEqual(data, NOT_FOUND)

    def test_nested_and_root_paths_404(self):
        for path in ("/", "/books/1/x"):
            with self.subTest(path=path):
                self.assertEqual(self.get_json(path, 404),
                                 {"error": "not found"})

    # --- author search (by name) ----------------------------------------
    def test_author_search(self):
        cases = [
            ("/books?author=Jane%20Austen", [1, 2]),       # full, encoded space
            ("/books?author=JANE+AUSTEN", [1, 2]),         # case, '+' as space
            ("/books?author=orw", [3, 4]),                 # partial
            ("/books?author=" + urllib.parse.quote("Gabriel Garc\u00eda"),
             [5]),                                         # spaces + UTF-8
            ("/books?author=Tolkien", []),                 # miss
            ("/books?author=", ALL_IDS),                   # empty
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                data = self.get_json(path, 200)
                self.assertIsInstance(data, list)
                self.assertEqual([b["id"] for b in data], expected)

    def test_author_miss_is_empty_list(self):
        self.assertEqual(self.get_json("/books?author=Tolkien", 200), [])

    def test_author_empty_returns_all_books(self):
        self.assertEqual(self.get_json("/books?author=", 200), BOOKS)

    # --- config / wiring -------------------------------------------------
    def test_parse_args(self):
        self.assertEqual(parse_args(["--port", "0"]).port, 0)
        defaults = parse_args([])
        self.assertEqual(defaults.port, 8000)
        self.assertEqual(defaults.host, "127.0.0.1")
        self.assertEqual(parse_args(["--host", "0.0.0.0"]).host, "0.0.0.0")

    def test_server_store(self):
        self.assertIsInstance(self.server.store, BookStore)
        self.assertNotEqual(self.server.server_port, 0)


class MainModuleTest(unittest.TestCase):
    def test_module_entry_point_serves_books(self):
        proc = subprocess.Popen(
            [sys.executable, "-m", "bookapi", "--port", "0"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            line = proc.stdout.readline()
            if not line:
                self.fail("server failed to start; stderr: "
                          + (proc.stderr.read() if proc.poll() is not None
                             else ""))
            m = re.search(r"Serving on http://127\.0\.0\.1:(\d+)", line)
            self.assertIsNotNone(m, f"unexpected first line: {line!r}")
            port = int(m.group(1))
            self.assertNotEqual(port, 0)
            status, headers, body = _http_get(
                f"http://127.0.0.1:{port}/books")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], CONTENT_TYPE)
            self.assertEqual(int(headers["Content-Length"]), len(body))
            self.assertEqual(json.loads(body.decode("utf-8")), BOOKS)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
