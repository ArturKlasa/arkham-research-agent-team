"""The agent loop shared by every agent, orchestrator included.

An agent is a system prompt plus tools. The loop calls the model, runs the
tools it asks for, and feeds the results back until the agent is finished:

- an expert finishes by calling `submit` with a payload that passes its schema
  and its gate check; a rejected submission is returned to the model so it can
  repair it in the same conversation;
- the orchestrator (no submit schema) finishes when it answers in plain text.

Nothing a tool does can crash the loop: tool errors are logged with their
traceback and handed back to the model as an error result it can react to.
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from typing import Callable

from .events import Block, Halt
from .llm import FatalError, chat

SUBMIT = "submit"
_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}


class AgentError(Exception):
    """The agent could not finish its task (out of turns, no submission). The task may be retried."""


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], str] | None = None


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    """Validate against the JSON Schema subset the harness uses. Returns problems, [] when valid."""
    kind = schema.get("type")
    if kind and (not isinstance(value, _TYPES[kind]) or (isinstance(value, bool) and kind != "boolean")):
        return [f"{path}: expected {kind}, got {type(value).__name__}"]
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if kind in ("string", "array") and len(value) < schema.get("minLength" if kind == "string" else "minItems", 0):
        errors.append(f"{path}: must not be empty")
    if kind == "object":
        errors += [f"{path}.{key}: required field is missing" for key in schema.get("required", []) if key not in value]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += schema_errors(value[key], sub, f"{path}.{key}")
    if kind == "array" and "items" in schema:
        for i, item in enumerate(value):
            errors += schema_errors(item, schema["items"], f"{path}[{i}]")
    return errors


def clip(text: str, limit: int = 20_000) -> str:
    """Bound a tool result, telling the model what was cut. Both ends are kept: failures and summaries come last."""
    if len(text) <= limit:
        return text
    return f"{text[: limit // 2]}\n… [{len(text) - limit} characters cut from the middle]\n{text[-limit // 2 :]}"


def fence(tool: str, text: str) -> str:
    """Mark a tool result as untrusted data.

    A closing tag inside the text is escaped, so it cannot end the fence early.
    """
    return f'<untrusted tool="{tool}">\n' + re.sub(r"</(\s*untrusted)", r"<\\/\1", text, flags=re.I) + "\n</untrusted>"


def run_agent(llm, bus, cfg, system, messages, tools, submit_schema=None, check=None, on_text=None):
    """Run the loop on `messages` (appended in place). Returns the accepted submit payload, or the final text."""
    offered = list(tools)
    if submit_schema:
        offered.append(
            Tool(SUBMIT, "Submit your final result. Call exactly once, when the work is complete.", submit_schema)
        )
    by_name = {t.name: t for t in tools}
    nudges = 0

    for turn in range(cfg.max_turns):
        reply = chat(llm, bus, cfg, system, messages, offered, on_text)
        # Emitted before the reply joins the transcript: if a hook vetoes here, no tool call is left unanswered.
        bus.emit(
            "llm",
            model=getattr(llm, "model", ""),
            turn=turn,
            stop=reply.stop,
            usage=reply.usage,
            text=reply.text[:2000],
            calls=[c.name for c in reply.tool_calls],
        )
        messages.append(reply.as_message())
        if reply.stop == "refusal":
            raise FatalError("the model declined this request")

        if not reply.tool_calls:
            if submit_schema is None and reply.stop != "max_tokens":
                return reply.text
            nudges += 1
            if nudges > 2:  # three turns in a row without acting
                raise AgentError("the agent stopped without finishing")
            nudge = (
                "Your output was cut off; continue."
                if reply.stop == "max_tokens"
                else f"Continue. When the work is complete, call `{SUBMIT}`."
            )
            messages.append({"role": "user", "content": nudge})
            continue

        nudges = 0
        # run the other calls of the turn before judging a submit
        calls = sorted(reply.tool_calls, key=lambda c: c.name == SUBMIT)
        for i, call in enumerate(calls):
            try:
                if reply.stop == "max_tokens":  # a truncated input can still parse as valid JSON: never run it
                    output, error = (
                        "Your output hit the length limit and this call was cut off. Retry with smaller pieces.",
                        True,
                    )
                elif call.name == SUBMIT and submit_schema:
                    problems = schema_errors(call.args, submit_schema) or (check(call.args) if check else [])
                    bus.emit("gate", passed=not problems, problems=problems)
                    if not problems:
                        return call.args
                    output, error = (
                        "Submission rejected. Fix these problems and submit again:\n- " + "\n- ".join(problems),
                        True,
                    )
                else:
                    output, error = _call_tool(bus, by_name.get(call.name), call)
            except BaseException:  # interrupted: keep the transcript valid (every call needs a result), then stop
                messages.extend(
                    {"role": "tool", "id": c.id, "content": "Interrupted.", "is_error": True} for c in calls[i:]
                )
                raise
            messages.append({"role": "tool", "id": call.id, "content": clip(output), "is_error": error})

    raise AgentError(f"turn budget of {cfg.max_turns} exhausted without a result")


def _call_tool(bus, tool: Tool | None, call) -> tuple[str, bool]:
    """Run one tool call. Returns (output, is_error); never raises for an ordinary failure."""
    if tool is None:
        return f"Unknown tool {call.name!r}.", True
    problems = schema_errors(call.args, tool.schema)
    if problems:
        return "Invalid arguments:\n- " + "\n- ".join(problems), True
    try:
        bus.emit("pre_tool", tool=tool.name, args=call.args)  # whole arguments: a policy hook must see all of a command
        output = tool.fn(call.args)
        bus.emit("post_tool", tool=tool.name, ok=True, output=output[:500])
        return fence(tool.name, output), False  # clip() later keeps both ends, so the tags survive it
    except Halt:  # the operator's stop ends the run; it is not a policy veto for the agent to work around
        raise
    except Block as veto:
        bus.emit("post_tool", tool=tool.name, ok=False, output=f"blocked by hook: {veto}")
        return f"Blocked by policy: {veto}", True
    except Exception as exc:
        bus.emit(
            "error",
            where=f"tool:{tool.name}",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
            will_retry=True,
        )
        return f"{type(exc).__name__}: {exc}", True
