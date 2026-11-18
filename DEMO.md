# Demo script

A 35-minute live walkthrough that shows each requirement of the brief. All commands run from the repository root,
with `ANTHROPIC_API_KEY` set. Browser zoom 125–150%. Two terminals: **T1** (server), **T2** (side demos).

## Before the call (10 min)
- [ ] Clean tree, `.venv/bin/python -m unittest discover -s tests -q` → 85 OK
- [ ] Fallback session ready (a finished real build under `runs/`, e.g. `20261117-154244`). If the API fails live:
      `.venv/bin/python -m harness --serve --session 20261117-154244`
- [ ] Empty scripted memory so the scripted team makes every mistake: `rm -rf /tmp/demo`

## 0–2 min · Start the live build (streaming orchestrator, entry point)
T1: `.venv/bin/python -m harness --serve --hooks examples/hooks.py`
Open the printed `http://localhost:8765/#<token>` link. Type:
> Build an MCP server (stdio transport, JSON-RPC 2.0) for accessing an Obsidian vault. Use a small mock vault
> directory of markdown notes with tags and [[wikilinks]]. Tools: list_notes, read_note, search_notes (text or #tag).

## 2–14 min · Architecture talk while the board moves (delegation, task tracking)
Point at the board: research → plan → prd → spec → impl:W1..Wn → audit, one expert per card
(researcher, planner, product, architect, engineer, auditor). Colours: blue running, orange retry,
amber re-opened by the audit, green done. **Click any card → its log trail.**
Talking points:
- The pipeline is code, not a prompt: the model never picks the next step.
- Handoffs are schema-validated JSON (`submit`), checked by a gate before the next agent sees them.
- Failures handled at the cheapest level: tool error → gate rejection → task retry → audit re-run → ask the user.
- Rules table in `harness/rules.py`: checked rules (code) and judged rules (auditor agent, must quote evidence).
- The harness runs the tests itself in a sandbox: "tests pass" is observed, not reported.

## 14–20 min · What was built (Planning → PRD → Spec → Implementation, view the result)
In the UI: `/show artifacts/plan`, `/show artifacts/prd`, `/show artifacts/spec`, `/show artifacts/audit`, `/show`
then one source file. `/status` → `run_command` and the workspace path.
T2: run the generated thing once (MCP over stdio):
`cd runs/<session>/workspace && printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | <run_command>`

## 20–24 min · Question answering and change request
> Which tools does the server expose, and how was search tested?
> Add a get_backlinks tool that returns the notes linking to a given note.
Watch: the affected tasks turn amber and re-run; the audit re-checks; the request is passed to the auditor.
A change to the spec re-opens every work item after it (README, Known limits): for a short demo, aim the change at
one work item, e.g. "change impl:W3 …", and raise the budget first if needed (`HARNESS_MAX_TOKENS=10000000`).

## 24–30 min · Recovery paths, guaranteed (exceptions, tracebacks, trail, auditor re-runs)
T2: `HARNESS_MEMORY=/tmp/demo/memory.jsonl .venv/bin/python -m harness --provider scripted`
> Build an API for consulting a mock user database
Shows: gate rejection [RES-GROUNDED], planner crash + retry, audit finding [IMPL-TESTS] → re-run.
Then: `/errors` (tracebacks) · `/trail planner` · `/status` (recoveries line).
Run it a second time with the same memory → mistakes gone, "lessons recalled" (self-improvement). `/memory`.

## 30–35 min · Bonus items
- Memory / self-improvement: `/memory`, `examples/self-improvement/` (real models, before vs after lessons).
- Hooks: show `examples/hooks.py` (token budget + command deny-list), loaded in T1 with `--hooks`.
- Gateway / remote: T2 `curl -s -w ' [%{http_code}]\n' localhost:8765/status` → 401 without the token.
  Remote: `--host 0.0.0.0` (+ `--tls-cert/--tls-key` for HTTPS), open on a phone.
- Sandbox (fork bomb + 3 GB allocation, contained for real):
  `.venv/bin/python -m unittest tests.test_harness.Boundaries.test_a_sandboxed_command_cannot_exhaust_the_machines_memory_or_processes -v`
- pi backend: `examples/pi-build/transcript.txt` (full build through pi-ai), `--provider pi`.
- Open-source / local model: `--provider openai --model gpt-oss:20b` (Ollama), or `--roles auditor=openai+gpt-oss:20b`.

## 35+ min · Performance and future work
`/status` in the UI: model calls and tokens per role, cached share of input, seconds per task, recoveries.
Reference numbers from the real builds: a medium build (the MCP server above) takes about 20 minutes and
116 model calls, about 2.6 million tokens and $6–7 on the Claude line-up; about two thirds of the input is read
from the prompt cache. Future work: README "Known limits and next steps".
