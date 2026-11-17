# impl:W1 (engineer)

## summary

I updated W1 to the revised plan (search by author name), but the full suite does not pass yet: 35 tests ran and 2 errored. Both errors are in the HTTP tests in tests/test_server.py (W3) and are caused by bookapi/server.py (W2). Neither is my file to change.

Why the 2 tests fail: bookapi/server.py still handles the old ?author_id= filter. It reads qs["author_id"] and calls store.books_by_author(int(v)). books_by_author now takes an author name, so the server crashes on that request and closes the connection without replying. The failing tests are test_filter_by_author_hit and test_filter_by_author_misses (the author_id=999 case); both also test the old author_id behaviour. W2 and W3 need to be redone to the revised spec: ?author=<name>, read with parse_qs(keep_blank_values=True) and passed straight to books_by_author.

What I changed:
- bookapi/data.py: imports only copy. BOOKS has the 5 records from the spec with exactly id, title, author and year; author_id is gone. books_by_author(name) matches part of the author's name, ignoring case (name.casefold() in author.casefold()). There is no trimming, regex, fuzzy matching or accent folding. An empty name matches every book and no match returns []. list_books, get_book and books_by_author return copies in dataset order, so callers can't change the store. Nothing in the file knows about HTTP.
- tests/test_data.py: rewritten with 23 tests covering design section 4:
  - every record has exactly the four keys with the right types (checked with type(x) is int, so True/False don't count as numbers), and no record has author_id
  - at least 3 books, an author shared by two books, an author name with a space, and unique ids
  - list_books() == BOOKS; get_book(1) returns book 1 and get_book(99999) returns None
  - name search, compared by ids: "Jane Austen" gives [1,2]; "jane austen" and "JANE AUSTEN" give [1,2]; "orwell" gives [3,4]; "García" and "garcía" give [5]; "Tolkien" gives []; "" gives all books
  - changing a returned list, book or search result, or the list passed to the constructor, leaves the store unchanged
  - a custom dataset is used, and an empty one works
  - the package re-exports BOOKS and BookStore
  I also removed the collections import, which isn't on the allowed import list.
- bookapi/__init__.py (re-exports BOOKS and BookStore, doesn't import the server) and tests/__init__.py (empty) were already right and are unchanged.

I still haven't added the id to the 404 body, as an earlier note asked. The spec requires the body to be exactly {"error": "not found"}.

## tests

Command: `python3 -m unittest -v` (workspace root). Outcome: Ran 35 tests, FAILED (errors=2).
- Passing: all 23 tests in tests.test_data and all 3 in tests.test_stdlib (`python3 -m unittest -v tests.test_data` gives "Ran 23 tests ... OK"; tests.test_stdlib gives "Ran 3 tests ... OK").
- Failing: tests.test_server.ServerTestCase.test_filter_by_author_hit and test_filter_by_author_misses (query='author_id=999'). Both raise http.client.RemoteDisconnected: the server crashes on the request because bookapi/server.py (W2) still calls books_by_author(int(author_id)). These files belong to W2 and W3 and need to be redone to the revised spec (?author=<name>).
