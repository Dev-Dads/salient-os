# Surface code panel: openai/gpt-5.1

_cost=$0.0641 finish=stop_

ID 1 / TITLE: Bootstrap prefetch race can lock user out (single-use URL)  
SEVERITY: MEDIUM  
LOCATION: collaborator/surface.py:83-108 (`_bootstrap` field, `_consume_bootstrap`, `_route_root`)  
CONCRETE SEQUENCE:  
1. User runs `python -m collaborator.surface`, sees `http://127.0.0.1:12345/?k=<bootstrap>`.  
2. Desktop search/indexer or browser “preload”/link prefetcher hits `GET /?k=<bootstrap>` before the user’s actual navigation.  
3. `_consume_bootstrap` marks `_bootstrap_used = True` and returns 200 with page+cookie+CSRF to the prefetch process; the human’s browser hasn’t yet gotten anything.  
4. When the human now navigates to the printed URL (without any cookie), `_consume_bootstrap` returns False → `_route_root` sends 403 and there is no way to recover without restarting the process.  
WHY IT BREAKS A CLAIM:  
- This is explicitly documented as a “trade” in the docstring, so not a hidden bug; but from an availability standpoint, it means the “single-use bootstrap” door can be accidentally consumed by a non-interactive agent and permanently deny the intended human the session. It does not darken an already-open watch surface, but it can prevent any watch surface from being opened from that launch.  
- It slightly undercuts C3’s spirit (“availability bounded: … a watch surface can never be darkened”) at initial access for some desktop environments, though it doesn’t allow an attacker to wedge slots or hide tasks once a session exists.  
WHETHER ANOTHER LAYER CATCHES IT: No; this is the first/only bootstrap mechanism.  
FIX:  
- Make bootstrap single-use per *session* but allow *re-issuance*: on failed `_consume_bootstrap` when `_bootstrap_used` is True and no valid session cookie is present, generate a fresh `_bootstrap` and either:  
  - redirect to the new `/?k=<new>` URL (still only usable once), or  
  - better, accept a second successful use of `?k=` only when no valid session cookie exists, and rely on the session cookie as the durable secret thereafter.  
- At minimum, expose a CLI option to disable bootstrap single-use for environments where prefetch is common.

---

ID 2 / TITLE: /submit reachable by non-browser local clients without CSRF or Origin checks  
SEVERITY: LOW  
LOCATION: collaborator/surface.py:124-133 (`_origin_ok`), 162-178 (`_guard_authed`, `check_origin`), 217-237 (`_route_submit`)  
CONCRETE REQUEST:  
- From a local process (no Origin header, no cookies by default):  
  1. First, a curl client that knows or guesses the `sal_session` value (e.g., user copies it from browser devtools) can drive /submit without CSRF if used outside the browser:  
     ```http
     POST /submit HTTP/1.1
     Host: 127.0.0.1:12345
     Cookie: sal_session=<session-secret>
     Content-Type: application/json
     Content-Length: 20

     {"text": "do x"}
     ```  
     This fails because `_csrf_ok` requires `X-Sal-Token`, so CSRF is enforced.  
  2. Try adding wrong or missing Origin: `_origin_ok` treats `Origin: null` or absent Origin as allowed, but CSRF is still mandatory.  
WHY IT BREAKS A CLAIM:  
- It does *not* break any stated claim: C1 is about *other origins/pages* and local processes without the token; this route still requires: correct Host, session cookie, and CSRF token; there is no path for “other local process (no token)” to hit /submit.  
- I include this as a *non-finding*: the combination of SameSite=Strict + CSRF header + no CORS does block browser-based CSRF; the relaxed Origin rule for non-browser clients is intentional and safe.  
WHETHER ANOTHER LAYER CATCHES IT:  
- Yes: `_csrf_ok` and `_session_ok` both gate /submit; the Host itself will not bypass governance.  
FIX:  
- None required for claims. Optionally, for even stricter semantics, require some Origin value for /submit when User-Agent indicates a browser, but that’s defense-in-depth, not necessary for C1.

(Recorded as a *blocked attack / non-finding* per instructions.)

---

ID 3 / TITLE: Possible starvation of /state under sustained connection-flood (but slots bounded)  
SEVERITY: LOW  
LOCATION: collaborator/surface.py:38-67 (`_BoundedThreadingHTTPServer`), 51-60 (`process_request`)  
CONCRETE SEQUENCE:  
1. Attacker on localhost opens many TCP connections very quickly to `127.0.0.1:<port>` and completes HTTP handshakes that cause handler threads to be spawned until `_conn_sem` is exhausted (`max_connections` default 16).  
2. Once saturated, any new connection’s `process_request` fails to acquire `_conn_sem` and calls `shutdown_request(request)`, closing the socket immediately.  
3. At this point, if the attacker keeps all 16 connections active, the browser may see dropped or refused connections; however, each connection is subject to `timeout = request_timeout` on the socket (15s by default) and BODY_READ_TIMEOUT=5s for `/submit` bodies, so after those timeouts each thread exits and releases the semaphore.  
WHY IT BREAKS A CLAIM:  
- It does *not* break C3 according to the code: slots are strictly bounded and timeouts are finite; an attacker cannot “indefinitely” pin all slots. There is a temporary availability reduction while timeouts expire, but not a persistent darkening or hidden state.  
WHETHER ANOTHER LAYER CATCHES IT:  
- Yes, the per-request socket timeout and bounded semaphore ensure eventual recovery; the host’s own worker queue is unaffected.  
FIX:  
- None strictly required; optionally, you could reduce `DEFAULT_REQUEST_TIMEOUT` further (under 10s) on localhost-only surfaces to accelerate recovery from pathological clients.

