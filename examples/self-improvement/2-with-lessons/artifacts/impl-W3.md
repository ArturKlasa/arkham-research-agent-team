# impl:W3 (engineer)

## summary

I updated README.md (W3, covers R12) to match the revised spec. I re-read the current bookapi/server.py, data.py and __main__.py. The routes, error messages, status codes, search rules and trailing-slash behaviour the README already described still match the code, so those sections are unchanged.

What I added for the revised spec:
- **Requirements section:** the project deliberately has no requirements.txt or other dependency file. Every module in `bookapi/` and `tests/` may import only from the fixed list of standard-library modules in `ALLOWED_TOP_LEVEL` (in `tests/test_data.py`). A test enforces this, so new code can't use other modules such as `pathlib`.
- **Project layout:** I added `tests/__init__.py`, the empty file that lets `python3 -m unittest` find the tests. The `tests/test_data.py` entry now says it holds the `ALLOWED_TOP_LEVEL` import check.

Everything else is unchanged: Python 3.7+ with no dependencies; the run command `python3 -m bookapi [--host H] [--port P]` (default 127.0.0.1:8000); the test command `python3 -m unittest -v`; the three endpoints with curl examples and JSON shapes; the `{"error": ...}` error body with the exact messages; the 200/400/404/405 table, including `Allow: GET` and headers-only HEAD responses; the search rules (case-insensitive substring match, URL-decoded with `%20`, `+` and UTF-8, no trimming, an empty value returns all books, no match returns `[]`, only the first `author` value is used); and `/books/` and `/books/1/` returning 404.

## tests

I ran `python3 -m unittest -v` and then `python3 -m unittest` after editing; both exited 0 with all 48 tests passing.

I also checked the README's claims against a live server (`python3 -m bookapi --port 8766`). Every response matched what the README says:
- `/books/` and `/books/1/` returned 404 with `{"error": "Not found"}`.
- `/books/03` returned 200 with book 3.
- `/books/-1` returned 400 with the invalid-id message.
- `/books?author=` returned 200 with the full list.
- `/books?author=ORWELL` returned 200 with the Orwell books.
