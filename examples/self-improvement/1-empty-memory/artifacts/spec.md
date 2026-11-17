# spec (architect)

## design


# Book API: technical spec

Uses only the Python 3.7+ standard library. Read-only HTTP JSON API over an in-memory mock book database.

## Layout
```
books/__init__.py      package marker (docstring only, no imports)
books/data.py          mock dataset + BookRepository (data-access layer)
books/server.py        HTTP handler, server factory, CLI main()
books/__main__.py      `python3 -m books` entry point -> books.server.main()
tests/__init__.py      empty (enables discovery from the repo root)
tests/test_data.py     pure data-layer tests
tests/test_server.py   integration tests (real server on port 0)
tests/test_project.py  stdlib-only import check + README content check
README.md
```
There are no requirements.txt, setup.py or pyproject files. Allowed imports: json, http.server, urllib.parse, urllib.request, urllib.error, argparse, threading, unittest, contextlib, io, os, sys, re, ast, pathlib, typing, plus the local packages `books` and `tests`.

## Data model (books/data.py)
Each book is a dict with exactly these keys: `{"id": int, "title": str, "author": str, "year": int}`.
`BOOKS` is a module-level list with exactly these 6 entries, in this order (the tests rely on them):
1. The Hobbit, J.R.R. Tolkien, 1937
2. The Fellowship of the Ring, J.R.R. Tolkien, 1954
3. Pride and Prejudice, Jane Austen, 1813
4. Emma, Jane Austen, 1815
5. Nineteen Eighty-Four, George Orwell, 1949
6. Dune, Frank Herbert, 1965

`class BookRepository`:
- `__init__(self, books=None)`: stores shallow copies of each dict from `books`, or from `BOOKS` if `books` is None.
- `all(self) -> list[dict]`: copies of every book, in dataset order.
- `get(self, book_id) -> dict | None`: returns a copy of the book with that id. Returns None if the id is missing or if `book_id` is not an int (bool also counts as not an int).
- `search_by_author(self, name: str) -> list[dict]`: copies of the books where `name.casefold() in book["author"].casefold()`, in dataset order. Returns [] if nothing matches. An empty string matches every book.

Module-level convenience functions `all()`, `get(book_id)` and `search_by_author(name)` delegate to the module-level `DEFAULT_REPOSITORY = BookRepository()`. They are defined with `def` at module level, so `all` intentionally shadows the builtin only inside this module.

## HTTP layer (books/server.py)
- `JSON_CONTENT_TYPE = "application/json"`
- `class BookRequestHandler(http.server.BaseHTTPRequestHandler)`:
  - `protocol_version` stays at the default "HTTP/1.0", so each request gets its own connection and unread request bodies cannot cause keep-alive problems.
  - `_send_json(self, status: int, payload, extra_headers=None)`: `body = json.dumps(payload).encode("utf-8")`. Then it calls send_response(status), sets `Content-Type: application/json` and `Content-Length: len(body)`, adds any extra_headers, calls end_headers(), and writes the body. It skips the body when `self.command == "HEAD"`. This is the only place that writes responses.
  - `send_error(self, code, message=None, explain=None)` is overridden to call `_send_json(code, {"error": message or <default reason phrase for code>})`. As a result, framework-level errors (malformed request line, 501, etc.) are also JSON, never HTML. Handler code itself never calls send_error and instead calls `_send_json` directly.
  - `log_message(self, format, *args)` is overridden as a no-op, so nothing is logged to stderr.
  - `do_GET` does the routing:
    1. `parts = urllib.parse.urlsplit(self.path)`. `path = parts.path`. If path is longer than "/" and ends in "/", one trailing "/" is stripped.
    2. `path == "/books"`: `qs = urllib.parse.parse_qs(parts.query, keep_blank_values=True)`. If "author" is in qs, return 200 with `repo.search_by_author(qs["author"][0])`. Otherwise return 200 with `repo.all()`. Other query parameters are ignored.
    3. If path matches `^/books/([^/]+)$`: `seg = urllib.parse.unquote(group1)`. If seg does not fully match `[0-9]+` (ASCII digits only), return 400 `{"error": "invalid book id"}`. Otherwise `book = repo.get(int(seg))`, and return 200 with the book, or 404 `{"error": "book not found"}` if it is None. The query string is ignored on this route.
    4. Anything else returns 404 `{"error": "not found"}`.
    - `repo = self.server.repository`. The data is injected through the server attribute.
  - `do_POST`, `do_PUT`, `do_DELETE`, `do_PATCH`, `do_HEAD` and `do_OPTIONS` all return 405 `{"error": "method not allowed"}` with the extra header `Allow: GET`.
  - Error messages are fixed strings. User input is never echoed into headers or error bodies.
