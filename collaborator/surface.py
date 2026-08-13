"""The page — ② Stage B. A localhost-only web surface over a running Host (``Collaborator``).

You launch one command, it prints a URL, you open it, you type a job to Sal, and you WATCH
the governed steps happen live — served only to your own machine, only to you.

This module is a WORKER surface, never an AUTHORITY path (P-01). It only ever calls
``host.submit(text)`` (queue a governed turn) and ``host.snapshot()`` (the render dict). It
imports NOTHING from governance / policycaps and cannot grant a capability, loosen a leash,
or set an autonomy flag. The single authority boundary stays ``govern_action`` inside the
Host; the page is your hand on the wheel, not a second wheel.

The hardened door (design + external design panel: red-team/collaborator/08-seam-page-design.md):
  * bind 127.0.0.1 ONLY (never 0.0.0.0);
  * a SINGLE-USE bootstrap token in the launch URL (``?k=``) that, on first load, is spent and
    exchanged for an ``HttpOnly; SameSite=Strict`` session cookie — so the durable secret never
    lives in a URL / JS / history / Referer, and an HttpOnly cookie is unreadable by any XSS;
  * CSRF is walled twice: the SameSite=Strict cookie (a foreign origin can't get it sent) AND a
    per-session CSRF token the page sends as the custom header ``X-Sal-Token`` (unforgeable
    cross-origin without a CORS preflight, and we send no permissive CORS headers);
  * a strict Host-header allowlist (anti DNS-rebinding) and an Origin pin on ``/submit``;
  * a strict CSP + no-referrer + nosniff, and the page renders snapshot strings via textContent
    only (no XSS surface from hostile model output);
  * availability is bounded: a pending-work 429 cap, a per-request timeout + bounded concurrency
    (anti-slowloris), and a Content-Length-checked 64 KiB body cap — the watch surface can never
    be darkened into hiding a held action.
"""

from __future__ import annotations

import http.server
import json
import secrets
import threading
from urllib.parse import parse_qs, urlparse

# The ONLY import from the rest of the Collaborator: the task-state constants, so the pending-
# work cap counts non-terminal tasks. NOTHING from governance/policycaps (P-01 — asserted in a
# test). The surface calls only host.submit()/host.snapshot().
from collaborator.host import CANCELLED, DONE, FAILED

COLLABORATOR_SURFACE_VERSION = "0.1.0"

_TERMINAL = frozenset({DONE, FAILED, CANCELLED})

DEFAULT_MAX_PENDING = 32
DEFAULT_BODY_CAP = 64 * 1024          # 64 KiB
DEFAULT_REQUEST_TIMEOUT = 5.0         # seconds — the WHOLE request (headers included). A legit
                                      # loopback request completes in ~ms; this bounds a slowloris
                                      # (even an UNAUTHENTICATED one) to a 5 s slot-hold, so the
                                      # bounded pool self-heals rather than staying dark.
BODY_READ_TIMEOUT = 5.0               # seconds — a real (small, local) body arrives at once; a
                                      # stalled/lying Content-Length can't pin a slot for longer
DEFAULT_MAX_CONNECTIONS = 16          # bounded concurrency — anti thread/FD exhaustion

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

_COOKIE_NAME = "sal_session"
_CSRF_HEADER = "X-Sal-Token"

# POST /control (Stage C) — the ENTIRE control surface, a fixed allowlist mapping an action name
# to a Host control method and the ORDERED string args it takes. Every method here only RESTRICTS
# (pause/resume/decline/veto) or EXPRESSES host config (set_leash — cap-bounded at govern_action;
# set_proactivity — surfacing only) or runs an already-permitted, re-gated action (approve/
# approve_proposal). There is NO grant/mint method on the Host, so no control can widen authority
# (P-01). The surface calls NOTHING on the Host outside this table + submit()/snapshot().
_CONTROLS = {
    "pause":            ("pause", ()),
    "resume":           ("resume", ()),
    "set_proactivity":  ("set_proactivity", ("level",)),
    "set_leash":        ("set_leash", ("tool", "leash")),
    "approve":          ("approve", ("task_id",)),
    "decline":          ("decline", ("task_id",)),
    "approve_proposal": ("approve_proposal", ("proposal_id",)),
    "veto":             ("veto", ("proposal_id",)),
}
_CONTROL_ARG_MAX = 256  # a task/proposal id, tool name, leash, or level is short; cap hostile args


