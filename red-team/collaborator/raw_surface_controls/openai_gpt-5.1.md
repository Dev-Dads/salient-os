# Surface controls panel: openai/gpt-5.1

_cost=$0.0986 finish=stop_

ID 1 / TITLE Unknown / malformed / extra fields in /control payload cannot reach non-allowlisted Host methods  
SEVERITY LOW  
LOCATION collaborator/surface.py:257-290 (`_route_control`)  

CONCRETE REQUEST OR SEQUENCE  
- Any attempt to spoof a different host method via `action` or extra keys is blocked by the fixed table and type checks, e.g.:  

```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=<valid>
X-Sal-Token: <valid>
Origin: http://127.0.0.1:PORT
Content-Type: application/json
Content-Length: 104

{
  "action": "set_leash",
  "tool": "write_file",
  "leash": "notify_only",
  "__class__": "__import__('os').system('id')"
}
```

WHY IT BREAKS A CLAIM  
- It does not break the claim; this is a confirmation of robustness:  
  - `action` is looked up in `_CONTROLS` (a literal dict) and must be a string (line ~270).  
  - Only the positional names listed in the tuple for that action are read from the payload (line ~274-281). Extra keys like `__class__` are ignored.  
  - The host method name itself (`method_name`) comes from `_CONTROLS`, not from the request.  
  - This prevents any crafted `action` or payload from reaching non-allowlisted Host methods or attributes via getattr.  

WHETHER ANOTHER LAYER CATCHES IT  
- The fixed `_CONTROLS` table + `isinstance(action, str)` check + constrained arg extraction is the primary and sufficient layer.  
- No other layer is required to prevent this class of dispatch manipulation.  

FIX  
- No fix required; current design is safe here.  
- (Optional hardening) Add a small comment near `getattr` to document that `method_name` is intentionally only ever drawn from `_CONTROLS`, not from request data, to keep this invariant explicit for future edits.

---

ID 2 / TITLE /control is guarded by the same cookie + CSRF + Host/Origin checks as /submit  
SEVERITY LOW  
LOCATION collaborator/surface.py:217-246 (`_guard_authed`), 301-332 (`_route_submit`), 334-352 (`_route_control`)  

CONCRETE REQUEST OR SEQUENCE  
1. Missing cookie, valid CSRF, trying to call /control:  

```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
X-Sal-Token: <valid>
Origin: http://127.0.0.1:PORT
Content-Type: application/json
Content-Length: 20

{"action":"pause"}
```

2. Valid cookie + CSRF, foreign Origin, calling /control:

```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=<valid>
X-Sal-Token: <valid>
Origin: http://evil.com
Content-Type: application/json
Content-Length: 20

{"action":"pause"}
```

WHY IT BREAKS A CLAIM  
- It does not break the claim; this is a confirmation that `/control` has the same wall as `/submit`.  
- `_route_control` does:  

```python
if not self._guard_authed(check_origin=True):
    return
```

- `_guard_authed(check_origin=True)` enforces:  
  - `_guard_common()` → Upgrade rejection + Host header allowlist;  
  - session cookie equality via `secrets.compare_digest`;  
  - CSRF header equality via `secrets.compare_digest`;  
  - and (with `check_origin=True`) an Origin pin identical to `/submit`.  
- Tests in `TestControl` explicitly assert that:  
  - Missing CSRF → 403, host untouched;  
  - Missing cookie → 403;  
  - Cross-origin Origin header → 403.  

WHETHER ANOTHER LAYER CATCHES IT  
- For unauthenticated or cross-origin attempts: `_guard_authed` is the enforcing layer; Host is never touched because `_route_control` returns immediately.  
- Body parsing and control dispatch only happen after auth succeeds.  

FIX  
- No fix required; C2’s “door unchanged” requirement is met.  
- (Optional) Add a brief test mirroring `TestHostAndOriginPins.test_cross_origin_submit_forbidden` specifically checking that `/control` 403s on foreign Origin with valid cookie+CSRF; behavior is already present but an explicit test would keep it pinned.