- `create_server(host="127.0.0.1", port=8000, repository=None) -> http.server.ThreadingHTTPServer`: builds the server with BookRequestHandler, sets `server.repository = repository or BookRepository()` and `server.daemon_threads = True`, and returns it without starting it. The actual port is `server.server_address[1]`, which matters when port 0 is used.
- `parse_args(argv=None) -> argparse.Namespace`: `--host` (default "127.0.0.1") and `--port` (int, default 8000).
- `main(argv=None) -> None`: parses the arguments, calls create_server, prints `Serving on http://{host}:{port}` to stdout, and runs serve_forever. On KeyboardInterrupt it exits; in all cases it calls server_close in a `finally` block.
- `books/__main__.py`: `from books.server import main` followed by `if __name__ == "__main__": main()`.

## Responses summary
| Request | Status | Body |
|---|---|---|
| GET /books | 200 | list of all books |
| GET /books?author=x | 200 | list of matches (case-insensitive substring), possibly [] |
| GET /books/<digits> existing | 200 | book object |
| GET /books/<digits> missing | 404 | {"error": "book not found"} |
| GET /books/<non-digits> | 400 | {"error": "invalid book id"} |
| GET other path | 404 | {"error": "not found"} |
| POST/PUT/DELETE/PATCH/HEAD/OPTIONS on any path | 405 | {"error": "method not allowed"} (no body for HEAD) |

Every response has `Content-Type: application/json` and an exact Content-Length.

## Testing approach
- `tests/test_data.py`: tests BookRepository and the module-level functions directly.
- `tests/test_server.py`: `setUpClass` calls `create_server(port=0)`, starts `serve_forever` in a daemon `threading.Thread`, and builds base_url from server_address. `tearDownClass` calls `server.shutdown()`, `server.server_close()` and `thread.join(timeout=5)`. The helper `request(method, path)` uses urllib.request, catches `urllib.error.HTTPError` and returns `(status, headers, raw_body_bytes)`. The suite has a separate test class with its own server that uses an injected custom BookRepository, to prove data comes from `self.server.repository`.
- `tests/test_project.py`: AST-scans every .py file under books/ and tests/ and checks that all imports are in the allow-list. Also checks that the README contents are present.
- Command: `python3 -m unittest -v` from the repo root.


## work_items

