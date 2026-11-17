"""Static check: only standard-library (and project) imports are used."""

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Allowlist exactly as in design section 4.
ALLOWED = {
    "argparse", "ast", "copy", "http", "json", "os", "pathlib", "re",
    "subprocess", "sys", "threading", "unittest", "urllib",
    "bookapi", "tests",
}


def _python_files():
    for package in ("bookapi", "tests"):
        yield from sorted((ROOT / package).rglob("*.py"))


def _top_level_imports(path):
    """Yield (lineno, top-level module name) for each absolute import."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import: stays inside the project
            if node.module:
                yield node.lineno, node.module.split(".")[0]


class StdlibOnlyTest(unittest.TestCase):
    def test_source_files_found(self):
        names = {p.relative_to(ROOT).as_posix() for p in _python_files()}
        for expected in ("bookapi/server.py", "bookapi/data.py",
                         "tests/test_server.py", "tests/test_stdlib.py"):
            self.assertIn(expected, names)

    def test_all_imports_in_allowlist(self):
        offenders = []
        for path in _python_files():
            for lineno, name in _top_level_imports(path):
                if name not in ALLOWED:
                    offenders.append(
                        f"{path.relative_to(ROOT).as_posix()}:{lineno}: {name}")
        self.assertEqual(offenders, [],
                         "non-allowlisted imports: " + ", ".join(offenders))

    def test_no_requirements_file(self):
        self.assertFalse((ROOT / "requirements.txt").exists(),
                         "requirements.txt must not exist (stdlib only)")


if __name__ == "__main__":
    unittest.main()
