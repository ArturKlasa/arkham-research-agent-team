# plan (planner)

## goal

Deliver a stdlib-only Python HTTP JSON API over an in-memory mock book database, with endpoints to list books, get a book by id (404 if missing) and search by author, plus unit tests.

## in_scope

- Book schema: id (int), title, author, year
- In-memory mock data module with a small data-access layer: all(), get(id), search_by_author(name)
- GET /books returns a JSON list of all books
- GET /books/<id> returns one book, or 404 with a JSON error body if missing
- GET /books?author=<name> uses case-insensitive substring matching and returns 200 with a list (empty if no matches)
- Non-integer id returns 400 with a JSON error. Unknown routes return 404 with JSON. Non-GET methods return 405 with JSON.
- http.server (ThreadingHTTPServer) handler with urllib.parse for the path and query. Mock data is injected via the server attribute.
- JSON responses use Content-Type: application/json and an accurate Content-Length. Errors are written manually as JSON, not with send_error.
- Handler overrides log_message to keep test output quiet
- unittest tests: pure data-layer tests, plus integration tests with the server on port 0 in a background thread using urllib.request
- Short README with run and test instructions (python -m unittest)

## out_of_scope

- Third-party dependencies or frameworks
- Persistent or real database
- Create, update and delete endpoints
- Authentication, TLS, rate limiting, production hardening
- Pagination, sorting, other search fields
- Docker or CI setup

## milestones

- 1. Fix decisions: routes, schema, search semantics, error format. Set up the project layout (books/data.py, books/server.py, tests/).
- 2. Implement the mock data and data-access layer, with unit tests.
- 3. Implement the HTTP handler and server entry point (list, get by id with 404/400, author search, JSON errors).
- 4. Write integration tests: list, get existing, get missing (404), bad id (400), author match, case-insensitive and substring match, no match, unknown route, wrong method, Content-Type header.
- 5. Run the full suite and manually smoke-test with curl. Write the README. Confirm no non-stdlib imports.

## risks

- The spec is silent on schema, routes and search semantics. The chosen defaults may differ from what the requester expects, so they are documented.
- Tests that start a real server may be flaky if ports or threads are not cleaned up. Mitigate with port 0, shutdown and server_close in tearDown.
- With HTTP/1.1 keep-alive, a wrong Content-Length can hang a client. Mitigate by always encoding the body first and computing its length.
- http.server is not production-grade. This is acceptable for a mock API but should be noted in the README.
- Untrusted query input must not be echoed into headers. Only JSON bodies are used.

## assumptions

- Python 3.7 or later (ThreadingHTTPServer is required)
- Read-only API; the data is a static in-memory list of about 5-10 books
- Author search is case-insensitive substring matching, and a search with no matches returns 200 with []
- A query string on /books/<id> is ignored
- Server binds to 127.0.0.1 on a configurable port