- **id**: W1; **title**: Mock dataset and data-access layer with unit tests; **description**: Create the package marker books/__init__.py (docstring only) and an empty tests/__init__.py. Implement books/data.py exactly as in the design: the BOOKS list with the 6 specified books (ids 1-6), BookRepository with all()/get()/search_by_author() returning shallow copies, the module-level DEFAULT_REPOSITORY, and the module-level all/get/search_by_author functions. Only stdlib imports are allowed (data.py should need none). tests/test_data.py (unittest) must check: BOOKS has 5-10 entries; each has exactly the keys id, title, author, year with types int, str, str, int (and id is not a bool); ids are unique. all() equals BOOKS in order. get(1) returns The Hobbit; get(99999) is None; get('1') is None; get(True) is None. search_by_author('J.R.R. Tolkien'), ('TOLK') and ('tolk') all return books [1, 2]; search_by_author('austen') returns [3, 4]; search_by_author('nobody') returns []; search_by_author('') returns all books. Mutating a returned dict does not change later results. A custom BookRepository([...]) uses only the injected books. The module-level functions match DEFAULT_REPOSITORY.; **files**: ['books/__init__.py', 'books/data.py', 'tests/__init__.py', 'tests/test_data.py']; **covers**: ['R2', 'R3']; **depends_on**: []
- **id**: W2; **title**: HTTP handler, server factory, CLI entry point and integration tests; **description**: Implement books/server.py exactly as in the design: BookRequestHandler with _send_json as the single response writer (json body encoded first, Content-Type application/json, exact Content-Length, no body for HEAD); a send_error override that emits JSON {"error": ...}; a no-op log_message; do_GET routing via urllib.parse.urlsplit/parse_qs/unquote (/books, /books?author=, /books/<[0-9]+>, 400 for non-digit ids, 404 for missing books and unknown paths, trailing slash tolerated); 405 JSON with Allow: GET for POST/PUT/DELETE/PATCH/HEAD/OPTIONS; data read only from self.server.repository; fixed error strings with no user input echoed. Also implement create_server(host='127.0.0.1', port=8000, repository=None) returning a ThreadingHTTPServer with .repository and daemon_threads=True, parse_args(argv), and main(argv) (prints 'Serving on http://host:port', serve_forever, server_close in finally, exits cleanly on KeyboardInterrupt). Create books/__main__.py, which calls main(). tests/test_server.py (unittest + urllib.request, server on port 0 in a background thread, shutdown/server_close/join in tearDownClass) must test: GET /books returns 200 with the full BOOKS list; /books/ with a trailing slash behaves the same; /books/1 returns 200 with book 1; /books/1?x=y is the same; /books/99999 returns 404 with an 'error' key; /books/abc and /books/-1 return 400 with an 'error' key; author=J.R.R.%20Tolkien, author=TOLKIEN and author=olk each return books [1, 2]; author=zzz returns 200 with []; GET /nope and /books/1/extra return 404 JSON; POST (with a small body), PUT and DELETE to /books each return 405 JSON with Allow: GET; for success, 400, 404 and 405 responses, Content-Type == 'application/json', int(Content-Length) == len(body), and the body does not start with '<'; GET /books?author=%0D%0AX-Injected:%20yes returns 200 [] and has no X-Injected header; requests made under contextlib.redirect_stderr(io.StringIO()) leave the captured stderr empty; a second test class whose server uses an injected BookRepository with one custom book gets exactly that book from /books; create_server(port=0).server_address[0] == '127.0.0.1'; parse_args(['--port','9001']).port == 9001 with default host 127.0.0.1; and every test run leaves no non-daemon threads running.; **files**: ['books/server.py', 'books/__main__.py', 'tests/test_server.py']; **covers**: ['R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11', 'R12']; **depends_on**: ['W1']
- **id**: W3; **title**: README and project-level checks (stdlib-only, docs); **description**: Write README.md with: a project summary; requirements (Python 3.7+, stdlib only, no dependencies); the run command `python3 -m books --port 8000` (with --host, default 127.0.0.1); the test command `python -m unittest` (and `python3 -m unittest -v`); the three endpoints GET /books, GET /books/<id> and GET /books?author=<name> with example curl commands and example responses; the book schema (id int, title str, author str, year int); search semantics (case-insensitive substring, no matches gives 200 []); the error format {"error": ...} with the 400/404/405 cases; and a note that http.server is not production-grade and is only meant for this mock API. Write tests/test_project.py (unittest): (1) uses ast to parse every .py file under books/ and tests/ and asserts that every Import/ImportFrom top-level module (ignoring relative imports) is in the allow-list {json, http, urllib, argparse, threading, unittest, contextlib, io, os, sys, re, ast, pathlib, typing, books, tests}; (2) asserts that requirements.txt, setup.py, Pipfile and pyproject.toml do not exist at the repo root; (3) asserts that README.md exists and contains 'python -m unittest', 'python3 -m books', '/books/<id>', '/books?author=' and the phrase 'not production-grade'. Resolve paths relative to the repo root via pathlib.Path(__file__).resolve().parent.parent.; **files**: ['README.md', 'tests/test_project.py']; **covers**: ['R1', 'R13']; **depends_on**: ['W1', 'W2']

## test_command

python3 -m unittest -v

## run_command

python3 -m books --port 8000
