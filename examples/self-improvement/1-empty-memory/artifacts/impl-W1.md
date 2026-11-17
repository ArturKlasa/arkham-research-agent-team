# impl:W1 (engineer)

## summary

I built work item W1: the mock book dataset, the data-access layer and its unit tests. All 17 tests pass.

Files:
- **`books/__init__.py`**: a docstring only.
- **`tests/__init__.py`**: empty.
- **`books/data.py`**: no imports.
  - `BOOKS`: the 6 books from the design, ids 1–6, in the specified order.
  - `BookRepository`: copies the books it's given when created (the built-in dataset if none are given) and stores those copies. `all()`, `get()` and `search_by_author()` all return copies in dataset order.
    - `get()` returns `None` for a missing id, and also for any id that isn't an int, including `True`/`False`.
    - `search_by_author()` matches any part of the author name, ignoring case. An empty name matches every book.
  - `DEFAULT_REPOSITORY`, plus module-level `all()`, `get()` and `search_by_author()` that call it.
- **`tests/test_data.py`**: covers everything the work item lists:
  - The dataset has 5–10 books, each with exactly the four keys and the right types (an id can't be a bool), and the ids are unique.
  - `all()` equals `BOOKS` in order.
  - `get()`: id 1 returns The Hobbit; 99999, `'1'` and `True` return `None`.
  - Search: `'J.R.R. Tolkien'`, `'TOLK'` and `'tolk'` each return books [1, 2]; `'austen'` returns [3, 4]; `'nobody'` returns []; `''` returns all books.
  - Changing a returned book doesn't affect later results.
  - A repository built from custom books uses only those books.
  - The module-level functions return the same results as `DEFAULT_REPOSITORY`.

I added three small tests beyond the list: a repository keeps its own copy of the books it's given, an empty repository returns nothing, and `get()` also rejects `1.0` and `None`.

## tests

I ran `python3 -m unittest -v` from the repo root. It exited with code 0: 17 tests ran and all passed (OK).
