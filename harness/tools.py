"""The tools agents act with, and the isolation around them.

Three trust boundaries live here:
- file tools are confined to the run's workspace (read/write) and the optional
  sources directory (read-only);
- commands run in a sandbox with no network, no home directory and no
  environment variables (so no API keys);
- web fetches are limited to public http(s) addresses, and to URLs the agent was given rather than ones it composed.
"""

from __future__ import annotations

import contextlib
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
PAGE = 16_000  # characters of a file returned per read; below the agent loop's limit, so a page is never cut again
NAT64 = ipaddress.ip_network("64:ff9b::/96")  # IPv6 addresses that wrap an IPv4 one


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


def make_tools(ws: Workspace, cfg, consulted: list, writable: set | None = None, fetchable: set | None = None) -> dict[str, Tool]:
    """Build the tool set for one agent run.

    `consulted` collects every file and URL the agent actually opened, so a rule can later check
    that cited sources are real. `writable` limits write_file to an engineer's own files.
    `fetchable` holds the URLs web_fetch may open (see web_fetch); the run's tools add to it.
    """
    fetchable = set() if fetchable is None else fetchable

    def list_files(a):
        return "\n".join(ws.files(a.get("path", "."))[:400]) or "(no files)"

    def read_file(a):
        target = ws.resolve(a["path"])
        consulted.append(ws.name(target))
        # A line longer than a page (minified JSON) is cut into page-sized pieces, so every file can be read to its end.
        lines = [line[i : i + PAGE] for line in target.read_text(errors="replace").splitlines(keepends=True) for i in range(0, len(line), PAGE)]
        first = max(a.get("offset", 1), 1) - 1
        size, last = 0, first
        while last < len(lines) and (last == first or size + len(lines[last]) <= PAGE):  # at least one line, then up to a page
            size, last = size + len(lines[last]), last + 1
        more = f"\n… [lines {first + 1}-{last} of {len(lines)}; call again with offset={last + 1} for the rest]" if last < len(lines) else ""
        text = "".join(lines[first:last])
        if ws.name(target).startswith("sources/"):  # reference files come from the user; workspace files are written by agents
            fetchable.update(urls(text))
        return text + more or "(empty)"

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
        # The sandbox mounts the whole workspace writable, so the write_file ownership rule is enforced here too:
        # whatever a command changes outside the engineer's own files is put back.
        before = _snapshot(ws.root, writable) if writable is not None else None
        try:
            code, output = run_sandboxed(cfg, ws.root, a["command"])
        finally:
            undone = _restore(ws.root, writable, before) if before is not None else []
        note = f"\nnote: files a command creates or changes outside your work item do not persist; undone: {', '.join(undone[:20])}" if undone else ""
        return f"exit code {code}\n{output}{note}"

    def web_search(a):
        results = search_web(a["query"])  # results are leads, not sources: a page counts once it is fetched
        fetchable.update(r["url"].split("#")[0] for r in results)
        return "\n\n".join(f"{r['title']}\n{r['url']}\n{r['snippet']}" for r in results) or "(no results)"

    def web_fetch(a):
        # Leaking data takes a URL the agent composed (https://evil.example/?d=<secret>). Such a URL never appears in
        # the user's messages, a search result or a reference file, so only URLs from those are opened. URLs in fetched
        # pages and workspace files do not count: either could be planted to spell out a secret one fetch at a time.
        # ponytail: the choice among allowed URLs and the search query still carry a few bits; an egress proxy closes that.
        if a["url"].split("#")[0] not in fetchable:
            raise PermissionError(f"{a['url']} did not come from the user, a search result or a reference file; fetch URLs exactly as they appear there")
        page = http_get(a["url"])
        consulted.append(a["url"])
        return clip(html_to_text(page) if "<html" in page[:2000].lower() or "<!doctype" in page[:200].lower() else page)

    path = {"type": "string", "description": "Relative path. Reference files, if any, are under sources/."}
    tools = [
        Tool("list_files", "List files under a directory of the workspace (default: everything).", _obj(path=path, _optional=["path"]), list_files),
        Tool("read_file", "Read a text file from the workspace or sources/. A long file comes in pages; pass `offset` to continue.", _obj(path=path, offset={"type": "integer", "description": "First line to return (default 1)."}, _optional=["offset"]), read_file),
        Tool("search_files", "Search files with a regular expression. Returns matching lines.", _obj(pattern={"type": "string"}, path=path, _optional=["path"]), search_files),
        Tool("write_file", "Create or overwrite a file in the workspace with the complete content.", _obj(path=path, content={"type": "string"}), write_file),
        Tool("run_command", "Run a shell command in the workspace inside an offline sandbox (Python standard library only). Returns the exit code and output.", _obj(command={"type": "string"}), run_command),
        Tool("web_search", "Search the web. Returns titles, URLs and snippets. Fetch a page before citing it.", _obj(query={"type": "string"}), web_search),
        Tool("web_fetch", "Fetch a public web page and return its text.", _obj(url={"type": "string"}), web_fetch),
    ]
    return {t.name: t for t in tools}


