"""The tools agents act with, and the isolation around them.

Three trust boundaries live here:
- file tools are confined to the run's workspace (read/write) and the optional
  sources directory (read-only);
- commands run in a sandbox with no network, no home directory and no
  environment variables (so no API keys);
- web fetches are limited to public http(s) addresses.
"""

from __future__ import annotations

import functools
import html
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .agent import Tool, clip

SKIP = {"__pycache__", ".git"}


class Workspace:
    """The files agents can see: `root` (read/write) and `sources/` (read-only, optional)."""

    def __init__(self, root: Path, sources: Path | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources = Path(sources).resolve() if sources else None

    def resolve(self, path: str, write: bool = False) -> Path:
        """Map an agent-supplied path to a real one, refusing anything outside the allowed roots."""
        base, rel = self.root, path
        if self.sources and (path == "sources" or path.startswith("sources/")):
            if write:
                raise PermissionError("sources/ is read-only")
            base, rel = self.sources, path[len("sources/") :] or "."
        target = (base / rel).resolve()  # resolves `..` and symlinks before the check
        if not target.is_relative_to(base):
            raise PermissionError(f"{path}: outside the workspace; use a relative path")
        return target

    def name(self, target: Path) -> str:
        """The path an agent uses for a real file."""
        if self.sources and target.is_relative_to(self.sources):
            return str(Path("sources") / target.relative_to(self.sources))
        return str(target.relative_to(self.root))

    def files(self, path: str = ".", with_sources: bool = True) -> list[str]:
        base = self.resolve(path)
        found = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file() and not SKIP & set(p.parts)]
        names = sorted(self.name(p) for p in found)
        if path == "." and self.sources and with_sources:
            names += self.files("sources")
        return names


