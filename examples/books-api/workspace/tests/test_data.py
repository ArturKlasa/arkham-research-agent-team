import unittest

from bookapi import BOOKS as PKG_BOOKS, BookStore as PkgBookStore
from bookapi.data import BOOKS, BookStore

EXPECTED_KEYS = {"id", "title", "author", "year"}
EXPECTED_TYPES = {"id": int, "title": str, "author": str, "year": int}
ALL_IDS = [1, 2, 3, 4, 5]


def ids(books):
    return [b["id"] for b in books]


class DatasetTests(unittest.TestCase):
    def test_schema_and_types(self):
        for book in BOOKS:
            self.assertEqual(set(book.keys()), EXPECTED_KEYS, book)
            for key, typ in EXPECTED_TYPES.items():
                self.assertIs(type(book[key]), typ, (book, key))

    def test_no_author_id(self):
        for book in BOOKS:
            self.assertNotIn("author_id", book)

    def test_at_least_three_books(self):
        self.assertGreaterEqual(len(BOOKS), 3)

    def test_shared_author(self):
        authors = [b["author"] for b in BOOKS]
        self.assertTrue(any(authors.count(a) >= 2 for a in authors))

    def test_author_name_with_space(self):
        self.assertTrue(any(" " in b["author"] for b in BOOKS))

    def test_unique_ids(self):
        book_ids = ids(BOOKS)
        self.assertEqual(len(book_ids), len(set(book_ids)))

    def test_exact_dataset_ids(self):
        self.assertEqual(ids(BOOKS), ALL_IDS)

    def test_package_reexports(self):
        self.assertIs(PKG_BOOKS, BOOKS)
        self.assertIs(PkgBookStore, BookStore)


class BookStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = BookStore()

    def test_list_books_returns_all(self):
        self.assertEqual(self.store.list_books(), BOOKS)

    def test_get_book_hit(self):
        book = self.store.get_book(1)
        self.assertIsNotNone(book)
        self.assertEqual(book, BOOKS[0])

    def test_get_book_miss(self):
        self.assertIsNone(self.store.get_book(99999))

    def test_books_by_author_hit(self):
        self.assertEqual(ids(self.store.books_by_author("Jane Austen")), [1, 2])

    def test_books_by_author_case_insensitive(self):
        for q in ("jane austen", "JANE AUSTEN"):
            with self.subTest(q=q):
                self.assertEqual(ids(self.store.books_by_author(q)), [1, 2])

    def test_books_by_author_partial(self):
        self.assertEqual(ids(self.store.books_by_author("orwell")), [3, 4])

    def test_books_by_author_non_ascii(self):
        for q in ("Garc\u00eda", "garc\u00eda"):
            with self.subTest(q=q):
                self.assertEqual(ids(self.store.books_by_author(q)), [5])

    def test_books_by_author_miss(self):
        self.assertEqual(self.store.books_by_author("Tolkien"), [])

    def test_books_by_author_empty_matches_all(self):
        self.assertEqual(self.store.books_by_author(""), BOOKS)

    def test_mutating_list_result_does_not_change_store(self):
        books = self.store.list_books()
        books[0]["title"] = "changed"
        books.append({"id": 100})
        self.assertEqual(self.store.list_books(), BOOKS)

    def test_mutating_get_result_does_not_change_store(self):
        book = self.store.get_book(1)
        book["title"] = "changed"
        self.assertEqual(self.store.get_book(1)["title"], "Pride and Prejudice")

    def test_mutating_filter_result_does_not_change_store(self):
        books = self.store.books_by_author("Orwell")
        books[0]["author"] = "changed"
        books.clear()
        self.assertEqual(self.store.books_by_author("Orwell")[0]["author"],
                         "George Orwell")

    def test_store_isolated_from_source_dataset(self):
        source = [{"id": 1, "title": "T", "author": "A B", "year": 2000}]
        store = BookStore(source)
        source[0]["title"] = "changed"
        source.append({"id": 2, "title": "U", "author": "C", "year": 2001})
        self.assertEqual(store.list_books(),
                         [{"id": 1, "title": "T", "author": "A B", "year": 2000}])

    def test_custom_dataset(self):
        custom = [
            {"id": 10, "title": "X", "author": "Ann Lee", "year": 2001},
            {"id": 11, "title": "Y", "author": "Ann Lee", "year": 2002},
            {"id": 12, "title": "Z", "author": "Bob Ray", "year": 2003},
        ]
        store = BookStore(custom)
        self.assertEqual(store.list_books(), custom)
        self.assertEqual(store.get_book(10)["title"], "X")
        self.assertIsNone(store.get_book(1))
        self.assertEqual(ids(store.books_by_author("ann")), [10, 11])
        self.assertEqual(store.books_by_author("Austen"), [])

    def test_empty_custom_dataset(self):
        store = BookStore([])
        self.assertEqual(store.list_books(), [])
        self.assertIsNone(store.get_book(1))
        self.assertEqual(store.books_by_author(""), [])


if __name__ == "__main__":
    unittest.main()
