"""HTTP gateway: the same orchestrator, reachable from a browser or another machine.

    GET  /        the web UI (a single page; holds no secrets)
    POST /chat    {"message": "..."} -> server-sent events: streamed text and progress lines
    GET  /status  the task board as JSON
    POST /stop    halt the run at the next task boundary (continue later with "resume")

Every endpoint except the page itself requires `Authorization: Bearer <token>`.
Given a certificate (`--tls-cert`, `--tls-key`) it serves HTTPS, so the token is not sent in the clear.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .events import Event, Halt, render

RETRY_DELAY = 0.5  # seconds a wrong token costs

PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research agent team</title>
<style>
  body { font: 15px/1.5 system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 16px;
         background: #111; color: #ddd; display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 20px; }
  main { min-width: 0; }
  #board { position: sticky; top: 16px; align-self: start; font-size: 13px; }
  #board h2 { font-size: 13px; font-weight: 600; margin: 0 0 8px; color: #aaa; }
  .card { border-left: 4px solid #555; background: #1a1a1a; border-radius: 4px; padding: 6px 10px; margin-bottom: 6px; }
  .card { cursor: pointer; }
  .card b { font-family: ui-monospace, monospace; } .card small { display: block; color: #999; }
  .running { border-color: #58a6ff; animation: pulse 1.2s infinite; } .done { border-color: #3fb950; }
  .failed { border-color: #ff7b72; } .reopened { border-color: #d29922; } .retrying { border-color: #db6d28; }
  @keyframes pulse { 50% { background: #1d2a3a; } }
  @media (max-width: 760px) { body { grid-template-columns: 1fr; } #board { position: static; order: -1; } }
  #log { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 13px; min-height: 60vh; }
  .you { color: #7cc4ff; } .event { color: #888; } .error { color: #ff7b72; }
  form { display: flex; gap: 8px; position: sticky; bottom: 0; background: #111; padding: 12px 0; }
  input, button { color: #eee; border-radius: 6px; font: inherit; }
  input { flex: 1; padding: 10px; background: #1c1c1c; border: 1px solid #333; }
  button { padding: 10px 14px; background: #2a2a2a; border: 1px solid #444; cursor: pointer; }
</style>
<main><div id="log"></div>
<form id="form">
  <input id="message" placeholder="Describe what to build, ask a question, or type /help" autofocus autocomplete="off">
  <button>Send</button><button type="button" id="stop">Stop</button>
</form></main>
<aside id="board"><h2>Task board</h2><div id="cards">no project yet</div></aside>
<script>
  const log = document.getElementById("log"), input = document.getElementById("message");
  let token = location.hash.slice(1) || sessionStorage.token ||
    prompt("Access token (printed when the server started)");
  sessionStorage.token = token;
  const auth = { Authorization: "Bearer " + token };
  function add(text, kind) {
    const span = document.createElement("span");
    span.className = kind; span.textContent = text;  // textContent: model output is never parsed as HTML
    log.append(span); window.scrollTo(0, document.body.scrollHeight);
  }
  // The board: polled from /status, so it shows delegation, retries and audit re-runs as they happen.
  const cards = document.getElementById("cards");
  // a pending task that already ran was sent back: by a failure (retrying) or by the audit (re-opened)
  function state(t) {
    return t.status === "pending" && t.runs ? (t.error ? "retrying" : "reopened") : t.status;
  }
  async function poll() {
    const response = await fetch("/status", { headers: auth }).catch(() => null);
    if (response && response.ok) {
      const report = await response.json();
      cards.replaceChildren(...report.tasks.map(t => {
        const card = document.createElement("div"), s = state(t);
        card.className = "card " + s;
        card.title = t.error || "click for its log trail";
        card.onclick = () => send("/trail " + t.id);
        card.innerHTML = "<b></b> <span></span><small></small>";
        card.children[0].textContent = t.id;
        card.children[1].textContent = s;
        card.children[2].textContent =
          `${t.agent} · runs ${t.runs} · ${t.seconds}s · ${(t.tokens / 1000).toFixed(1)}k tok`;
        return card;
      }));
      if (report.tasks.length) {
        const textContent = `${report.status} · audit re-runs since the last change ${report.audit_cycles_used}`;
        cards.append(Object.assign(document.createElement("small"), { textContent }));
      }
    }
    if (!response || response.status !== 401) setTimeout(poll, 1000);
  }
  poll();
  document.getElementById("stop").onclick = () =>
    fetch("/stop", { method: "POST", headers: auth }).then(() => add("\\n(stop requested)\\n", "event"));
  document.getElementById("form").onsubmit = (submitted) => {
    submitted.preventDefault();
    const message = input.value.trim(); if (!message) return;
    input.value = ""; send(message);
  };
  async function send(message) {
    add("\\n> " + message + "\\n", "you");
    const response = await fetch("/chat", { method: "POST", headers: auth, body: JSON.stringify({ message }) });
    if (!response.ok) return add(response.status + " " + await response.text() + "\\n", "error");
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\\n\\n"); buffer = frames.pop();
      for (const frame of frames) {
        const item = JSON.parse(frame.slice(6));
        add(item.type === "text" ? item.text : "\\n" + item.text + "\\n", item.type);
      }
    }
  }
</script>
"""


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # a client that fails the TLS handshake or hangs up is not worth a traceback on the console
        if not isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError)):
            super().handle_error(request, client_address)


