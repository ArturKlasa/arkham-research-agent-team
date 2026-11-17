"""Mock dataset and data layer for the book API. No HTTP knowledge here."""

import copy

BOOKS = [
    {"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen",
     "year": 1813},
    {"id": 2, "title": "Emma", "author": "Jane Austen", "year": 1815},
    {"id": 3, "title": "Nineteen Eighty-Four", "author": "George Orwell",
     "year": 1949},
    {"id": 4, "title": "Animal Farm", "author": "George Orwell", "year": 1945},
    {"id": 5, "title": "Cien a\u00f1os de soledad",
     "author": "Gabriel Garc\u00eda M\u00e1rquez", "year": 1967},
]


class BookStore:
    """In-memory book store. All returned objects are deep copies."""

    def __init__(self, books=None):
        self._books = copy.deepcopy(books if books is not None else BOOKS)

    def list_books(self):
        """Return deep copies of all books, in dataset order."""
        return copy.deepcopy(self._books)

    def get_book(self, book_id):
        """Return a deep copy of the book with the given id, or None."""
        for book in self._books:
            if book["id"] == book_id:
                return copy.deepcopy(book)
        return None

    def books_by_author(self, name):
        """Return deep copies of books whose author name contains `name`.

        Case-insensitive substring match (str.casefold). No trimming, regex,
        fuzzy matching or accent folding. An empty name matches every book;
        no match returns [].
        """
        needle = name.casefold()
        return [copy.deepcopy(b) for b in self._books
                if needle in b["author"].casefold()]