---

ID 3 / TITLE Shared _read_json_body between /submit and /control preserves caps and fail-closed behaviour  
SEVERITY LOW  
LOCATION collaborator/surface.py:288-324 (`_read_json_body`), 326-352 (`_route_submit`), 334-352 (`_route_control`)  

CONCRETE REQUEST OR SEQUENCE  
- Oversized body to /control:  

```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=<valid>
X-Sal-Token: <valid>
Origin: http://127.0.0.1:PORT
Content-Type: application/json
Content-Length: 70000

{"action":"pause","x":"y"...}
```

WHY IT BREAKS A CLAIM  
- It does not break any claim; this is a confirmation that the refactor to `_read_json_body` did not weaken `/submit` or add a new weakness for `/control`.  
- `_read_json_body` enforces:  
  - Content-Length required (411 if missing);  
  - Content-Length must be an integer, ≥0 and ≤ `surface.body_cap` (default 64 KiB) → 400 or 413 otherwise;  
  - Tight 5s body read timeout with short-read detection;  
  - JSON parse must succeed and top-level value must be a dict.  
- Both `_route_submit` and `_route_control` call `_read_json_body()` and then immediately `return` if it yields `None` (error already sent).  
- Tests in `TestSubmit` confirm the body-cap behaviour for `/submit`; `TestControl` relies on the same method for `/control` and successfully exercises malformed bodies (bad/missing/extra args) resulting in 400 with host untouched.  

WHETHER ANOTHER LAYER CATCHES IT  
- This is the main layer for request-body defence; `_guard_authed` ensures only authenticated callers reach it.  
- For authority, body parsing failures mean we never call Host methods.  

FIX  
- No fix needed; behaviour is correct and aligned across routes.  
- (Optional) Add a specific `/control` body-cap test akin to `test_submit_body_cap` to ensure no regression on the control route.

---

ID 4 / TITLE set_leash cannot loosen past signed leash_cap; Page shows effective capped leash, not raw override  
SEVERITY LOW  
LOCATION  
- collaborator/view.py:66-76 (`set_leash`), 104-111 (`JudgmentView._leashes`)  
- collaborator/governance.py:276-288 (`_resolve_leash`), 295-305 (initial cap in `govern_action`), 384-425 (terminal cap in `execute_and_verify`), 501-510 (`reauthorized_or_denied`)  

CONCRETE REQUEST OR SEQUENCE  
1. Host session is configured with a leash cap that forbids autonomous execution, e.g. signed cap for `run_command` is `propose_first`.  
2. User presses the leash dropdown on the page to choose the “act_then_report” option for `run_command`, which triggers:  

```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=<valid>
X-Sal-Token: <valid>
Origin: http://127.0.0.1:PORT
Content-Type: application/json
Content-Length: ...

{"action": "set_leash", "tool": "run_command", "leash": "act_then_report"}
```

WHY IT BREAKS A CLAIM  
- It does not break the P-01 claim; this is the critical confirmation for “cannot loosen past the signed cap.”  
- Flow:  
  - `/control` maps to `Collaborator.set_leash` (host.py:122-129).  
  - That function calls `view.set_leash(session, tool_name, leash)`, which:  
    - rejects values not in the three-known `_VALID_LEASHES`;  
    - stores `session.leash_overrides[tool_name] = leash`.  
  - At decision time, `govern_action` computes leash as:  
    - `_resolve_leash(session, tool, override)` → validates against `_VALID_LEASHES`, else fails closed to `PROPOSE_FIRST`;  
    - then applies `apply_cap(leash, leash_cap(session, tool.name))`. This is a pure cap: it takes the stricter of override and signed cap.  
  - For `run_command`, even if the override is `ACT_THEN_REPORT`, the subsequent emission/offline floors and caps (including the run-command autonomy floors) re-floor to `PROPOSE_FIRST` where the cap or environment doesn’t allow autonomy.  
  - `execute_and_verify` re-applies `apply_cap(leash, leash_cap(...))` at execution, and also denies autonomous `run_command` completely unless structural requirements + signed caps are satisfied, regardless of the override.  
  - The view (`JudgmentView._leashes`) renders the effective capped leash:  

    ```python
    return {name: apply_cap(overrides.get(name, tool.default_leash),
                            leash_cap(self.session, name))
            for name, tool in toolset().items()}
    ```

    so the page never shows a “looser than cap” leash.  

