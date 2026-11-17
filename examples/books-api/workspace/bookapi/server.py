"""HTTP layer for the book API: handler, server class and entry point."""

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from bookapi.data import BookStore

NOT_FOUND = {"error": "not found"}
CONTENT_TYPE = "application/json; charset=utf-8"
_ID_RE = re.compile(r"[0-9]+")


class BookRequestHandler(BaseHTTPRequestHandler):
    """Routes GET requests to the data store and replies with JSON."""

    def log_message(self, format, *args):  # noqa: A002 - signature from base
        """Silence per-request logging."""
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        store = self.server.store
        parts = urlsplit(self.path)
        path = parts.path

        if path == "/books":
            qs = parse_qs(parts.query, keep_blank_values=True)
            if "author" in qs:
                # parse_qs has already decoded %XX escapes (UTF-8) and '+'.
                self._send_json(200, store.books_by_author(qs["author"][0]))
                return
            self._send_json(200, store.list_books())
            return

        if path.startswith("/books/"):
            seg = path[len("/books/"):]
            if "/" not in seg:
                if _ID_RE.fullmatch(seg):
                    book = store.get_book(int(seg))
                    if book is not None:
                        self._send_json(200, book)
                        return
                self._send_json(404, NOT_FOUND)
                return

        self._send_json(404, NOT_FOUND)


class BookServer(ThreadingHTTPServer):
    """Threading HTTP server that exposes the data store as ``.store``."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, store=None):
        super().__init__(server_address, BookRequestHandler)
        self.store = store if store is not None else BookStore()


def make_server(host="127.0.0.1", port=0, store=None):
    """Create a BookServer. Port 0 lets the OS pick a free port."""
    return BookServer((host, port), store=store)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Book JSON API (demo).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to listen on (default 8000)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    server = make_server(args.host, args.port)
    try:
        print(f"Serving on http://{args.host}:{server.server_port}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
