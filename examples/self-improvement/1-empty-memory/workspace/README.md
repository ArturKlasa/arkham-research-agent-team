# Book API

A small, read-only HTTP JSON API for looking up books in an in-memory mock
database. It is built only on the Python standard library (`http.server`,
`json`, `urllib.parse`). It has no frameworks and no third-party packages.

## Requirements

- Python 3.7 or newer
- Standard library only. There are no dependencies to install, and there is
  no `requirements.txt`, `setup.py` or `pyproject.toml`.

## Running the server

From the repository root:

```sh
python3 -m books --port 8000
```

Options:

| Option   | Default     | Description                 |
|----------|-------------|-----------------------------|
| `--host` | `127.0.0.1` | Interface to bind to        |
| `--port` | `8000`      | TCP port to listen on       |

The server prints `Serving on http://127.0.0.1:8000`. Press Ctrl+C to stop it.

## Running the tests

From the repository root:

```sh
python -m unittest
```

Use this for verbose output:

```sh
python3 -m unittest -v
```

The integration tests start a real server on an ephemeral port (port 0) in a
background thread and shut it down when they finish.

## Book schema

Each book is a JSON object with exactly these fields:

| Field    | Type   | Example            |
|----------|--------|--------------------|
| `id`     | int    | `1`                |
| `title`  | string | `"The Hobbit"`     |
| `author` | string | `"J.R.R. Tolkien"` |
| `year`   | int    | `1937`             |

The mock dataset has 6 books with ids 1 to 6.

## Endpoints

Every response, including errors, has `Content-Type: application/json` and an
exact `Content-Length`.

### `GET /books`: list all books

```sh
curl http://127.0.0.1:8000/books
```

```json
[
  {"id": 1, "title": "The Hobbit", "author": "J.R.R. Tolkien", "year": 1937},
  {"id": 2, "title": "The Fellowship of the Ring", "author": "J.R.R. Tolkien", "year": 1954},
  {"id": 3, "title": "Pride and Prejudice", "author": "Jane Austen", "year": 1813},
  {"id": 4, "title": "Emma", "author": "Jane Austen", "year": 1815},
  {"id": 5, "title": "Nineteen Eighty-Four", "author": "George Orwell", "year": 1949},
  {"id": 6, "title": "Dune", "author": "Frank Herbert", "year": 1965}
]
```

### `GET /books/<id>`: get one book by id

```sh
curl http://127.0.0.1:8000/books/1
```

```json
{"id": 1, "title": "The Hobbit", "author": "J.R.R. Tolkien", "year": 1937}
```

`<id>` must be made of ASCII digits only. This route ignores any query string.

```sh
curl -i http://127.0.0.1:8000/books/99999   # 404 {"error": "book not found"}
curl -i http://127.0.0.1:8000/books/abc     # 400 {"error": "invalid book id"}
```

### `GET /books?author=<name>`: search books by author

```sh
curl "http://127.0.0.1:8000/books?author=tolkien"
```

```json
[
  {"id": 1, "title": "The Hobbit", "author": "J.R.R. Tolkien", "year": 1937},
  {"id": 2, "title": "The Fellowship of the Ring", "author": "J.R.R. Tolkien", "year": 1954}
]
```

Search rules:

- **Case-insensitive substring match** on the author name. For example,
  `TOLKIEN`, `tolk` and `olk` all match `J.R.R. Tolkien`.
- Spaces and special characters must be URL-encoded, for example
  `/books?author=Jane%20Austen`.
- **No matches** returns `200` with an empty list `[]`, not 404.
- An empty value (`/books?author=`) matches every book.
- Other query parameters are ignored.

## Errors

Errors are always JSON objects with an `error` key and are never HTML:

```json
{"error": "book not found"}
```

| Status | When                                                       | Body                                  |
|--------|------------------------------------------------------------|---------------------------------------|
| 400    | `GET /books/<id>` with a non-integer id (e.g. `/books/abc`) | `{"error": "invalid book id"}`        |
| 404    | `GET /books/<id>` for an id that does not exist             | `{"error": "book not found"}`         |
| 404    | Any unknown path (e.g. `/nope`, `/books/1/extra`)           | `{"error": "not found"}`              |
| 405    | POST, PUT, DELETE, PATCH, HEAD or OPTIONS on any path       | `{"error": "method not allowed"}` plus the header `Allow: GET` |

Error messages are fixed strings. Request input is never echoed back.

## Project layout

```
books/__init__.py      package marker
books/__main__.py      `python3 -m books` entry point
books/data.py          mock dataset + BookRepository (data-access layer)
books/server.py        HTTP handler, create_server(), CLI main()
tests/test_data.py     data-layer unit tests
tests/test_server.py   integration tests against a real server
tests/test_project.py  stdlib-only import check + README check
```

## Production note

Python's `http.server` is **not production-grade**. It does only basic
security checks and is not built for performance or hardening. In this
project it is used only to serve this small mock API for local development and
testing. Do not expose it to untrusted networks.
