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

from .memory import read_jsonl


class Block(Exception):
    """Raised by a hook to veto a `task_start` or `pre_tool` event."""


class Bus:
    def __init__(self, trail: Path):
        self.trail = trail
        trail.parent.mkdir(parents=True, exist_ok=True)
        self.seq = len(trail.read_text().splitlines()) if trail.exists() else 0
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

    def emit(self, type: str, **data) -> dict:
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

    def read(self, **match) -> list[dict]:
        """The trail, optionally filtered, e.g. read(agent="engineer") or read(type="error")."""
        return [e for e in read_jsonl(self.trail) if all(e.get(k) == v for k, v in match.items())]


def render(e: dict) -> str | None:
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
        return "\n".join(lines + [f"    ✖ [{f['rule']}] {f['task']}: {f['evidence'][:200]}" for f in e["findings"]])
    if kind == "rerun":
        return f"↺ re-running {', '.join(e['tasks'])} (audit cycle {e['cycle']})"
    if kind == "lesson":
        return f"    ✎ lesson recorded for {e['role']}: [{e['rule']}]"
    if kind == "interrupted":
        return f"⏸ {task} interrupted — state saved"
    return None
