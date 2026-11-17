# research (researcher)

## summary

sources/ is empty, so there are no reference files. I opened only the Python http.server docs (3.15.2). I did not open the unittest, urllib.parse or json docs. A stdlib-only build with http.server and unittest is feasible. The design suggestions below are mine, not sourced facts: GET /books, GET /books/{id} with a JSON 404, GET /books?author=<name> (case-insensitive substring match), an in-memory list of dicts as the mock DB, urllib.parse for the query string, and json for bodies. For tests, run a ThreadingHTTPServer on port 0 in a background thread and call it with urllib.request. Also unit-test the data layer directly.

## findings

- **claim**: http.server provides HTTPServer and ThreadingHTTPServer (threaded, added in 3.7). Both take a RequestHandlerClass, typically a BaseHTTPRequestHandler subclass. The handler dispatches to do_<METHOD>() methods, e.g. do_GET.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: The handler's `path` attribute includes the query string, so the code must split it itself (e.g. with urllib.parse) to read ?author=.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: send_error() produces an HTML error body by default (error_content_type is 'text/html'). For a JSON 404, write the response manually with send_response, send_header and wfile.write, or override error_content_type and error_message_format.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: protocol_version defaults to HTTP/1.0. If set to HTTP/1.1, every response must include an accurate Content-Length header. wfile is a binary stream, so JSON must be encoded to bytes.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: The docs warn that http.server is not recommended for production and only implements basic security checks. send_header does no CRLF validation, so untrusted input must not be placed in headers.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: HTTPServer exposes server_port, and the handler can reach the server through its `server` attribute. This allows binding to port 0 in tests and reading the assigned port, and also allows sharing the data store via the server object. Port 0 itself is my inference, not stated on the page.; **source**: https://docs.python.org/3/library/http.server.html

## open_questions

- Exact response schema (field names, error body shape) is not specified by the task, so the team must choose one.
- Author search semantics (exact vs substring, case sensitivity) are unspecified.
- unittest, urllib.parse and json docs were not opened, so no sourced facts about them.
