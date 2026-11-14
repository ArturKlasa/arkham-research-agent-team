"""LLM servicing — the only module that talks to a model provider.

Everything above this file works with three provider-neutral shapes:

    user message       {"role": "user", "content": str}
    assistant message  {"role": "assistant", "content": str, "tool_calls": [ToolCall], "native": ...}
    tool result        {"role": "tool", "id": str, "content": str, "is_error": bool}

A backend only has to implement chat(system, messages, tools, on_text) -> Reply
and raise TransientError / FatalError so the harness knows whether to retry.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field


class TransientError(Exception):
    """Worth retrying: rate limit, overload, network failure, malformed stream."""


class FatalError(Exception):
    """Retrying cannot help: bad credentials, invalid request, model refusal."""


@dataclass
class ToolCall:
    id: str
    name: str
    args: object  # a dict when the model produced valid JSON; validated by the agent loop


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end"  # end | tool_use | max_tokens | refusal
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    native: object = None  # provider-specific assistant content, replayed verbatim on the next turn

    def as_message(self) -> dict:
        return {"role": "assistant", "content": self.text, "tool_calls": self.tool_calls, "native": self.native}


def chat(llm, bus, cfg, system, messages, tools, on_text=None) -> Reply:
    """One model call with exponential backoff on transient errors. Retries are logged to the trail."""
    for attempt in range(cfg.llm_retries + 1):
        try:
            return llm.chat(system, messages, tools, on_text)
        except TransientError as exc:
            if attempt == cfg.llm_retries:
                raise
            delay = round(cfg.retry_base * 2**attempt + random.uniform(0, cfg.retry_base), 1)
            bus.emit("llm_retry", error=str(exc), delay=delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def make_llm(cfg):
    if cfg.provider == "anthropic":
        return AnthropicLLM(cfg)
    if cfg.provider == "openai":
        return OpenAICompatLLM(cfg)
    if cfg.provider == "scripted":
        from .demo import ScriptedTeam

        return ScriptedTeam()
    raise ValueError(f"unknown provider {cfg.provider!r} (use anthropic, openai or scripted)")


class AnthropicLLM:
    """Claude through the official SDK, streamed, with adaptive thinking."""

    # Models that accept the server-side refusal fallback: a request declined by a safety
    # classifier is re-run on a fallback model inside the same call.
    FALLBACK = ("claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")

    def __init__(self, cfg):
        import anthropic  # imported lazily so the other providers need no dependency

        self.sdk = anthropic
        self.client = anthropic.Anthropic(max_retries=0)  # the harness owns retries, see chat()
        self.model, self.effort = cfg.model or "claude-opus-5-5", cfg.effort

    def chat(self, system, messages, tools, on_text=None) -> Reply:
        sdk = self.sdk
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if self.model in self.FALLBACK else {}
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=64000,
                system=system,
                messages=self._messages(messages),
                # Eager streaming sends large tool inputs (file bodies) as they are generated; the
                # server then skips validation, so run_agent validates every input against its schema.
                tools=[{"name": t.name, "description": t.description, "input_schema": t.schema, "eager_input_streaming": True} for t in tools],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},  # agent loops resend a growing prefix; cache it
                **extra,
            ) as stream:
                for event in stream:
                    if event.type == "text" and on_text:
                        on_text(event.text)
                msg = stream.get_final_message()
        except (sdk.RateLimitError, sdk.APIConnectionError) as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}") from exc
        except sdk.APIStatusError as exc:
            # A status below 400 means the error arrived inside an already-open stream (typically "overloaded").
            kind = TransientError if exc.status_code >= 500 or exc.status_code < 400 or exc.status_code in (408, 409) else FatalError
            raise kind(f"{type(exc).__name__} ({exc.status_code}): {exc.message}") from exc
        except ValueError as exc:  # the SDK could not parse a streamed tool input at all: re-issue the turn
            raise TransientError(f"malformed tool input in stream: {exc}") from exc

        return Reply(
            text="".join(b.text for b in msg.content if b.type == "text"),
            tool_calls=[ToolCall(b.id, b.name, b.input) for b in msg.content if b.type == "tool_use"],
            stop={"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}.get(msg.stop_reason, "end"),
            usage={
                "in": msg.usage.input_tokens + (msg.usage.cache_read_input_tokens or 0) + (msg.usage.cache_creation_input_tokens or 0),
                "out": msg.usage.output_tokens,
            },
            # Thinking blocks must go back unchanged, so the whole content list is replayed as-is.
            native=msg.content or [{"type": "text", "text": "(no output)"}],
        )

    @staticmethod
    def _messages(messages):
        out = []
        for m in messages:
            if m["role"] == "tool":  # all results of one turn go back in a single user message
                block = {"type": "tool_result", "tool_use_id": m["id"], "content": m["content"], "is_error": m.get("is_error", False)}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                out.append({"role": "assistant", "content": m.get("native") or m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out


class OpenAICompatLLM:
    """Any /chat/completions server: Ollama, vLLM, LM Studio, OpenRouter, OpenAI.

    This is the path for open-source and locally executed models. Standard library only.
    """

    def __init__(self, cfg):
        if not cfg.model:
            raise ValueError("provider=openai needs a model, e.g. HARNESS_MODEL=qwen2.5-coder:7b")
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"
        self.model = cfg.model
        self.key = cfg.api_key or os.environ.get("OPENAI_API_KEY", "")

    def chat(self, system, messages, tools, on_text=None) -> Reply:
        body = {
            "model": self.model,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": system}, *self._messages(messages)],
        }
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}} for t in tools]
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {self.key}"} if self.key else {})}
        request = urllib.request.Request(self.url, json.dumps(body).encode(), headers)

        reply, calls = Reply(), {}  # calls: stream index -> partially assembled tool call
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                for raw in response:  # server-sent events, one "data: {json}" line per chunk
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:") or line.endswith("[DONE]"):
                        continue
                    chunk = json.loads(line[5:])
                    if chunk.get("usage"):
                        reply.usage = {"in": chunk["usage"].get("prompt_tokens", 0), "out": chunk["usage"].get("completion_tokens", 0)}
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            reply.text += delta["content"]
                            if on_text:
                                on_text(delta["content"])
                        for part in delta.get("tool_calls") or []:
                            call = calls.setdefault(part.get("index", 0), {"id": "", "name": "", "args": ""})
                            fn = part.get("function") or {}
                            call["id"] = part.get("id") or call["id"]
                            call["name"] = fn.get("name") or call["name"]
                            call["args"] += fn.get("arguments") or ""
                        if choice.get("finish_reason") == "length":
                            reply.stop = "max_tokens"
        except urllib.error.HTTPError as exc:  # must precede URLError, its base class
            detail = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}"
            raise (TransientError if exc.code in (408, 409, 429) or exc.code >= 500 else FatalError)(detail) from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}") from exc

        for call in calls.values():
            try:
                args = json.loads(call["args"] or "{}")
            except json.JSONDecodeError:
                args = call["args"]  # left as a string: schema validation rejects it and the model retries
            reply.tool_calls.append(ToolCall(call["id"] or uuid.uuid4().hex[:12], call["name"], args))
        if reply.tool_calls and reply.stop == "end":
            reply.stop = "tool_use"
        return reply

    @staticmethod
    def _messages(messages):
        out = []
        for m in messages:
            if m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["id"], "content": m["content"]})
            elif m["role"] == "assistant":
                calls = [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args if isinstance(c.args, dict) else {})}}
                    for c in m.get("tool_calls") or []
                ]
                out.append({"role": "assistant", "content": m["content"] or ("" if not calls else None), **({"tool_calls": calls} if calls else {})})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out
