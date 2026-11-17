# plan (planner)

## goal

Deliver a stdlib-only Python HTTP JSON API over an in-memory mock book database, with endpoints to list books, get a book by id (404 if missing) and search by author, plus unit tests.

## in_scope

- Single package/module using http.server (ThreadingHTTPServer) and BaseHTTPRequestHandler, json, urllib.parse only
- In-memory mock data: ~8-10 books with id (int), title, author, year
- GET /books returns a JSON list of all books
- GET /books/<id> returns one book; 404 JSON body {"error": ...} if the id is missing
- GET /books?author=X returns the matching books; case-insensitive substring match; empty list if none match
- Non-integer id returns 400 with JSON error; unknown route returns 404 JSON; non-GET methods return 405 JSON
- All responses use Content-Type application/json; charset=utf-8 and an accurate Content-Length
- Own JSON error writer instead of send_error, which defaults to HTML
- unittest suite: unit tests for the data/search logic, plus integration tests that start the server on port 0 in a thread and call it with urllib.request, then shut it down
- Short README with run and test commands (python -m unittest)
- Server entry point with host/port configurable, default 127.0.0.1:8000

## out_of_scope

- Real database or persistence
- Create/update/delete endpoints and authentication
- Pagination, sorting, and other filters
- Production hardening, TLS, and deployment
- Third-party dependencies, frameworks, and linters
- OpenAPI docs

## milestones

- 1. Fix design decisions: routes, JSON shape, status codes, search semantics, and record them in the README
- 2. Implement the data layer: mock books and pure functions list_books, get_book(id), search_by_author(name)
- 3. Implement the HTTP handler and routing, with query parsing, JSON helper and error responses, and a main() that serves
- 4. Write unit tests for the data layer and handler tests against a live server on an ephemeral port (200, 404 missing id, 400 bad id, author search hit, miss, case-insensitivity, unknown route, 405)
- 5. Run the full test suite, do a manual curl smoke test, and finalise the README

## risks

- http.server HTTP/1.0 vs 1.1: with HTTP/1.1 a wrong Content-Length makes clients hang; compute the length from the encoded bytes
- Author names in the query need URL-decoding and may contain spaces or unicode; use parse_qs
- Tests that leave the server running or use a fixed port can hang or be flaky; use port 0, shutdown() and server_close() in tearDown
- Never echo user input into headers (no CRLF validation in send_header)
- Handler logging clutters test output; override log_message

## assumptions

- Python 3.7 or later is available (ThreadingHTTPServer)
- The route shape /books, /books/<id>, /books?author=X is acceptable since the task does not specify one
- Author search is case-insensitive substring matching
- A non-integer id returns 400, a valid but unknown id returns 404
- A trailing slash is tolerated or returns 404, and it is documented either way
- No reference files exist in sources/, so the design relies on the official http.server docs