def build(orchestrator, host: str, port: int, token: str, cert: str = "", key: str = "") -> ThreadingHTTPServer:
    busy = threading.Lock()  # one session, one request at a time; a session map lifts this
    denied = threading.Lock()
    stop = threading.Event()
    bus = orchestrator.engine.bus

    def halt(event):  # the stop button is just a hook that pauses before the next task
        if stop.is_set():
            raise Halt("stopped by the operator; say resume to continue")

    bus.on(halt, "task_start")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code: int, body: str, kind: str = "text/plain") -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            # bytes: any header value compares
            ok = hmac.compare_digest(self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode())
            if not ok:
                with denied:  # wrong tokens are answered one at a time, so guessing in parallel gains nothing
                    time.sleep(RETRY_DELAY)
                self.reply(401, "missing or invalid token")
            return ok

        def do_GET(self):
            if self.path == "/":
                return self.reply(200, PAGE, "text/html")
            if self.path != "/status":
                return self.reply(404, "not found")
            if self.authorized():
                self.reply(200, json.dumps(orchestrator.engine.report()), "application/json")

        def do_POST(self):
            if not self.authorized():
                return
            if self.path == "/stop":
                stop.set()
                return self.reply(200, "stopping at the next task boundary")
            if self.path != "/chat":
                return self.reply(404, "not found")
            try:
                message = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))["message"]
            except (ValueError, KeyError, TypeError):
                return self.reply(400, 'expected a JSON body like {"message": "..."}')
            command = message.startswith("/")  # slash commands only read state: answer them even mid-run
            if not command and not busy.acquire(blocking=False):
                return self.reply(409, "the team is busy with another request")
            try:
                if not command:  # a slash command asked mid-run must not cancel a pending stop
                    stop.clear()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.stream(message)
            finally:
                if not command:
                    busy.release()

        def stream(self, message: str) -> None:
            def push(kind: str, text: str) -> None:
                try:
                    self.wfile.write(f"data: {json.dumps({'type': kind, 'text': text})}\n\n".encode())
                    self.wfile.flush()
                except OSError:
                    pass  # the client went away; the run continues and its state stays on the board

            def progress(event: Event) -> None:
                line = render(event)
                if line:
                    push("event", line)

            if not message.startswith("/"):  # a command's reply must not pick up the running build's progress
                bus.on(progress)
            try:
                if message.startswith("/"):
                    push("text", orchestrator.command(message))
                else:
                    orchestrator.send(message, lambda text: push("text", text))
            except Exception as exc:
                push("error", f"{type(exc).__name__}: {exc}")
            finally:
                bus.off(progress)

    server = Server((host, port), Handler)
    if cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key or None)
        # the handshake runs in the request's own thread, so a silent client cannot hold up the accept loop
        server.socket = tls.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    return server


def serve(orchestrator, host: str, port: int, cert: str = "", key: str = "") -> None:
    token = os.environ.get("HARNESS_TOKEN") or secrets.token_urlsafe(16)
    server = build(orchestrator, host, port, token, cert, key)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    scheme = "https" if cert else "http"
    print(f"Session {orchestrator.session} · open {scheme}://{shown}:{server.server_port}/#{token}", flush=True)
    if not cert and host not in ("127.0.0.1", "localhost"):
        print(
            "Listening on a network interface over plain HTTP: "
            "pass --tls-cert and --tls-key, or use an SSH tunnel, before real remote use."
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
