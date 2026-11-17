# impl:W2 (engineer)

## summary

I built the HTTP server (`books/server.py`), the `python3 -m books` entry point (`books/__main__.py`) and the integration tests (`tests/test_server.py`), following the design. The full suite passes: 39 tests, including the 13 data-layer tests from W1.

**`books/server.py`**
- **One response writer:** every response goes through `_send_json`. It encodes the JSON body first, then sets `Content-Type: application/json` and an exact `Content-Length`, and sends no body for HEAD requests.
- **Errors:** `send_error` is overridden, so errors raised by `http.server` itself (such as 501 for an unknown method) also come back as `{"error": ...}` instead of HTML. Our own code never calls `send_error`.
- **No logging:** `log_message` does nothing, so nothing is written to stderr.
- **Routing in `do_GET`:** paths and queries are parsed with `urllib.parse`, and one trailing slash is ignored.
  - `/books` returns all books, or matching books when `?author=` is given.
  - `/books/<digits>` returns the book, or 404 if it doesn't exist.
  - An id that isn't plain digits (e.g. `abc`, `-1`) gets 400.
  - Any other path gets 404.
  - Data is read only from `self.server.repository`.
- **Other methods:** POST, PUT, DELETE, PATCH, HEAD and OPTIONS all get 405 with `Allow: GET`. Error messages are fixed strings and never include anything from the request.
- **Startup:** `create_server()` defaults to 127.0.0.1, attaches the repository and sets `daemon_threads=True`. `parse_args()` handles `--host`/`--port`. `main()` prints `Serving on http://host:port`, runs the server, stops quietly on Ctrl+C and always closes the server.

**Choices beyond the spec**
- **Very long ids:** an id made of more digits than Python will convert to an integer now gets 404 instead of causing a server-side traceback.
- **Request bodies on 405:** for 405 responses, the server reads any request body up to 64 KB before replying. Without this, the client can see a connection reset.
- **Repository check:** `create_server` uses `repository if repository is not None` instead of `repository or ...`, so an injected repository is never replaced with the default dataset.

**`tests/test_server.py`** covers every case listed in the work item. It also tests:
- PATCH, OPTIONS and HEAD returning 405 (HEAD with an empty body);
- an unknown method (`BREW`) getting a JSON 501;
- the very-long-id 404 and `/books/1.5` getting 400;
- `main()` printing its address and closing the server on Ctrl+C;
- no non-daemon threads left running, checked both as a test and in `tearDownModule`.

## tests

Command: `python3 -m unittest -v` from the repo root (Python 3.10.12). Result: `Ran 39 tests ... OK`, exit code 0.

That is 13 tests from `tests/test_data.py` and 26 from `tests/test_server.py`. They covered listing books, the trailing slash, getting by id with and without a query string, 404/400/405 responses, author search, the Content-Type and Content-Length checks, the CR/LF header-injection request, empty stderr, the injected repository, the server factory, `parse_args`, `main()` and leftover threads.

I also ran a manual check of the entry point: `python3 -m books --port 8765` printed `Serving on http://127.0.0.1:8765`, GET `/books/6` returned the Dune book as JSON, and nothing was written to stderr.
