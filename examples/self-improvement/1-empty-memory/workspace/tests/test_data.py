"""Pure data-layer tests for books.data."""

import unittest

from books import data
from books.data import BOOKS, DEFAULT_REPOSITORY, BookRepository


def ids(books):
    return [book["id"] for book in books]


class DatasetTests(unittest.TestCase):
    def test_size(self):
        self.assertGreaterEqual(len(BOOKS), 5)
        self.assertLessEqual(len(BOOKS), 10)

    def test_schema(self):
        expected = {"id": int, "title": str, "author": str, "year": int}
        for book in BOOKS:
            with self.subTest(book=book):
                self.assertEqual(set(book), set(expected))
                for key, typ in expected.items():
                    self.assertIsInstance(book[key], typ)
                self.assertNotIsInstance(book["id"], bool)
                self.assertNotIsInstance(book["year"], bool)

    def test_unique_ids(self):
        self.assertEqual(len(set(ids(BOOKS))), len(BOOKS))


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = BookRepository()

    def test_all(self):
        self.assertEqual(self.repo.all(), BOOKS)

    def test_get_existing(self):
        book = self.repo.get(1)
        self.assertIsNotNone(book)
        self.assertEqual(book["title"], "The Hobbit")
        self.assertEqual(book, BOOKS[0])

    def test_get_missing(self):
        self.assertIsNone(self.repo.get(99999))

    def test_get_non_int(self):
        self.assertIsNone(self.repo.get("1"))
        self.assertIsNone(self.repo.get(True))
        self.assertIsNone(self.repo.get(1.0))
        self.assertIsNone(self.repo.get(None))

    def test_search_tolkien(self):
        for name in ("J.R.R. Tolkien", "TOLK", "tolk"):
            with self.subTest(name=name):
                self.assertEqual(ids(self.repo.search_by_author(name)), [1, 2])

    def test_search_austen(self):
        self.assertEqual(ids(self.repo.search_by_author("austen")), [3, 4])

    def test_search_no_match(self):
        self.assertEqual(self.repo.search_by_author("nobody"), [])

    def test_search_empty_matches_all(self):
        self.assertEqual(self.repo.search_by_author(""), BOOKS)

    def test_mutation_isolated(self):
        self.repo.get(1)["title"] = "Changed"
        self.repo.all()[0]["title"] = "Changed"
        self.repo.search_by_author("tolk")[0]["title"] = "Changed"
        self.assertEqual(self.repo.get(1)["title"], "The Hobbit")
        self.assertEqual(BOOKS[0]["title"], "The Hobbit")

    def test_constructor_copies_input(self):
        source = [{"id": 7, "title": "T", "author": "A", "year": 2000}]
        repo = BookRepository(source)
        source[0]["title"] = "Mutated"
        self.assertEqual(repo.get(7)["title"], "T")

    def test_custom_repository(self):
        custom = [{"id": 42, "title": "Custom", "author": "Some Writer", "year": 2020}]
        repo = BookRepository(custom)
        self.assertEqual(repo.all(), custom)
        self.assertEqual(repo.get(42), custom[0])
        self.assertIsNone(repo.get(1))
        self.assertEqual(repo.search_by_author("tolk"), [])
        self.assertEqual(ids(repo.search_by_author("writer")), [42])

    def test_empty_repository(self):
        repo = BookRepository([])
        self.assertEqual(repo.all(), [])
        self.assertIsNone(repo.get(1))


class ModuleFunctionTests(unittest.TestCase):
    def test_match_default_repository(self):
        self.assertEqual(data.all(), DEFAULT_REPOSITORY.all())
        self.assertEqual(data.all(), BOOKS)
        self.assertEqual(data.get(1), DEFAULT_REPOSITORY.get(1))
        self.assertIsNone(data.get(99999))
        self.assertEqual(data.search_by_author("tolk"),
                         DEFAULT_REPOSITORY.search_by_author("tolk"))
        self.assertEqual(ids(data.search_by_author("TOLK")), [1, 2])

    def test_module_mutation_isolated(self):
        data.get(2)["author"] = "X"
        self.assertEqual(data.get(2)["author"], "J.R.R. Tolkien")


if __name__ == "__main__":
    unittest.main()
