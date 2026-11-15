"""Example hooks: `python -m harness --hooks examples/hooks.py`.

A hook is a function that receives every event (or only the types it names).
Raising Block from a `pre_tool` or `task_start` hook vetoes that action: a
vetoed tool call is returned to the agent as an error, a vetoed task fails.
"""

from harness.events import Block

TOKEN_BUDGET = 2_000_000
DENIED = ("pip install", "curl ", "wget ", "rm -rf /")


def register(bus):
    spent = {"tokens": 0}

    def count_tokens(event):
        spent["tokens"] += event["usage"]["in"] + event["usage"]["out"]

    def enforce_budget(event):
        if spent["tokens"] > TOKEN_BUDGET:
            raise Block(f"token budget of {TOKEN_BUDGET} exhausted")

    def deny_commands(event):
        command = event["args"].get("command", "") if event["tool"] == "run_command" else ""
        if any(fragment in command for fragment in DENIED):
            raise Block(f"command not allowed by policy: {command[:80]}")

    bus.on(count_tokens, "llm")
    bus.on(enforce_budget, "task_start")
    bus.on(deny_commands, "pre_tool")
