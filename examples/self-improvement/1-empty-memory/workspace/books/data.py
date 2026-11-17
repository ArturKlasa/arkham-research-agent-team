"""Mock book dataset and data-access layer.

Each book is a dict with exactly the keys ``id`` (int), ``title`` (str),
``author`` (str) and ``year`` (int).
"""

BOOKS = [
    {"id": 1, "title": "The Hobbit", "author": "J.R.R. Tolkien", "year": 1937},
    {"id": 2, "title": "The Fellowship of the Ring", "author": "J.R.R. Tolkien", "year": 1954},
    {"id": 3, "title": "Pride and Prejudice", "author": "Jane Austen", "year": 1813},
    {"id": 4, "title": "Emma", "author": "Jane Austen", "year": 1815},
    {"id": 5, "title": "Nineteen Eighty-Four", "author": "George Orwell", "year": 1949},
    {"id": 6, "title": "Dune", "author": "Frank Herbert", "year": 1965},
]


class BookRepository:
    """Read-only access to a list of books. All results are shallow copies."""

    def __init__(self, books=None):
        source = BOOKS if books is None else books
        self._books = [dict(book) for book in source]

    def all(self):
        """Return copies of every book, in dataset order."""
        return [dict(book) for book in self._books]

    def get(self, book_id):
        """Return a copy of the book with ``book_id``, or None.

        Non-int ids (including bool) return None.
        """
        if not isinstance(book_id, int) or isinstance(book_id, bool):
            return None
        for book in self._books:
            if book["id"] == book_id:
                return dict(book)
        return None

    def search_by_author(self, name):
        """Return copies of books whose author contains ``name`` (case-insensitive)."""
        needle = name.casefold()
        return [dict(book) for book in self._books
                if needle in book["author"].casefold()]


DEFAULT_REPOSITORY = BookRepository()


def all():  # noqa: A001 - intentionally shadows the builtin within this module
    """Return all books from DEFAULT_REPOSITORY."""
    return DEFAULT_REPOSITORY.all()


def get(book_id):
    """Return one book from DEFAULT_REPOSITORY, or None."""
    return DEFAULT_REPOSITORY.get(book_id)


def search_by_author(name):
    """Search DEFAULT_REPOSITORY by author (case-insensitive substring)."""
    return DEFAULT_REPOSITORY.search_by_author(name)
