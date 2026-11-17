"""Project-level checks: stdlib-only imports, no dependency files, README content."""

import ast
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

ALLOWED_MODULES = {
    "json", "http", "urllib", "argparse", "threading", "unittest",
    "contextlib", "io", "os", "sys", "re", "ast", "pathlib", "typing",
    "books", "tests",
}

SCANNED_DIRS = ("books", "tests")
FORBIDDEN_FILES = ("requirements.txt", "setup.py", "Pipfile", "pyproject.toml")
README_SNIPPETS = (
    "python -m unittest",
    "python3 -m books",
    "/books/<id>",
    "/books?author=",
    "not production-grade",
)


def _python_files():
    files = []
    for name in SCANNED_DIRS:
        files.extend(sorted((REPO_ROOT / name).rglob("*.py")))
    return files


def _top_level_imports(path):
    """Yield (lineno, top-level module) for every absolute import in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import: local by definition
            if node.module:
                yield node.lineno, node.module.split(".")[0]


class StdlibOnlyImportsTest(unittest.TestCase):
    def test_python_files_found(self):
        files = _python_files()
        names = {p.relative_to(REPO_ROOT).as_posix() for p in files}
        self.assertIn("books/server.py", names)
        self.assertIn("books/data.py", names)
        self.assertIn("tests/test_project.py", names)

    def test_all_imports_are_allowed(self):
        violations = []
        for path in _python_files():
            for lineno, module in _top_level_imports(path):
                if module not in ALLOWED_MODULES:
                    violations.append("{}:{}: {}".format(
                        path.relative_to(REPO_ROOT).as_posix(), lineno, module))
        self.assertEqual(violations, [], "non-allow-listed imports found")


class NoDependencyFilesTest(unittest.TestCase):
    def test_no_dependency_files_at_root(self):
        for name in FORBIDDEN_FILES:
            with self.subTest(file=name):
                self.assertFalse((REPO_ROOT / name).exists(),
                                 "{} must not exist".format(name))


class ReadmeTest(unittest.TestCase):
    def test_readme_exists_and_documents_project(self):
        readme = REPO_ROOT / "README.md"
        self.assertTrue(readme.is_file(), "README.md is missing")
        text = readme.read_text(encoding="utf-8")
        for snippet in README_SNIPPETS:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)


if __name__ == "__main__":
    unittest.main()
