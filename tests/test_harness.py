"""Tests for the harness logic: delegation, task tracking, gating, auditing, re-running, resuming.

They run offline against the scripted team (harness/demo.py), which makes one
mistake of every kind on purpose.    python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from harness.agent import Tool, schema_errors
from harness.board import Board, Task
from harness.config import Config
from harness.demo import ScriptedTeam
from harness.events import Block
from harness.llm import AnthropicLLM, FatalError, OpenAICompatLLM, ToolCall, TransientError
from harness.orchestrator import Orchestrator
from harness.server import build
from harness.rules import _file_ownership
from harness.tools import Workspace, _CheckedRedirects, check_public, detect_sandbox, run_sandboxed

try:
    SANDBOX = detect_sandbox()
except RuntimeError:
    SANDBOX = "none"

TASK = "Build an API for consulting a mock user database"


class HarnessCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def team(self, session="s1", llm=None, faults=True, **settings) -> Orchestrator:
        cfg = Config(provider="scripted", runs=self.tmp / "runs", memory=self.tmp / "memory.jsonl", retry_base=0.0, sandbox=SANDBOX, **settings)
        return Orchestrator(cfg, session, llm or ScriptedTeam(faults))

    def events(self, orch, **match):
        return orch.engine.bus.read(**match)


class Pipeline(HarnessCase):
    def test_clean_run_goes_straight_through(self):
        orch = self.team(faults=False)
        report = orch.engine.start(TASK)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["audit_cycles_used"], 0)
        self.assertEqual([t["id"] for t in report["tasks"]], ["research", "plan", "prd", "spec", "impl:W1", "impl:W2", "audit"])
        self.assertTrue(all(t["runs"] == 1 for t in report["tasks"]))
        self.assertEqual(report["files"], ["api.py", "db.py", "test_api.py", "test_db.py"])
        self.assertEqual(self.events(orch, type="error"), [])
        self.assertIn("R2", orch.engine.show("artifacts/prd"))

    def test_every_failure_is_recovered_at_the_cheapest_level(self):
        orch = self.team()
        report = orch.engine.start(TASK)
        runs = {t["id"]: t["runs"] for t in report["tasks"]}
        self.assertEqual(report["status"], "completed")

        # 2. gate: a submission violating a checked rule is bounced and repaired in the same conversation (no extra run)
        rejected = [(e["task"], e["problems"][0][:14]) for e in self.events(orch, type="gate") if not e["passed"]]
        self.assertEqual(rejected, [("research", "[RES-GROUNDED]"), ("prd", "[PRD-IDS] requ")])
        self.assertEqual((runs["research"], runs["prd"]), (1, 1))

        # 3. crash: the task is retried with a fresh context, and the traceback is on the trail
        crash = self.events(orch, type="error", task="plan")[0]
        self.assertTrue(crash["will_retry"])
        self.assertIn("simulated crash inside the planner", crash["traceback"])
        self.assertEqual(runs["plan"], 2)

        # transient provider error: retried inside the call, the task never notices
        self.assertEqual(len(self.events(orch, type="llm_retry", task="spec")), 1)
        self.assertEqual(runs["spec"], 1)

        # 4. audit: the failing suite re-opens W1 (and W2, which depends on it); the auditor's finding re-opens only W2
        self.assertEqual([e["tasks"] for e in self.events(orch, type="rerun")], [["impl:W1"], ["impl:W2"]])
        self.assertEqual((runs["impl:W1"], runs["impl:W2"], runs["audit"]), (2, 3, 3))
        self.assertIn("404", orch.engine.show("api.py"))
        self.assertEqual(report["audit"], {"passed": True, "findings": []})

        # every agent has its own trail
        for agent in ("researcher", "planner", "product", "architect", "engineer", "auditor"):
            self.assertTrue(self.events(orch, agent=agent), agent)

    def test_auditor_only_rejudges_what_changed(self):
        orch = self.team()
        orch.engine.start(TASK)
        verdicts = [e for e in self.events(orch, type="gate", agent="auditor") if e["passed"]]
        # sweep 1 stops at the failing suite (0 judgements), sweep 2 judges all 6 steps, sweep 3 only the redone W2
        self.assertEqual(len(verdicts), 6 + 1)

    def test_open_findings_are_reported_when_the_rerun_budget_runs_out(self):
        orch = self.team(max_audit_cycles=1)
        report = orch.engine.start(TASK)
        self.assertEqual(report["status"], "completed_with_open_findings")
        self.assertEqual([f["rule"] for f in report["audit"]["findings"]], ["IMPL-SPEC"])

    def test_a_task_that_keeps_failing_is_escalated_then_resumable(self):
        class BrokenPlanner(ScriptedTeam):
            def planner(self, messages, tools):
                raise RuntimeError("still broken")

        orch = self.team(llm=BrokenPlanner(faults=False))
        report = orch.engine.start(TASK)
        tasks = {t["id"]: t for t in report["tasks"]}
        self.assertEqual(report["status"], "failed")
        self.assertEqual((tasks["plan"]["status"], tasks["plan"]["runs"]), ("failed", 3))
        self.assertEqual(tasks["prd"]["status"], "pending")  # nothing downstream ran

        orch.engine.llm = ScriptedTeam(faults=False)  # the cause is fixed
        self.assertEqual(orch.engine.resume()["status"], "completed")

    def test_audit_findings_survive_an_interruption_while_they_are_being_acted_on(self):
        orch = self.team()

        def interrupt(event):
            raise KeyboardInterrupt

        orch.engine.bus.on(interrupt, "audit")  # Ctrl-C at the moment the first audit reports its finding
        self.assertRaises(KeyboardInterrupt, orch.engine.start, TASK)
        self.assertEqual(self.team().engine.board.tasks["audit"].status, "pending")  # on disk: not "done" with findings unacted on

        orch.engine.bus.off(interrupt)
        report = orch.engine.resume()
        self.assertEqual((report["status"], report["audit_cycles_used"]), ("completed", 2))

    def test_a_submit_is_judged_after_the_other_calls_of_the_same_turn(self):
        class WritesAndSubmitsAtOnce(ScriptedTeam):
            def engineer(self, messages, tools):
                reply = super().engineer(messages, tools)  # the write_file calls
                reply.tool_calls.insert(0, self.call("submit", summary="done", tests="none").tool_calls[0])
                return reply

        orch = self.team(llm=WritesAndSubmitsAtOnce(faults=False))
        report = orch.engine.start(TASK)
        self.assertEqual((report["status"], report["audit_cycles_used"]), ("completed", 0))
        self.assertEqual([e for e in self.events(orch, type="gate") if not e["passed"]], [])

    def test_change_request_redoes_the_task_and_everything_downstream_only(self):
        orch = self.team(faults=False)
        orch.engine.start(TASK)
        orch.send("change prd Also expose the user's email", lambda text: None)
        runs = {t.id: t.runs for t in orch.engine.board.tasks.values()}
        self.assertEqual(runs, {"research": 1, "plan": 1, "prd": 2, "spec": 2, "impl:W1": 2, "impl:W2": 2, "audit": 2})
        self.assertEqual(orch.engine.report()["status"], "completed")
        self.assertRaises(ValueError, orch.engine.change, "nonsense", "x")

    def test_interrupted_run_resumes_without_redoing_finished_work(self):
        class Interrupted(ScriptedTeam):
            def architect(self, messages, tools):
                raise KeyboardInterrupt

        self.assertRaises(KeyboardInterrupt, self.team(llm=Interrupted(faults=False)).engine.start, TASK)

        orch = self.team(faults=False)  # a new process on the same session directory
        statuses = {t.id: t.status for t in orch.engine.board.tasks.values()}
        self.assertEqual(statuses, {"research": "done", "plan": "done", "prd": "done", "spec": "pending", "audit": "pending"})
        report = orch.engine.resume()
        self.assertEqual(report["status"], "completed")
        self.assertEqual({t["id"]: t["runs"] for t in report["tasks"]}["research"], 1)


class MemoryAndHooks(HarnessCase):
    def test_lessons_from_one_session_prevent_the_same_mistakes_in_the_next(self):
        first = self.team("first")
        first.engine.start(TASK)
        second = self.team("second")  # fresh scripted team, same memory file
        report = second.engine.start(TASK)

        self.assertEqual([e for e in self.events(second, type="gate") if not e["passed"]], [])
        self.assertEqual((first.engine.report()["audit_cycles_used"], report["audit_cycles_used"]), (2, 0))
        handoff = second.engine.handoff(second.engine.board.tasks["prd"])
        self.assertIn("[PRD-IDS]", handoff)

    def test_question_answering_is_delegated_tracked_and_grounded(self):
        sources = self.tmp / "refs"
        sources.mkdir()
        (sources / "schema.md").write_text("Table users(id integer, name text).")
        orch = self.team(sources=sources)
        said = []
        orch.send("ask Which tables does the reference schema define?", said.append)

        asked = orch.engine.board.tasks["ask:1"]
        self.assertEqual((asked.agent, asked.status), ("researcher", "done"))
        self.assertEqual(asked.result["sources"], ["sources/schema.md"])
        self.assertIn("Table users", "".join(said))  # streamed back through the orchestrator

    def test_tool_failures_go_back_to_the_agent_and_experts_can_delegate_to_a_peer(self):
        class CuriousPlanner(ScriptedTeam):
            def planner(self, messages, tools):
                steps = [("read_file", {"path": "missing.txt"}), ("ask_researcher", {"question": "Is sqlite3 in the standard library?"})]
                done = len(self.results(messages))
                return self.call(steps[done][0], **steps[done][1]) if done < len(steps) else super().planner(messages, tools)

        orch = self.team(llm=CuriousPlanner(faults=False))
        self.assertEqual(orch.engine.start(TASK)["status"], "completed")

        failure = self.events(orch, type="error", task="plan")[0]  # rung 1: logged with traceback, plan not retried
        self.assertEqual(failure["where"], "tool:read_file")
        self.assertIn("FileNotFoundError", failure["traceback"])
        self.assertEqual(orch.engine.board.tasks["plan"].runs, 1)

        asked = orch.engine.board.tasks["ask:1"]  # the sub-delegation is a tracked task of its own
        self.assertEqual((asked.agent, asked.status, asked.meta["parent"]), ("researcher", "done", "plan"))
        self.assertTrue(self.events(orch, task="ask:1", agent="researcher"))

    def test_a_hook_can_veto_a_tool_and_the_failure_is_contained(self):
        orch = self.team(faults=False, max_turns=4)

        def no_writes(event):
            if event["tool"] == "write_file":
                raise Block("read-only mode")

        orch.engine.bus.on(no_writes, "pre_tool")
        report = orch.engine.start(TASK)
        failed = next(t for t in report["tasks"] if t["id"] == "impl:W1")
        self.assertEqual(report["status"], "failed")
        self.assertIn("turn budget", failed["error"])
        self.assertTrue(any("read-only mode" in e["output"] for e in self.events(orch, type="post_tool", tool="write_file")))


class Boundaries(HarnessCase):
    def test_board_invalidation_cascades_to_dependents_only(self):
        board = Board(self.tmp / "board.json")
        for task_id, deps in [("a", []), ("b", ["a"]), ("c", ["b"]), ("d", ["a"]), ("e", [])]:
            board.add(Task(task_id, "step", "x", deps, status="done", result={}))
        board.invalidate("b", ["fix it"])
        self.assertEqual({t.id: t.status for t in board.tasks.values()}, {"a": "done", "b": "pending", "c": "pending", "d": "done", "e": "done"})
        self.assertEqual(board.tasks["b"].feedback, ["fix it"])
        self.assertEqual(Board(self.tmp / "board.json").tasks["c"].status, "pending")  # persisted

    def test_file_tools_cannot_leave_the_workspace(self):
        (self.tmp / "refs").mkdir()
        ws = Workspace(self.tmp / "ws", self.tmp / "refs")
        (self.tmp / "ws" / "escape").symlink_to("/etc")
        for path in ("../outside.txt", "/etc/passwd", "escape/passwd", "sources/../../x"):
            self.assertRaises(PermissionError, ws.resolve, path)
        self.assertRaises(PermissionError, ws.resolve, "sources/new.txt", True)
        self.assertEqual(ws.resolve("pkg/mod.py"), (self.tmp / "ws" / "pkg" / "mod.py").resolve())

    def test_web_fetch_refuses_local_addresses(self):
        for url in ("http://127.0.0.1:8000/", "http://localhost/", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
            self.assertRaises(PermissionError, check_public, url)
        request = urllib.request.Request("https://example.com")  # a public page redirecting inward is refused too
        self.assertRaises(PermissionError, _CheckedRedirects().redirect_request, request, None, 302, "Found", {}, "http://127.0.0.1/admin")

    def test_spec_paths_must_be_plain_so_the_write_tool_and_the_rules_agree(self):
        spec = lambda *files: {"work_items": [{"id": "W1", "files": list(files), "covers": [], "depends_on": []}]}
        self.assertEqual(_file_ownership(spec("pkg/api.py", "tests/test_api.py"), None, None), [])
        for odd in ("./api.py", "pkg//api.py", "pkg/", "../api.py", "/abs/api.py"):
            self.assertTrue(_file_ownership(spec(odd, "test_api.py"), None, None), odd)

    def test_a_torn_line_in_memory_or_trail_does_not_stop_a_session(self):
        orch = self.team(faults=False)
        orch.engine.memory.lesson("product", "PRD-IDS", "ids are unique", "s0")
        for path in (orch.cfg.memory, orch.engine.bus.trail):
            with open(path, "a") as f:
                f.write('{"kind": "lesson", "ro')
        again = self.team(faults=False)
        self.assertEqual(len(again.engine.memory.items), 1)
        self.assertEqual(again.engine.start(TASK)["status"], "completed")
        self.assertTrue(again.engine.bus.read(type="task_done"))

    def test_submissions_are_validated_against_their_schema(self):
        schema = {"type": "object", "required": ["id", "tags"], "properties": {"id": {"type": "string", "minLength": 1}, "tags": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}}}
        self.assertEqual(schema_errors({"id": "x", "tags": ["a"]}, schema), [])
        self.assertEqual(len(schema_errors({"id": "", "tags": ["a", "z", 3]}, schema)), 3)
        self.assertEqual(schema_errors("not json", schema), ["$: expected object, got str"])
        self.assertEqual(schema_errors({"tags": []}, schema), ["$.id: required field is missing"])

    @unittest.skipIf(SANDBOX == "none", "no sandbox available on this machine")
    def test_sandboxed_commands_have_no_network_no_secrets_and_no_host_files(self):
        cfg = Config(sandbox=SANDBOX)
        probe = "import os, socket; print(sorted(os.environ)); print(os.path.exists(os.path.expanduser('~/.ssh'))); socket.create_connection(('1.1.1.1', 53), 3)"
        (self.tmp / "probe.py").write_text(probe)
        code, output = run_sandboxed(cfg, self.tmp, "python3 probe.py")
        self.assertNotEqual(code, 0)  # the connection attempt fails
        self.assertNotIn("KEY", output.splitlines()[0])
        self.assertEqual(output.splitlines()[1], "False")


class OpenAICompatible(HarnessCase):
    """The open-source / local model path, checked against a fake /chat/completions server."""

    def serve(self, status=200):
        chunks = [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_file", "arguments": '{"pa'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th": "a.py"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 5}},
        ]
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(status)
                self.end_headers()
                for chunk in chunks if status == 200 else []:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return OpenAICompatLLM(Config(provider="openai", model="any", base_url=f"http://127.0.0.1:{server.server_port}/v1")), seen

    def test_streams_text_and_assembles_tool_calls(self):
        llm, seen = self.serve()
        tool = Tool("read_file", "Read a file.", {"type": "object", "properties": {"path": {"type": "string"}}})
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "", "tool_calls": []}, {"role": "tool", "id": "c0", "content": "ok"}]
        streamed = []
        reply = llm.chat("be brief", history, [tool], streamed.append)
        self.assertEqual((streamed, reply.text, reply.stop, reply.usage), (["Hel", "lo"], "Hello", "tool_use", {"in": 7, "out": 5}))
        self.assertEqual([(c.id, c.name, c.args) for c in reply.tool_calls], [("call_1", "read_file", {"path": "a.py"})])
        self.assertEqual([m["role"] for m in seen["body"]["messages"]], ["system", "user", "assistant", "tool"])
        self.assertEqual(seen["body"]["tools"][0]["function"]["name"], "read_file")

    def test_replayed_history_is_always_acceptable_to_the_server(self):
        empty_turn = {"role": "assistant", "content": "", "tool_calls": []}
        cut_off_call = {"role": "assistant", "content": "", "tool_calls": [ToolCall("c1", "write_file", '{"path": "a.py", "cont')]}
        sent = OpenAICompatLLM._messages([empty_turn, cut_off_call])
        self.assertEqual(sent[0], {"role": "assistant", "content": ""})  # never null content without tool calls
        self.assertEqual(sent[1]["tool_calls"][0]["function"]["arguments"], "{}")  # never invalid JSON

    def test_errors_are_classified_for_the_retry_logic(self):
        self.assertRaises(TransientError, self.serve(status=503)[0].chat, "s", [], [])
        self.assertRaises(FatalError, self.serve(status=401)[0].chat, "s", [], [])


@unittest.skipUnless(importlib.util.find_spec("anthropic"), "anthropic SDK not installed")
class ClaudeBackend(HarnessCase):
    """The default provider, checked through the real SDK against a fake Messages API (no key, no network)."""

    EVENTS = [
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"path": "a'}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '.py"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 15}},
        {"type": "message_stop"},
    ]

    def setUp(self):
        super().setUp()
        events, self.requests = self.EVENTS, []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                status = {"overloaded": 529, "invalid": 400}.get(body["system"], 200)
                self.send_response(status)
                self.send_header("content-type", "text/event-stream" if status == 200 else "application/json")
                self.end_headers()
                error = {"type": "error", "error": {"type": "overloaded_error", "message": body["system"]}}
                if status != 200:
                    return self.wfile.write(json.dumps(error).encode())
                for event in events[:5] + [error] if body["system"] == "overloaded mid-stream" else events:
                    self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        patch = unittest.mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}"})
        patch.start()
        self.addCleanup(patch.stop)
        self.llm = AnthropicLLM(Config())
        self.tool = Tool("read_file", "Read a file.", {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})

    def test_streams_and_replays_the_whole_assistant_turn(self):
        streamed = []
        reply = self.llm.chat("sys", [{"role": "user", "content": "hi"}], [self.tool], streamed.append)
        self.assertEqual((streamed, reply.text, reply.stop, reply.usage), (["Hello"], "Hello", "tool_use", {"in": 10, "out": 15}))
        self.assertEqual([(c.id, c.name, c.args) for c in reply.tool_calls], [("toolu_1", "read_file", {"path": "a.py"})])

        history = [{"role": "user", "content": "hi"}, reply.as_message(), {"role": "tool", "id": "toolu_1", "content": "body", "is_error": False}]
        self.llm.chat("sys", history, [self.tool])
        sent = self.requests[-1]["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user"])
        self.assertEqual([b["type"] for b in sent[1]["content"]], ["thinking", "text", "tool_use"])  # thinking goes back unchanged
        self.assertEqual(sent[2]["content"][0]["tool_use_id"], "toolu_1")

    def test_errors_are_classified_for_the_retry_logic(self):
        self.assertRaises(TransientError, self.llm.chat, "overloaded", [{"role": "user", "content": "hi"}], [])
        self.assertRaises(TransientError, self.llm.chat, "overloaded mid-stream", [{"role": "user", "content": "hi"}], [])
        self.assertRaises(FatalError, self.llm.chat, "invalid", [{"role": "user", "content": "hi"}], [])


class Gateway(HarnessCase):
    def test_remote_chat_requires_the_token_and_streams_events(self):
        orch = self.team(faults=False)
        server = build(orch, "127.0.0.1", 0, "secret")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"

        def post(path, token, body=b"{}"):
            request = urllib.request.Request(url + path, body, {"Authorization": f"Bearer {token}"})
            return urllib.request.urlopen(request, timeout=60).read().decode()

        with self.assertRaises(urllib.error.HTTPError) as denied:
            post("/chat", "wrong")
        self.assertEqual(denied.exception.code, 401)

        frames = [json.loads(line[6:]) for line in post("/chat", "secret", json.dumps({"message": TASK}).encode()).split("\n\n") if line]
        self.assertIn("▶ research · researcher (attempt 1)", [f["text"] for f in frames if f["type"] == "event"])
        self.assertIn("Status: completed.", "".join(f["text"] for f in frames if f["type"] == "text"))

        status = urllib.request.urlopen(urllib.request.Request(url + "/status", headers={"Authorization": "Bearer secret"})).read()
        self.assertEqual(json.loads(status)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
