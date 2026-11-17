# spec (architect)

## design

Stdlib-only book API (Python 3.7+). No dependency file is needed or allowed.

IMPORT ALLOWLIST (one shared contract for all work items)
The only top-level modules any .py file in bookapi/ or tests/ may import are listed in this set. It is the single source of truth.
ALLOWED_TOP_LEVEL = {"__future__", "argparse", "ast", "contextlib", "copy", "http", "io", "json", "os", "re", "sys", "threading", "typing", "unittest", "urllib", "bookapi", "tests"}
- The set covers these submodules: http.server, urllib.parse, urllib.request, urllib.error and unittest.mock. Only the top-level name before the first dot is checked.
- W1 defines this exact set as the constant ALLOWED_TOP_LEVEL in tests/test_data.py. The R1 test compares against this set only. It does not use sys.stdlib_module_names and has no other fallback.
- W2 (server.py, __main__.py, test_server.py) must not import any module outside this set. For example, use os rather than pathlib, and io.StringIO with contextlib.redirect_stderr/redirect_stdout.
- Relative imports (ImportFrom with level > 0) count as local and are allowed.
- Type hints: Python 3.7 cannot evaluate `dict | None`. Use typing.Optional/List/Dict, or put `from __future__ import annotations` at the top of the file.

LAYOUT
bookapi/__init__.py   package marker. Has a docstring and __all__ only.
bookapi/data.py       mock data plus pure functions (no I/O)
bookapi/server.py     HTTP handler, routing, JSON writer, make_server(), main()
bookapi/__main__.py   `from bookapi.server import main; raise SystemExit(main())`
tests/__init__.py     empty. Needed so `python3 -m unittest` discovery finds tests/.
tests/test_data.py    data-layer unit tests plus the R1 import check
tests/test_server.py  integration tests against a live server on port 0
README.md

DATA MODEL (bookapi/data.py)
BOOKS is a module-level tuple of 10 dicts. Each dict has exactly these keys: {"id": int, "title": str, "author": str, "year": int}.
- Ids are 1..10 and unique.
- At least 2 books share an author: "Jane Austen" x2 and "George Orwell" x2.
- One author has a non-ASCII name: "Gabriel García Márquez".

Functions:
- list_books() -> List[dict]: copies (dict(b)) of all books in id order.
- get_book(book_id: int) -> Optional[dict]: a copy of the matching book, or None.
- search_by_author(name: str) -> List[dict]: copies of the books where name.casefold() in book["author"].casefold(), in id order. An empty name matches all books. The input is not stripped.

HTTP CONTRACT (bookapi/server.py)
The server keeps the default protocol_version, HTTP/1.0, so the connection closes after each response. The path is split with urllib.parse.urlsplit(self.path). Routing is exact, with no trailing-slash tolerance.

GET "/books":
- If the query contains an author param, parse it with qs = parse_qs(query, keep_blank_values=True) and take qs["author"][0] (UTF-8 percent and + decoding). Return 200 with search_by_author(value).
- Otherwise return 200 with list_books(). Other params are ignored.

GET paths that match ^/books/([^/]+)$:
- If the segment matches ^[0-9]+$, convert it with int() and look it up. Return 200 with the book object, or 404 {"error": "Book not found"}.
- Otherwise return 400 {"error": "Invalid book id; must be a non-negative integer"}.
- The query string is ignored.

Any other GET path returns 404 {"error": "Not found"}. This includes "/books/" and "/books/1/".

POST, PUT, DELETE, PATCH, HEAD and OPTIONS on any path return 405 {"error": "Method not allowed"} plus the header "Allow: GET". A HEAD response sends the headers (Content-Length of the would-be body included) but no body.

A success body is the bare JSON array or object. An error body is {"error": "<fixed constant message>"}. User input never reaches headers.

class BookRequestHandler(BaseHTTPRequestHandler):
- server_version = "BookAPI/1.0"
- _send_json(self, status, payload, extra_headers=None, include_body=True). This is the only response writer; send_error is never used.
  1. body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
  2. send_response(status)
  3. Content-Type: application/json; charset=utf-8
  4. Content-Length: str(len(body))
  5. the extra headers
  6. end_headers()
  7. if include_body, wfile.write(body)
- _send_error_json(status, message, **kw) calls _send_json(status, {"error": message}, **kw).
- do_GET implements the routing above.
- _method_not_allowed(include_body=True) sends the 405. do_POST, do_PUT, do_DELETE, do_PATCH and do_OPTIONS call it. do_HEAD calls it with include_body=False.
- log_message(self, format, *args) is a no-op.

Module functions:
- make_server(host="127.0.0.1", port=8000) -> http.server.ThreadingHTTPServer. It binds BookRequestHandler and sets daemon_threads = True.
- main(argv=None) -> int:
  - argparse options: --host (default 127.0.0.1) and --port (int, default 8000).
  - It calls make_server(args.host, args.port) through the module-global name, so tests can monkeypatch it.
  - It prints "Serving on http://HOST:PORT" using server.server_address.
  - It calls serve_forever(). A KeyboardInterrupt is caught quietly. server_close() runs in a finally block. Returns 0.
- The module ends with an `if __name__ == "__main__": sys.exit(main())` guard.

TESTS
tests/test_data.py:
- BOOKS has 8 to 10 records. Each has exactly the 4 keys with the right types; id and year are int and not bool. Ids are unique.
- list_books() equals list(BOOKS), and mutating its result does not change BOOKS.
- get_book returns the book for a hit and None for a miss.
- search_by_author:
  - exact author name
  - partial, differently cased name gives the same result
  - a miss returns []
  - "garcía" matches
