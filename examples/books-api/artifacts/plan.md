# plan (planner)

## goal

Deliver a stdlib-only Python HTTP JSON API over an in-memory mock book database, with endpoints to list books, get a book by id (JSON 404 if missing) and search by author name, plus unit tests.

## in_scope

- Single-module server using http.server (ThreadingHTTPServer + BaseHTTPRequestHandler), json and urllib.parse only
- In-memory mock data: list of dicts with id (int), title, author (name string), year
- GET /books returns 200 with a JSON array of all books
- GET /books/{id} returns 200 with the book object; any id that does not resolve to a book (unknown or non-integer) returns the JSON 404 {"error": "not found"}
- GET /books?author=<name> returns 200 with a JSON array of books whose author name contains the query, case-insensitive substring match (documented rule); empty array if nothing matches
- Empty or missing author parameter: missing means plain list; empty value matches all books (consistent with substring rule)
- Unknown routes return the same JSON 404
- Responses use Content-Type application/json; charset=utf-8, UTF-8 encoded bytes and an accurate Content-Length
- Data-layer functions (list, get by id, filter by author name) separate from the HTTP handler
- Unit tests with unittest: data-layer tests (including author hit, miss, case-insensitivity, partial match), plus HTTP tests against a server on port 0 in a background thread using urllib.request (list, get existing, get missing 404, non-integer id 404, ?author= hit, miss, case-insensitive, URL-encoded name with spaces, unknown route 404)
- Short README with run and test commands and the author search rule

## out_of_scope

- Fuzzy, regex or accent-insensitive author matching beyond case-insensitive substring
- Write endpoints (POST/PUT/DELETE), authentication, pagination, sorting
- Persistent database or any third-party dependency
- Production hardening, TLS, HTTP/1.1 keep-alive tuning
- Separate authors resource or endpoint
- Combining author search with other filters

## milestones

- 1. Define mock dataset and data-layer functions (list, get by id, filter by author name) with unit tests
- 2. Implement request handler: routing, query parsing, JSON helper that sends status, headers and body, 404 handling
- 3. Add server entry point (main, configurable port) and wire the data store via the server object
- 4. Write HTTP-level tests using a port-0 server in a background thread; ensure clean shutdown
- 5. Run the full test suite with python -m unittest, write README, final check that only the stdlib is used

## risks

- Default send_error returns HTML, so the 404 must be written manually as JSON; covered by a test on the Content-Type and body
- The handler path includes the query string; mitigated by parsing with urllib.parse.urlsplit/parse_qs (which also decodes percent-encoding) and testing the author filter
- Wrong Content-Length or non-UTF-8 encoding could hang or corrupt clients; encode body to bytes first and measure its length
- Tests may be flaky from port clashes or unclosed servers; use port 0, and shutdown() and server_close() in tearDown
- Unspecified schema and search semantics may differ from reader expectations; chosen rule (case-insensitive substring) documented in README and tests
- Author values reflected in responses only in JSON body, never in headers (send_header does no CRLF validation)
- http.server is not production-grade; documented as a demo only

## assumptions

- The task does not fix a schema, so the book fields and the error body {"error": "not found"} are our choice
- Search by author is by author name via the author query parameter on /books, case-insensitive substring match, as suggested in the research brief
- Any id that is not an existing integer id is treated as unknown and returns 404
- Only GET is required; other methods may get the default server behavior
- Python 3.7+ is available for ThreadingHTTPServer
- The research brief's design suggestions are unsourced and chosen by us; the unittest, json and urllib.parse docs were not checked
