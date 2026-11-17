"""Entry point: `python -m harness` (terminal chat) or `python -m harness --serve` (web UI + HTTP gateway)."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from .config import Config
from .events import render
from .orchestrator import HELP, Orchestrator

DIM, RESET = ("\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "")


class Printer:
    """Interleaves the orchestrator's streamed text with progress lines without mangling either."""

    def __init__(self):
        self.midline = False

    def text(self, chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()
        self.midline = not chunk.endswith("\n")

    def event(self, event: dict) -> None:
        line = render(event)
        if line:
            self.text(("\n" if self.midline else "") + f"{DIM}{line}{RESET}\n")


def load_hooks(path: str, bus) -> None:
    """Import a user hook file and let it subscribe to the bus: it must define register(bus)."""
    spec = importlib.util.spec_from_file_location("user_hooks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register(bus)


def main() -> None:
    parser = argparse.ArgumentParser(prog="harness", description="Research agent team")
    parser.add_argument("--provider", help="anthropic | openai (any OpenAI-compatible server, e.g. Ollama) | scripted (offline demo)")
    parser.add_argument("--model", help="one model for every agent (anthropic default: a model per role, see --roles)")
    parser.add_argument("--roles", help="per-role models: role=[provider+]model[@effort],... e.g. architect=claude-opus-5-5@high,auditor=openai+qwen3:4b")
    parser.add_argument("--base-url", help="for --provider openai, e.g. http://localhost:11434/v1")
    parser.add_argument("--sandbox", help="auto | bwrap | docker | none")
    parser.add_argument("--sources", type=Path, help="directory of reference files the agents may read")
    parser.add_argument("--session", help="resume an existing session id (a directory under runs/)")
    parser.add_argument("--hooks", help="python file defining register(bus)")
    parser.add_argument("--pause-after", help="stop after this step for review, e.g. prd; `resume` continues")
    parser.add_argument("--serve", action="store_true", help="serve the web UI / HTTP gateway instead of the terminal chat")
    parser.add_argument("--host", default="127.0.0.1", help="bind address for --serve; 0.0.0.0 allows remote access")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    chosen = {k: v for k, v in vars(args).items() if v is not None and k in ("provider", "model", "roles", "base_url", "sandbox", "sources", "pause_after")}
    orchestrator = Orchestrator(Config.load(**chosen), args.session)
    if args.hooks:
        load_hooks(args.hooks, orchestrator.engine.bus)

    if args.serve:
        from .server import serve

        return serve(orchestrator, args.host, args.port)

    out = Printer()
    orchestrator.engine.bus.on(out.event)
    print(f"Session {orchestrator.session} · {orchestrator.cfg.provider} · files in {orchestrator.engine.dir}\nDescribe what to build, ask a question, or type /help.")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return print()
        if line in ("/quit", "/exit"):
            return
        if not line:
            continue
        try:
            if line.startswith("/"):
                print(HELP if line == "/help" else orchestrator.command(line))
            else:
                orchestrator.send(line, out.text)
                out.text("\n" if out.midline else "")
        except KeyboardInterrupt:
            out.text("\n⏸ Interrupted. Progress is saved; say \"resume\" to continue.\n")
        except Exception as exc:  # a failed turn must not end the session
            out.text(f"\n✖ {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
