# BookAPI

A small read-only HTTP JSON API over an in-memory mock database of 10 books.
It uses only the Python standard library (`http.server`, `json`, `urllib.parse`).

## Requirements

- Python 3.7 or newer (it uses `http.server.ThreadingHTTPServer`)
- No third-party dependencies, so there is nothing to `pip install`. The project
  deliberately has no `requirements.txt` or other dependency file.
- Every module in `bookapi/` and `tests/` imports only from a fixed allowlist of
  standard-library modules (`ALLOWED_TOP_LEVEL` in `tests/test_data.py`). A test
  enforces this, so new code must not use other modules, such as `pathlib`.

## Running

From the project root:

```sh
python3 -m bookapi [--host H] [--port P]
```

By default the server listens on `127.0.0.1:8000`. Examples:

```sh
python3 -m bookapi                               # http://127.0.0.1:8000
python3 -m bookapi --host 0.0.0.0 --port 9001    # all interfaces, port 9001
```

On startup the server prints `Serving on http://HOST:PORT`. Press Ctrl+C to stop it.
Per-request logging is turned off.

## Testing

```sh
python3 -m unittest -v
```

This runs the data-layer unit tests (`tests/test_data.py`) and the integration
tests (`tests/test_server.py`). The integration tests start a live server on an
ephemeral port (port 0) in a background thread and shut it down when they finish.

## Data shape

Each book is a JSON object with exactly these fields:

```json
{"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen", "year": 1813}
```

`id` and `year` are integers. `title` and `author` are strings. Successful
responses return the array or object directly, with no envelope. Non-ASCII text
is sent as UTF-8 and is not `\u`-escaped.

## Endpoints

Only `GET` is supported.

### List all books: `GET /books`

```sh
curl -i http://127.0.0.1:8000/books
```

`200 OK`. The body is a JSON array of all books in id order:

```json
[
  {"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen", "year": 1813},
  {"id": 2, "title": "Emma", "author": "Jane Austen", "year": 1815},
  ...
]
```

Query parameters other than `author` are ignored.

### Get one book: `GET /books/<id>`

```sh
curl -i http://127.0.0.1:8000/books/3
```

`200 OK`. The body is a single JSON object:

```json
{"id": 3, "title": "Nineteen Eighty-Four", "author": "George Orwell", "year": 1949}
```

- `<id>` must contain only ASCII digits (`^[0-9]+$`). Leading zeros are accepted, so `/books/03` is book 3.
- A well-formed id that does not exist, such as `/books/99999`, returns `404` with `{"error": "Book not found"}`.
- A non-integer id, such as `/books/abc` or `/books/-1`, returns `400` with
  `{"error": "Invalid book id; must be a non-negative integer"}`.
- Any query string on this route is ignored.

```sh
curl -i http://127.0.0.1:8000/books/99999   # 404
curl -i http://127.0.0.1:8000/books/abc     # 400
```

### Search by author: `GET /books?author=<name>`

```sh
curl -i "http://127.0.0.1:8000/books?author=orwell"
curl -i "http://127.0.0.1:8000/books?author=jane%20austen"
curl -i "http://127.0.0.1:8000/books?author=jane+austen"
curl -i "http://127.0.0.1:8000/books?author=Garc%C3%ADa"
```

`200 OK`. The body is a JSON array of the matching books in id order, for example
for `author=orwell`:

```json
[
  {"id": 3, "title": "Nineteen Eighty-Four", "author": "George Orwell", "year": 1949},
  {"id": 4, "title": "Animal Farm", "author": "George Orwell", "year": 1945}
]
```

Search rules:

- **Case-insensitive substring match** on the `author` field (both sides are
  `casefold()`ed). `orwell`, `ORWELL` and `George Orwell` all match George Orwell.
- **URL-decoded:** the value is decoded as UTF-8 percent-encoding, and `+` becomes
  a space. Both `jane%20austen` and `jane+austen` work, as do non-ASCII names
  such as `Garc%C3%ADa`.
- The value is not trimmed, so leading or trailing spaces count as part of the search.
- **An empty value** (`/books?author=`) matches every book, so it returns the full list.
- **No match** returns `200` with an empty array `[]`, not a 404.
- If `author` is given more than once, only the first value is used.

## Errors

Every error response has this body:

```json
{"error": "<message>"}
```

Every response, success or error, has the header
`Content-Type: application/json; charset=utf-8` and a correct `Content-Length`.

| Status | When |
|--------|------|
| `200 OK` | `GET /books`, `GET /books?author=...` (including no matches, which return `[]`), and `GET /books/<id>` for an existing id |
| `400 Bad Request` | `GET /books/<id>` where `<id>` is not made only of ASCII digits, e.g. `/books/abc`. Body: `{"error": "Invalid book id; must be a non-negative integer"}` |
| `404 Not Found` | `GET /books/<id>` for a well-formed id that does not exist (`{"error": "Book not found"}`), or any unknown path such as `/nope` (`{"error": "Not found"}`) |
| `405 Method Not Allowed` | Any `POST`, `PUT`, `DELETE`, `PATCH`, `OPTIONS` or `HEAD` request, on any path. Body: `{"error": "Method not allowed"}`. The response includes `Allow: GET`. `HEAD` responses send the headers but no body. |

```sh
curl -i -X POST http://127.0.0.1:8000/books   # 405, Allow: GET
curl -i http://127.0.0.1:8000/nope            # 404
```

## Trailing slashes

Routing is exact, and trailing slashes are **not** accepted:

- `/books/` returns `404` with `{"error": "Not found"}`. Use `/books`.
- `/books/1/` returns `404` with `{"error": "Not found"}`. Use `/books/1`.

## Project layout

```
bookapi/__init__.py    package marker
bookapi/__main__.py    entry point for `python3 -m bookapi`
bookapi/data.py        mock BOOKS data plus list_books / get_book / search_by_author
bookapi/server.py      request handler, routing, JSON writer, make_server(), main()
tests/__init__.py      empty marker so `python3 -m unittest` discovers tests/
tests/test_data.py     data-layer unit tests and the ALLOWED_TOP_LEVEL import check
tests/test_server.py   integration tests against a live server
```
