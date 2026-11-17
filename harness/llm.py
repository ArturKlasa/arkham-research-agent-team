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
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class TransientError(Exception):
    """Worth retrying: rate limit, overload, network failure, malformed stream."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = retry_after  # seconds the server asked us to wait (Retry-After), 0 when it did not say


def _retry_after(headers) -> float:
    """A Retry-After header in seconds, capped at two minutes. The HTTP-date form and junk count as no hint."""
    try:
        return min(float((headers or {}).get("retry-after", "")), 120.0)
    except ValueError:
        return 0.0


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
    streamed = []

    def relay(text):
        streamed.append(text)
        on_text(text)

    for attempt in range(cfg.llm_retries + 1):
        try:
            return llm.chat(system, messages, tools, relay if on_text else None)
        except TransientError as exc:
            if attempt == cfg.llm_retries:
                raise
            delay = round(max(cfg.retry_base * 2**attempt + random.uniform(0, cfg.retry_base), exc.retry_after), 1)
            bus.emit("llm_retry", error=str(exc), delay=delay)
            if streamed:  # the text already shown is not part of any reply: say so, rather than silently showing a second version
                on_text("\n[connection lost; the reply starts again]\n")
                streamed.clear()
            time.sleep(delay)
    raise AssertionError("unreachable")


# The default Claude line-up, one model per agent role (see the README for why). Format: role=model[@effort],...
CLAUDE_ROLES = (
    "orchestrator=claude-sonnet-5-5,researcher=claude-sonnet-5-5,planner=claude-sonnet-5-5,product=claude-sonnet-5-5,"
    "architect=claude-opus-5-5@high,engineer=claude-opus-5-5@medium,auditor=claude-opus-5-5@high"
)


def make_llm(cfg):
    """The backend for cfg.provider, or a Roster when models are set per role.

    With provider=anthropic or pi and no --model, CLAUDE_ROLES applies and cfg.roles overrides single roles of it.
    An explicit --model is the model for every role cfg.roles does not list. A role entry may name its own
    provider, "auditor=openai+gpt-oss:120b", so one role can run on another model family than the rest.
    """
    if cfg.provider == "scripted":
        from .demo import ScriptedTeam

        return ScriptedTeam()
    backends = {"anthropic": AnthropicLLM, "openai": OpenAICompatLLM, "pi": PiLLM}
    backend = backends.get(cfg.provider)
    if backend is None:
        raise ValueError(f"unknown provider {cfg.provider!r} (use anthropic, openai, pi or scripted)")
    from dataclasses import replace

    from .experts import EXPERTS

    spec = (CLAUDE_ROLES if cfg.provider in ("anthropic", "pi") and not cfg.model else "") + "," + cfg.roles  # later entries win
    by_role = {}
    for item in filter(None, (part.strip() for part in spec.split(","))):
        role, _, rest = item.partition("=")
        provider, plus, rest = rest.partition("+") if "+" in rest else (cfg.provider, "", rest)
        model, _, effort = rest.partition("@")  # "@", not ":", because Ollama model names contain a colon
        if role not in {"orchestrator", *EXPERTS} or not model or provider not in backends:
            raise ValueError(f"bad role entry {item!r}: use role=[provider+]model[@effort] with a role from orchestrator, {', '.join(EXPERTS)}")
        by_role[role] = backends[provider](replace(cfg, provider=provider, model=model, effort=effort or cfg.effort))
    return Roster(backend(cfg), by_role) if by_role else backend(cfg)  # the default serves roles not listed


class Roster:
    """One backend per agent role; a role that is not listed uses the default backend."""

    def __init__(self, default, by_role: dict):
        self.default, self.by_role = default, by_role

    def pick(self, role: str):
        return self.by_role.get(role, self.default)


def for_role(llm, role: str):
    """The backend an agent of this role talks to. A plain backend serves every role."""
    return llm.pick(role) if isinstance(llm, Roster) else llm


class AnthropicLLM:
    """Claude through the official SDK, streamed, with adaptive thinking."""

    # Models that accept the server-side refusal fallback: a request declined by a safety
    # classifier is re-run on a fallback model inside the same call.
    FALLBACK = ("claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")

    def __init__(self, cfg):
        import anthropic  # imported lazily so the other providers need no dependency
        import httpx2  # the SDK's HTTP library: a connection lost mid-stream surfaces as its error, not the SDK's

        self.sdk, self.dropped = anthropic, httpx2.TransportError
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
        except (sdk.RateLimitError, sdk.APIConnectionError, self.dropped) as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}", _retry_after(getattr(getattr(exc, "response", None), "headers", None))) from exc
        except sdk.APIStatusError as exc:
            # A status below 400 means the error arrived inside an already-open stream (typically "overloaded").
            detail = f"{type(exc).__name__} ({exc.status_code}): {exc.message}"
            if exc.status_code >= 500 or exc.status_code < 400 or exc.status_code in (408, 409):
                raise TransientError(detail, _retry_after(exc.response.headers)) from exc
            raise FatalError(detail) from exc
        except ValueError as exc:  # the SDK could not parse a streamed tool input at all: re-issue the turn
            raise TransientError(f"malformed tool input in stream: {exc}") from exc
        if msg.stop_reason is None:  # the connection closed cleanly before message_stop: text or tool input may be cut
            raise TransientError("the stream ended before the reply was complete")

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


class PiLLM:
    """Any model pi knows, through pi-ai, the model layer of the pi agent toolkit, in a small Node bridge (pi/bridge.mjs).

    Only the model connection is pi's. The agent loop, the tools, the hooks and the sandbox stay the harness's own:
    pi-ai returns tool calls without running them. Set up once with `npm install --prefix pi` (Node 22.19+).
    Models are named provider/model as in pi's catalogue; a bare name means an Anthropic model.
    """

    BRIDGE = ["node", str(Path(__file__).resolve().parent.parent / "pi" / "bridge.mjs")]
    TIMEOUT = 900  # seconds for one call; pi's own HTTP timeout is shorter, this stops a bridge that hangs
    PERMANENT = re.compile(r"\b(400|401|403|404|invalid|unauthori[sz]ed|authentication|forbidden|not.found|api.key|credit|no model)\b", re.I)

    def __init__(self, cfg):
        provider, slash, model = (cfg.model or "claude-opus-5-5").partition("/")
        self.provider, self.model, self.effort = (provider, model, cfg.effort) if slash else ("anthropic", provider, cfg.effort)

    def chat(self, system, messages, tools, on_text=None) -> Reply:
        context = {"systemPrompt": system, "messages": self._messages(messages), "tools": [{"name": t.name, "description": t.description, "parameters": t.schema} for t in tools]}
        request = json.dumps({"provider": self.provider, "model": self.model, "effort": self.effort, "context": context})
        try:  # stderr joins stdout: one pipe cannot deadlock, and a line that is not JSON is the bridge's own failure
            bridge = subprocess.Popen(self.BRIDGE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        except OSError as exc:
            raise FatalError(f"cannot start the pi bridge (is Node installed?): {exc}") from exc
        watchdog = threading.Timer(self.TIMEOUT, bridge.kill)
        watchdog.start()
        message, noise = None, []
        try:
            bridge.stdin.write(request)
            bridge.stdin.close()
            for line in bridge.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    event = {"error": line.strip()}
                if "text" in event and on_text:
                    on_text(event["text"])
                message = event.get("message", message)
                noise += [event["error"]] if event.get("error") else []
            bridge.wait()
        except BrokenPipeError:  # the bridge died before reading the request; what it printed says why
            noise.append(bridge.stdout.read().strip())
        finally:
            watchdog.cancel()
            bridge.kill()  # a no-op once it has exited; ends it when the turn was interrupted
            bridge.wait()
            bridge.stdout.close()
        if message is None:
            said = " ".join(noise)[-600:] or f"no reply (exit code {bridge.returncode})"
            if "ERR_MODULE_NOT_FOUND" in said or "Cannot find package" in said:
                raise FatalError("pi-ai is not installed: run `npm install --prefix pi`")
            raise (FatalError if self.PERMANENT.search(said) else TransientError)(f"pi bridge: {said}")
        if message.get("rawStopReason") == "refusal":
            return Reply(stop="refusal", native=message)
        if message["stopReason"] in ("error", "aborted"):
            # pi reports a failed call as text, not as a status: what is clearly permanent is fatal, the rest is retried
            said = message.get("errorMessage") or message["stopReason"]
            raise (FatalError if self.PERMANENT.search(said) else TransientError)(f"pi: {said}")
        calls = [b for b in message["content"] if b["type"] == "toolCall"]
        usage = message["usage"]
        return Reply(
            text="".join(b["text"] for b in message["content"] if b["type"] == "text"),
            # arguments that were not valid JSON arrive as {} with the reason beside them: pass the reason, so the loop rejects the call
            tool_calls=[ToolCall(b["id"], b["name"], b.get("argumentsError") or b["arguments"]) for b in calls],
            stop={"toolUse": "tool_use", "length": "max_tokens"}.get(message["stopReason"], "end"),
            usage={"in": usage["input"] + usage.get("cacheRead", 0) + usage.get("cacheWrite", 0), "out": usage["output"]},
            native=message,  # thinking blocks and signatures must go back unchanged
        )

    @staticmethod
    def _messages(messages):
        out, names = [], {}
        for m in messages:
            if m["role"] == "tool":  # pi wants the tool's name with its result
                out.append({"role": "toolResult", "toolCallId": m["id"], "toolName": names.get(m["id"], ""), "content": [{"type": "text", "text": m["content"]}], "isError": m.get("is_error", False)})
            elif m["role"] == "assistant":
                names.update({c.id: c.name for c in m.get("tool_calls", [])})
                native = m.get("native")
                if not (isinstance(native, dict) and native.get("role") == "assistant"):  # not from pi: rebuild it from what the harness kept
                    blocks = [{"type": "text", "text": m["content"]}] if m["content"] else []
                    blocks += [{"type": "toolCall", "id": c.id, "name": c.name, "arguments": c.args} for c in m.get("tool_calls", [])]
                    native = {"role": "assistant", "content": blocks or [{"type": "text", "text": "(no output)"}]}
                out.append(native)
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

        reply, calls, complete = Reply(), {}, False  # calls: stream index -> partially assembled tool call
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                for raw in response:  # server-sent events, one "data: {json}" line per chunk
                    line = raw.decode("utf-8").strip()
                    complete = complete or line.endswith("[DONE]")
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
                        complete = complete or bool(choice.get("finish_reason"))
                        if choice.get("finish_reason") == "length":
                            reply.stop = "max_tokens"
        except urllib.error.HTTPError as exc:  # must precede URLError, its base class
            detail = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}"
            if exc.code in (408, 409, 429) or exc.code >= 500:
                raise TransientError(detail, _retry_after({k.lower(): v for k, v in (exc.headers or {}).items()})) from exc
            raise FatalError(detail) from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise TransientError(f"{type(exc).__name__}: {exc}") from exc
        if not complete:  # closed without a finish reason or [DONE]: text or tool arguments may be cut
            raise TransientError("the stream ended before the reply was complete")

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