# --- the bounded, loopback threading server ---------------------------------------------------

class _BoundedThreadingHTTPServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer with a hard cap on concurrent handler threads, so a slowloris flood
    cannot exhaust threads/FDs. Combined with the tight per-request socket timeout (5 s), slow
    connections are reaped rather than accumulated.

    Honest residual (panel C3): the slot is taken before auth, so an UNAUTHENTICATED local process
    can still rotate connections to briefly occupy the pool and delay a live ``/state`` poll. The
    5 s timeout bounds each hold and the pool self-heals, and a delayed poll retries — so the live
    VIEW can be transiently degraded but NEVER goes permanently dark. Critically, this touches only
    the live view: no task state is corrupted, no held action is lost or hidden from the Host's
    record, no authority is forged, and ``/state`` never lies. Truly defeating a local process bent
    on degrading a local service (which could also just spike CPU or exhaust FDs itself) is outside
    a loopback single-user threat model; the guarantee we keep is integrity, not local availability."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, max_connections: int = DEFAULT_MAX_CONNECTIONS, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._conn_sem = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address) -> None:
        # Non-blocking: when all slots are in use, DROP the connection rather than grow threads or
        # block the accept loop (which would also stall shutdown). A dropped client simply retries.
        if not self._conn_sem.acquire(blocking=False):
            try:
                self.shutdown_request(request)
            except Exception:  # noqa: BLE001
                pass
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._conn_sem.release()


class SalSurface:
    """Serve an already-running Host on a loopback socket. Construct, then ``serve_forever()``
    (blocking) or use ``serve()`` to run it on a daemon thread. Read ``.url`` for the launch
    link (carries the single-use bootstrap token)."""

    def __init__(self, host, *, host_addr: str = "127.0.0.1", port: int = 0,
                 bootstrap: "str | None" = None, max_pending: int = DEFAULT_MAX_PENDING,
                 body_cap: int = DEFAULT_BODY_CAP, request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
                 max_connections: int = DEFAULT_MAX_CONNECTIONS) -> None:
        if host_addr not in _LOOPBACK:
            raise ValueError(f"surface binds loopback only, not {host_addr!r} (never 0.0.0.0)")
        self.host = host
        self.max_pending = int(max_pending)
        self.body_cap = int(body_cap)

        # Secrets: a single-use bootstrap (URL), a session secret (HttpOnly cookie), and a CSRF
        # token (custom header). All generated in-process — never an argv, never a file.
        self._bootstrap = bootstrap or secrets.token_urlsafe(32)
        self._session = secrets.token_urlsafe(32)
        self._csrf = secrets.token_urlsafe(32)
        self._bootstrap_lock = threading.Lock()
        self._bootstrap_used = False

        handler = _make_handler(self, request_timeout)
        self._server = _BoundedThreadingHTTPServer(
            (host_addr, port), handler, max_connections=max_connections)
        self._server._surface = self  # handler reaches the surface via the server
        self.host_addr = host_addr
        self.port = self._server.server_address[1]
        self._origin = f"http://{host_addr}:{self.port}"
        # Strict Host-header allowlist (exact match; anti DNS-rebinding).
        self._allowed_hosts = frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})
        self._thread: "threading.Thread | None" = None
        self._serving = False

    # --- lifecycle -----------------------------------------------------------

    @property
    def url(self) -> str:
        return f"{self._origin}/?k={self._bootstrap}"

    def serve_forever(self) -> None:
        self._serving = True
        try:
            self._server.serve_forever()
        finally:
            self._serving = False

    def serve(self) -> "SalSurface":
        """Run the server on a daemon thread and return self (for tests / embedded launchers)."""
        self._serving = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="sal-surface", daemon=True)
        self._thread.start()
        return self

    def shutdown(self) -> None:
        # BaseServer.shutdown() blocks forever if serve_forever() was never started — only call it
        # when we're actually serving. server_close() (releasing the socket) is always safe.
        if self._serving:
            self._server.shutdown()
            self._serving = False
        self._server.server_close()

    # --- auth primitives (constant-time throughout) --------------------------

    def _consume_bootstrap(self, candidate: str) -> bool:
        """Single-use: the bootstrap authenticates exactly one first-load. A leaked/replayed
        ``?k=`` (Referer, history, scrollback) is already spent and worthless. (Trade: if a local
        prefetcher hits the URL before your navigation it spends the token — you'd re-run the
        launcher for a fresh URL. On loopback, no external unfurler can reach it, so this is rare.)
        Once a session cookie exists, ``_route_root`` uses the cookie path and never touches this,
        so reloads are unaffected."""
        with self._bootstrap_lock:
            if self._bootstrap_used:
                return False
            if not secrets.compare_digest(candidate, self._bootstrap):
                return False
            self._bootstrap_used = True
            return True

    def _session_ok(self, cookie_value: str) -> bool:
        return secrets.compare_digest(cookie_value or "", self._session)

    def _csrf_ok(self, header_value: str) -> bool:
        return secrets.compare_digest(header_value or "", self._csrf)

    def _host_ok(self, host_header: str) -> bool:
        return (host_header or "") in self._allowed_hosts

    def _origin_ok(self, origin_header: "str | None") -> bool:
        # Absent Origin is allowed (non-browser local client); a PRESENT foreign origin is refused.
        return origin_header is None or origin_header == self._origin

    def _non_terminal_task_count(self) -> int:
        tasks = self.host.snapshot().get("tasks", [])
        return sum(1 for t in tasks if t.get("state") not in _TERMINAL)


