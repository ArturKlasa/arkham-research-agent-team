"""The workflow engine: delegation, task tracking, auditing and re-running.

The pipeline is code, not a prompt. The engine owns the task board and drives
it with one loop: take the next ready task, delegate it to its expert, record
the outcome. Failure handling is a ladder, cheapest rung first:

1. a tool fails             -> the error goes back to the agent, which adapts       (agent.py)
2. a submission breaks      -> rejected by the gate; the agent repairs it in the
   a checked rule              same conversation                                    (gate)
3. the agent itself fails   -> the task is retried with a fresh context and the
   (crash, out of turns)       failure as feedback, up to max_attempts              (execute)
4. the audit finds a defect -> the blamed task and everything downstream of it
                               are re-opened with the findings, up to
                               max_audit_cycles                                     (_act_on_audit)
5. still failing            -> the run stops and reports what failed and why;
                               the user decides (change request, retry)             (report)
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from . import auditor
from .agent import Tool, run_agent
from .board import Board, Task
from .events import Block, Bus, Halt
from .experts import ANSWER, ASK, EXPERTS, READ, TEXT, obj
from .llm import FatalError
from .memory import Memory
from .rules import RULES, SUITE, violations
from .tools import Workspace, make_tools

# (task id, expert, depends on). Work-item tasks are added once the spec exists; see _expand.
PIPELINE = [
    ("research", "researcher", []),
    ("plan", "planner", ["research"]),
    ("prd", "product", ["plan"]),
    ("spec", "architect", ["prd"]),
    ("audit", "auditor", ["spec"]),
]


class Engine:
    def __init__(self, cfg, llm, run_dir: Path):
        self.cfg, self.llm, self.dir = cfg, llm, Path(run_dir)
        self.ws = Workspace(self.dir / "workspace", cfg.sources)
        self.bus = Bus(self.dir / "trail.jsonl")
        self.board = Board(self.dir / "board.json")
        self.memory = Memory(cfg.memory)
        self.bus.on(self._meter, "llm")

    # --- entry points ---------------------------------------------------------------------------

    def start(self, task_text: str) -> dict:
        if "research" in self.board.tasks:
            raise ValueError("this session already has a project; request changes to it, or start a new session")
        self.board.meta.update(task=task_text, audit_cycles=0, accepted=False)
        for task_id, agent, deps in PIPELINE:
            self.board.add(Task(task_id, task_id, agent, list(deps)))
        return self.run()

    def run(self) -> dict:
        """Drive the board until nothing is runnable. Also how an interrupted session resumes."""
        while task := self.board.ready():
            self.execute(task)
        return self.report()

    def resume(self) -> dict:
        """Continue after an interruption or a failure: failed steps get a fresh set of attempts."""
        for task in self.board.pipeline() + [t for t in self.board.tasks.values() if t.kind == "audit"]:
            if task.status == "failed":
                task.status, task.attempts = "pending", 0
        return self.run()

    def change(self, task_id: str, feedback: str) -> dict:
        """A user change request: re-open one task with the feedback; everything downstream follows."""
        task = self.board.tasks.get(task_id)
        if task is None or task.kind in ("audit", "adhoc"):
            raise ValueError(f"unknown task {task_id!r}; choose one of {[t.id for t in self.board.pipeline()]}")
        self.board.meta.update(audit_cycles=0, accepted=False)
        self.board.meta.setdefault("changes", []).append({"task": task_id, "text": feedback})
        self.board.invalidate(task_id, [])
        self.memory.note("feedback", task.agent, f"A user asked for this change at the {task.kind} step: {feedback}", self.dir.name)
        return self.run()

    def changes(self, task: Task) -> str:
        """The user's change requests that bind a task (made on it or on a step upstream of it), as a brief section.

        They are kept for the whole session, unlike feedback (cleared once a task succeeds), so a later
        re-run cannot quietly undo a change and the auditor knows the deviation was asked for.
        """
        asked = [c["text"] for c in self.board.meta.get("changes", []) if task.id == c["task"] or task.id in self.board.dependents(c["task"])]
        return "# Changes the user asked for (they override the original task and earlier steps where they differ)\n" + "\n".join(f"- {text}" for text in asked) if asked else ""

    def approve(self) -> dict:
        status = self.report()["status"]
        if not status.startswith("completed"):
            raise ValueError(f"there is no finished result to approve: the run is {status}")
        self.board.meta["accepted"] = True
        self.board.save()
        return self.report()

    def consult(self, expert: str, question: str, parent: str | None = None) -> str:
        """Ad-hoc delegation: one question to one expert, tracked on the board like any other task."""
        number = sum(t.kind == "adhoc" for t in self.board.tasks.values()) + 1
        task = self.board.add(Task(f"ask:{number}", "adhoc", expert, meta={"question": question, "parent": parent}))
        while task.status == "pending":
            self.execute(task)
        if task.status != "done":
            raise RuntimeError(f"the {expert} could not answer: {task.error}")
        return task.result["answer"] + ("\n\nSources:\n" + "\n".join(task.result["sources"]) if task.result["sources"] else "")

    # --- running one task -----------------------------------------------------------------------

    def execute(self, task: Task) -> None:
        """Run one attempt of one task. Never raises for a task failure: the outcome is on the board."""
        task.status, task.attempts, task.runs, task.error = "running", task.attempts + 1, task.runs + 1, None
        self.board.save()
        started = time.time()
        with self.bus.scope(task=task.id, agent=task.agent, attempt=task.attempts):
            try:
                self.bus.emit("task_start")  # hooks may veto
                if task.kind == "audit":
                    findings = auditor.sweep(self)
                    result = {"passed": not findings, "findings": findings}
                else:
                    result = self.delegate(task)
                task.result, task.status, task.version, task.feedback = result, "done", task.version + 1, []
                self._finish(task)
                self.bus.emit("task_done", seconds=round(time.time() - started, 1))
            except (KeyboardInterrupt, Halt):  # leave the task runnable so `resume` picks it up
                task.status, task.attempts = ("failed" if task.kind == "adhoc" else "pending"), task.attempts - 1
                self.bus.emit("interrupted")
                raise
            except Exception as exc:
                final = isinstance(exc, (FatalError, Block)) or task.attempts >= self.cfg.max_attempts
                task.error = f"{type(exc).__name__}: {exc}"
                task.status = "failed" if final else "pending"
                if not final:
                    task.feedback.append(f"Attempt {task.attempts} failed with {task.error}")
                self.bus.emit("error", where="task", error=task.error, traceback=traceback.format_exc(), will_retry=not final)
            finally:
                task.seconds = round(task.seconds + time.time() - started, 1)
                self.board.save()

    def delegate(self, task: Task) -> dict:
        """Hand a task to its expert and return the accepted submission."""
        expert = EXPERTS[task.agent]
        # Filled by the tools; read by the grounding rules. A revision keeps the log of the accepted run it builds on,
        # so the sources of findings it keeps count as opened without fetching them all again.
        task.meta["consulted"] = list(task.meta.get("consulted", [])) if task.result else []
        messages = [{"role": "user", "content": self.handoff(task)}]
        schema = ANSWER if task.kind == "adhoc" else expert.schema
        return run_agent(self.llm, self.bus, self.cfg, expert.system, messages, self._tools(task, expert), schema, lambda payload: self.gate(task, payload))

    def handoff(self, task: Task) -> str:
        """Everything the expert needs, and nothing else: assignment, upstream results, feedback, memory."""
        expert, project = EXPERTS[task.agent], self.board.meta.get("task", "")
        if task.kind == "adhoc":
            parts = [f"# Question\n{task.meta['question']}"] + ([f"# Context: the project this session is building\n{project}"] if project else [])
        else:
            parts = [f"# Project task\n{project}", f"# Your assignment\n{expert.assignment}"]
            if task.kind == "implement":
                existing = self.ws.files(with_sources=False) or "none yet"
                parts.append(f"# Your work item\n{json.dumps(task.meta['item'], indent=1)}\n\nFiles already in the workspace: {existing}")
            parts += [f"# Upstream result: {dep}\n{json.dumps(self.board.tasks[dep].result, indent=1)}" for dep in expert.needs]
            if task.result:
                parts.append(f"# Your previous submission (revise it rather than starting over)\n{json.dumps(task.result, indent=1)}")
        if self.ws.sources:
            parts.append("# Reference files\nRead-only reference material is available under sources/.")
        parts.append(self.changes(task))
        if task.feedback:
            parts.append("# Feedback you must address\n" + "\n".join(f"- {note}" for note in task.feedback))
        recalled = self.memory.recall(task.agent, task.meta.get("question") or project, exclude_run=self.dir.name)
        if recalled:
            parts.append("# Lessons and notes from earlier runs\n" + "\n".join(f"- {note}" for note in recalled))
        return "\n\n".join(filter(None, parts))

    def gate(self, task: Task, payload: dict) -> list[str]:
        """Checked rules, applied the moment an expert submits. Violations go back to the expert and into memory."""
        found = violations(payload, task, self)
        for rule in {rule.id: rule for rule, _ in found}.values():
            self._learn(task.agent, rule.id, rule.text)
        return [f"[{rule.id}] {problem}" for rule, problem in found]

    def _tools(self, task: Task, expert) -> list[Tool]:
        writable = set(task.meta["item"]["files"]) if task.kind == "implement" else None
        tools = make_tools(self.ws, self.cfg, task.meta["consulted"], writable)
        tools[ASK] = Tool(
            ASK,
            "Ask the researcher to establish one specific fact from the reference files or the web. Use only when a missing fact blocks you.",
            obj(question=TEXT),
            lambda args: self.consult("researcher", args["question"], parent=task.id),
        )
        read_only = READ + ("web_search", "web_fetch")  # answering a question must not change the project
        return [tools[name] for name in expert.tools if task.kind != "adhoc" or name in read_only]

    def _finish(self, task: Task) -> None:
        artifacts = self.dir / "artifacts"
        artifacts.mkdir(exist_ok=True)
        (artifacts / f"{task.id.replace(':', '-')}.md").write_text(f"# {task.id} ({task.agent})\n\n{to_markdown(task.result)}\n")
        if task.kind == "spec":
            self._expand(task.result)
        if task.kind == "audit":  # before the audit is saved as done, so findings are never left unacted on
            self._act_on_audit(task.result["findings"])
        if task.kind == "research":
            self.memory.note("research", "researcher", f"For the task \"{self.board.meta['task'][:200]}\": {task.result['summary'][:1500]}", self.dir.name)

    def _expand(self, spec: dict) -> None:
        """Fan the spec out into one engineer task per work item, wired by the items' dependencies."""
        before = {t.id: t for t in self.board.tasks.values() if t.kind == "implement"}
        self.board.drop("implement")
        for item in spec["work_items"]:
            # A work item that survives a spec revision keeps its history: previous submission, feedback, counters.
            task = before.get(f"impl:{item['id']}") or Task(f"impl:{item['id']}", "implement", "engineer")
            task.deps = ["spec"] + [f"impl:{dep}" for dep in item["depends_on"]]
            task.status, task.attempts, task.meta["item"] = "pending", 0, item
            self.board.add(task)
        audit = self.board.tasks.pop("audit")
        audit.deps = [t.id for t in self.board.pipeline()]  # the audit waits for every step
        self.board.add(audit)
        owned = {name for item in spec["work_items"] for name in item["files"]}
        for name in self.ws.files(with_sources=False):  # a revised spec may drop files; don't let stale ones linger
            if name not in owned:
                (self.ws.root / name).unlink()

    # --- acting on the audit --------------------------------------------------------------------

    def _act_on_audit(self, findings: list[dict]) -> None:
        self.bus.emit("audit", findings=findings)
        if not findings:
            return
        rules = {rule.id: rule for rule in RULES + [SUITE]}
        for f in findings:
            advice = "" if f["fix"] == rules[f["rule"]].text else f" Auditor's advice last time: {f['fix']}"
            self._learn(self.board.tasks[f["task"]].agent, f["rule"], rules[f["rule"]].text + advice)
        if self.board.meta["audit_cycles"] >= self.cfg.max_audit_cycles:
            return  # out of budget: finish and report the open findings rather than loop forever
        self.board.meta["audit_cycles"] += 1
        blamed: dict[str, list[str]] = {}
        for f in findings:
            blamed.setdefault(f["task"], []).append(f"Audit finding [{f['rule']}]: {f['evidence']}\nRequired fix: {f['fix']}")
        for task_id, notes in blamed.items():
            self.board.invalidate(task_id, notes)
        self.bus.emit("rerun", tasks=sorted(blamed), cycle=self.board.meta["audit_cycles"])

    def _learn(self, role: str, rule_id: str, text: str) -> None:
        self.memory.lesson(role, rule_id, text, self.dir.name)
        self.bus.emit("lesson", role=role, rule=rule_id)

    def _meter(self, event: dict) -> None:
        task = self.board.tasks.get(event.get("task"))
        if task:
            task.tokens += event["usage"]["in"] + event["usage"]["out"]

    # --- what the user sees ---------------------------------------------------------------------

    def report(self) -> dict:
        tasks = list(self.board.tasks.values())  # a copy: the web gateway reads this while a run adds tasks
        steps = [t for t in tasks if t.kind != "adhoc"]
        audit, spec = self.board.tasks.get("audit"), self.board.tasks.get("spec")
        if not steps:
            status = "no project yet"
        elif any(t.status == "failed" for t in steps):
            status = "failed"
        elif any(t.status != "done" for t in steps):
            status = "incomplete"
        else:
            status = "completed" if audit.result["passed"] else "completed_with_open_findings"
        return {
            "status": status,
            "accepted_by_user": self.board.meta.get("accepted", False),
            "workspace": str(self.ws.root),
            "run_command": spec.result["run_command"] if spec and spec.result else None,
            "tasks": [
                {"id": t.id, "agent": t.agent, "status": t.status, "runs": t.runs, "seconds": t.seconds, "tokens": t.tokens, **({"error": t.error} if t.error else {})}
                for t in tasks
            ],
            "audit": audit.result if audit and audit.status == "done" else None,
            "audit_cycles_used": self.board.meta.get("audit_cycles", 0),
            "files": self.ws.files(with_sources=False),
        }

    def show(self, name: str = "") -> str:
        """Read an artifact (artifacts/<step>) or a workspace file; with no name, list what exists."""
        artifacts = self.dir / "artifacts"
        if not name:
            listing = [f"artifacts/{p.stem}" for p in sorted(artifacts.glob("*.md"))] + self.ws.files(with_sources=False)
            return "\n".join(listing) or "(nothing yet)"
        if name.startswith("artifacts/"):
            path = (artifacts / name.removeprefix("artifacts/")).with_suffix(".md").resolve()
            if not path.is_relative_to(artifacts.resolve()):
                raise PermissionError(name)
            return path.read_text()
        return self.ws.resolve(name).read_text(errors="replace")


def to_markdown(value, depth: int = 2) -> str:
    """Render a submission as a readable document."""
    if isinstance(value, dict):
        return "\n\n".join(f"{'#' * depth} {key}\n\n{to_markdown(item, depth + 1)}" for key, item in value.items())
    if isinstance(value, list):
        rows = ["; ".join(f"**{k}**: {v}" for k, v in item.items()) if isinstance(item, dict) else str(item) for item in value]
        return "\n".join(f"- {row}" for row in rows) or "_none_"
    return str(value)
