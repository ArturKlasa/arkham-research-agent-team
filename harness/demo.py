"""A scripted stand-in for the model (`--provider scripted`).

It plays every agent for one canned task, an HTTP API over a mock user
database, and deliberately makes one mistake of each kind so that every rung of
the engine's failure ladder can be watched (and tested) offline, with no API
key and the same result every time:

    researcher  cites a source it never opened    -> gate rejects, repaired in the same conversation
    planner     crashes on its first attempt      -> task retried with a fresh context
    product     submits a duplicate requirement   -> gate rejects, repaired in the same conversation
    architect   first model call fails            -> transient error, retried with backoff
    engineer W1 ships a bug                       -> the harness-run test suite fails, audit re-opens W1
    engineer W2 omits the 404 case                -> the auditor agent finds it, audit re-opens W2

The rule-related mistakes are skipped when the matching lesson is already in the
agent's handoff, so a second session (same memory file) visibly needs fewer re-runs.
"""

from __future__ import annotations

import json
import re
import uuid

from .llm import Reply, ToolCall, TransientError

DB = '''import sqlite3


def connect():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("create table users (id integer primary key, name text)")
    db.executemany("insert into users values (?, ?)", [(1, "Ada"), (2, "Linus")])
    return db


def get_user(db, user_id):
    row = db.execute("select id, name from users where id = ?", (user_id,)).fetchone()
    return {"id": row[0], "name": row[1]} if row else None
'''

TEST_DB = '''import unittest

import db


class GetUser(unittest.TestCase):
    def test_found(self):
        self.assertEqual(db.get_user(db.connect(), 1), {"id": 1, "name": "Ada"})

    def test_missing(self):
        self.assertIsNone(db.get_user(db.connect(), 99))
'''

API = '''import json
from http.server import BaseHTTPRequestHandler, HTTPServer

import db

DB = db.connect()


def lookup(path):
    """Answer GET /users/<id> as (status, body)."""
    try:
        user = db.get_user(DB, int(path.rsplit("/", 1)[-1]))
    except ValueError:
        return 400, {"error": "id must be an integer"}
    if user is None:
        return 404, {"error": "user not found"}
    return 200, user


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, body = lookup(self.path)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
'''

TEST_API = '''import unittest

import api


class Lookup(unittest.TestCase):
    def test_found(self):
        self.assertEqual(api.lookup("/users/1")[0], 200)

    def test_missing(self):
        self.assertEqual(api.lookup("/users/99")[0], 404)
'''

# The flawed first drafts.
DB_BUG = DB.replace("(user_id,)", "(user_id + 1,)")
API_NO_404 = API.replace('    if user is None:\n        return 404, {"error": "user not found"}\n', "")
TEST_API_NO_404 = TEST_API[: TEST_API.index("    def test_missing")].rstrip() + "\n"

REQUIREMENTS = [
    {
        "id": "R1",
        "text": "Look up a user by id in the mock database.",
        "acceptance": "get_user returns the stored record for id 1 and None for an unknown id.",
    },
    {
        "id": "R2",
        "text": "Serve GET /users/<id> as JSON.",
        "acceptance": "A known id answers 200 with the user; an unknown id answers 404.",
    },
]
SPEC = {
    "design": (
        "db.py owns an in-memory SQLite database seeded with two users and exposes get_user(db, id). "
        "api.py maps GET /users/<id> to (status, JSON body) via lookup(path) and serves it with http.server."
    ),
    "work_items": [
        {
            "id": "W1",
            "title": "Mock database",
            "description": "connect() and get_user(db, user_id).",
            "files": ["db.py", "test_db.py"],
            "covers": ["R1"],
            "depends_on": [],
        },
        {
            "id": "W2",
            "title": "HTTP API",
            "description": "lookup(path) and the request handler.",
            "files": ["api.py", "test_api.py"],
            "covers": ["R2"],
            "depends_on": ["W1"],
        },
    ],
    "test_command": "python3 -m unittest -v",
    "run_command": "python3 api.py",
}


def unwrap(result: str) -> str:
    """A tool result without the <untrusted> fence the agent loop puts around it."""
    return re.sub(r'\A<untrusted tool="\w+">\n|\n</untrusted>\Z', "", result)


