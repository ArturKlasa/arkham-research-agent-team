"""Memory shared by all sessions: how the team gets better from run to run.

Two kinds of entries, both written automatically and fetched automatically
when a task is handed to an agent:

- lessons  a rule an agent role violated. Stored once per (role, rule) with a
           hit counter, and always shown to that role in later runs.
- notes    past research briefs and user change requests. Retrieved only when
           relevant to the task at hand.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter
from pathlib import Path


def read_jsonl(path: Path):
    """Yield the records of a JSONL file, skipping a line torn by a crash mid-write."""
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]{3,}", text.lower()))


class Memory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: list[dict] = []
        self._load()

    def _load(self) -> None:
        """Re-read the file: other sessions share it, so every write starts from what is on disk."""
        self.items = list(read_jsonl(self.path))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(m) + "\n" for m in self.items))
        os.replace(tmp, self.path)

    def lesson(self, role: str, rule: str, text: str, run: str) -> None:
        """Record that `role` violated `rule`. Repeats strengthen the existing lesson instead of duplicating it."""
        self._load()
        for m in self.items:
            if m["kind"] == "lesson" and m["role"] == role and m["key"] == rule:
                m.update(hits=m["hits"] + 1, text=text, ts=time.time())
                break
        else:
            self.items.append({"kind": "lesson", "role": role, "key": rule, "text": text, "hits": 1, "run": run, "ts": time.time()})
        self._save()

    def note(self, kind: str, role: str, text: str, run: str) -> None:
        self._load()
        if not any(m["kind"] == kind and m["text"] == text for m in self.items):
            self.items.append({"kind": kind, "role": role, "key": "", "text": text, "hits": 1, "run": run, "ts": time.time()})
            self._save()

    def recall(self, role: str, query: str, exclude_run: str = "", k: int = 3) -> list[str]:
        """What `role` should know before starting on `query`: its lessons, plus the most relevant notes."""
        lessons = sorted((m for m in self.items if m["kind"] == "lesson" and m["role"] == role), key=lambda m: -m["hits"])[:6]
        out = [f"[{m['key']}] {m['text']} (violated {m['hits']}x before)" for m in lessons]

        # lexical overlap weighted by rarity (IDF); switch to embeddings when the store outgrows it
        notes = [(m, _words(m["text"])) for m in self.items if m["kind"] != "lesson" and m["role"] == role and m["run"] != exclude_run]
        frequency = Counter(word for _, words in notes for word in words)
        asked = _words(query)
        scored = [(sum(math.log(1 + len(notes) / frequency[w]) for w in asked & words), m) for m, words in notes]
        ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0])[:k]
        return out + [f"({m['kind']} from a past run) {m['text']}" for _, m in ranked]