def make_tools(ws: Workspace, cfg, consulted: list, writable: set | None = None) -> dict[str, Tool]:
    """Build the tool set for one agent run.

    `consulted` collects every file and URL the agent actually opened, so a rule can later check
    that cited sources are real. `writable` limits write_file to an engineer's own files.
    """

    def list_files(a):
        return "\n".join(ws.files(a.get("path", "."))[:400]) or "(no files)"

    def read_file(a):
        target = ws.resolve(a["path"])
        consulted.append(ws.name(target))
        return clip(target.read_text(errors="replace"), 40_000)

    def search_files(a):
        pattern, hits = re.compile(a["pattern"]), []
        for name in ws.files(a.get("path", ".")):
            for n, line in enumerate(ws.resolve(name).read_text(errors="replace").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{name}:{n}: {line.strip()[:200]}")
                    consulted.append(name)
        return "\n".join(hits[:100]) or "(no matches)"

    def write_file(a):
        target = ws.resolve(a["path"], write=True)
        if writable is not None and ws.name(target) not in writable:
            raise PermissionError(f"{a['path']} is not one of your work item's files: {sorted(writable)}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(a["content"])
        return f"wrote {ws.name(target)} ({len(a['content'])} characters)"

    def run_command(a):
        code, output = run_sandboxed(cfg, ws.root, a["command"])
        return f"exit code {code}\n{output}"

    def web_search(a):
        results = search_web(a["query"])  # results are leads, not sources: a page counts once it is fetched
        return "\n\n".join(f"{r['title']}\n{r['url']}\n{r['snippet']}" for r in results) or "(no results)"

    def web_fetch(a):
        page = http_get(a["url"])
        consulted.append(a["url"])
        return clip(html_to_text(page) if "<html" in page[:2000].lower() or "<!doctype" in page[:200].lower() else page)

    path = {"type": "string", "description": "Relative path. Reference files, if any, are under sources/."}
    tools = [
        Tool("list_files", "List files under a directory of the workspace (default: everything).", _obj(path=path, _optional=["path"]), list_files),
        Tool("read_file", "Read a text file from the workspace or sources/.", _obj(path=path), read_file),
        Tool("search_files", "Search files with a regular expression. Returns matching lines.", _obj(pattern={"type": "string"}, path=path, _optional=["path"]), search_files),
        Tool("write_file", "Create or overwrite a file in the workspace with the complete content.", _obj(path=path, content={"type": "string"}), write_file),
        Tool("run_command", "Run a shell command in the workspace inside an offline sandbox (Python standard library only). Returns the exit code and output.", _obj(command={"type": "string"}), run_command),
        Tool("web_search", "Search the web. Returns titles, URLs and snippets. Fetch a page before citing it.", _obj(query={"type": "string"}), web_search),
        Tool("web_fetch", "Fetch a public web page and return its text.", _obj(url={"type": "string"}), web_fetch),
    ]
    return {t.name: t for t in tools}


def _obj(_optional=(), **properties) -> dict:
    return {"type": "object", "properties": properties, "required": [k for k in properties if k not in _optional]}


# --- sandbox -----------------------------------------------------------------------------------


def run_sandboxed(cfg, workspace: Path, command: str) -> tuple[int, str]:
    """Run a shell command with the workspace as the only writable place. Returns (exit code, output)."""
    mode = detect_sandbox() if cfg.sandbox == "auto" else cfg.sandbox
    env = None
    if mode == "bwrap":
        argv = _bwrap(workspace) + ["sh", "-c", command]
    elif mode == "docker":
        user = f"{os.getuid()}:{os.getgid()}"
        argv = ["docker", "run", "--rm", "--network", "none", "--memory", "1g", "--cpus", "2", "--user", user, "-e", "HOME=/tmp"]
        argv += ["-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{workspace}:/work", "-w", "/work", cfg.docker_image]
        argv += ["timeout", str(cfg.command_timeout), "sh", "-c", command]  # the container enforces its own timeout
    elif mode == "none":  # explicit opt-out; still no secrets in the environment
        argv, env = ["sh", "-c", command], {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"}
    else:
        raise ValueError(f"unknown sandbox mode {mode!r}")
    try:
        # wall-clock timeout only (docker adds memory/cpu caps); add cgroup limits or a microVM for hostile code
        done = subprocess.run(argv, cwd=workspace, env=env, capture_output=True, text=True, errors="replace", timeout=cfg.command_timeout + 15)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {cfg.command_timeout}s"
    return done.returncode, clip((done.stdout + done.stderr).strip() or "(no output)")


def _bwrap(workspace: Path) -> list[str]:
    """bubblewrap: read-only /usr, the workspace at /work, fresh namespaces (no network), empty environment."""
    argv = ["bwrap", "--ro-bind", "/usr", "/usr", "--ro-bind-try", "/etc/alternatives", "/etc/alternatives", "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache"]
    for top in ("/bin", "/sbin", "/lib", "/lib32", "/lib64"):  # usually symlinks into /usr; mirror the host layout
        if os.path.islink(top):
            argv += ["--symlink", os.readlink(top), top]
        elif os.path.exists(top):
            argv += ["--ro-bind", top, top]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--bind", str(workspace), "/work", "--chdir", "/work"]
    argv += ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    return argv + ["--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]


@functools.lru_cache(maxsize=None)
def detect_sandbox() -> str:
    """Pick the strongest sandbox that actually works on this machine."""
    if shutil.which("bwrap"):
        with tempfile.TemporaryDirectory() as tmp:  # some kernels forbid unprivileged namespaces: probe, don't assume
            if subprocess.run(_bwrap(Path(tmp)) + ["true"], capture_output=True).returncode == 0:
                return "bwrap"
    if shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
        return "docker"
    raise RuntimeError("no sandbox available (install bubblewrap or docker), or set HARNESS_SANDBOX=none to run generated code on the host")


# --- web ---------------------------------------------------------------------------------------

_BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


def check_public(url: str) -> None:
    """Refuse URLs that could reach this machine or its network (SSRF guard)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PermissionError(f"{url}: only http(s) URLs are allowed")
    # checks at resolve time only; pin the resolved IP for the request if DNS rebinding is in the threat model
    for info in socket.getaddrinfo(parts.hostname, None):
        if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
            raise PermissionError(f"{url}: resolves to a non-public address")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)  # a public page must not redirect us into the local network
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str) -> str:
    check_public(url)
    opener = urllib.request.build_opener(_CheckedRedirects)
    with opener.open(urllib.request.Request(url, headers=_BROWSER), timeout=20) as response:
        return response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8", "replace")


def search_web(query: str) -> list[dict]:
    """Keyless web search through DuckDuckGo's HTML endpoint."""
    # scrapes HTML, so a markup change breaks it; swap in a search API (Tavily, Brave) for production
    page = http_get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    results = []
    for (href, title), snippet in zip(links, snippets + [""] * len(links)):
        target = urllib.parse.parse_qs(urllib.parse.urlsplit(html.unescape(href)).query).get("uddg", [html.unescape(href)])[0]
        results.append({"title": _strip_tags(title), "url": target, "snippet": _strip_tags(snippet)})
    return results[:8]


def _strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


class _TextExtractor(HTMLParser):
    HIDDEN = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        self.hidden += tag in self.HIDDEN

    def handle_endtag(self, tag):
        self.hidden -= tag in self.HIDDEN and self.hidden > 0

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def html_to_text(page: str) -> str:
    parser = _TextExtractor()
    parser.feed(page)
    return "\n".join(parser.parts)
