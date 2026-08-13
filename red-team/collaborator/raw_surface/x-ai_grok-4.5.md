# Surface code panel: x-ai/grok-4.5

_cost=$0.1053 finish=stop_

# Adversarial Review: `collaborator/surface.py` (Stage B)

Assumption: author is wrong until the code proves otherwise. Claims attacked in the shipped source only.

---

## Findings

### F1 / Unauthenticated concurrency exhaustion can darken the watch surface
**SEVERITY:** HIGH  
**LOCATION:** `surface.py:99-112` (`_BoundedThreadingHTTPServer.process_request`), `surface.py:288` (`timeout = request_timeout`), `surface.py:404-418` (`do_GET` favicon / 404 paths skip auth but still need a slot once accepted)

**CONCRETE SEQUENCE:**
1. From any local process (no bootstrap, no cookie, no CSRF): open 16 TCP connections to `127.0.0.1:<port>`.
2. On each connection, send an incomplete HTTP request slowly, e.g.  
   `GET /state HTTP/1.1\r\nHost: 127.0.0.1:<port>\r\n` …then one byte every few seconds (stay under the 15s socket timeout, or reconnect on drop).
3. Continuously replace connections as they time out or are closed.
4. In parallel, the human browser’s `fetch("/state")` / `/submit` hits `acquire(blocking=False)` → connection **silently dropped** (no HTTP body; not even 503).

**WHY IT BREAKS A CLAIM:** C3 promises a slowloris/flood/connection exhaustion cannot darken the surface or hide a held action from the operator. Slots are taken **before** `_guard_authed` runs. A no-token local attacker can occupy all `DEFAULT_MAX_CONNECTIONS` (16) handler threads indefinitely by rotation. Held tasks remain in the Host, but the watch door does not serve `/state` — that is operational hiding/darkening, which is exactly the D4 safety-adjacent failure mode.

**ANOTHER LAYER?** No. Host snapshot integrity is fine; the door’s accept path is the layer that fails. Pending 429 / body 413 do not apply to incomplete non-authed requests.

**FIX:**  
- Apply a much tighter timeout for unauthenticated/header phase (e.g. 1s), and/or  
- Reject with an immediate 403 after Host+auth fail **without** lingering on body, and shed unauthed connections first; and/or  
- Separate semaphores (reserve N slots for sockets that already presented a valid session cookie on a prior connection — harder) or reverse-proxy style “max concurrent unauthed = 2”.  
- At minimum: on semaphore refusal send a short `503` + `Connection: close` before `shutdown_request` so the client can distinguish overload, and lower `timeout` for pre-auth.

---

### F2 / Pending-work cap is TOCTOU (soft bypass under concurrency)
**SEVERITY:** LOW  
**LOCATION:** `surface.py:456-463` then `surface.py:492` (`_route_submit`)

**CONCRETE SEQUENCE:**
1. Bootstrap legitimately; hold cookie + `X-Sal-Token`.
2. Fire `DEFAULT_MAX_CONNECTIONS` parallel  
   `POST /submit` with `Content-Length` + `{"text":"x"}` + cookie + CSRF + matching Origin while `_non_terminal_task_count() == max_pending - 1` (or even `== max_pending` if races interleave with completions).
3. Each handler evaluates `snapshot()` count, all pass the `< max_pending` check, all call `host.submit`.

**WHY IT BREAKS A CLAIM:** C3/D4 “429 cap” is not atomic with enqueue. Cap becomes `max_pending + O(max_connections)`, not a hard ceiling. Not unbounded (still gated by connection slots and body limits), so not a full C3 break.

**ANOTHER LAYER?** Host is a single worker serializing turns — safety of governance holds; registry growth is bounded-ish, not infinite.

**FIX:** Make the Host expose `try_submit(text, max_pending) -> Optional[id]` that checks+inserts under `Collaborator._lock`, or return 429 from `submit` itself when saturated.

---

### F3 / Origin pin is a single address; Host allowlist is two names
**SEVERITY:** MEDIUM (footgun; availability of `/submit` for the real user — not a cross-origin grant)
**LOCATION:** `surface.py:168-171` (`_origin` / `_allowed_hosts`), `surface.py:214-216` (`_origin_ok`), `surface.py:454` (`check_origin=True`)

**CONCRETE SEQUENCE:**
1. Launcher prints `http://127.0.0.1:<port>/?k=…` but human opens  
   `http://localhost:<port>/?k=<bootstrap>` (allowed by `_host_ok`).
2. Cookie is host-only for `localhost`; HTML loads; JS `fetch('/submit', …)` sends  
   `Origin: http://localhost:<port>`.
3. Server has `self._origin == "http://127.0.0.1:<port>"` → `_origin_ok` false → **403**. Submit permanently broken for that session; watch still works (`/state` does not check Origin).