# --- the request handler ----------------------------------------------------------------------

def _make_handler(surface: SalSurface, request_timeout: float):

    class _Handler(http.server.BaseHTTPRequestHandler):
        # Do not leak server/python versions.
        server_version = "sal"
        sys_version = ""
        # HTTP/1.0 (the default): the connection closes after each response, so a client can't hold
        # a handler thread / concurrency slot open on keep-alive. Every response carries an explicit
        # Content-Length regardless. Polling opens a fresh connection each tick — fine on loopback.
        timeout = request_timeout  # StreamRequestHandler applies this to the socket (anti-slowloris)

        # --- logging: method + PATH ONLY, never the query string (which carries the ?k= token) ---
        def log_message(self, fmt, *args) -> None:  # noqa: A003
            try:
                print(f"sal-surface {self.command} {urlparse(self.path).path}")
            except Exception:  # noqa: BLE001
                pass

        # --- helpers ----------------------------------------------------------
        def _sfc(self) -> SalSurface:
            return self.server._surface

        def _security_headers(self, nonce: "str | None" = None) -> None:
            if nonce is not None:
                csp = ("default-src 'none'; connect-src 'self'; "
                       f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                       "img-src 'self' data:; base-uri 'none'; form-action 'none'")
            else:
                csp = "default-src 'none'; base-uri 'none'; form-action 'none'"
            self.send_header("Content-Security-Policy", csp)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")

        def _send(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8",
                  *, nonce: "str | None" = None, set_cookie: "str | None" = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if set_cookie is not None:
                self.send_header("Set-Cookie", set_cookie)
            self._security_headers(nonce=nonce)
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:  # noqa: BLE001 — client hung up; nothing to leak
                pass

        def _deny(self, code: int, msg: str) -> None:
            # Generic bodies — never echo a secret, a header value, or a stack detail.
            self._send(code, msg.encode("utf-8"))

        def _upgrade_requested(self) -> bool:
            up = self.headers.get("Upgrade")
            conn = (self.headers.get("Connection") or "").lower()
            return bool(up) or "upgrade" in conn

        def _cookie_session(self) -> str:
            raw = self.headers.get("Cookie")
            if not raw:
                return ""
            # Cheap, bounded manual parse — no cookie-library work on a hostile header (the header
            # line is already capped at 64 KiB by http.client). O(n), no pathological parse cost.
            for part in raw.split(";"):
                name, _, value = part.strip().partition("=")
                if name == _COOKIE_NAME:
                    return value
            return ""

        def _guard_common(self) -> bool:
            """Upgrade + Host pin. Returns True if the request may proceed."""
            if self._upgrade_requested():
                self._deny(400, "bad request")
                return False
            if not self._sfc()._host_ok(self.headers.get("Host")):
                self._deny(403, "forbidden")
                return False
            return True

        def _guard_authed(self, *, check_origin: bool = False) -> bool:
            """Full session auth for /state and /submit: cookie session + CSRF header (+Origin)."""
            if not self._guard_common():
                return False
            s = self._sfc()
            if not s._session_ok(self._cookie_session()):
                self._deny(403, "forbidden")
                return False
            if not s._csrf_ok(self.headers.get(_CSRF_HEADER)):
                self._deny(403, "forbidden")
                return False
            if check_origin and not s._origin_ok(self.headers.get("Origin")):
                self._deny(403, "forbidden")
                return False
            return True

        # --- routes -----------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                self._route_root()
            elif path == "/state":
                self._route_state()
            elif path == "/favicon.ico":
                self._send(204, b"")
            elif path in ("/submit", "/control"):
                self._deny(405, "method not allowed")
            else:
                self._deny(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/submit":
                self._route_submit()
            elif path == "/control":
                self._route_control()
            elif path in ("/", "/state"):
                self._deny(405, "method not allowed")
            else:
                self._deny(404, "not found")

        # any other verb -> 405 on a known path, 404 otherwise
        def _bad_method(self) -> None:
            path = urlparse(self.path).path
            known = path in ("/", "/state", "/submit", "/control")
            self._deny(405 if known else 404, "method not allowed" if known else "not found")

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _bad_method

        def _route_root(self) -> None:
            if not self._guard_common():
                return
            s = self._sfc()
            # Reload path: a valid session cookie already exists -> serve the page again.
            authed = s._session_ok(self._cookie_session())
            if not authed:
                # First load: require the single-use bootstrap.
                qs = parse_qs(urlparse(self.path).query)
                candidate = (qs.get("k") or [""])[0]
                if not s._consume_bootstrap(candidate):
                    self._deny(403, "forbidden")
                    return
            nonce = secrets.token_urlsafe(16)
            body = _PAGE_HTML.format(nonce=nonce, csrf=s._csrf).encode("utf-8")
            cookie = (f"{_COOKIE_NAME}={s._session}; HttpOnly; SameSite=Strict; Path=/")
            self._send(200, body, "text/html; charset=utf-8", nonce=nonce, set_cookie=cookie)

        def _route_state(self) -> None:
            # Origin-pin /state too (not just /submit) — the custom-header wall already blocks a
            # cross-origin read, but symmetry costs nothing and is one more independent layer.
            if not self._guard_authed(check_origin=True):
                return
            try:
                snap = self._sfc().host.snapshot()
                body = json.dumps(snap).encode("utf-8")
            except Exception:  # noqa: BLE001
                self._deny(500, "error")
                return
            self._send(200, body, "application/json; charset=utf-8")

        def _read_json_body(self) -> "dict | None":
            """Read + parse a JSON object body under the caps shared by /submit and /control:
            Content-Length required and capped BEFORE reading, then a TIGHT body-read deadline (a
            legit body is already in the socket buffer; a stalled/lying Content-Length gets 408
            fast rather than pinning a slot for the whole request timeout — D4 slowloris). On any
            problem it sends the right error and returns None."""
            s = self._sfc()
            raw_len = self.headers.get("Content-Length")
            if raw_len is None:
                self._deny(411, "length required")
                return None
            try:
                length = int(raw_len)
            except ValueError:
                self._deny(400, "bad request")
                return None
            if length < 0 or length > s.body_cap:
                self._deny(413, "payload too large")
                return None
            try:
                self.connection.settimeout(BODY_READ_TIMEOUT)
                body = self.rfile.read(length)
            except (TimeoutError, OSError):
                self._deny(408, "request timeout")
                return None
            if len(body) != length:  # short read — client lied / closed early
                self._deny(400, "bad request")
                return None
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:  # noqa: BLE001
                self._deny(400, "bad request")
                return None
            if not isinstance(payload, dict):
                self._deny(400, "bad request")
                return None
            return payload

        def _route_submit(self) -> None:
            if not self._guard_authed(check_origin=True):
                return
            s = self._sfc()
            # Pending-work cap (D4): refuse rather than let the queue/registry grow unbounded.
            try:
                if s._non_terminal_task_count() >= s.max_pending:
                    self._deny(429, "busy")
                    return
            except Exception:  # noqa: BLE001
                self._deny(500, "error")
                return
            payload = self._read_json_body()
            if payload is None:
                return
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                self._deny(400, "bad request")
                return
            task_id = s.host.submit(text)
            self._send(200, json.dumps({"task_id": task_id}).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _route_control(self) -> None:
            """Stage C — steer a running job. Dispatch through the FIXED `_CONTROLS` allowlist ONLY;
            an unknown action or a bad arg is a 400 and the Host is never touched. A control that the
            Host rejects (unknown tool/leash/level, task not awaiting, proposal gone) comes back
            {"ok": false} — the page just shows nothing changed. No action can grant a capability."""
            if not self._guard_authed(check_origin=True):
                return
            payload = self._read_json_body()
            if payload is None:
                return
            action = payload.get("action")
            spec = _CONTROLS.get(action) if isinstance(action, str) else None
            if spec is None:
                self._deny(400, "bad request")
                return
            method_name, keys = spec
            args = []
            for k in keys:
                v = payload.get(k)
                if not isinstance(v, str) or not v or len(v) > _CONTROL_ARG_MAX:
                    self._deny(400, "bad request")
                    return
                args.append(v)
            try:
                result = getattr(self._sfc().host, method_name)(*args)
            except Exception:  # noqa: BLE001
                self._deny(500, "error")
                return
            ok = True if result is None else bool(result)
            self._send(200, json.dumps({"ok": ok}).encode("utf-8"),
                       "application/json; charset=utf-8")

    return _Handler


# --- the page (self-contained; theme-aware; renders snapshot strings via textContent only) ----

_PAGE_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sal</title>
<style nonce="{nonce}">
  :root {{ --bg:#f7f7f8; --card:#fff; --ink:#1a1a1f; --muted:#8a8a94; --line:#e5e5ea;
    --ok:#1f9d55; --bad:#c0392b; --warn:#c77d0a; --accent:#4b6bfb; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme=light]) {{
    --bg:#16161a; --card:#1f1f26; --ink:#f0f0f4; --muted:#9a9aa6; --line:#2c2c36;
    --ok:#2ecc71; --bad:#e74c3c; --warn:#e0a028; --accent:#7d92fb; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:960px; margin:0 auto; padding:24px; }}
  h1 {{ font-size:19px; margin:0 0 2px; display:flex; align-items:center; gap:10px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:18px; }}
  .status {{ display:inline-block; padding:2px 10px; border-radius:999px; font-weight:600;
    font-size:12px; color:#fff; }}
  .status.active {{ background:var(--ok); }} .status.paused {{ background:var(--warn); }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px;
    margin-bottom:16px; }}
  .card h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--muted); margin:0 0 12px; }}
  .composer {{ display:flex; gap:8px; }}
  .composer textarea {{ flex:1; resize:vertical; min-height:44px; border:1px solid var(--line);
    border-radius:8px; padding:10px; font:inherit; background:var(--bg); color:var(--ink); }}
  .composer button {{ border:0; border-radius:8px; padding:0 18px; font-weight:600;
    background:var(--accent); color:#fff; cursor:pointer; }}
  .composer button:disabled {{ opacity:.5; cursor:default; }}
  .chip {{ display:inline-flex; gap:8px; align-items:center; border:1px solid var(--line);
    border-radius:8px; padding:5px 10px; margin:0 6px 6px 0; font-size:13px; }}
  .chip b {{ font-weight:600; }} .chip span {{ color:var(--accent); font-weight:600; }}
  .cap {{ display:inline-block; background:var(--line); border-radius:6px; padding:2px 8px;
    margin:0 6px 6px 0; font-size:12px; font-family:ui-monospace,monospace; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:720px) {{ .grid {{ grid-template-columns:1fr; }} }}
  ul {{ list-style:none; margin:0; padding:0; }}
  li {{ padding:9px 0; border-top:1px solid var(--line); }}
  li:first-child {{ border-top:0; }}
  .badge {{ color:#fff; border-radius:6px; padding:1px 7px; font-size:11px; font-weight:600; }}
  code {{ font-family:ui-monospace,monospace; font-size:13px; }}
  .leash,.origin {{ font-size:11px; color:var(--muted); }} .origin {{ font-style:italic; }}
  .sum {{ color:var(--muted); font-size:12px; margin-top:3px; word-break:break-word; }}
  .conf {{ display:inline-block; min-width:34px; font-weight:700; color:var(--accent); }}
  .tstate {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; }}
  .empty {{ color:var(--muted); font-style:italic; }}
  .counts {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:6px; }}
  .counts div {{ font-size:12px; color:var(--muted); }}
  .counts b {{ display:block; font-size:18px; color:var(--ink); }}
  footer {{ color:var(--muted); font-size:12px; margin-top:20px; }}
  .err {{ color:var(--bad); font-size:12px; }}
  .ctl {{ border:1px solid var(--line); background:var(--bg); color:var(--ink); border-radius:7px;
    padding:2px 10px; font-size:12px; font-weight:600; cursor:pointer; }}
  .ctl:hover {{ border-color:var(--accent); }}
  .ctl.approve {{ color:var(--ok); }} .ctl.danger {{ color:var(--bad); }}
  select.dial, select.leashsel {{ border:1px solid var(--line); background:var(--bg);
    color:var(--ink); border-radius:6px; padding:1px 5px; font:inherit; font-size:12px;
    font-weight:600; cursor:pointer; }}
  .rowbtns {{ display:inline-flex; gap:6px; margin-top:5px; }}
</style></head>
<body>
<div class="wrap">
  <h1>Sal <span id="status" class="status active">…</span>
    <button id="pauseBtn" class="ctl">Pause</button></h1>
  <div class="sub">One presence you talk to and watch. Every step is governed — importance can
    buy it more scrutiny, never more permission. Proactivity:
    <select id="proactivity" class="dial">
      <option value="off">off</option>
      <option value="conservative">conservative</option>
      <option value="eager">eager</option>
    </select></div>

  <div class="card">
    <h2>Give Sal a job</h2>
    <div class="composer">
      <textarea id="prompt" placeholder="Type an instruction… (Enter to send, Shift+Enter for a new line)"></textarea>
      <button id="send">Send</button>
    </div>
    <div id="sendErr" class="err"></div>
  </div>

  <div class="card">
    <h2>Leashes &amp; capabilities (host authority)</h2>
    <div id="leashes"></div>
    <div style="margin-top:8px">Capabilities: <span id="caps"></span></div>
    <div class="counts" id="counts"></div>
  </div>

  <div class="grid">
    <div class="card"><h2>Attending &amp; running</h2><ul id="attending"></ul></div>
    <div class="card"><h2>Proposing (awaiting you)</h2><ul id="proposals"></ul></div>
  </div>

  <div class="card"><h2>Tasks</h2><ul id="tasks"></ul></div>

  <footer>Your hand is on the wheel: pause after the current step, approve or wave off a held step,
    approve or veto a proposal, tighten a leash, set how forward Sal is. A control can only restrict
    or express your setting — never grant Sal a capability. Pause takes effect after the current
    step finishes.</footer>
</div>

<script nonce="{nonce}">
const CSRF = "{csrf}";
// Drop any ?k= bootstrap from the address bar immediately (belt-and-suspenders; it's already spent).
try {{ history.replaceState(null, "", "/"); }} catch (e) {{}}

const $ = (id) => document.getElementById(id);
const BADGE = {{ ran:"var(--ok)", failed:"var(--bad)", held:"var(--warn)",
  paused:"var(--warn)", denied:"var(--bad)", notified:"var(--muted)" }};

function el(tag, cls, text) {{
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;   // textContent ONLY — no innerHTML of model data
  return n;
}}

function decisionLi(d) {{
  const li = el("li");
  const b = el("span", "badge", d.status);
  b.style.background = BADGE[d.status] || "var(--muted)";
  li.appendChild(b); li.appendChild(document.createTextNode(" "));
  li.appendChild(el("code", null, d.tool)); li.appendChild(document.createTextNode(" "));
  li.appendChild(el("span", "leash", d.leash || "")); li.appendChild(document.createTextNode(" "));
  li.appendChild(el("span", "origin", d.origin || ""));
  li.appendChild(el("div", "sum", d.summary || ""));
  return li;
}}

function proposalLi(p) {{
  const li = el("li");
  li.appendChild(el("span", "conf", (p.confidence ?? 0).toFixed(2)));
  li.appendChild(document.createTextNode(" "));
  li.appendChild(el("code", null, p.tool)); li.appendChild(document.createTextNode(" — "));
  li.appendChild(document.createTextNode(p.rationale || ""));
  const row = el("div", "rowbtns");
  const ap = el("button", "ctl approve", "Approve");
  ap.addEventListener("click", () => control("approve_proposal", {{ proposal_id: p.id }}));
  const vt = el("button", "ctl danger", "Veto");
  vt.addEventListener("click", () => control("veto", {{ proposal_id: p.id }}));
  row.appendChild(ap); row.appendChild(vt);
  li.appendChild(row);
  return li;
}}

function taskLi(t) {{
  const li = el("li");
  const st = el("span", "tstate", t.state);
  st.style.color = (t.state === "done") ? "var(--ok)"
    : (t.state === "failed" || t.state === "cancelled") ? "var(--bad)"
    : (t.state === "awaiting_approval" || t.state === "paused") ? "var(--warn)" : "var(--accent)";
  li.appendChild(st); li.appendChild(document.createTextNode(" "));
  li.appendChild(el("code", null, t.id));
  li.appendChild(el("div", "sum", t.prompt || ""));
  if (t.reply) li.appendChild(el("div", "sum", t.reply));
  if (t.state === "awaiting_approval") {{
    li.appendChild(el("div", "leash", "holding a step for your approval"));
    const row = el("div", "rowbtns");
    const ap = el("button", "ctl approve", "Approve");
    ap.addEventListener("click", () => control("approve", {{ task_id: t.id }}));
    const dc = el("button", "ctl danger", "Decline");
    dc.addEventListener("click", () => control("decline", {{ task_id: t.id }}));
    row.appendChild(ap); row.appendChild(dc);
    li.appendChild(row);
  }}
  if (t.error) li.appendChild(el("div", "err", t.error));
  return li;
}}

function fill(ul, items, render, emptyText) {{
  ul.textContent = "";
  if (!items || !items.length) {{ ul.appendChild(el("li", "empty", emptyText)); return; }}
  items.forEach((x) => ul.appendChild(render(x)));
}}

function renderCounts(c) {{
  const box = $("counts"); box.textContent = "";
  [["governed", c.governed], ["ran", c.ran], ["held", c.held],
   ["paused", c.paused], ["proposals", c.proposals_pending]].forEach(([k, v]) => {{
    const d = el("div"); d.appendChild(el("b", null, String(v ?? 0)));
    d.appendChild(document.createTextNode(k)); box.appendChild(d);
  }});
}}

function render(s) {{
  const st = $("status");
  st.textContent = s.paused ? "PAUSED" : "ACTIVE";
  st.className = "status " + (s.paused ? "paused" : "active");
  $("pauseBtn").textContent = s.paused ? "Resume" : "Pause";
  if (document.activeElement !== $("proactivity")) $("proactivity").value = s.proactivity || "conservative";

  const lb = $("leashes"); lb.textContent = "";
  const leashes = s.leashes || {{}};
  const names = Object.keys(leashes);
  if (!names.length) lb.appendChild(el("span", "empty", "none"));
  names.forEach((name) => {{
    const chip = el("span", "chip");
    chip.appendChild(el("b", null, name));
    const sel = el("select", "leashsel");
    ["act_then_report", "propose_first", "notify_only"].forEach((lv) => {{
      const o = el("option", null, lv); o.value = lv;
      if (lv === leashes[name]) o.selected = true;
      sel.appendChild(o);
    }});
    sel.addEventListener("change", () => control("set_leash", {{ tool: name, leash: sel.value }}));
    chip.appendChild(sel);
    lb.appendChild(chip);
  }});

  const caps = $("caps"); caps.textContent = "";
  const cs = s.capabilities || [];
  if (!cs.length) caps.appendChild(el("span", "empty", "none"));
  cs.forEach((c) => caps.appendChild(el("span", "cap", c)));

  renderCounts(s.counts || {{}});
  fill($("attending"), (s.attending || []).slice().reverse(), decisionLi, "nothing yet");
  fill($("proposals"), s.proposals || [], proposalLi, "no proposals waiting");
  fill($("tasks"), (s.tasks || []).slice().reverse(), taskLi, "no tasks yet");
}}

let backoff = 1000;
async function poll() {{
  try {{
    const r = await fetch("/state", {{ headers: {{ "X-Sal-Token": CSRF }} }});
    if (r.ok) {{ render(await r.json()); backoff = 1000; }}
    else backoff = Math.min(backoff * 2, 10000);
  }} catch (e) {{ backoff = Math.min(backoff * 2, 10000); }}
  setTimeout(poll, backoff);
}}

async function send() {{
  const box = $("prompt"); const text = box.value.trim();
  $("sendErr").textContent = "";
  if (!text) return;
  $("send").disabled = true;
  try {{
    const r = await fetch("/submit", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json", "X-Sal-Token": CSRF }},
      body: JSON.stringify({{ text }}),
    }});
    if (r.ok) box.value = "";
    else if (r.status === 429) $("sendErr").textContent = "Sal is busy — too many tasks in flight.";
    else $("sendErr").textContent = "Could not submit (" + r.status + ").";
  }} catch (e) {{ $("sendErr").textContent = "Could not reach Sal."; }}
  $("send").disabled = false;
  poll_now();
}}

function poll_now() {{ fetch("/state", {{ headers: {{ "X-Sal-Token": CSRF }} }})
  .then((r) => r.ok ? r.json() : null).then((s) => s && render(s)).catch(() => {{}}); }}

// A control only restricts or expresses your setting — the server re-validates every one and can
// never grant Sal a capability. POST, then re-poll to show the new governed state.
async function control(action, extra) {{
  try {{
    await fetch("/control", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json", "X-Sal-Token": CSRF }},
      body: JSON.stringify(Object.assign({{ action }}, extra || {{}})),
    }});
  }} catch (e) {{}}
  poll_now();
}}

$("send").addEventListener("click", send);
$("prompt").addEventListener("keydown", (e) => {{
  if (e.key === "Enter" && !e.shiftKey) {{ e.preventDefault(); send(); }}
}});
$("pauseBtn").addEventListener("click", () =>
  control($("status").textContent === "PAUSED" ? "resume" : "pause"));
$("proactivity").addEventListener("change", () =>
  control("set_proactivity", {{ level: $("proactivity").value }}));
poll();
</script>
</body></html>"""


# --- launcher ---------------------------------------------------------------------------------

def _build_default_host():
    """Wire a real Host the same way the live e2e does (env-configured)."""
    import os
    import tempfile
    from pathlib import Path

    from collaborator.host import Collaborator
    from collaborator.model_client import OllamaClient
    from collaborator.session import Session

    base = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11500/v1")
    model = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")
    temp = float(os.environ.get("TEMP", "0.0"))
    ws = Path(os.environ.get("SAL_WORKSPACE", "") or tempfile.mkdtemp(prefix="sal_surface_"))
    session = Session(workspace=ws,
                      capabilities=("fs.read:project", "fs.write:project", "shell.exec"),
                      proactivity=os.environ.get("SAL_PROACTIVITY", "conservative"),
                      default_importance=0.5)
    client = OllamaClient(base, model, timeout=180, temperature=temp)
    return Collaborator(session, client).start(), ws


def main() -> int:
    import os

    host, ws = _build_default_host()
    port = int(os.environ.get("SAL_PORT", "0"))
    surface = SalSurface(host, port=port)
    print(f"Sal is up → {surface.url}   (Ctrl-C to stop)")
    print(f"  workspace: {ws}")
    try:
        surface.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        surface.shutdown()
        host.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
