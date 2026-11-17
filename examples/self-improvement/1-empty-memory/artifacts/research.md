# research (researcher)

## summary

The sources/ directory is empty, so there are no reference files. I read the official Python http.server documentation, which confirms the API can be built with the standard library alone. The route and schema suggestions at the end are my recommendations, not facts from a source.

## findings

- **claim**: HTTPServer (a socketserver.TCPServer subclass) and ThreadingHTTPServer (same, but one thread per request, added in 3.7) both require a RequestHandlerClass. The handler can reach the server through its `server` attribute, which is a way to inject the mock data.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: BaseHTTPRequestHandler must be subclassed. A request method X is dispatched to do_X(), so GET needs do_GET(). `self.path` includes the query string, so the handler must split it, e.g. with urllib.parse, to read `?author=`.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: Responses are written with send_response, send_header, end_headers and then wfile. wfile is a binary stream, so JSON must be encoded to bytes and sent with Content-Type: application/json and Content-Length. If protocol_version is set to HTTP/1.1, an accurate Content-Length is mandatory. The default is HTTP/1.0.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: send_error(code, message, explain) returns an HTML error body by default, because error_content_type defaults to 'text/html'. For a JSON 404, either write the response manually with a JSON body or override error_content_type and error_message_format.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: The docs warn that http.server is not recommended for production and only implements basic security checks. send_header does not sanitize its input, so untrusted input such as the author query must never be echoed into headers. The default log_message writes to stderr and can be overridden to quiet the tests.; **source**: https://docs.python.org/3/library/http.server.html

## open_questions

- No spec was given for the book schema (suggested: id, title, author, year), the routes (suggested: GET /books, GET /books/<id>, GET /books?author=<name>), or whether author search is case-insensitive or substring. The team must decide.
- No Python version was specified. The docs I read were for 3.15.2, and ThreadingHTTPServer needs 3.7 or later.
- I did not verify a testing approach in any source. Suggested: unittest with the server on port 0 in a background thread, using urllib.request, plus pure unit tests for the data layer.
- The error-response format is undefined, including for a non-integer id and for an author search with no matches (suggest 200 with an empty list).
