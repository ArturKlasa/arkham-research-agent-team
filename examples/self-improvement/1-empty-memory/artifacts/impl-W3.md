# impl:W3 (engineer)

## summary

I wrote README.md and tests/test_project.py. The full suite of 43 tests passes with `python3 -m unittest -v`. The README's main test command, `python -m unittest`, wasn't run because there is no `python` command in this sandbox.

**README.md** covers everything the work item asked for:
- a project summary and requirements (Python 3.7+, standard library only, nothing to install, no dependency files);
- the run command `python3 -m books --port 8000`, with `--host` (default 127.0.0.1) and `--port` (default 8000);
- the test commands `python -m unittest` and `python3 -m unittest -v`;
- the three endpoints (`GET /books`, `GET /books/<id>`, `GET /books?author=<name>`), each with curl examples and sample responses taken from the real dataset;
- the book schema (id int, title str, author str, year int);
- search rules: case-insensitive substring match, no matches gives 200 `[]`, an empty value matches every book, and names must be URL-encoded;
- the `{"error": ...}` format and every error case: 400 for a non-integer id, 404 for a missing book or unknown path, and 405 with `Allow: GET` for other methods. The error messages match what `books/server.py` actually returns;
- the note that `http.server` is not production-grade and is only meant for this mock API.

**tests/test_project.py** has 4 tests, with all paths resolved from the repo root via `pathlib.Path(__file__).resolve().parent.parent`:
1. It reads every .py file under books/ and tests/ and checks that each import's top-level module is on the allowed list. Relative imports are skipped, and any failure names the file, line and module. A separate check confirms the scan actually finds files, so it can't pass by finding nothing.
2. It checks that requirements.txt, setup.py, Pipfile and pyproject.toml don't exist at the repo root.
3. It checks that README.md exists and contains 'python -m unittest', 'python3 -m books', '/books/<id>', '/books?author=' and 'not production-grade'.

I didn't change any files from W1 or W2; their current imports already pass the allowed-list check.

## tests

Command (from the repo root): `python3 -m unittest -v`
Result: Ran 43 tests in 0.129s, OK (exit code 0). This includes the data, server and project tests.

Command: `python3 -m unittest tests.test_project -v`
Result: Ran 4 tests, OK. All four passed: the dependency-files check, the README check, the allowed-imports check and the check that files were found.

Not verified: `python -m unittest` (the command given in the README) wasn't run because the sandbox has no `python` command ("sh: 1: python: not found"). `python3` runs the same tests.