- R1 import check:
  - Walk bookapi/ and tests/ with os.walk. The roots are found from os.path.dirname(os.path.abspath(__file__)) and its parent.
  - Parse each .py file with ast.parse (encoding utf-8).
  - For ast.Import, take alias.name.split(".")[0]. For ast.ImportFrom with level == 0, take module.split(".")[0].
  - Assert that every name found is in ALLOWED_TOP_LEVEL.

tests/test_server.py (imports limited to ALLOWED_TOP_LEVEL):
- setUpClass:
  - server = make_server("127.0.0.1", 0)
  - port = server.server_address[1]
  - start a daemon threading.Thread(target=server.serve_forever)
- tearDownClass: server.shutdown(), server.server_close(), thread.join(5).
- request(method, path) -> (status, headers, body_bytes). It uses urllib.request.Request plus urlopen(timeout=5) and catches urllib.error.HTTPError to get the code, headers and body.
- assert_json_headers(headers, body) checks Content-Type == "application/json; charset=utf-8" and int(Content-Length) == len(body). It runs on every response.
- Cases:
  - /books equals data.list_books()
  - /books/1 equals get_book(1)
  - /books/99999 returns 404 with a str error
  - /books/abc returns 400
  - ?author=orwell and ?author=ORWELL give the same 2 books
  - ?author=zzz returns 200 and []
  - ?author=jane%20austen and ?author=jane+austen both match 2 books
  - ?author=Garc%C3%ADa matches
  - /nope and /books/ return 404
  - POST, PUT and DELETE on /books return 405 with Allow: GET
- Silent logging: wrap one request in contextlib.redirect_stderr(io.StringIO()) and assert the buffer stays empty.
- main() arguments:
  - Use unittest.mock.patch("bookapi.server.make_server") with a stub that records its args. The stub returns a fake server with server_address set, serve_forever raising KeyboardInterrupt, and server_close as a no-op.
  - main([]) calls the stub with ("127.0.0.1", 8000).
  - main(["--host", "0.0.0.0", "--port", "9001"]) calls it with ("0.0.0.0", 9001).
  - Both return 0. Stdout is captured with contextlib.redirect_stdout(io.StringIO()).

## work_items

- **id**: W1; **title**: Data layer, data unit tests and import-allowlist check; **description**: Create bookapi/__init__.py with a docstring and __all__. Create bookapi/data.py with BOOKS (10 records, including Jane Austen x2, George Orwell x2 and Gabriel García Márquez) and list_books, get_book and search_by_author exactly as in the design. Use typing hints or __future__ annotations so the code runs on 3.7. Create tests/__init__.py (empty). Create tests/test_data.py covering dataset shape, types and unique ids; copy semantics; hit and miss; case-insensitive partial match; and the unicode match. It also defines the constant ALLOWED_TOP_LEVEL exactly as listed in the design's IMPORT ALLOWLIST section (no sys.stdlib_module_names, no other additions). An ast-based test then walks bookapi/ and tests/ with os.walk and asserts that every absolute import's top-level name is in that set. Relative imports are allowed. Import only modules from ALLOWED_TOP_LEVEL.; **files**: ['bookapi/__init__.py', 'bookapi/data.py', 'tests/__init__.py', 'tests/test_data.py']; **covers**: ['R1', 'R2', 'R11']; **depends_on**: []
- **id**: W2; **title**: HTTP server, entry point and integration tests; **description**: Implement bookapi/server.py: BookRequestHandler with _send_json/_send_error_json (never send_error); exact routing for /books, /books?author= (parse_qs with keep_blank_values) and /books/<id> (400 unless ^[0-9]+$, 404 if missing); 404 for other paths, including trailing slashes; 405 with Allow: GET for POST/PUT/DELETE/PATCH/OPTIONS/HEAD, with no body for HEAD; and a no-op log_message. Add make_server(host, port), returning a ThreadingHTTPServer with daemon_threads=True, and main(argv) with argparse --host/--port defaulting to 127.0.0.1:8000. Create bookapi/__main__.py. Write tests/test_server.py with a live server on port 0, started in setUpClass and stopped in tearDownClass with shutdown/server_close/join. Test all status codes; Content-Type and Content-Length on every response; the author search cases (case, miss, %20 and +, unicode); silent logging via contextlib.redirect_stderr(io.StringIO()); and main() argument handling via unittest.mock.patch of bookapi.server.make_server. HARD CONSTRAINT: every import in these three files must have its top-level name in the design's ALLOWED_TOP_LEVEL set. Use no pathlib or any other module outside it, or W1's R1 test fails.; **files**: ['bookapi/server.py', 'bookapi/__main__.py', 'tests/test_server.py']; **covers**: ['R1', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11']; **depends_on**: ['W1']
- **id**: W3; **title**: README; **description**: Write README.md covering: requirements (Python 3.7+, no dependencies); the run command `python3 -m bookapi [--host H] [--port P]` (default 127.0.0.1:8000); the test command `python3 -m unittest -v` (python -m unittest); the three endpoints (GET /books, GET /books/<id>, GET /books?author=X) with example curl calls and JSON shapes; the error body {"error": ...}; status codes 200/400/404/405 and when each occurs; search semantics (case-insensitive substring on author, URL-decoded, an empty value matches all, no match returns []); and trailing-slash behaviour (/books/ and /books/1/ return 404).; **files**: ['README.md']; **covers**: ['R12']; **depends_on**: ['W2']

## test_command

python3 -m unittest -v

## run_command

python3 -m bookapi --host 127.0.0.1 --port 8000
