"""Unit tests for bookapi.data plus a static stdlib-only import check."""

import ast
import os
import unittest

from bookapi import data
from bookapi.data import BOOKS, get_book, list_books, search_by_author

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)

# Single source of truth: copied verbatim from the design's IMPORT ALLOWLIST.
ALLOWED_TOP_LEVEL = {"__future__", "argparse", "ast", "contextlib", "copy", "http", "io", "json", "os", "re", "sys", "threading", "typing", "unittest", "urllib", "bookapi", "tests"}


class DatasetShapeTests(unittest.TestCase):
    def test_record_count(self):
        self.assertGreaterEqual(len(BOOKS), 8)
        self.assertLessEqual(len(BOOKS), 10)

    def test_fields_and_types(self):
        for book in BOOKS:
            with self.subTest(book=book):
                self.assertEqual(set(book), {"id", "title", "author", "year"})
                self.assertIs(type(book["id"]), int)
                self.assertIs(type(book["year"]), int)
                self.assertIsInstance(book["title"], str)
                self.assertIsInstance(book["author"], str)

    def test_unique_ids(self):
        ids = [b["id"] for b in BOOKS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_has_duplicate_author_and_non_ascii_author(self):
        authors = [b["author"] for b in BOOKS]
        self.assertTrue(any(authors.count(a) >= 2 for a in authors))
        self.assertTrue(any(any(ord(c) > 127 for c in a) for a in authors))


class ListBooksTests(unittest.TestCase):
    def test_equals_dataset(self):
        self.assertEqual(list_books(), list(BOOKS))

    def test_id_order(self):
        ids = [b["id"] for b in list_books()]
        self.assertEqual(ids, sorted(ids))

    def test_mutation_does_not_affect_dataset(self):
        original = [dict(b) for b in BOOKS]
        result = list_books()
        result[0]["title"] = "MUTATED"
        result.append({"id": 999})
        self.assertEqual(list(BOOKS), original)
        self.assertEqual(list_books(), original)


class GetBookTests(unittest.TestCase):
    def test_hit(self):
        self.assertEqual(get_book(1), BOOKS[0])

    def test_miss(self):
        self.assertIsNone(get_book(99999))

    def test_returns_copy(self):
        book = get_book(1)
        book["title"] = "MUTATED"
        self.assertNotEqual(get_book(1)["title"], "MUTATED")


class SearchByAuthorTests(unittest.TestCase):
    def expected(self, author):
        return [dict(b) for b in BOOKS if b["author"] == author]

    def test_exact_match(self):
        result = search_by_author("George Orwell")
        self.assertEqual(result, self.expected("George Orwell"))
        self.assertEqual(len(result), 2)

    def test_partial_case_insensitive(self):
        self.assertEqual(search_by_author("oRWeL"), search_by_author("George Orwell"))
        self.assertEqual(search_by_author("JANE AUSTEN"), self.expected("Jane Austen"))

    def test_miss_returns_empty_list(self):
        self.assertEqual(search_by_author("zzz"), [])

    def test_unicode_match(self):
        result = search_by_author("Garc\u00eda")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["author"], "Gabriel Garc\u00eda M\u00e1rquez")
        self.assertEqual(search_by_author("GARC\u00cdA"), result)

    def test_empty_name_matches_all(self):
        self.assertEqual(search_by_author(""), list_books())

    def test_returns_copies(self):
        result = search_by_author("Orwell")
        result[0]["author"] = "MUTATED"
        self.assertEqual(len(search_by_author("Orwell")), 2)
        self.assertTrue(all(b["author"] != "MUTATED" for b in data.BOOKS))


def _py_files():
    for top in (os.path.join(ROOT, "bookapi"), TESTS_DIR):
        for dirpath, _dirs, files in os.walk(top):
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)


def _imported_top_modules(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module.split(".")[0]


class StdlibOnlyImportTests(unittest.TestCase):
    def test_allowlist_is_exact(self):
        self.assertEqual(len(ALLOWED_TOP_LEVEL), 17)

    def test_only_allowlisted_imports(self):
        files = list(_py_files())
        self.assertTrue(files)
        for path in files:
            for mod in _imported_top_modules(path):
                with self.subTest(file=os.path.relpath(path, ROOT), module=mod):
                    self.assertIn(mod, ALLOWED_TOP_LEVEL)

    def test_no_dependency_file(self):
        for name in ("requirements.txt", "Pipfile", "poetry.lock"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, name)), name)


if __name__ == "__main__":
    unittest.main()
