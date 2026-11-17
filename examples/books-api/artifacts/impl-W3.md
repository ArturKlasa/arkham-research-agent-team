# impl:W3 (engineer)

## summary

I've rewritten `tests/test_server.py` for the revised plan, where search is by author name. All my new tests pass, but the full suite still has one failure. It comes from `tests/test_stdlib.py` (W4's file, which I can't edit): its import allowlist is out of date and doesn't include `subprocess`. The design lists `subprocess` as allowed, and the new module test needs it.

**What changed:**
- **Removed:** the old `author_id` tests. The revised spec drops that filter.
- **Author search tests:** each case checks the returned book ids.

  | Request | Expected ids |
  |---|---|
  | `Jane%20Austen` | [1, 2] |
  | `JANE+AUSTEN` | [1, 2] |
  | `orw` | [3, 4] |
  | `quote("Gabriel García")` | [5] |
  | `Tolkien` | `[]` with status 200 |
  | `author=` (empty) | the full `BOOKS` list |

- **New `MainModuleTest`:** it starts `python -m bookapi --port 0` as a separate process from the repo root and reads the port from the first line of output. It then fetches `/books` and checks the status, headers and that the body equals `BOOKS`. Afterwards it stops the process (waiting up to 5 seconds, then killing it if needed) and closes the pipes. If the server fails to start, the test fails instead of hanging.
- **Kept as before:**
  - A `ServerTests` class (renamed from `ServerTestCase`) that starts a server on a free port in a background thread and shuts it down cleanly after each test.
  - A `get()` helper that also returns the status, headers and body of 404 responses.
  - Checks on every response that Content-Type is `application/json; charset=utf-8` and that Content-Length matches the body size.
  - Tests for `/books`, `/books/1`, and the accented characters in `/books/5`.
  - 404 tests for `/books/99999`, `/books/abc`, `/books/` and `/nope`, plus `/` and `/books/1/x`.
  - `parse_args` (port and host) and `server.store` being a `BookStore`.

**To fix the failure:** W4 needs to add `subprocess` to `ALLOWED` in `tests/test_stdlib.py`. That file also allows `collections`, which isn't in the design's list but is needed because `tests/test_data.py` imports it. I didn't try to hide the import from the check.

**404 body:** I didn't apply an earlier user request to include the id in the 404 body. Requirements R5 and R7 say the body must be exactly `{"error": "not found"}`, and the tests check for that.

## tests

1. `timeout 120 python3 -m unittest -v` from the workspace root (the spec's test command): exit code 1. 37 tests ran in 5.1s, with 36 passing and 1 failing. The failure is `tests.test_stdlib.StdlibOnlyTest.test_all_imports_in_allowlist`, which reports `tests/test_server.py:7: subprocess`. That's because W4's allowlist doesn't include `subprocess` yet, though the design allows it. Nothing hung.
2. `timeout 120 python3 -m unittest tests.test_server tests.test_data -v`: exit code 0, 34 tests passed in 5.1s. That covers all 11 tests in `tests/test_server.py`, including `MainModuleTest`, which starts and stops the server process cleanly.