(Also a *non-finding*; I’ve walked the mechanics and it behaves as designed.)

---

ID 4 / TITLE: CSP for non-HTML responses omits connect-src self (minor)  
SEVERITY: LOW  
LOCATION: collaborator/surface.py:140-153 (`_security_headers`), 203-215 (`_route_state`)  
CONCRETE REQUEST:  
- `GET /state` from the browser after bootstrap; handler calls `_send(..., nonce=None)` so `_security_headers` uses the “else” branch:  
  ```python
  csp = "default-src 'none'; base-uri 'none'; form-action 'none'"
  ```  
- The page’s JS uses `fetch("/state", ...)` and `fetch("/submit", ...)`, and the initial HTML payload had a CSP with `connect-src 'self'`, but this CSP is not attached to subsequent /state or /submit responses.  
WHY IT BREAKS A CLAIM:  
- It does not violate any of the certification claims (C1-C4). CSP on `/state` JSON is largely irrelevant; the main script is already nonce-locked. The spec text says “Every response carries `Content-Security-Policy: default-src 'none'; connect-src 'self'; script-src ...; style-src ...; img-src ...; base-uri 'none'; form-action 'none'`”, but the implementation only does that for HTML and omits `connect-src` on /state and /submit. That’s a spec-vs-impl mismatch and a very minor hardening nit.  
WHETHER ANOTHER LAYER CATCHES IT:  
- Not necessary; the browser enforces CSP on the HTML document, and the JS fetches are allowed by default.  
FIX:  
- Include `connect-src 'self'` even when `nonce` is None, i.e.:  
  ```python
  else:
      csp = "default-src 'none'; connect-src 'self'; base-uri 'none'; form-action 'none'"
  ```  
- This aligns behavior with the design doc and slightly tightens the policy.

---

ID 5 / TITLE: Launcher wires host with broad capabilities by default (scope/UI nit)  
SEVERITY: LOW  
LOCATION: collaborator/surface.py:401-417 (`_build_default_host`)  
CONCRETE SEQUENCE:  
1. User runs `python -m collaborator.surface` with no environment overrides.  
2. `_build_default_host()` creates a Session with capabilities `("fs.read:project", "fs.write:project", "shell.exec")`.  
3. The page shows these live capabilities and leashes honestly; there are no UI buttons to change them.  
WHY IT BREAKS A CLAIM:  
- It does *not* break C2 or C4: these capabilities are part of Stage A Host configuration, not granted via the web surface. The surface makes no authority changes and accurately reflects them. I call this out only because a casual user might not fully realize how broad `shell.exec` is, but that’s documentation/UX, not a violation of P-01 or “scope honesty”.  
WHETHER ANOTHER LAYER CATCHES IT:  
- Governance enforces caps via `govern_action`; surface does not bypass this.  
FIX:  
- Consider defaulting the launcher to a more conservative capability set (e.g., omit `shell.exec` unless an env var opts in), or prominently document this in CLI help.  

(Again, this is *not* a door or authority bug; just a nit noted for completeness.)

---

C1 (door integrity)  
- ANALYSIS:  
  - CSRF: `/state` and `/submit` both require a session cookie (SameSite=Strict; HttpOnly) and a CSRF header `X-Sal-Token`, checked with `secrets.compare_digest`. A cross-origin page cannot: (a) get the cookie sent (Strict), or (b) set a custom header without a CORS preflight, and the server sends no `Access-Control-Allow-*`.  
  - Cross-origin reads: Even if a foreign page could cause a navigation, it could not read `/state` due to SameSite+no CORS; and it cannot even cause a non-navigational successful `/submit` because of the CSRF header requirement.  
  - Host / Origin: `_host_ok` is an exact string match for `127.0.0.1:<port>` or `localhost:<port>`; anything else (missing port, trailing dot, different port) is blocked, preventing DNS-rebinding. `_origin_ok` forbids foreign `Origin` values on `/submit`; absent Origin (curl, direct HTTP clients) is allowed, but still requires cookie+CSRF.  
  - Token discipline: `_bootstrap` is only in memory; logging strips the query string; CSP includes `no-referrer`; the JS immediately `history.replaceState` to `/` so the bootstrap is not persisted in the bar. All secret comparisons use `compare_digest`.  
  - Race: `_consume_bootstrap` uses a lock and a boolean; concurrent `GET /?k=` requests will result in only one 200+session and the rest 403. That’s safe from a security standpoint, though one prefetch-consuming the token is an availability wrinkle (ID 1).  
  - Routes: `/state` and `/submit` all go through `_guard_authed`; there is no unguarded alternate path to state or submit.  