- A `proposed`-source intent with leash override is still floored as documented (emission and run_command floors).  

WHETHER ANOTHER LAYER CATCHES IT  
- The primary enforcement is `apply_cap` + `leash_cap` in `govern_action` and `execute_and_verify`.  
- The view’s `_leashes()` ensures the UI reflects effective authority, not raw overrides, so the human can’t be misled into thinking a stricter signed cap was loosened.  
- `reauthorized_or_denied` re-gates at approval time and denies if the leash has been tightened to `NOTIFY_ONLY` since hold.  

FIX  
- No fix required; this is implementing the “cap is the ceiling” invariant correctly.  
- (Optional) Keep tests pinning that a leash override to `act_then_report` for a capped tool still yields `propose_first` or `notify_only` in `JudgmentView.snapshot()["leashes"]`.

---

ID 5 / TITLE approve / approve_proposal only execute already-permitted, re-gated actions; cannot mint new capabilities  
SEVERITY LOW  
LOCATION collaborator/host.py:150-186 (approve/approve_proposal and worker handlers), collaborator/governance.py:511-550 (`reauthorized_or_denied`), 550-743 (`execute_and_verify`)  

CONCRETE REQUEST OR SEQUENCE  
- User approving a held `run_command` action from the web page (task in `awaiting_approval`):  

1. Task is held with HELD decisions created by `govern_action` earlier.  
2. UI renders the task with Approve/Decline buttons and user clicks Approve →  

```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=<valid>
X-Sal-Token: <valid>
Origin: http://127.0.0.1:PORT
Content-Type: application/json

{"action": "approve", "task_id": "task-1234abcd"}
```

3. `/control` maps to `host.approve(task_id)` which enqueues a `_ResumeJob(task_id)`.  
4. Worker processes `_ResumeJob`:  
   - For held decisions, it calls `loop.approve_held_decision(self.session, d)` for each HELD decision, which in turn uses `execute_and_verify` after `reauthorized_or_denied`.  

WHY IT BREAKS A CLAIM  
- It does not break the claim; this confirms that approve paths do not mint capabilities.  
- `reauthorized_or_denied` re-derives:  
  - new policy based on current `granted_capabilities(session)`;  
  - recomputes required_cap from frozen args (and canonical host for egress);  
  - denies if capability is no longer granted, if URL is now ineligible, or if workspace path no longer resolves;  
  - denies if current signed leash cap has been tightened to `NOTIFY_ONLY`.  
- `execute_and_verify` (called only after a passed re-gate) re-applies leash caps and the run-command and egress floors at the moment of use, and will outright deny an autonomous `run_command` if structural conditions or signed caps are missing.  
- There is no host or view method to set or widen `session.capabilities`; only the session constructor or env wiring grants them, and the surface never calls anything in that space.  

WHETHER ANOTHER LAYER CATCHES IT  
- The signature-cap system (`granted_capabilities`, `leash_cap`, `apply_cap`, `enforced`) and the re-gate in `reauthorized_or_denied` are independent from the control surface.  
- Even if `/control` could somehow enqueue an approval job for a decision with stale authority, `reauthorized_or_denied` would deny it at execution time.  

FIX  
- No fix needed; authority is re-checked and never expanded by the approve controls.  
- (Optional) Add a test that revokes a capability between hold and approval and confirms that an approval via `/control` results in a DENIED decision rather than a run.