class ScriptedTeam:
    def __init__(self, faults: bool = True):
        self.faults = faults
        self.fired: set[str] = set()

    def chat(self, system, messages, tools, on_text=None) -> Reply:
        role = re.match(r"You are the (\w+)", system).group(1)
        reply = getattr(self, role)(messages, {t.name: t for t in tools})
        if reply.text and on_text:
            for word in re.findall(r"\S+\s*", reply.text):  # stream word by word, like a real model
                on_text(word)
        return reply

    # --- helpers ---

    def once(self, fault: str) -> bool:
        """True the first time a fault is asked for, while faults are enabled."""
        if not self.faults or fault in self.fired:
            return False
        self.fired.add(fault)
        return True

    @staticmethod
    def call(name, **args) -> Reply:
        return Reply(tool_calls=[ToolCall(uuid.uuid4().hex[:8], name, args)], stop="tool_use")

    @staticmethod
    def results(messages) -> list[str]:
        return [unwrap(m["content"]) for m in messages if m["role"] == "tool"]

    # --- the user-facing agent ---

    def orchestrator(self, messages, tools) -> Reply:
        last = messages[-1]
        if last["role"] == "tool":
            result = unwrap(last["content"])
            try:
                report = json.loads(result)
            except ValueError:
                return Reply(text=result)
            if not isinstance(report, dict) or "tasks" not in report:
                return Reply(text=result)
            done = sum(t["status"] == "done" for t in report["tasks"])
            return Reply(
                text=(
                    f"Status: {report['status']}. {done}/{len(report['tasks'])} tasks done, "
                    f"{report['audit_cycles_used']} audit re-run round(s) since the last change. "
                    f"Files: {', '.join(report['files']) or 'none'} in {report['workspace']}. "
                    "Use /show to inspect them, or tell me what to change."
                )
            )
        text = last["content"]
        if text.lower().startswith("ask ") or text.rstrip().endswith("?"):
            return self.call(
                "ask_expert", expert="researcher", question=text[4:] if text.lower().startswith("ask ") else text
            )
        if text.lower().startswith("change "):
            _, task_id, feedback = text.split(" ", 2)
            return self.call("request_changes", task_id=task_id, feedback=feedback)
        if text.lower() in ("resume", "approve", "status"):
            return self.call(text.lower())
        reply = self.call("start_project", task=text)
        reply.text = (
            "Scripted team: whatever the request, it builds its one canned task (a user-lookup API over a mock "
            "database) to show every recovery path.\n"
        )
        return reply

    # --- the experts ---

    def researcher(self, messages, tools) -> Reply:
        results = self.results(messages)
        if not results:
            return self.call("list_files", path=".")
        reference = next((line for line in results[0].splitlines() if line.startswith("sources/")), None)
        if reference and len(results) == 1:
            return self.call("read_file", path=reference)
        sources = [reference] if reference else []
        if "answer" in tools["submit"].schema["properties"]:  # an ad-hoc question
            found = results[1][:300] if reference else "nothing relevant was found."
            return self.call("submit", answer="From the reference files: " + found, sources=sources)
        warned = "RES-GROUNDED" in messages[0]["content"] or any("rejected" in r for r in results)
        if not warned and self.once("researcher-ungrounded"):
            sources = sources + ["https://example.com/never-opened"]
        findings = [{"claim": "Reference material for the task.", "source": s} for s in sources]
        return self.call(
            "submit",
            summary="SQLite ships with Python (sqlite3) and http.server is enough for a small JSON API.",
            findings=findings,
            open_questions=[],
        )

    def planner(self, messages, tools) -> Reply:
        if self.once("planner-crash"):
            raise RuntimeError("simulated crash inside the planner")
        return self.call(
            "submit",
            goal="A minimal HTTP API to look up users in a mock database.",
            in_scope=["mock database with seed data", "GET /users/<id> endpoint"],
            out_of_scope=["authentication", "writes"],
            milestones=["database module", "HTTP API"],
            risks=["none significant"],
            assumptions=["standard library only"],
        )

    def product(self, messages, tools) -> Reply:
        warned = "PRD-IDS" in messages[0]["content"] or any("rejected" in r for r in self.results(messages))
        requirements = (
            REQUIREMENTS
            if warned or not self.once("product-duplicate-id")
            else [REQUIREMENTS[0], {**REQUIREMENTS[1], "id": "R1"}]
        )
        return self.call("submit", overview="A read-only user lookup API.", requirements=requirements)

    def architect(self, messages, tools) -> Reply:
        if self.once("architect-transient"):
            raise TransientError("simulated 529 overloaded")
        return self.call("submit", **SPEC)

    def engineer(self, messages, tools) -> Reply:
        if self.results(messages):
            return self.call("submit", summary="Implemented the work item.", tests="python3 -m unittest -v")
        handoff = messages[0]["content"]
        if re.search(r'# Your work item\n\{\s*"id": "W1"', handoff):
            files = {"db.py": DB if "IMPL-TESTS" in handoff or not self.faults else DB_BUG, "test_db.py": TEST_DB}
        elif "IMPL-SPEC" in handoff or not self.faults:
            files = {"api.py": API, "test_api.py": TEST_API}
        else:
            files = {"api.py": API_NO_404, "test_api.py": TEST_API_NO_404}
        calls = [
            ToolCall(uuid.uuid4().hex[:8], "write_file", {"path": path, "content": content})
            for path, content in files.items()
        ]
        return Reply(tool_calls=calls, stop="tool_use")

    def auditor(self, messages, tools) -> Reply:
        verdict = tools["submit"].schema["properties"]["verdicts"]["items"]["properties"]
        rules, step = verdict["rule"]["enum"], verdict["blame"]["enum"][0]
        results = self.results(messages)
        if "impl:W2" in messages[0]["content"] and not results:
            return self.call("read_file", path="api.py")
        missing_404 = bool(results) and "404" not in results[0]
        verdicts = [
            {
                "rule": r,
                "passed": False,
                "evidence": "api.py lookup() returns 200 with a null body for an unknown id; R2 requires 404.",
                "fix": "Return 404 when the user is not found, and test it.",
                "blame": step,
            }
            if r == "IMPL-SPEC" and missing_404
            else {
                "rule": r, "passed": True, "evidence": "Checked against the upstream result.", "fix": "", "blame": step
            }
            for r in rules
        ]
        return self.call("submit", verdicts=verdicts)
