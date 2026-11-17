"""In-memory mock book data and pure lookup functions (no I/O)."""

from typing import Dict, List, Optional, Tuple, Union

Book = Dict[str, Union[int, str]]

BOOKS = (
    {"id": 1, "title": "Pride and Prejudice", "author": "Jane Austen", "year": 1813},
    {"id": 2, "title": "Emma", "author": "Jane Austen", "year": 1815},
    {"id": 3, "title": "Nineteen Eighty-Four", "author": "George Orwell", "year": 1949},
    {"id": 4, "title": "Animal Farm", "author": "George Orwell", "year": 1945},
    {"id": 5, "title": "One Hundred Years of Solitude",
     "author": "Gabriel Garc\u00eda M\u00e1rquez", "year": 1967},
    {"id": 6, "title": "Moby-Dick", "author": "Herman Melville", "year": 1851},
    {"id": 7, "title": "To Kill a Mockingbird", "author": "Harper Lee", "year": 1960},
    {"id": 8, "title": "The Great Gatsby", "author": "F. Scott Fitzgerald", "year": 1925},
    {"id": 9, "title": "Brave New World", "author": "Aldous Huxley", "year": 1932},
    {"id": 10, "title": "Frankenstein", "author": "Mary Shelley", "year": 1818},
)  # type: Tuple[Book, ...]


def _sorted_books() -> List[Book]:
    return sorted(BOOKS, key=lambda b: b["id"])


def list_books() -> List[Book]:
    """Return copies of all books, in id order."""
    return [dict(b) for b in _sorted_books()]


def get_book(book_id: int) -> Optional[Book]:
    """Return a copy of the book with the given id, or None."""
    for b in BOOKS:
        if b["id"] == book_id:
            return dict(b)
    return None


def search_by_author(name: str) -> List[Book]:
    """Return copies of books whose author contains name (case-insensitive), in id order.

    An empty name matches every book. The input is not stripped.
    """
    needle = name.casefold()
    return [dict(b) for b in _sorted_books() if needle in str(b["author"]).casefold()]
