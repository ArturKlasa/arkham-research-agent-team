"""The audit sweep: go over every step of the pipeline and validate the rules.

Order matters, cheapest and most certain first:

1. checked rules on every step (deterministic, free);
2. the test suite, run by the harness itself in the sandbox, so a passing
   suite is observed rather than taken on an engineer's word;
3. judged rules, one independent auditor-agent run per task.

The sweep stops before a more expensive phase when a cheaper one already
found problems: there is no point paying a model to judge work that is
mechanically broken and about to be redone.

A finding is {"rule", "task", "evidence", "fix"}; `task` is the task to blame,
which the engine re-opens together with everything downstream of it.
"""

from __future__ import annotations

import json
import re

from .agent import run_agent
from .experts import EXPERTS, TEXT, arr, obj
from .llm import for_role
from .rules import SUITE, judged, violations
from .tools import make_tools, run_sandboxed


def sweep(eng) -> list[dict]:
    tasks = eng.board.pipeline()

    findings = [_finding(rule.id, task.id, problem, rule.text) for task in tasks for rule, problem in violations(task.result, task, eng)]
    findings = findings or run_suite(eng)
    if findings:
        return findings

    stale: set[str] = set()  # downstream of a failed task: will be redone, so judging it now is wasted
    for task in tasks:
        if task.id in stale or task.meta.get("judged") == task.version:  # unchanged since it last passed
            continue
        failed = [v for v in judge(eng, task) if not v["passed"]]
        findings += [_finding(v["rule"], task.id, v["evidence"], v["fix"]) for v in failed]
        if failed:
            stale |= eng.board.dependents(task.id)
        else:
            task.meta["judged"] = task.version
            eng.board.save()  # keep progress: a crash mid-sweep does not re-judge passed tasks
    return findings


def run_suite(eng) -> list[dict]:
    """Run the spec's test command. A failure is blamed on the work items whose files show up in the output."""
    command = eng.board.tasks["spec"].result["test_command"]
    code, output = run_sandboxed(eng.cfg, eng.ws.root, command)
    eng.bus.emit("suite", command=command, exit_code=code, output=output[-3000:])
    tested = re.search(r"\bRan [1-9]\d* tests?\b|\b[1-9]\d* passed\b", output)  # unittest's summary line, or pytest's
    if code == 0 and tested:
        return []
    builders = [t for t in eng.board.pipeline() if t.kind == "implement"]
    if not tested and (code == 0 or "Ran 0 tests" in output):
        # A suite that tests nothing proves nothing. The spec owns the command and the test layout; the test files may be at fault too.
        evidence = f"`{command}` exited with code {code} without running a single test:\n{output[-3000:]}"
        fix = "Make test_command run the unittest suite from the workspace root, and make the test files discoverable by it (test*.py with TestCase classes)."
        owners = [t.id for t in builders if any("test" in f.rsplit("/", 1)[-1] for f in t.meta["item"]["files"])]
        return [_finding(SUITE.id, task_id, evidence, fix) for task_id in ["spec"] + owners]
    named = {t.id for t in builders if any(re.search(r"(?<![\w-])" + re.escape(f), output) for f in t.meta["item"]["files"])}
    # A traceback names the test file, but the defect may be in code the test exercises: include everything the named items build on.
    blamed = named | {t.id for t in builders if eng.board.dependents(t.id) & named}
    evidence = f"`{command}` exited with code {code}:\n{output[-3000:]}"
    return [_finding(SUITE.id, t.id, evidence, "Run the full test command and make it pass before submitting.") for t in builders if t.id in blamed or not named]


def judge(eng, task) -> list[dict]:
    """One auditor-agent run: a verdict with evidence for each judged rule of the task's step."""
    rules = judged(task.kind)
    if not rules:
        return []
    auditor = EXPERTS["auditor"]
    upstream = {dep: eng.board.tasks[dep].result for dep in EXPERTS[task.agent].needs}
    brief = "\n\n".join(
        part
        for part in [
            f"# Project task\n{eng.board.meta['task']}",
            f"# Step under audit\n{task.id}, produced by the {task.agent}." + (f"\nWork item:\n{json.dumps(task.meta['item'], indent=1)}\nIts files are in the workspace; read them." if task.kind == "implement" else ""),
            f"# What that step was given\n{json.dumps(upstream, indent=1)}",
            eng.changes(task),
            f"# Output under audit\n{json.dumps(task.result, indent=1)}",
            "# Rules to judge\n" + "\n".join(f"- {r.id}: {r.text}" for r in rules),
            "Submit exactly one verdict per rule. For a failed rule, `evidence` quotes the defect and `fix` says what to change.",
        ]
        if part
    )
    ids = [r.id for r in rules]
    schema = obj(verdicts=arr(obj(rule={"type": "string", "enum": ids}, passed={"type": "boolean"}, evidence=TEXT, fix={"type": "string"}), 1))

    def complete(payload):  # the auditor is validated too: no rule skipped, none judged twice
        got = sorted(v["rule"] for v in payload["verdicts"])
        return [] if got == sorted(ids) else [f"expected exactly one verdict for each of {ids}, got {got}"]

    tools = make_tools(eng.ws, eng.cfg, consulted=[])
    result = run_agent(for_role(eng.llm, "auditor"), eng.bus, eng.cfg, auditor.system, [{"role": "user", "content": brief}], [tools[n] for n in auditor.tools], schema, complete)
    return result["verdicts"]


def _finding(rule: str, task: str, evidence: str, fix: str) -> dict:
    return {"rule": rule, "task": task, "evidence": evidence, "fix": fix}
