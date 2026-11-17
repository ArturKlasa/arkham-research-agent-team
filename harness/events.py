"""Event bus.

Everything an agent or the engine does is emitted here. Each event is appended
to the run's trail (JSONL) and then handed to the registered hooks. That one
mechanism provides the per-agent log trail, the traceback store, live progress
for the UI, and policy hooks that can veto an action.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .memory import read_jsonl

# An event always has `seq`, `ts` and `type`; `task`, `agent` and `attempt` when someone is acting; then the fields of
# its type (an `error` has `where`, `error`, `traceback` and `will_retry`, for example).
Event = dict[str, Any]


class Block(Exception):
    """Raised by a hook to veto a `task_start` or `pre_tool` event."""


class Halt(Block):
    """Raised by a `task_start` hook to pause the run: the task stays runnable and `resume` continues."""


class Bus:
    def __init__(self, trail: Path):
        self.trail = trail
        trail.parent.mkdir(parents=True, exist_ok=True)
        written = trail.read_text() if trail.exists() else ""
        if written and not written.endswith("\n"):  # a crash tore the last line: end it, or the next event joins it
            with trail.open("a") as f:
                f.write("\n")
        self.seq = len(written.splitlines())
        self.hooks: list[tuple[tuple[str, ...], object]] = []
        self.context: dict = {}  # who is acting: task, agent, attempt

    def on(self, fn, *types: str):
        """Call fn(event) for the given event types (all types when none given)."""
        self.hooks.append((types, fn))
        return fn

    def off(self, fn):
        self.hooks = [h for h in self.hooks if h[1] is not fn]

    @contextmanager
    def scope(self, **context):
        """Stamp every event emitted inside the block with who is acting."""
        outer, self.context = self.context, {**self.context, **context}
        try:
            yield
        finally:
            self.context = outer

    def emit(self, type: str, **data) -> Event:
        self.seq += 1
        event = {"seq": self.seq, "ts": round(time.time(), 3), "type": type, **self.context, **data}
        with self.trail.open("a") as f:  # logged before hooks run, so a veto is itself on record
            f.write(json.dumps(event, default=str) + "\n")
        for types, fn in list(self.hooks):
            if types and type not in types:
                continue
            try:
                fn(event)
            except Block:
                raise
            except Exception as exc:  # a broken hook must never break the run
                if type != "hook_error":
                    self.emit("hook_error", hook=getattr(fn, "__name__", repr(fn)), error=repr(exc))
        return event

    def read(self, **match) -> list[Event]:
        """The trail, optionally filtered, e.g. read(agent="engineer") or read(type="error")."""
        return [e for e in read_jsonl(self.trail) if all(e.get(k) == v for k, v in match.items())]


def render(e: Event) -> str | None:
    """One human-readable progress line for an event, or None if it is not worth showing live."""
    kind, task = e["type"], e.get("task", "")
    if kind == "task_start":
        return f"▶ {task} · {e.get('agent')} (attempt {e.get('attempt')})"
    if kind == "task_done":
        return f"✔ {task} done in {e['seconds']}s"
    if kind == "pre_tool":
        arg = next(iter(e["args"].values()), "") if e["args"] else ""
        return f"    · {e['tool']}({str(arg)[:70]!r})"
    if kind == "gate" and not e["passed"]:
        return "    ✖ submission rejected: " + "; ".join(e["problems"])[:300]
    if kind == "llm_retry":
        return f"    ↻ model call failed ({e['error'][:120]}), retrying in {e['delay']}s"
    if kind == "error" and e["where"].startswith("tool:"):
        return f"    ! {e['where']} raised {e['error'][:200]} (returned to the agent)"
    if kind == "error":
        return f"✖ {task} failed: {e['error'][:300]}" + (" — will retry" if e.get("will_retry") else " — giving up")
    if kind == "audit":
        lines = [f"⚖ audit: {len(e['findings'])} finding(s)" if e["findings"] else "⚖ audit: all rules pass"]
        return "\n".join(
            lines + [
                f"    ✖ [{f['rule']}] {f['task']}: {_brief(f['evidence'])}\n      → {f['fix'][:200]}"
                for f in e["findings"]
            ]
        )
    if kind == "rerun":
        return f"↺ re-running {', '.join(e['tasks'])} (audit cycle {e['cycle']})"
    if kind == "lesson":
        return f"    ✎ lesson recorded for {e['role']}: [{e['rule']}]"
    if kind == "recall":
        return f"    ✎ {e['role']} starts with lessons from earlier runs: {', '.join(e['rules'])}"
    if kind == "run_done":
        return summary_text(e)
    if kind == "interrupted":
        return f"⏸ {task} interrupted — state saved"
    return None


def _brief(evidence: str) -> str:
    """One line of audit evidence: the first line, plus the failing tests when it is test output."""
    first, *rest = evidence.splitlines() or [""]
    failed = [line.split(" (")[0] for line in rest if line.endswith(("... FAIL", "... ERROR"))]
    return (first + (" failing: " + ", ".join(failed) if failed else ""))[:200]


def summary_text(s: dict) -> str:
    """The end-of-run block: what the session cost and how often each recovery path fired."""
    calls = sum(r["calls"] for r in s["roles"].values())
    lines = [
        f"■ run {s['status']} · {s['seconds']}s of task time · {calls} model calls · tests: {s['tests'] or 'not run'}"
    ]
    tokens = any(r["in"] or r["out"] for r in s["roles"].values())  # the scripted team reports none
    caching = any(r["cached"] for r in s["roles"].values())  # local models report none

    def spent(r: dict) -> str:
        share = f" ({r['cached'] * 100 // (r['in'] or 1):>2}% from cache)" if caching else ""
        return f"  {r['in']:>9,} in{share}  {r['out']:>8,} out" if tokens else ""

    lines += [f"    {role:<12} {r['calls']:>3} calls{spent(r)}" for role, r in s["roles"].items()]
    lines.append(
        f"    recoveries: {s['gate_rejections']} gate rejections · {s['tool_errors']} tool errors "
        f"· {s['task_retries']} task retries · {s['model_retries']} model-call retries · "
        f"{s['audit_cycles']} audit re-runs in the session · {s['lessons']} lessons recorded · "
        f"{s['lessons_recalled']} lessons recalled into handoffs"
    )
    return "\n".join(lines)
