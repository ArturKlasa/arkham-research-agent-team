# impl:W2 (engineer)

## summary

I changed `bookapi/server.py` so book search now works by author name, as the revised plan asks. The full test command still fails: 4 of 35 tests fail. All 4 are in `tests/test_server.py`, which is W3's file and still tests the old `?author_id=` filter. A separate check against a live server passed all 17 cases.

**What changed:** `GET /books?author=<name>` now passes the decoded name to `store.books_by_author`. That does a case-insensitive substring match, so an empty `author=` returns all books and a name that matches nothing returns 200 with `[]`. I removed the old `?author_id=` handling and its regex. Other query parameters are ignored, so `?author_id=...` now just returns the full list.

**What didn't change:**
- `/books/{id}` returns the book, or 404 with exactly `{"error": "not found"}` if the id is unknown or not a number. Unknown routes return the same 404.
- Every response is sent as `application/json; charset=utf-8`, the body is UTF-8, and Content-Length is its length in bytes.
- `send_error` is never used, and request data never goes into a header.
- `BookServer` keeps the data store on `.store`. `make_server`, `parse_args` (`--host`, `--port`) and `main` (prints `Serving on http://host:port` with `flush=True`) are unchanged, and so is `__main__.py`.
- The only imports are the standard-library ones the design lists, plus `bookapi.data`.

**What W3 needs to do:** In `tests/test_server.py`, replace `test_filter_by_author_hit` and `test_filter_by_author_misses` with the `?author=` cases from design section 4 (Jane%20Austen, JANE+AUSTEN, orw, quote('Gabriel García'), Tolkien and empty). Until then, the full test command will keep failing. I didn't edit that file because it belongs to W3.

**Not done on purpose:** an earlier run asked for the requested id to be included in the 404 body. I left it out because R5, R7 and the design require the body to be exactly `{"error": "not found"}`.

## tests

1) `python3 -m unittest -v` from the workspace root: ran 35 tests, FAILED (failures=4), so the test command does not pass yet. The 4 failures are W3's old tests in `tests/test_server.py`. `test_filter_by_author_hit` expects `/books?author_id=2` to return ids [3,4], and `test_filter_by_author_misses` expects `author_id=999`, `author_id=abc` and an empty `author_id=` to return []. That filter was removed in the revised plan, so those requests now return the full list. All the other tests passed, including the remaining HTTP tests.

2) `python3 -m unittest tests.test_data tests.test_stdlib`: ran 26 tests, OK.

3) `python3 smoke_tmp.py`, a temporary script that I deleted afterwards. Result: SMOKE OK, exit 0. It started `make_server(port=0)` in a background thread and requested 17 paths. For each one it checked the status, the parsed body, the Content-Type, and that Content-Length equals the number of body bytes.
- **Books:** /books, /books/1 and /books/5 (non-ASCII) returned 200 with the right body.
- **404s:** /books/99999, /books/abc, /books/, /nope, / and /books/1/x each returned 404 with `{"error": "not found"}`.
- **Author search, compared by ids:**
  - `?foo=1` and `?author=` returned all books.
  - `?author=Tolkien` returned 200 with [].
  - `Jane%20Austen` and `JANE+AUSTEN` returned [1,2].
  - `orw` returned [3,4].
  - `quote('Gabriel García')` and `garc%C3%ADa` returned [5].

The script also checked parse_args (port 0, default port 8000, --host) and that `server.store` is a BookStore. It then started `python3 -m bookapi --port 0` as a separate process, read the port from the "Serving on" line, and got 200 with the full book list from /books. That process was then stopped with terminate and wait, and nothing hung.