**WHY IT MATTERS:** Not a C1 bypass (foreign origins still die). It is an asymmetric defense that can deny the legitimate operator while documenting localhost and 127.0.0.1 as equivalent. C3/C4 honesty of “one input works” suffers.

**ANOTHER LAYER?** N/A (self-DoS / misconfig).

**FIX:**  
```python
self._allowed_origins = frozenset({
    f"http://127.0.0.1:{self.port}",
    f"http://localhost:{self.port}",
})
# _origin_ok: origin is None or origin in self._allowed_origins
```

---

### F4 / `::1` is bind-legal but never Host-legal
**SEVERITY:** LOW  
**LOCATION:** `surface.py:78` (`_LOOPBACK`), `surface.py:146-147` vs `surface.py:170-171`

**CONCRETE REQUEST:** `SalSurface(host, host_addr="::1")` then  
`GET / HTTP/1.1` + `Host: [::1]:<port>` → `_host_ok` false → 403 on every route that guards.

**WHY:** Dead configuration path; not default. Not a rebinding hole (rebinding Host is attacker DNS name, still refused).

**FIX:** Either remove `::1` from `_LOOPBACK` or add `[::1]:{port}` (and matching Origin) to allowlists when bound there.

---

### F5 / Host pin not applied on every path
**SEVERITY:** LOW  
**LOCATION:** `surface.py:410-414` (`/favicon.ico`, unknown GET → 404 without `_guard_common`)

**CONCRETE REQUEST:** DNS-rebind browser sends `GET /favicon.ico` or `GET /nope` with `Host: evil.example` → 204/404 with security headers, no auth.

**WHY IT DOES NOT BREAK C1:** No snapshot, no submit, no secret in body. Rebinding still cannot pass `_guard_authed` on `/state` or `/submit`, and cannot consume bootstrap without correct `?k=`.

**FIX:** Call `_guard_common()` at the start of `do_GET`/`do_POST` before routing (defense-in-depth consistency).

---

### F6 / Prefetch / single-use bootstrap race (documented trade — not CRITICAL)
**SEVERITY:** LOW (stated non-goal / documented)  
**LOCATION:** `surface.py:188-204`, `surface.py:422-432`

**SEQUENCE:** Two concurrent `GET /?k=<bootstrap>` (browser + local prefetcher). Lock in `_consume_bootstrap` makes exactly one winner; loser 403. Human may need a fresh launcher URL.

**CLAIM IMPACT:** Does not break single-use integrity; does not leak the durable session. Panel trade — name only.

**FIX (optional):** Second-factor bind of bootstrap to `User-Agent` is not worth it; document “disable prefetch on loopback” is enough.

---

## Attack walk-throughs that **failed** (blocked = valuable non-findings)

### CSRF drive of `POST /submit` from another origin/tab (no token)
- **`<form method=POST action=http://127.0.0.1:port/submit">`:** cannot set `X-Sal-Token`. Even if cookie were present, `_csrf_ok` fails → 403.  
- **Cross-site + SameSite=Strict:** cookie not sent on cross-site requests.  
- **Cross-port localhost (same-site, different origin):** modern browsers **do** treat different ports as same-site, so `SameSite=Strict` **alone would send** `sal_session` from `http://127.0.0.1:OTHER` → `http://127.0.0.1:PORT`. This is why the custom header is load-bearing. Foreign JS cannot set `X-Sal-Token` without a CORS preflight; server never sends `Access-Control-Allow-*`; `OPTIONS` is 405 via `_bad_method` with no ACAO → preflight fails. Simple/no-cors requests cannot attach the header.  
- **Origin pin:** present foreign `Origin` → 403 even if both secrets were stolen (defense-in-depth).  
**Result: no concrete CSRF submit. Blocked.**

### Cross-origin read of `GET /state`
- Requires cookie **and** `X-Sal-Token` (`_guard_authed`, `check_origin=False` but CSRF still required).  
- Simple GET (no custom header) → 403.  
- GET with custom header → preflight dies (no CORS).  
- Opaque no-cors response unreadable even if misconfigured.  
- No `Access-Control-Allow-Origin` on 200.  
**Result: no concrete cross-origin state read. Blocked.**

### DNS rebinding past `_host_ok`
- Allowlist is exact `127.0.0.1:<port>` / `localhost:<port>` (`surface.py:170-171`, `208-209`).  
- Rebind arrives as `Host: attacker.tld` → 403 before auth usefully succeeds on guarded routes.  
- Whitespace variants: HTTP header parsers fold/strip before app code (as tests note).  
**Result: no concrete rebind bypass of `/state` or `/submit`. Blocked.**