- CERTIFICATION: **C1 (door integrity): CERTIFIED.** I do not find a concrete CSRF/rebinding/token-leak bypass; the only concern is the prefetch race, which is an availability trade, not a door integrity failure.

C2 (P‑01 / no authority via the door)  
- ANALYSIS:  
  - Imports: `surface.py` only imports `CANCELLED, DONE, FAILED` from `collaborator.host` plus launcher-only `Collaborator`, `Session`, `OllamaClient`. It does not import `governance`, `policycaps`, or any capability-minting modules. Tests assert this via an AST walk.  
  - Dataflow:  
    - `/state` calls `self._sfc().host.snapshot()` and JSON-encodes the result; it does not write or mutate host state.  
    - `/submit` validates JSON input and calls `s.host.submit(text)`; `FakeHost` and `Collaborator` both treat `text` as a task prompt/user_message. There is no route that calls `pause`, `resume`, `set_leash`, `set_proactivity`, `approve`, `veto`, etc.  
  - Host side: `submit()` enqueues a `_TurnJob`; all authority flows through `run_turn`/`govern_action` in existing host code. The surface never touches those paths directly.  
- CERTIFICATION: **C2 (P‑01 / no authority): CERTIFIED.** All HTTP data is confined to model directives via `submit()`; there is no reachable path from a request to a capability grant or leash/authority change.

C3 (availability / can’t be darkened into a safety problem)  
- ANALYSIS:  
  - Pending cap: `_non_terminal_task_count()` derives from `host.snapshot()['tasks']`, filtering out terminal states; `/submit` refuses with 429 when `>= max_pending`. That caps task registry and inflow.  
  - Slowloris / lying Content-Length: `ThreadingHTTPServer.timeout` is set (15s); `/submit` requires `Content-Length`, enforces size before reading, and then uses `BODY_READ_TIMEOUT`=5s on `self.connection.settimeout()` while reading the body. If bytes don’t arrive, it 408s quickly and frees the slot; if less data arrives than claimed, it 400s.  
  - Concurrency: `_BoundedThreadingHTTPServer` wraps `process_request` with a bounded semaphore (default 16). When saturated, new connections are immediately dropped with `shutdown_request` rather than blocking accept. `daemon_threads = True` means threads don’t hang process shutdown.  
  - Watch surface semantics: `/state` is extremely cheap (one snapshot + JSON dump). A flood can at worst get 429s or 413s/408s; it cannot corrupt the host state or make `/state` lie about a held action. Slots are bounded and reclaimed by timeouts; there’s no unbounded queue of open sockets.  
  - The bootstrap prefetch issue (ID 1) can deny *first* access but does not darken a running surface or hide held actions once the page is up.  
- CERTIFICATION: **C3 (availability / can’t be darkened): CERTIFIED.** The bounded semaphore, request/body timeouts, body cap, and 429 limit ensure an attacker cannot indefinitely pin all slots or hide held actions; the only availability nit is a one-time-bootstrap prefetch race, which does not create a safety-relevant darkening of an existing watch surface.

C4 (scope honesty / watch-only B)  
- ANALYSIS:  
  - The HTML and JS expose exactly one control: the text composer + Send button mapped to `/submit`. There are no pause/approve/veto/tighten buttons or any API calls other than `/state` and `/submit`.  
  - Held tasks: `taskLi` renders state and, when `t.state === "awaiting_approval"`, adds `"awaiting your approval — Stage C adds the button"`, clearly indicating the missing control is left for Stage C.  
  - Leashes and capabilities: the page lists `s.leashes` and `s.capabilities` exactly as provided by host snapshot, without altering them. There is no hidden lever to change them from the page.  
  - Proactivity: displayed as text from `s.proactivity`; there is no UI to change it.  
- CERTIFICATION: **C4 (scope honesty): CERTIFIED.** The page is truthfully watch + submit only, with held/paused state shown honestly and no hidden or misleading control surface.

---

STEELMAN (arguments *for* the design as shipped)  
- The surface is very deliberately minimal and hard-walled: only two host calls, strict loopback binding, dual CSRF defenses, and a tight Host/Origin pinning strategy make C1 and P‑01 unusually robust for a localhost UI. Availability is bounded not just heuristically but structurally: max 16 concurrent handler threads, a 32-task cap, and aggressive timeouts eliminate the usual slowloris and queue-blowout pitfalls.  
- The single-use bootstrap plus `HttpOnly; SameSite=Strict` cookie and CSP with textContent-only rendering remove the common local-surface pitfalls: no durable bearer token in URLs or logs, and essentially no practical XSS surface even when the model produces hostile text. For a Stage-B “worker only” surface, this is a strong, principled implementation.

VERDICT: **MINOR_ISSUES.** The shipped `surface.py` is sound with respect to C1–C4; the only notable issue is the bootstrap prefetch race, which is an availability nuisance rather than a door or authority break.  
Single highest-value fix: **Make the bootstrap URL recoverable from accidental prefetch consumption** (e.g., allow regeneration or accept reuse when no session cookie exists) to eliminate the one practical availability footgun.
