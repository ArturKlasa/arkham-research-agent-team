"""HTTP layer: request handler, server factory and CLI entry point."""

import argparse
import http.server
import json
import re
import urllib.parse

from books.data import BookRepository

JSON_CONTENT_TYPE = "application/json"

_BOOK_PATH_RE = re.compile(r"^/books/([^/]+)$")
_DIGITS_RE = re.compile(r"[0-9]+", re.ASCII)
_MAX_DRAIN_BYTES = 64 * 1024


class BookRequestHandler(http.server.BaseHTTPRequestHandler):
    """Read-only JSON API over ``self.server.repository``."""

    # Keep the default HTTP/1.0: one request per connection.
    protocol_version = "HTTP/1.0"

    # -- response writing -------------------------------------------------
    def _send_json(self, status, payload, extra_headers=None):
        """The single place that writes responses."""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        """Framework-level errors are emitted as JSON, never HTML."""
        if not message:
            try:
                message = http.HTTPStatus(code).phrase
            except ValueError:
                message = "error"
        self._send_json(code, {"error": message})

    def log_message(self, format, *args):  # noqa: A002 - signature from base
        """Suppress request logging."""

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        repo = self.server.repository
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        if path == "/books":
            qs = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
            if "author" in qs:
                self._send_json(200, repo.search_by_author(qs["author"][0]))
            else:
                self._send_json(200, repo.all())
            return

        match = _BOOK_PATH_RE.match(path)
        if match:
            seg = urllib.parse.unquote(match.group(1))
            if not _DIGITS_RE.fullmatch(seg):
                self._send_json(400, {"error": "invalid book id"})
                return
            try:
                book_id = int(seg)
            except ValueError:
                # Absurdly long digit strings exceed int conversion limits;
                # no such book can exist.
                book = None
            else:
                book = repo.get(book_id)
            if book is None:
                self._send_json(404, {"error": "book not found"})
            else:
                self._send_json(200, book)
            return

        self._send_json(404, {"error": "not found"})

    def _method_not_allowed(self):
        self._drain_body()
        self._send_json(405, {"error": "method not allowed"},
                        extra_headers={"Allow": "GET"})

    def _drain_body(self):
        """Read a small request body so closing the socket does not reset it."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < length <= _MAX_DRAIN_BYTES:
            self.rfile.read(length)

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed


def create_server(host="127.0.0.1", port=8000, repository=None):
    """Build (but do not start) a ThreadingHTTPServer serving the book API."""
    server = http.server.ThreadingHTTPServer((host, port), BookRequestHandler)
    server.repository = repository if repository is not None else BookRepository()
    server.daemon_threads = True
    return server


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Serve the mock book JSON API.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to listen on (default: 8000)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = create_server(args.host, args.port)
    host, port = server.server_address[:2]
    print("Serving on http://{}:{}".format(host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
