"""HTTP JSON API over the mock book database (stdlib only)."""

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from bookapi.data import get_book, list_books, search_by_author

_BOOK_PATH_RE = re.compile(r"^/books/([^/]+)$")
_DIGITS_RE = re.compile(r"^[0-9]+$")

MSG_NOT_FOUND = "Not found"
MSG_BOOK_NOT_FOUND = "Book not found"
MSG_INVALID_ID = "Invalid book id; must be a non-negative integer"
MSG_METHOD_NOT_ALLOWED = "Method not allowed"


class BookRequestHandler(BaseHTTPRequestHandler):
    """Request handler serving /books, /books?author=X and /books/<id>."""

    server_version = "BookAPI/1.0"

    def _send_json(self, status, payload, extra_headers=None, include_body=True):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _send_error_json(self, status, message, **kw):
        self._send_json(status, {"error": message}, **kw)

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path

        if path == "/books":
            qs = parse_qs(parts.query, keep_blank_values=True)
            if "author" in qs:
                self._send_json(200, search_by_author(qs["author"][0]))
            else:
                self._send_json(200, list_books())
            return

        match = _BOOK_PATH_RE.match(path)
        if match:
            segment = match.group(1)
            if not _DIGITS_RE.match(segment):
                self._send_error_json(400, MSG_INVALID_ID)
                return
            book = get_book(int(segment))
            if book is None:
                self._send_error_json(404, MSG_BOOK_NOT_FOUND)
            else:
                self._send_json(200, book)
            return

        self._send_error_json(404, MSG_NOT_FOUND)

    def _method_not_allowed(self, include_body=True):
        self._send_error_json(
            405,
            MSG_METHOD_NOT_ALLOWED,
            extra_headers={"Allow": "GET"},
            include_body=include_body,
        )

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def do_HEAD(self):
        self._method_not_allowed(include_body=False)

    def log_message(self, format, *args):  # noqa: A002 - signature from base class
        """Suppress per-request logging."""
        return


def make_server(host="127.0.0.1", port=8000):
    """Create a threading HTTP server bound to (host, port)."""
    server = ThreadingHTTPServer((host, port), BookRequestHandler)
    server.daemon_threads = True
    return server


def main(argv=None):
    """Command-line entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(description="Serve the mock book JSON API.")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="bind port (default 8000)")
    args = parser.parse_args(argv)

    server = make_server(args.host, args.port)
    try:
        host, port = server.server_address[:2]
        print("Serving on http://{}:{}".format(host, port), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