def _obj(_optional=(), **properties) -> dict:
    return {"type": "object", "properties": properties, "required": [k for k in properties if k not in _optional]}


def _snapshot(root: Path, own: set) -> dict:
    """Content of every file outside `own`, by workspace path. A symlink is recorded as its target, never followed."""
    # held in memory per command, contents only (not modes): fine for generated projects; an overlay mount scales further
    found = {}
    for folder, dirs, names in os.walk(root):  # walk lists a symlink to a directory with the directories, unfollowed
        for name in names + [d for d in dirs if os.path.islink(os.path.join(folder, d))]:
            path = Path(folder, name)
            rel = str(path.relative_to(root))
            if rel not in own and (path.is_symlink() or path.is_file()):
                found[rel] = os.readlink(path) if path.is_symlink() else path.read_bytes()
    return found


def _restore(root: Path, own: set, before: dict) -> list[str]:
    """Put back every file outside `own` that differs from the snapshot. Returns their names."""
    after = _snapshot(root, own)
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    real = root.resolve()
    inside = lambda name: Path(os.path.realpath((root / name).parent)) == real / Path(name).parent  # no symlink on the way
    for name in changed:  # parents first: removing a directory or symlink that took a file's place takes its children too
        path = root / name
        if inside(name):
            with contextlib.suppress(FileNotFoundError, NotADirectoryError):
                shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    for name in changed:
        if name in before and inside(name):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(before[name]) if isinstance(before[name], bytes) else os.symlink(before[name], root / name)
    return changed


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
    output = (done.stdout + done.stderr).strip() or "(no output)"
    if mode == "bwrap" and done.returncode and output.startswith("bwrap:"):  # the sandbox failed, not the command
        raise RuntimeError(f"sandbox failure: {output[:500]}")
    return done.returncode, clip(output)


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


def urls(text: str) -> set[str]:
    """The http(s) URLs in a text, without fragments or trailing punctuation."""
    found = set()
    for url in re.findall(r"https?://[^\s<>\"'`]+", text):
        url = url.split("#")[0].rstrip(".,;:!?]")
        while url.endswith(")") and url.count("(") < url.count(")"):  # keep Wikipedia's Foo_(bar), drop "(see https://x)"
            url = url[:-1].rstrip(".,;:!?]")
        found.add(url)
    return found


def check_public(url: str) -> None:
    """Refuse URLs that could reach this machine or its network (SSRF guard)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PermissionError(f"{url}: only http(s) URLs are allowed")
    # checks at resolve time only; pin the resolved IP for the request if DNS rebinding is in the threat model
    for info in socket.getaddrinfo(parts.hostname, None):
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if address in NAT64:  # judge the IPv4 address inside, or 64:ff9b::7f00:1 reaches 127.0.0.1 through a gateway
            address = ipaddress.ip_address(int(address) & 0xFFFFFFFF)
        if not address.is_global:
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
