# Book API

A small HTTP JSON API for looking up books in an in-memory mock database.
It uses only the Python standard library (Python 3.7+). There are no
third-party packages and no `requirements.txt`.

> **Demo only. Not production-grade.** The server is built on the standard
> library's `http.server` (`ThreadingHTTPServer`). It has no authentication,
> TLS, persistence, rate limiting or other hardening. Use it for local demos
> and tests only.

## Run

From the project root:

```sh
python3 -m bookapi --port 8000
```

Options: `--port` (int, default `8000`) and `--host` (default `127.0.0.1`).
The server prints `Serving on http://127.0.0.1:8000`. Press Ctrl+C to stop it.

Example:

```sh
curl http://127.0.0.1:8000/books
curl http://127.0.0.1:8000/books/1
curl "http://127.0.0.1:8000/books?author=jane%20austen"
curl "http://127.0.0.1:8000/books?author=orw"
```

## Test

From the project root:

```sh
python3 -m unittest -v
```

## Endpoints

Only `GET` is supported.

| Method | Path                     | Success                                         | Failure |
|--------|--------------------------|-------------------------------------------------|---------|
| GET    | `/books`                 | 200, JSON array of all books                    | n/a     |
| GET    | `/books/{id}`            | 200, the book object with that id               | 404 if the id is unknown or not an integer |
| GET    | `/books?author=<name>`   | 200, JSON array of matching books (may be `[]`) | n/a     |
| GET    | any other path           | n/a                                             | 404     |

Every response, including errors, has
`Content-Type: application/json; charset=utf-8`, a UTF-8 encoded body and an
accurate `Content-Length`.

## Book fields

| Field    | Type   | Description         |
|----------|--------|---------------------|
| `id`     | int    | Unique book id      |
| `title`  | string | Book title          |
| `author` | string | Author's name       |
| `year`   | int    | Publication year    |

Example:

```json
{"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen", "year": 1813}
```

## Errors

Unknown book ids (for example `/books/99999`), non-integer ids (for example
`/books/abc`, `/books/`) and unknown routes (for example `/nope`) all return
status **404** with this body:

```json
{"error": "not found"}
```

## Author search

`GET /books?author=<name>` returns the books whose author name contains
`<name>` as a **case-insensitive substring**, in dataset order.

- `author=jane%20austen`, `author=JANE+AUSTEN` and `author=Jane%20Austen`
  all return the two Jane Austen books.
- Spaces in the query must be encoded as `%20` or `+`. An unencoded space
  (for example `author=Jane Austen`) makes the request line invalid, and
  `http.server` rejects it with an HTTP 400 (HTML, not JSON) before it
  reaches the API.
- Partial names match: `author=orw` returns the George Orwell books.
- Values are URL-decoded: `%20` and `+` become spaces, and UTF-8
  percent-escapes are decoded (for example `author=Garc%C3%ADa`).
- An empty value (`author=`) matches all books.
- A name that matches nothing returns 200 and `[]`.
- There is no fuzzy, regex or accent-insensitive matching (`Garcia` does
  not match `García`), and no trimming of whitespace.
- Without an `author` parameter, `/books` returns the full list. Other query
  parameters are ignored.

## Layout

```
bookapi/data.py      mock dataset (BOOKS) and BookStore data layer
bookapi/server.py    HTTP handler, BookServer, make_server, main
bookapi/__main__.py  `python3 -m bookapi` entry point
tests/               unittest suites (data layer, HTTP, stdlib-only imports)
```