---

ID 6 / TITLE Scope honesty: UI only exposes the 8 allowlisted controls, correctly mapped and labeled  
SEVERITY LOW  
LOCATION collaborator/surface.py:366-567 (`_PAGE_HTML` JS wiring)  

CONCRETE REQUEST OR SEQUENCE  
- Inspecting the UI and JS wiring:  

  - Pause/Resume:  

    ```js
    $("pauseBtn").addEventListener("click", () =>
      control($("status").textContent === "PAUSED" ? "resume" : "pause"));
    ```

  - Proactivity:  

    ```js
    $("proactivity").addEventListener("change", () =>
      control("set_proactivity", { level: $("proactivity").value }));
    ```

  - Leash per tool:  

    ```js
    sel.addEventListener("change", () =>
      control("set_leash", { tool: name, leash: sel.value }));
    ```

  - Held tasks:  

    ```js
    ap.addEventListener("click", () => control("approve", { task_id: t.id }));
    dc.addEventListener("click", () => control("decline", { task_id: t.id }));
    ```

  - Proposals:  

    ```js
    ap.addEventListener("click", () => control("approve_proposal", { proposal_id: p.id }));
    vt.addEventListener("click", () => control("veto", { proposal_id: p.id }));
    ```

WHY IT BREAKS A CLAIM  
- It does not break a claim; it confirms C4 (scope honesty).  
- Every interactive control in the UI maps 1:1 to one of the eight actions in `_CONTROLS`.  
- There are no hidden control paths:  
  - No JS calls any other `action` string.  
  - The footer text is consistent with what’s wired: pause, approve/decline held tasks, approve/veto proposals, tighten leashes, set proactivity.  
- The `/control` handler only recognizes the eight actions present in `_CONTROLS`; unknown actions are 400 and host-untouched.  

WHETHER ANOTHER LAYER CATCHES IT  
- Server-side, `_CONTROLS` is the full “control surface,” so even if the client tried to send an extra action, it would be rejected.  
- There is no mismatch where UI describes a capability that is not actually enforced or that bypasses governance.  

FIX  
- No fix needed; scope honesty is maintained.  
- (Optional) Add a small negative test that the JS `control()` is never called with something not in `_CONTROLS` (e.g. via a static scan or runtime assertion in dev builds).

---

ID 7 / TITLE Bad /control arguments are rejected early without partial host calls or crashes  
SEVERITY LOW  
LOCATION collaborator/surface.py:270-290 (`_route_control`), tests/test_collaborator_surface.py:225-246 (`TestControl`)  

CONCRETE REQUEST OR SEQUENCE  

Examples that must and do fail before reaching Host:

```http
POST /control
...
{"action": "set_leash", "tool": "write_file"}          # missing leash

POST /control
...
{"action": "set_leash", "tool": "", "leash": "notify_only"}   # empty string

POST /control
...
{"action": "approve", "task_id": 5}                    # non-string

POST /control
...
{"action": "set_leash", "tool": "x"*300, "leash": "notify_only"}  # oversized
```

WHY IT BREAKS A CLAIM  
- It does not break the claim; it confirms C3 (fail-safe dispatch).  
- For each key in the control’s `keys` tuple, `_route_control` enforces:  

  ```python
  if not isinstance(v, str) or not v or len(v) > _CONTROL_ARG_MAX:
      self._deny(400, "bad request")
      return
  ```

- The loop returns immediately on first bad arg:  
  - No host call is made (`getattr(...)(*args)` is never reached).  
  - No partial invocation is possible because arguments are constructed into a list atomically and then used in a single call.  
- Tests in `TestControl.test_bad_or_missing_arg_400_host_untouched` explicitly assert both the 400 and that `host.controls` remains empty.  

WHETHER ANOTHER LAYER CATCHES IT  
- The primary guard is this validation loop.  
- The host methods themselves are pure and tolerant, but they are never invoked when arguments are malformed.  