### Token leaks (log / error / Referer / timing)
- `log_message` logs `urlparse(self.path).path` only — query/`?k=` stripped (`surface.py:291-295`).  
- `_deny` bodies are fixed generic strings (`surface.py:334-336`).  
- `/state` is `json.dumps(snap)` — secrets not in Host snapshot path; tests pin absence.  
- CSRF lives in HTML/JS by design; session is `HttpOnly` (not `document.cookie`).  
- `Referrer-Policy: no-referrer` + CSP `default-src 'none'` + `history.replaceState` to `/` limit bootstrap URL egress after first paint.  
- Bootstrap/session/csrf all use `secrets.compare_digest` (`surface.py:199-207`); no `==` on secrets (AST-tested).  
**Result: no concrete secret exfil via log/error/Referer/timing in this code. Blocked.**

### Single-use bootstrap double-spend
- `threading.Lock` + flag flip inside `_consume_bootstrap` (`surface.py:196-204`).  
**Result: not racy. Blocked.**

### Hostile body → authority (C2 / P-01)
Trace:
1. `_route_submit` → only `payload["text"]` as `str` (`surface.py:485-491`).  
2. `s.host.submit(text)` only (`surface.py:492`).  
3. `Collaborator.submit` builds a `Task` and enqueues `_TurnJob` — no leash/cap/autonomy APIs (`host.py` submit path).  
4. Surface imports: `CANCELLED, DONE, FAILED` from `collaborator.host` only (`surface.py:68`); AST test forbids `governance` / `policycaps` / `policy`.  
5. No routes call `pause` / `approve` / `set_leash` / `set_proactivity` / `veto` / `decline`.  
**Result: hostile body is a model directive only; seam still governs. P-01 holds. Blocked.**

### Classic body / queue weapons
- No `Content-Length` → 411; over cap → 413 before read; `read(length)` under `BODY_READ_TIMEOUT=5s` → 408; short read → 400 (`surface.py:464-484`).  
- Lying huge CL refused pre-read. Stalled body cannot hold a slot for the full 15s once body phase starts.  
- These paths work **as coded** for authed submit. (F1 remains the gap: pre-auth / non-body stall.)

---

## Missing defenses the shipped code should add

1. **Pre-auth / unauthed connection budget** (see F1) — highest value.  
2. **Atomic pending cap inside Host.submit** (F2).  
3. **Origin allowlist aligned with Host allowlist** (F3).  
4. **`_guard_common()` on all verbs/paths** including favicon/404 (F5).  
5. **Optional:** `Secure` when serving TLS later; `__Host-sal_session` if HTTPS; explicit `Connection: close` on all responses (HTTP/1.0 default already closes).  
6. **Tests gap:** no test for connection-semaphore drop, body-read 408, concurrent bootstrap, or localhost-vs-127.0.0.1 Origin — pin these before calling availability “closed.”

---

## Certification lines

| Claim | Verdict | One sentence |
|-------|---------|--------------|
| **C1** (door integrity) | **CERTIFIED** | No concrete no-token / cross-origin / rebind path in this code reads `/state` or drives `/submit`; SameSite is insufficient cross-port on localhost but `X-Sal-Token` + no CORS + Host pin close the bypasses that were attempted. |
| **C2** (P-01 / no authority) | **CERTIFIED** | Surface imports no governance/policycaps and only calls `host.submit`/`host.snapshot`; hostile JSON cannot grant, loosen, emit, or steer authority fields. |
| **C3** (availability / can’t darken) | **NOT-CERTIFIED** | Unauthenticated slowloris/connection rotation can pin all semaphore slots and drop legitimate `/state` polls, darkening held-action visibility despite honest Host state (F1); pending cap is also only soft under race (F2). |
| **C4** (scope honesty) | **CERTIFIED** | Only submit + watch are exposed; held/paused copy correctly defers controls to Stage C; no hidden control route. |

---

## STEELMAN

This is a careful stdlib door: single-use bootstrap → `HttpOnly; SameSite=Strict` session, load-bearing custom CSRF header with zero CORS, exact Host allowlist, CSP nonce + `textContent`-only UI, and Content-Length-capped body reads with a tight body deadline. P-01 is structurally real (import surface + call surface), not aspirational comments. The dual CSRF design correctly compensates for the localhost cross-port SameSite footgun that many “Strict cookie = done” designs miss.

---

## VERDICT: **MINOR_ISSUES**

Door integrity and P-01 authority hold under adversarial reading; the serious residual is **availability darkening via unauthenticated slot exhaustion**, plus small allowlist asymmetries — not an authority break.

**Single highest-value fix:** shed or hard-limit **unauthenticated** concurrent connections (short pre-auth timeout + separate unauthed budget) so a no-token local flood cannot pin all handler slots and black out `/state`.
