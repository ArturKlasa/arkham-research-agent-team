"""Settings. Every field can be set with a HARNESS_<NAME> environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass
class Config:
    # LLM servicing
    provider: str = "anthropic"  # anthropic | openai (any OpenAI-compatible server, e.g. Ollama) | scripted
    model: str = ""  # anthropic defaults to claude-opus-5-5; required for provider=openai
    effort: str = "medium"  # anthropic only: low | medium | high | xhigh | max
    base_url: str = "http://localhost:11434/v1"  # provider=openai; the default is a local Ollama
    api_key: str = ""  # provider=openai; falls back to OPENAI_API_KEY, may be empty for local servers

    # Resilience limits
    max_turns: int = 40  # LLM turns one agent may take on one task
    max_attempts: int = 3  # fresh-context attempts per task before it is failed and escalated
    max_audit_cycles: int = 2  # audit -> re-run rounds before reporting open findings
    llm_retries: int = 3  # retries of one LLM call on a transient error
    retry_base: float = 1.0  # seconds; backoff is retry_base * 2**n plus jitter

    # Isolation
    sandbox: str = "auto"  # auto | bwrap | docker | none (unsafe: runs generated code on the host)
    docker_image: str = "python:3.12-slim"
    command_timeout: int = 120  # seconds per sandboxed command

    # Storage
    runs: Path = Path("runs")  # one directory per session: board, trail, artifacts, workspace
    memory: Path = Path("memory/memory.jsonl")  # lessons and notes shared by all sessions
    sources: Path | None = None  # optional read-only reference files the agents may research

    def __post_init__(self):
        for f in fields(self):
            raw = os.environ.get(f"HARNESS_{f.name.upper()}")
            if raw is not None and getattr(self, f.name) == f.default:  # explicit arguments beat the environment
                setattr(self, f.name, (Path if f.default is None else type(f.default))(raw))
