# impl:W4 (engineer)

## summary

I fixed the error the audit found in README.md: the Author search section no longer claims that an unencoded space works. The full test suite still passes.

- **The fix:** the list of examples that return the two Jane Austen books is now `author=jane%20austen`, `author=JANE+AUSTEN` and `author=Jane%20Austen`. I removed `author=Jane Austen` from it.
- **New note:** spaces in the query must be written as `%20` or `+`. With a plain space (for example `author=Jane Austen`), the built-in server rejects the request with an HTTP 400 HTML page, not JSON, before it reaches the API.
- **Unchanged:** the rest of the README (run and test commands, endpoints table, book fields and types, error body, the rest of the search rule, demo-only note) and tests/test_stdlib.py. The audit found tests/test_stdlib.py meets the design.

I still haven't added the id to the 404 body, as an earlier user note asked. The requirements and design say the body must be exactly `{"error": "not found"}`, and that is what the README documents.

## tests

From the project root I ran `python3 -m unittest`. All 37 tests passed in about 5s without hanging ("Ran 37 tests … OK"), including the 3 in tests/test_stdlib.py. The `exit=0` that printed came from `tail` in my pipe, not from the test run, so the "OK" line is what shows it passed. I also searched the README with grep to confirm the unencoded form now appears only in the warning note.
