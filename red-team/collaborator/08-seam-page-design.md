# ② Stage B — "the page" — technical design spec

*Answerable to `docs/seam-surface-plain-language.md` (Step B). This is Claude's to
maintain; the plain-language doc is the authority above it.*

## The proof this stage must produce

Launch one command → it prints a URL → open it in your browser → type a job to Sal →
**watch the governed steps happen live in the page**, each showing the *real* result, and
the final reply land — all without reloading. Served **only to your own machine, only to
you.**

## Scope — what B is / isn't (honoring the plan's B/C split)

- **B is:** submit (one text box) + **watch** (a live view of what Sal is attending to,
  running, proposing, plus the leashes, the trust dial, and the capabilities — as what
  they really are right now). The door is hardened (below).
- **B is not:** control *buttons*. Pause, approve/veto, tighten-a-leash become real
  buttons in **Stage C**. B renders them as *state you can see*, not levers you can pull.
- **The one input B has is `submit`** — and submit only *queues a governed turn*; it
  grants nothing. Every action inside that turn still flows through `govern_action`.

### Flagged design point (for the panel + Josh)

A watch-only B means a task that **HOLDS** (a `propose_first` write, or `run_command`)
stalls as "awaiting your approval" with **no page button to approve until Stage C**.
Spec choice: **honor the split** — B shows a held task *honestly* ("Sal is holding this
for you — approving arrives in Stage C") and the demo uses `act_then_report` leashes so
steps *run and are visible*. Governance is still on-screen: each step is gated, recorded,
and shown with its real result; the leashes/dial/caps are shown as what they are.
*Alternative if Josh prefers a non-dead-end demo:* pull `approve`/`decline` (the two
task-scoped executing controls that already exist on the Host) into B and leave the
richer steering (pause, veto proposals, tighten leash) for C. **Recommendation: honor the
split;** it keeps B small and keeps C a real, self-contained demo.

## The one rule (P-01, restated for a network surface)

The page is a **new way IN, never a new way to grant POWER.** Concretely, the surface
process only ever calls `host.submit()` and `host.snapshot()`. It does **not** import or
touch `governance` / `policycaps` / capability minting. There is no code path from an
HTTP request to a capability grant. `govern_action` stays the single authority chokepoint;
the surface is the hand on the wheel, not a second wheel.

## Architecture

New module `collaborator/surface.py`, stdlib-only (matches the repo's no-dependency
rule and `view.render_html`'s self-contained ethos). It wraps an **already-running**
`Collaborator` host — it never constructs governance state itself.

```
SalSurface(host, *, token=None, host_addr="127.0.0.1", port=0)
  .serve_forever()          # blocks; ThreadingHTTPServer on a loopback socket
  .url                      # "http://127.0.0.1:<port>/?k=<token>"
  .shutdown()               # clean stop
serve(host, ...) -> SalSurface        # construct + start in a daemon thread (tests/launchers)
```

- **Server:** `http.server.ThreadingHTTPServer` + a `BaseHTTPRequestHandler` subclass.
  Threading so a slow `/state` read never blocks `/submit` (both are cheap Host calls, but
  a worker turn must never be able to wedge the socket).
- **Bind:** `127.0.0.1` **only**, hard-coded default; `port=0` picks an ephemeral port
  unless one is given. **Never `0.0.0.0`** — asserted in code and in a test.

## Routes (method + path allowlisted; everything else → 404/405)

| Method | Path       | Auth                          | Does                                             |
|--------|------------|-------------------------------|-------------------------------------------------|
| GET    | `/`        | single-use bootstrap in `?k=` | validates, sets the session cookie, serves the page |
| GET    | `/state`   | session cookie + CSRF header  | `json(host.snapshot())` — polled ~1 Hz          |
| POST   | `/submit`  | session cookie + CSRF header + same-origin | `host.submit(body.text)` → `{task_id}` |

No control routes in B. `/favicon.ico` → 204. Any `Connection: Upgrade` / WebSocket
handshake → `400` (we speak only GET/POST on these paths — never an upgrade). Everything
else → 404. Wrong method on a known path → 405.

## The hardened door (Josh's choice: loopback + token + origin check)

*Revised after the design panel (all 5 certified P-01/scope; the two unanimous
not-certifieds — a durable bearer token in the URL, and an unbounded queue — are closed
**structurally** below, not "planned").*

1. **Loopback bind only** — an off-box attacker cannot reach the socket at all. `0.0.0.0`
   is refused in code and asserted in a test.
2. **Single-use bootstrap → session cookie (closes the token-in-URL leak, D1).** The launch
   URL carries a one-time `?k=<bootstrap>` (`secrets.token_urlsafe(32)`). `GET /?k=` compares
   it constant-time (`compare_digest`), **consumes it** (single-use — a leaked/replayed `?k=`
   via Referer, history, or terminal scrollback is already spent and worthless), and sets the
   real session secret in an **`HttpOnly; SameSite=Strict; Path=/` cookie**. The session
   secret therefore **never lives in a URL, in JS, in `window.name`, or in a Referer** — JS
   can't read an `HttpOnly` cookie, so XSS can't exfiltrate it either. The page immediately
   `history.replaceState`s the address bar to `/`. Reload works (the cookie persists); a wrong
   or already-spent bootstrap with no valid cookie → `403`.
3. **CSRF wall = the cookie's `SameSite=Strict` + a custom header (double defense).** The
   session cookie is `SameSite=Strict`, so a page on any other origin cannot get it sent —
   not on a `fetch`, a `<form>` POST, an `<img>`, or even a top-level navigation. As a second,
   independent wall the page also holds a per-session **CSRF token in JS memory** (embedded in
   the served HTML) and sends it as a **custom header `X-Sal-Token`** on `/state` and
   `/submit`; the server requires it (constant-time) on **both** routes. A custom header is
   unforgeable cross-origin without a CORS preflight, and we send **no** permissive CORS
   headers — so even if `SameSite` were somehow bypassed, a foreign page can neither read
   `/state` nor forge `/submit`. (Panel note answered: the custom-header token is the
   **primary** CSRF wall, enforced on every non-bootstrap route; Host/Origin pins are
   defense-in-depth, never the sole check.)
4. **Host-header pin (anti DNS-rebinding)** — strict allowlist: the `Host` header must be
   **exactly** `127.0.0.1:<port>` or `localhost:<port>`. Missing, IPv6, trailing-dot
   (`localhost.`), extra whitespace, or any other host → `403`. A rebinding attack arrives
   with the attacker's hostname in `Host`; this refuses it even though the socket is loopback.
5. **Origin pin on state-changing requests** — if an `Origin` header is present on `/submit`
   it must equal our own origin; a foreign `Origin` → `403` (defense-in-depth atop 3).
6. **Strict CSP + escape-only rendering (closes the XSS surface, D1).** Every response carries
   `Content-Security-Policy: default-src 'none'; connect-src 'self'; script-src 'nonce-<n>';
   style-src 'nonce-<n>'; img-src 'self' data:; base-uri 'none'; form-action 'none'`,
   `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `Cache-Control:
   no-store`. All snapshot-derived strings (tool names, summaries, prompts, replies) are
   inserted via `textContent` / an escaper — **never** `innerHTML` — so hostile model output
   or a hostile prompt can't script the page.
7. **Token discipline** — session/CSRF/bootstrap secrets are never logged, echoed in an error,
   or placed in `/state`. Default `BaseHTTPRequestHandler` logging (which would print the
   `?k=`) is silenced; we log method + path + status only, never the query string. Secrets are
   generated in-process (never an argv — `ps`-invisible — and never written to a file).

## Availability — bound the door so it can't be darkened (D4)

The panel's fair point: for a *governed* agent, a watch surface that goes dark (or a queue
that starves) delays a human seeing a **held** action — safety-adjacent, not mere perf. So
availability is bounded **by construction**, not left "at worst it queues work":

1. **Pending-work cap.** `/submit` refuses with `429` when the number of non-terminal tasks
   (QUEUED + RUNNING + AWAITING_APPROVAL + PAUSED) is already at a cap (default 32). A flood
   can't grow the registry/queue without bound; `/state` and any held task stay responsive
   and visible. (The Host stays the single serial worker; this just caps the inflow.)
2. **Anti-slowloris.** The handler sets a per-request socket `timeout` (default 15 s) so a
   client that opens a connection and dribbles bytes is dropped, not held. Concurrency is
   bounded by a small worker pool / connection cap so thread + FD exhaustion can't take the
   socket down. `/state` and `/submit` are cheap Host calls, so a real client is never near
   these limits.
3. **Body cap enforced before reading.** `/submit` checks `Content-Length` and reads **at
   most** 64 KiB (`> cap → 413`, host not called); a missing/oversized/chunked length is
   refused rather than read into memory.

These keep D4's real property true: a flood or a slow client can, at worst, get `429`/`413`/
dropped — it can never corrupt state, make the door lie, or hide a held action.

## The page (single self-contained document — no external assets, CSP-friendly)

Reuses the visual language and theme-aware CSS already proven in `view.render_html`
(same tokens, same light/dark handling), but renders **client-side from the `/state`
JSON** so it can live-update by polling without a full-page swap.

- **Header:** "Sal", a live status pill (`active` / `paused`), the trust dial
  (`proactivity`, read-only in B), and the capabilities row — all straight from the
  snapshot.
- **Composer:** one text box + Send. Enter submits; the box clears; the new task appears
  in Tasks as `queued → running → …`. This is B's only input.
- **Live panels** (re-rendered each poll from the snapshot): **Attending** (last 8 governed
  steps: badge · tool · leash · origin · summary — the exact projection `render_html`
  uses), **Ran**, **Proposals** (shown; "approve/veto arrive in Stage C"), **Leashes**,
  **Tasks** (id · state · reply · held-count; a held task reads "awaiting your approval —
  Stage C").
- **Polling:** `fetch('/state', {headers:{'X-Sal-Token':…}})` every ~1 s;
  `AbortController` + backoff on error so a brief hiccup doesn't spin. No websockets/SSE in
  B — polling is simpler, honest, and enough for a single local viewer. (SSE is a possible
  C/③ nicety.)
- **Honest footer:** one line stating the leashes shown are live, and that pausing/
  approving/tightening become buttons in Stage C.

## Launcher — `python -m collaborator.surface`

Mirrors the live e2e wiring so a live run is one command:

- Reads `OLLAMA_BASE` / `OLLAMA_MODEL` / `TEMP` (defaults: `127.0.0.1:11500/v1`,
  `gpt-oss:120b`, `0.0`), builds a `Session` (workspace = a temp dir or `$SAL_WORKSPACE`;
  capabilities default to the e2e set), an `OllamaClient`, and a `Collaborator(...).start()`.
- Starts the surface, prints exactly one line:
  `Sal is up → http://127.0.0.1:<port>/?k=<bootstrap>   (Ctrl-C to stop)`.
- `Ctrl-C` → `surface.shutdown()` then `host.stop()` — clean, no orphan threads.

## Tests — `tests/test_collaborator_surface.py` (stdlib `unittest`, offline)

Drive a **fake in-process host** (records `submit` calls; returns a canned `snapshot`) — no
model, no network beyond the loopback socket. Every wait bounded. The client helper does the
`GET /?k=` bootstrap first to capture the session cookie + CSRF token, then reuses them.

- **Bootstrap:** `GET /` with no/wrong `?k=` → 403; with the right `?k=` → 200, sets an
  `HttpOnly; SameSite=Strict` session cookie; the **same** `?k=` used **again** → 403
  (single-use consumed).
- **`/state` auth:** with cookie + CSRF header → 200 + JSON; **without** the CSRF header → 403;
  with a wrong CSRF header → 403; with **no cookie** → 403.
- **Host-header spoof** (`Host: evil.com`, and the variants: missing, `localhost.`, IPv6,
  trailing space) with valid creds → 403 (DNS-rebind sim; strict allowlist).
- **Cross-origin** `Origin: https://evil.com` on `/submit` → 403.
- **Upgrade rejection:** a `Connection: Upgrade` / `Upgrade: websocket` handshake → 400.
- `POST /submit` (creds + JSON body) → 200 `{task_id}` **and** the fake host recorded the text.
- `/state` returns the snapshot shape (keys: paused, proactivity, capabilities, leashes,
  attending, ran, proposals, counts, tasks, busy).
- **Body cap:** a `Content-Length` >64 KiB `/submit` → 413, host **not** called.
- **Pending cap (D4):** with the fake host reporting ≥32 non-terminal tasks, `/submit` → 429,
  host **not** called.
- **Security headers:** every response carries the strict `CSP`, `Referrer-Policy: no-referrer`,
  and `X-Content-Type-Options: nosniff`; no secret appears in `/state` or any error body.
- Unknown path → 404; wrong method on a known path → 405.
- **Structural:** server bound to `127.0.0.1` (not `0.0.0.0`); `compare_digest` used for every
  secret comparison; surface module does **not** import `governance`/`policycaps` (P-01 —
  grep-style assert that the only Host calls are `submit`/`snapshot`).

## Live proof — `red-team/collaborator/e2e_sparky_page.py`

On Sparky against gpt-oss:120b: launch the surface, then **drive it over HTTP** (POST a
real job, poll `/state`) and assert the governed steps + a DONE task appear through the
socket — the same integrated behavior Stage A proved, now through the page's own doorway.
Captured log = the Stage B proof artifact.

## Build / review loop

1. This spec → **external design panel** (pressure-test before building — Josh's rule).
2. Build `surface.py` + page + tests; full suite green.
3. **Internal red-team** of the shipped code (the door invariants especially).
4. **External certification panel** (every non-doc PR).
5. Live proof on Sparky.
6. Merge → 20-min heartbeat.

## Explicitly deferred to Stage C / later

- Control **buttons** (pause, approve/decline, veto, tighten leash) — C.
- SSE/websocket push instead of polling — C/③ nicety.
- Any multi-user / login auth — ③ (chassis) concern; single-user localhost doesn't need it.
- The look/"front door" question — a ③ decision per the plain-language plan.

## Design-panel disposition (2026-08-13, 5 vendors, $0.60)

- **D2 (P-01 / no authority via the door): CERTIFIED 5/5.** A hostile `/submit` body only ever
  becomes a model directive still gated by `govern_action`; no HTTP path reaches a capability
  grant / leash / `autonomous` / emit. The core thesis stands unmodified.
- **D3 (honest scope): CERTIFIED 5/5.** Watch-only B with held tasks shown as "awaiting Stage
  C" is honest; no false lever. (UI-copy nits only.)
- **D1 (door integrity): NOT-CERTIFIED 5/5 → closed structurally above.** Panel confirmed the
  CSRF/rebind story is sound; the real gaps were (a) a **durable bearer token in the URL**
  (Referer/history/scrollback leak) and (b) an **XSS** surface. Fixes: single-use bootstrap →
  `HttpOnly SameSite=Strict` session cookie (§door 2–3) + strict CSP + `textContent`-only
  rendering (§door 6).
- **D4 (availability): NOT-CERTIFIED 3/5 → closed structurally above.** Unbounded queue + no
  slowloris/connection timeouts could darken the watch surface and delay a held action. Fixes:
  pending-work `429` cap, per-request timeout + bounded concurrency, `Content-Length`-checked
  body read (§Availability).
- **Out of scope / already-handled:** gpt-5.1's `_should_propose` "rarely fires" note is Stage-A
  Host behavior, intentional and live-proven (the idle trigger fired on its own in the Stage-A
  Sparky run); not a Stage-B change. Compromised-human-endpoint (token already the human's) is
  outside this threat model. The code itself still gets its own external certification panel.
