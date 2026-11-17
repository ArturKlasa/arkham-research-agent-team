# research (researcher)

## summary

sources/ is empty, so there are no reference files. I read the official Python http.server documentation (opened at https://docs.python.org/3/library/http.server.html). It confirms the API can be built with the standard library alone. The design suggestions below (routes, JSON shape, test approach) are my recommendations, not facts from a source.

## findings

- **claim**: http.server provides HTTPServer and ThreadingHTTPServer (the latter added in 3.7). Both need a RequestHandlerClass. BaseHTTPRequestHandler must be subclassed, with do_GET etc. implemented. The method name is built from the request method.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: The handler's `path` attribute includes the query string, so the code must split it itself (e.g. with urllib.parse) to handle /books?author=X and /books/<id>. This follows from the documented `path` attribute. The urllib.parse suggestion is mine and I did not open that doc.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: send_response(code), send_header(), and end_headers() are used to build a reply. The body is written to wfile. send_error(code, message) sends an error reply, but its default content type is text/html (error_content_type). A JSON API should write its own JSON 404 body, or override error_content_type and error_message_format.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: protocol_version defaults to HTTP/1.0. If set to HTTP/1.1, every response must carry an accurate Content-Length header.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: http.server is not recommended for production. Its send_header does no CRLF validation, so untrusted input must not be echoed into headers. This is fine for a mock API.; **source**: https://docs.python.org/3/library/http.server.html
- **claim**: The docs show a run() pattern: build the server with a (host, port) tuple and call serve_forever(). For tests, binding to port 0 lets the OS pick a free port (my suggestion, not stated in the doc). Run the server in a thread and call shutdown() afterwards.; **source**: https://docs.python.org/3/library/http.server.html

## open_questions

- Exact route shapes (/books, /books/<id>, /books?author=X or /authors/<name>/books) are not specified by the task. The team must choose.
- Whether author search should be case-insensitive or substring-based is unspecified. The team must decide.
- The behaviour for a non-integer id (400 or 404) is unspecified. I did not open the unittest or json docs. Those APIs are standard library but were not verified here.