FIX  
- No fix required.  
- (Optional) Consider returning a small JSON error body for `400` to make debugging easier, but plaintext `"bad request"` is acceptable for this local surface.

---

ID 8 / TITLE /control shares the same Upgrade + Host header protections as all other routes  
SEVERITY LOW  
LOCATION collaborator/surface.py:195-214 (`_guard_common`), 223-228 (`_guard_authed`), 258-266 (`do_GET` and `_bad_method`), 271-279 (`do_POST`)  

CONCRETE REQUEST OR SEQUENCE  

- Attempt WebSocket-style upgrade:

```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=<valid>
X-Sal-Token: <valid>
Origin: http://127.0.0.1:PORT
Connection: Upgrade
Upgrade: websocket
Content-Length: 0
```

WHY IT BREAKS A CLAIM  
- It does not break any claim; it validates that `/control` is equally hardened:  
  - `_guard_authed` calls `_guard_common()` first, which:  
    - Rejects any “Upgrade” request with 400;  
    - Enforces Host allowlist to mitigate DNS rebinding.  
  - `/control` is only accessible via POST; GET returns 405, and other verbs map to `_bad_method` which also denies.  
- There is no path to smuggle a different protocol or cross-host request into `/control`.  

WHETHER ANOTHER LAYER CATCHES IT  
- `_guard_common` is the layer for both `/submit`, `/state`, and `/control`.  
- The BoundedThreadingHTTPServer and per-request timeouts also mitigate slowloris-style attacks before authentication.  

FIX  
- No fix needed.  
- (Optional) Add a specific test `test_control_upgrade_rejected` mirroring `test_upgrade_rejected` to keep behaviour pinned for `/control`.

---

### CERTIFICATION LINES

C1 (no authority via a control): CERTIFIED – The /control surface can only invoke a fixed set of Host methods that either restrict or express configuration; `set_leash` is strictly capped by signed leash caps and run-time floors, and no control can mint capabilities, bypass the capability gate, or trigger emissions beyond what the signed configuration already allows.

C2 (door unchanged): CERTIFIED – /control is guarded by the same single-use bootstrap → session cookie, CSRF header, Host allowlist, Origin pin, body cap, and Upgrade rejection as /submit, and the `_read_json_body` refactor preserved all Stage-B door invariants.

C3 (dispatch integrity / fail-safe): CERTIFIED – Unknown actions, missing/non-string/oversized or malformed args to /control are rejected with 400 before any Host call, host rejections return `{"ok": false}`, and the handler structure prevents crashes or partial host invocations.

C4 (scope honesty): CERTIFIED – The page exposes only the eight allowlisted controls, each wired directly and exclusively to its corresponding Host method, with no hidden levers or mislabeling about governance or authority state.

---

### STEELMAN

The most plausible concern is that adding a “set_leash” control might, via mistakes in leash resolution or caps, inadvertently raise autonomy for tools like `run_command` or egress tools. Here, however, the combination of `_resolve_leash` validation, signed `leash_cap` application, emission/run-command autonomy floors, and terminal re-capping in `execute_and_verify` ensures that any override is always constrained by the signed ceiling and additional structural conditions.

Another realistic worry is that adding a generic `/control` route plus a shared JSON body reader might weaken door guarantees or create a path to arbitrary `getattr` on the Host. In practice, `_CONTROLS` is a fixed literal mapping, `getattr` uses only those method names, and the auth + origin + body caps are identical to `/submit`, with tests pinning both correct dispatch and rejection paths.

---

### VERDICT

VERDICT: SOUND – The Stage-C control surface is tightly scoped, correctly reuses the hardened door, caps all authority at the signed governance layer, and provides only restrictive or configuration-expressive controls; the single highest-value fix would be to add a small number of explicit tests pinning `/control`’s body-cap and Upgrade/Origin behaviour (mirroring existing `/submit` and `/state` tests) to guard against future regressions.
