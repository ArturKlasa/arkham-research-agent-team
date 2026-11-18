# Demo script

A 40-minute live walkthrough that shows each requirement of the brief. All commands run from the repository root,
with `ANTHROPIC_API_KEY` set. Browser zoom 125–150%. Two terminals: **T1** (main server), **T2** (side demos).
`HARNESS_TOKEN=demo` keeps the web link the same across restarts: `http://localhost:8765/#demo`.

## Before the call (10 min)
- [ ] Clean tree, `.venv/bin/python -m unittest discover -s tests -q` → 86 OK
- [ ] Fallback session ready: `20261117-184352`, a finished real build of the todo app below (it ended with one
      open audit finding, which makes a ready-made change request). If the API fails live:
      `HARNESS_TOKEN=demo .venv/bin/python -m harness --serve --session 20261117-184352`
- [ ] Empty scripted memory so the scripted team makes every mistake: `rm -rf /tmp/demo`

## 0–2 min · Start the live build (streaming orchestrator, entry point)
T1: `HARNESS_TOKEN=demo .venv/bin/python -m harness --serve --hooks examples/hooks.py`
Open `http://localhost:8765/#demo` and type:
> Build a simple todo app

A real build takes about 15 minutes and $4 (reference run: 84 model calls, two audit re-runs).

## 2–14 min · Architecture talk while the board moves (delegation, task tracking)
Point at the board: research → plan → prd → spec → impl:W1..Wn → audit, one expert per card
(researcher, planner, product, architect, engineer, auditor). The legend explains the card colours; progress lines
are coloured too (red rejection or failure, orange retry, amber audit, green done, purple lesson).
**Click any card → its log trail.** The buttons above the input run `/status`, `/show`, `/errors`, `/memory`.
Talking points:
- The pipeline is code, not a prompt: the model never picks the next step.
- The PRD numbers the requirements (R1, R2 …); the spec splits the work into work items (W1, W2 …), each owning its
  files and tracing to requirements. One engineer task per work item.
- Handoffs are schema-validated JSON (`submit`), checked by a gate before the next agent sees them.
- Failures handled at the cheapest level: tool error → gate rejection → task retry → audit re-run → ask the user.
- Rules table in `harness/rules.py`: checked rules (code) and judged rules (auditor agent, must quote evidence).
- The harness runs the tests itself in a sandbox: "tests pass" is observed, not reported.
- An audit finding re-opens the step it blames (in the reference run: the PRD, twice) and everything after it.

## 14–19 min · What was built (Planning → PRD → Spec → Implementation, view the result)
In the UI: `/show artifacts/plan`, `/show artifacts/prd`, `/show artifacts/spec`, `/show artifacts/audit`, `/show`
then one source file. `/status` → the workspace path.
T2: run the generated app once (its `README.md` in the workspace lists the commands). `TODO_FILE` keeps its data
file out of the workspace, where a later audit would find it:
`cd runs/<session>/workspace && export TODO_FILE=/tmp/demo/todos.json && python3 -m todo add "Buy milk" && python3 -m todo list && cd -`

## 19–23 min · Question answering and change request
> How are ids kept unique after a delete, and where is that tested?

If the audit left an open finding, answer the orchestrator's question with "yes, fix it". Otherwise ask for a small
change to one work item (`/show artifacts/spec` shows which item owns which file), e.g.
> change impl:W3: make `list` print "No todos yet" when the list is empty

Watch: only that work item turns amber and re-runs, then the audit re-checks it with the request in hand. A change to
the PRD or the spec would re-open every work item after it (README, Known limits).

## 23–27 min · Token budget: stop, restart, resume
T2: `HARNESS_TOKEN=demo HARNESS_MAX_TOKENS=50000 .venv/bin/python -m harness --serve --port 8766`
Open `http://localhost:8766/#demo`, type `Build a simple todo app`, note the session id T2 printed.
After about 3 minutes the next task is refused before any model call: its card turns red with
`token budget spent (… of 50000); raise HARNESS_MAX_TOKENS and resume`.
Ctrl-C in T2, then reopen the same session with a larger budget:
`HARNESS_TOKEN=demo HARNESS_MAX_TOKENS=5000000 .venv/bin/python -m harness --serve --port 8766 --session <id>`
Reload the tab: the board is as it was. Type `resume`: finished tasks stay done, the refused one continues.
Point out: the process was restarted and the board, trail, workspace and chat all came back from `runs/<id>/`.
Press **Stop** once the point is made (it pauses before the next task).

## 27–32 min · Recovery paths, guaranteed (exceptions, tracebacks, trail, auditor re-runs)
T2: `HARNESS_MEMORY=/tmp/demo/memory.jsonl .venv/bin/python -m harness --provider scripted`
> Build an API for consulting a mock user database

The scripted team uses no model: it makes one mistake of every kind on purpose, in about two seconds.
Shows: gate rejection [RES-GROUNDED], planner crash + retry, model-call retry, audit findings [IMPL-TESTS] and
[IMPL-SPEC] → re-runs. Then: `/errors` (tracebacks) · `/trail planner` · `/status` (recoveries line).
Run it a second time with the same memory → mistakes gone, "lessons recalled" (self-improvement). `/memory`.

## 32–37 min · Bonus items
- Memory / self-improvement: `/memory`, `examples/self-improvement/` (real models, before vs after lessons).
- Hooks: show `examples/hooks.py` (token budget + command deny-list), loaded in T1 with `--hooks`.
- Human review between steps: `--pause-after prd` stops after the PRD; `resume` continues.
- Gateway / remote: T2 `curl -s -w ' [%{http_code}]\n' localhost:8765/status` → 401 without the token.
  Remote: `--host 0.0.0.0` (+ `--tls-cert/--tls-key` for HTTPS), open on a phone.
- Sandbox (fork bomb + 3 GB allocation, contained for real):
  `.venv/bin/python -m unittest tests.test_harness.Boundaries.test_a_sandboxed_command_cannot_exhaust_the_machines_memory_or_processes -v`
- pi backend: `examples/pi-build/transcript.txt` (full build through pi-ai), `--provider pi`.
- Open-source / local model: `--provider openai --model gpt-oss:20b` (Ollama), or `--roles auditor=openai+gpt-oss:20b`.

## 37+ min · Performance and future work
`/status` in the UI: model calls and tokens per role, cached share of input, seconds per task, recoveries.
Reference numbers from the real builds on the Claude line-up:
- Todo app (`20261117-184352`): about 15 minutes of task time, 84 model calls, 1.17 million input tokens (about two
  thirds from the prompt cache) and 105 thousand output, about $4. Two PRD findings each re-ran all four work items.
- MCP server for an Obsidian vault (`20261117-154244`): about 20 minutes, 116 model calls, about 2.6 million tokens,
  $6–7.

Future work: README "Known limits and next steps", first of all re-running only the work items a change touches.
