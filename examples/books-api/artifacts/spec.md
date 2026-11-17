# spec (architect)

## design

# Book API: technical spec (revised: search is by author name)

This revision follows the updated plan. Search now matches on the author **name** through `GET /books?author=<name>`. The match is a case-insensitive substring. The `author_id` field and the `?author_id=` filter have been removed.

The code uses the Python 3.7+ standard library only. There is no requirements file and there are no third-party packages. Layout from the workspace root:

```
bookapi/__init__.py      package marker; re-exports BookStore, BOOKS
bookapi/data.py          mock dataset + data layer (no HTTP imports)
bookapi/server.py        HTTP handler, server class, make_server, parse_args, main
bookapi/__main__.py      `python3 -m bookapi` entry point
tests/__init__.py        empty (makes tests discoverable by `python3 -m unittest`)
tests/test_data.py       data-layer unit tests
tests/test_server.py     HTTP-level tests (port-0 server in a background thread + module run test)
tests/test_stdlib.py     static import check (stdlib only)
README.md
```

## 1. Data layer: bookapi/data.py
Imports: `copy` only.

`BOOKS: list[dict]` is the module-level mock dataset. Each record has exactly these keys: `id` (int), `title` (str), `author` (str, the author's name) and `year` (int). Use these exact records:
1. {id:1, title:"Pride and Prejudice", author:"Jane Austen", year:1813}
2. {id:2, title:"Emma", author:"Jane Austen", year:1815}
3. {id:3, title:"Nineteen Eighty-Four", author:"George Orwell", year:1949}
4. {id:4, title:"Animal Farm", author:"George Orwell", year:1945}
5. {id:5, title:"Cien años de soledad", author:"Gabriel García Márquez", year:1967}

Ids are unique. Two authors each have two books, and every author name contains a space. The non-ASCII record checks that the body is UTF-8 and that Content-Length counts bytes.

`class BookStore`:
- `__init__(self, books=None)`: stores `copy.deepcopy(books if books is not None else BOOKS)` and keeps dataset order.
- `list_books(self) -> list[dict]`: deep copies of all books, in dataset order.
- `get_book(self, book_id: int) -> dict | None`: a deep copy of the book whose `id == book_id`, or None.
- `books_by_author(self, name: str) -> list[dict]`: deep copies of the books where `name.casefold() in book["author"].casefold()`, in dataset order.
  - This is the documented search rule: a case-insensitive substring match.
  - There is no trimming, regex, fuzzy matching or accent folding.
  - An empty `name` matches every book. No match returns `[]`.

Callers can never change the store's data through a returned object. Nothing in this layer knows about HTTP.

## 2. HTTP layer: bookapi/server.py
Imports: `argparse`, `json`, `re`, `http.server` (ThreadingHTTPServer, BaseHTTPRequestHandler), `urllib.parse` (urlsplit, parse_qs), and `from bookapi.data import BookStore`.

Constants:
- `NOT_FOUND = {"error": "not found"}`
- `CONTENT_TYPE = "application/json; charset=utf-8"`
- `_ID_RE = re.compile(r"[0-9]+")`, used with fullmatch

`class BookRequestHandler(BaseHTTPRequestHandler)`:
- `log_message(self, format, *args)` is overridden to do nothing, so test output stays clean.
- `_send_json(self, status: int, payload)`:
  1. `body = json.dumps(payload, ensure_ascii=False).encode("utf-8")`
  2. `self.send_response(status)`
  3. `self.send_header("Content-Type", CONTENT_TYPE)`
  4. `self.send_header("Content-Length", str(len(body)))`
  5. `self.end_headers()`
  6. `self.wfile.write(body)`

  Never use `send_error`, because it returns HTML. Request data, including author values, is never put into a header. It appears only in the JSON body.
- `do_GET(self)`: `store = self.server.store`, `parts = urlsplit(self.path)`, `path = parts.path`. Routing:
  - **`path == "/books"`**: parse `qs = parse_qs(parts.query, keep_blank_values=True)`. parse_qs decodes `%20` and `+` to spaces and decodes UTF-8 percent-escapes.
    - If `"author"` is in qs, respond 200 with `store.books_by_author(qs["author"][0])`. `?author=` gives `""`, which returns all books. A name that matches nothing returns 200 with `[]`.
    - Otherwise respond 200 with `store.list_books()`. Other query parameters are ignored.
  - **`path` starts with `"/books/"`** and the remainder `seg` contains no "/":
    - If `_ID_RE.fullmatch(seg)` and `store.get_book(int(seg))` is not None, respond 200 with the book.
    - Otherwise respond 404 with NOT_FOUND. This covers non-integer ids, the empty segment in `/books/`, and unknown ids.
  - **Anything else** (`/`, `/nope`, `/books/1/x`): respond 404 with NOT_FOUND.

  Only GET is implemented. Other methods get the default http.server behavior.

`class BookServer(ThreadingHTTPServer)`:
- `daemon_threads = True`, `allow_reuse_address = True`
- `__init__(self, server_address, store=None)`: calls `super().__init__(server_address, BookRequestHandler)`, then sets `self.store = store if store is not None else BookStore()`. The handler reaches the data store through `self.server.store`.

Functions:
- `make_server(host="127.0.0.1", port=0, store=None) -> BookServer`. Port 0 lets the OS pick a free port, which callers read from `server.server_port`.
- `parse_args(argv=None) -> argparse.Namespace`: accepts `--host` (default "127.0.0.1") and `--port` (int, default 8000).
- `main(argv=None) -> int`:
  1. Parse the args and call `make_server`.
  2. Run `print(f"Serving on http://{host}:{server.server_port}", flush=True)`. The flush is required by the subprocess test.
  3. Call `serve_forever()`. On KeyboardInterrupt, do nothing.
  4. In `finally`, call `server_close()`.
  5. Return 0.

## 3. bookapi/__main__.py
```
from bookapi.server import main
raise SystemExit(main())
```
Run with `python3 -m bookapi --port 8000`.

## 4. Tests (unittest; run with `python3 -m unittest -v` from the root)

### tests/test_data.py
- Every record in BOOKS has exactly the keys {id, title, author, year}, with types int/str/str/int. Use `type(x) is int` so bools are rejected.
- `len(BOOKS) >= 3`.
- At least one author name is shared by at least 2 books, and at least one author name contains a space.
- Ids are unique.
- `list_books() == BOOKS`.
- `get_book(1)` returns book 1, and `get_book(99999)` returns None.
- `books_by_author` cases, compared by ids:

  | Query | Expected ids |
  |---|---|
  | "Jane Austen" (hit) | [1, 2] |
  | "jane austen" and "JANE AUSTEN" (case) | [1, 2] |
  | "orwell" (partial) | [3, 4] |
  | "García" and "garcía" (non-ASCII) | [5] |
  | "Tolkien" (miss) | [] |
  | "" (empty) | all ids |

- Changing a returned dict or list does not change the store.
- A custom dataset passed to the constructor is used.

### tests/test_server.py
Fixture `class ServerTests`:
- `setUp`: `self.server = make_server(port=0)`. Start `self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)`. Set `self.base = f"http://127.0.0.1:{self.server.server_port}"`.
- `tearDown`: `self.server.shutdown()`, `self.server.server_close()`, `self.thread.join(timeout=5)`.

Helper `get(path) -> (status, headers, body_bytes)`:
- Uses `urllib.request.urlopen(self.base + path, timeout=5)`.
- Catches `urllib.error.HTTPError` as `e`, reads `e.code`, `e.headers` and `e.read()`, then closes `e`.

Every case below asserts Content-Type == `application/json; charset=utf-8` and `int(Content-Length) == len(body)`.

Cases:
- `/books`: 200, and the JSON equals `BOOKS`.
- `/books/1`: 200, and the body equals book 1.
- `/books/5`: 200, and the non-ASCII title and author round-trip.
- 404s: `/books/99999`, `/books/abc`, `/books/` and `/nope` each return 404 with parsed body `{"error": "not found"}`.
- Author search (build query strings with `urllib.parse.quote`):

  | Request | Expected |
  |---|---|
  | `/books?author=Jane%20Austen` (full name, encoded space) | 200, ids [1, 2] |
  | `/books?author=JANE+AUSTEN` (different case, `+` as space) | 200, ids [1, 2] |
  | `/books?author=orw` (partial) | 200, ids [3, 4] |
  | `/books?author=` + `quote("Gabriel García")` (spaces and UTF-8) | 200, ids [5] |
  | `/books?author=Tolkien` (miss) | 200, `[]` |
  | `/books?author=` (empty) | 200, equals BOOKS |

- `parse_args(["--port","0"]).port == 0`, the default port is 8000, and `--host` is accepted.
- `self.server.store` is a BookStore.

`class MainModuleTest` (R10, the module entry point). This test must not hang:
1. Start `subprocess.Popen([sys.executable, "-m", "bookapi", "--port", "0"], cwd=<repo root via pathlib>, stdout=PIPE, stderr=PIPE, text=True)`.
2. Read one stdout line and parse the port from `Serving on http://127.0.0.1:PORT`. An empty line means the server failed to start, and the test fails.
3. GET `/books` returns 200 and equals BOOKS.
4. In `finally`: `proc.terminate()` and `proc.wait(timeout=5)`. If the wait times out, call `proc.kill()`. Close the pipes.

### tests/test_stdlib.py
Uses `ast` to parse every `.py` file under `bookapi/` and `tests/`. It collects the top-level module name of each Import/ImportFrom (skipping relative imports) and asserts each one is in this allowlist:

{argparse, ast, copy, http, json, os, pathlib, re, subprocess, sys, threading, unittest, urllib, bookapi, tests}

It also asserts that there is no requirements.txt in the root.

## 5. README.md
Must cover:
- Run command: `python3 -m bookapi --port 8000`
- Test command: `python3 -m unittest -v`
- An endpoints table: `GET /books`, `GET /books/{id}`, `GET /books?author=<name>`
- The book fields with types: id int, title str, author str, year int
- The error body `{"error": "not found"}`, used for unknown or non-integer ids and unknown routes
- The author search rule: a case-insensitive substring match on the author name. Values are URL-decoded. An empty `author=` matches all books, and no match returns `[]`. There is no fuzzy or accent-insensitive matching.
- A note that the server is a demo built on http.server and is not production-grade


## work_items

- **id**: W1; **title**: Mock dataset and data layer with unit tests; **description**: Build bookapi/data.py exactly as in design section 1.

- BOOKS holds the 5 given records. Each has keys id, title, author and year. There is no author_id.
- BookStore has list_books, get_book and books_by_author(name). books_by_author is a case-insensitive substring match on the author name using casefold. An empty name matches all books. All methods return deep copies.
- bookapi/__init__.py re-exports BookStore and BOOKS. It must not import server.
- tests/__init__.py is empty.
- Write tests/test_data.py with the cases in design section 4: schema and types, at least 3 books, a shared author, an author name with a space, unique ids, list and get (hit and miss), and author hit, case, partial, non-ASCII, miss and empty. Also test that returned objects are isolated from the store and that a custom dataset is used.; **files**: ['bookapi/__init__.py', 'bookapi/data.py', 'tests/__init__.py', 'tests/test_data.py']; **covers**: ['R2', 'R9']; **depends_on**: []
- **id**: W2; **title**: HTTP handler, server class and entry point; **description**: Build bookapi/server.py as in design section 2. It contains:
- NOT_FOUND, CONTENT_TYPE and _ID_RE.
- BookRequestHandler:
  - log_message does nothing.
  - _send_json writes UTF-8 bytes with an accurate Content-Length. It never calls send_error and never puts request data in headers.
  - do_GET routes /books, /books?author=<name> and /books/{id}, and returns the JSON 404 for anything else. It parses the query with parse_qs(keep_blank_values=True) and passes the decoded author value to store.books_by_author.
- BookServer(ThreadingHTTPServer), which holds .store.
- make_server and parse_args (--host, --port).
- main, which prints 'Serving on http://host:port' with flush=True.

Add bookapi/__main__.py so that `python3 -m bookapi --port N` serves. Use only the stdlib imports listed in the design.; **files**: ['bookapi/server.py', 'bookapi/__main__.py']; **covers**: ['R1', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R10']; **depends_on**: ['W1']
- **id**: W3; **title**: HTTP-level tests; **description**: Write tests/test_server.py as in design section 4.

ServerTests fixture and helper:
- setUp starts make_server(port=0) with serve_forever in a daemon thread.
- tearDown calls shutdown(), server_close() and join(timeout=5).
- A get() helper handles HTTPError so the 404 status, headers and body can be read.

ServerTests cases:
- /books returns the full list.
- /books/1 returns book 1.
- /books/5 round-trips UTF-8.
- /books/99999, /books/abc, /books/ and /nope each return 404 with body {"error": "not found"}.
- Author search, compared by ids:
  - Jane%20Austen returns [1,2].
  - JANE+AUSTEN returns [1,2].
  - orw returns [3,4].
  - quote('Gabriel García') returns [5].
  - Tolkien returns 200 and [].
  - author= returns all books.
- Content-Type and Content-Length == len(body) are checked on every response.
- parse_args handles port and host.
- server.store is a BookStore.

MainModuleTest runs `sys.executable -m bookapi --port 0` through subprocess from the repo root. It reads the port from the first stdout line, GETs /books and checks the body equals BOOKS, then calls terminate and wait(timeout=5), with kill as a fallback.

All tests must pass under `python3 -m unittest -v` without hanging.; **files**: ['tests/test_server.py']; **covers**: ['R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R10', 'R11']; **depends_on**: ['W2']
- **id**: W4; **title**: README and stdlib-only import check; **description**: Write README.md as in design section 5. It must include:
- the run command (python3 -m bookapi --port 8000)
- the test command (python3 -m unittest -v)
- the endpoints, including GET /books?author=<name>
- the book fields and types (id, title, author, year)
- the error body {"error": "not found"}
- the author search rule: a case-insensitive substring match on the author name; values are URL-decoded; an empty value matches all books; no match returns []
- a note that the server is a demo and not production-grade

Write tests/test_stdlib.py. It uses ast to check that every import in bookapi/ and tests/ is in the allowlist from the design (which includes subprocess), and that there is no requirements.txt.; **files**: ['README.md', 'tests/test_stdlib.py']; **covers**: ['R1', 'R12', 'R11']; **depends_on**: ['W2']

## test_command

python3 -m unittest -v

## run_command

python3 -m bookapi --port 8000
