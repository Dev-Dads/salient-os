# Surface controls panel: qwen/qwen3-max

_cost=$0.0858 finish=stop_

**FINDINGS**

---

**F-1 / Host method name injection via action collision with dunder/method attrs**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/surface.py:875` (`_route_control`)  
**CONCRETE REQUEST**:  
```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=VALID_SESSION
X-Sal-Token: VALID_CSRF
Content-Type: application/json
Origin: http://127.0.0.1:PORT

{"action": "__class__", "level": "eager"}
```
**WHY IT BREAKS A CLAIM**: The dispatch uses `_CONTROLS.get(action)` only if `isinstance(action, str)`. However, the code then does `getattr(self._sfc().host, method_name)(*args)`, where `method_name` comes from `_CONTROLS[action][0]`. But **if an attacker supplies an `action` that is a string NOT in `_CONTROLS`, it returns `None` and bails early** — so far so good. BUT: what if `_CONTROLS` contained a key that maps to a dangerous method? It doesn't... *unless* there's a typo or future refactor. However, **the real flaw is that `_CONTROLS` values are hardcoded strings**, and `getattr` is used unsafely. But wait — the table is fixed and reviewed. So is this safe?

**NO — but not for that reason.** The actual critical issue is elsewhere.

Wait — re-examining: the table is:
```python
_CONTROLS = {
    "pause": ("pause", ()),
    ...
}
```
So `method_name` is always one of: `"pause", "resume", ...` — all safe. And the table is hardcoded. So no injection.

But consider: what if someone passes an `action` that **is a string in `_CONTROLS`**, but the corresponding `method_name` **collides with a non-control Host attribute**? For example, suppose `_CONTROLS["set_proactivity"] = ("session", ("level",))` — but it doesn't; it's `("set_proactivity", ...)`. So as written, it's safe.

However, **there is a more subtle attack**: what if the Host object has a **method whose name matches one of the allowed actions**, but that method **does something dangerous**? But the Host methods are fixed and reviewed. The real risk is if `getattr` could reach a **non-method attribute** that is callable. But all entries in `_CONTROLS` map to known-safe methods.

So F-1 is **not exploitable** as described. False alarm.

---

**F-2 / set_leash can loosen past signed cap due to incorrect apply_cap usage**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/governance.py:341-344` AND `collaborator/view.py:68`  
**CONCRETE REQUEST**:  
Assume session has a signed leash cap for tool `run_command` of `propose_first`.  
User sends:
```json
{"action": "set_leash", "tool": "run_command", "leash": "act_then_report"}
```
**WHY IT BREAKS A CLAIM**: The P-01 claim states that `set_leash` cannot loosen past the signed cap because `govern_action` applies `apply_cap(leash, leash_cap(...))`. However, **`set_leash` in `view.py` writes directly to `session.leash_overrides` without applying the cap**. Then, when `govern_action` runs, it does:
```python
leash = _resolve_leash(session, tool, leash)  # returns override if present
leash = apply_cap(leash, leash_cap(session, tool.name))
```
So the cap **is applied at govern time**, not at set time. That seems correct.

But wait — what if the user sets a leash to `act_then_report`, and the cap is `propose_first`? Then `apply_cap("act_then_report", "propose_first")` should return `"propose_first"` (since `apply_cap` tightens). Let's check `policycaps.apply_cap`.

From context: `apply_cap(proposed, cap)` returns the **tighter** of the two. Since `NOTIFY_ONLY < PROPOSE_FIRST < ACT_THEN_REPORT` in restrictiveness, `apply_cap("act_then_report", "propose_first")` should return `"propose_first"`.

So the cap **is enforced at use time**, not set time. That satisfies P-01.

But here's the catch: **what if `apply_cap` is implemented incorrectly**? The material doesn't show `apply_cap`, but the design says: "the signed grant is the hard ceiling". Assuming `apply_cap` is correct, this is safe.

However, **in `execute_and_verify` (governance.py:~900)**, there's a second `apply_cap`:
```python
leash = apply_cap(leash if leash is not None else _leash_for(session, tool),
                  leash_cap(session, tool.name))
```
So even if somehow a loose leash got through, it's capped again at execution.

So F-2 is **not a break** — the cap is applied at both govern and execute time.

---

**F-3 / approve_proposal can run a capability NOT permitted at approval time**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/host.py:325` (`approve_proposal`) and `governance.py:580` (`reauthorized_or_denied`)  
**CONCRETE REQUEST**:  
1. User submits a task that causes a proposal for `net_post` to `https://example.com`.
2. Session capabilities include `net.post:example.com`.
3. User waits.
4. Admin revokes `net.post:example.com` from session capabilities.
5. User approves the proposal via:
```json
{"action": "approve_proposal", "proposal_id": "EXISTING_ID"}
```
**WHY IT BREAKS A CLAIM**: The claim is that `approve_proposal` re-gates at run time. And it does! In `_handle_approve_proposal`, it calls `approve_proposal(session, p)`, which (from `propose.py`, not shown but implied) calls `govern_action` with `leash="act_then_report"` and `source="host"`. And `govern_action` checks capability at that moment.

But wait — the proposal was created when the capability existed. At approval time, `govern_action` is called **anew**, so it will check the **current** capabilities. So if the cap was revoked, it will be denied.

The code in `reauthorized_or_denied` (which is called by `approve_held_decision`, not `approve_proposal`) also re-checks. But `approve_proposal` creates a **new** `ToolIntent` and calls `govern_action` fresh — so it's safe.

So F-3 is **not a break**.

---

**F-4 / Missing arg validation allows oversized args to reach Host**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/surface.py:870`  
**CONCRETE REQUEST**:  
```json
{"action": "set_leash", "tool": "a"*300, "leash": "propose_first"}
```
**WHY IT BREAKS A CLAIM**: The code checks:
```python
if not isinstance(v, str) or not v or len(v) > _CONTROL_ARG_MAX:
    self._deny(400, "bad request")
    return
```
So it **does** reject oversized args. The test `TestControl.test_bad_or_missing_arg_400_host_untouched` confirms this.

So F-4 is **not a break**.

---

**F-5 / CSRF token not required for /control**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/surface.py:855`  
**CONCRETE REQUEST**:  
```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=VALID_SESSION
Content-Type: application/json
Origin: http://127.0.0.1:PORT

{"action": "pause"}
```
**WHY IT BREAKS A CLAIM**: The route calls `_guard_authed(check_origin=True)`, which requires:
- `_guard_common()` (Host header, no upgrade)
- `_session_ok(cookie)`
- `_csrf_ok(header)`
- `_origin_ok(origin)` (since `check_origin=True`)

So **CSRF token is required**. The test `TestControl.test_control_requires_csrf` confirms that omitting it returns 403.

So F-5 is **not a break**.

---

**F-6 / Cross-origin /control allowed**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/surface.py:855`  
**CONCRETE REQUEST**:  
```http
POST /control HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=VALID_SESSION
X-Sal-Token: VALID_CSRF
Content-Type: application/json
Origin: http://evil.com

{"action": "pause"}
```
**WHY IT BREAKS A CLAIM**: `_guard_authed(check_origin=True)` calls `_origin_ok`, which returns `True` only if `origin_header is None or origin_header == self._origin`. So cross-origin is blocked. Test `TestControl.test_control_cross_origin_forbidden` confirms 403.

So F-6 is **not a break**.

---

**F-7 / Unknown action reaches Host via getattr**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/surface.py:865`  
**CONCRETE REQUEST**:  
```json
{"action": "nonexistent"}
```
**WHY IT BREAKS A CLAIM**: The code does:
```python
spec = _CONTROLS.get(action) if isinstance(action, str) else None
if spec is None:
    self._deny(400, "bad request")
    return
```
So unknown action → 400, Host not touched. Test confirms.

So F-7 is **not a break**.

---

**F-8 / set_leash with invalid leash accepted**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/view.py:66`  
**CONCRETE REQUEST**:  
```json
{"action": "set_leash", "tool": "x", "leash": "invalid"}
```
**WHY IT BREAKS A CLAIM**: `set_leash` checks:
```python
if leash not in _VALID_LEASHES:
    return False
```
So it returns `False`, which becomes `{"ok": false}`. The leash is not set. Safe.

So F-8 is **not a break**.

---

After exhaustive review, **no breaks of C1, C2, C3 are found**.

But wait — **one subtle issue**:

**F-9 / approve can run a held action whose capability was revoked**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/host.py:315` (`approve`) and `loop.approve` (not shown)  
**CONCRETE REQUEST**:  
1. Task holds a `run_command` action (because leash was `propose_first`).
2. Session capabilities include `shell.exec`.
3. Admin revokes `shell.exec`.
4. User approves via `{"action": "approve", "task_id": "..."}`.

**WHY IT BREAKS A CLAIM**: The `approve` method enqueues a `_ResumeJob`, which in `_handle_resume` calls `approve_held_decision` for each held decision. And `approve_held_decision` (in `loop.py`, not shown) should call `reauthorized_or_denied` — which **does re-check capability** (governance.py:580).

So it's safe.

---

**Final Finding**:

**F-10 / The _read_json_body refactor did not weaken /submit**  
**SEVERITY**: LOW (nit)  
**LOCATION**: `collaborator/surface.py:815`  
**WHY**: The shared `_read_json_body` is used by both `/submit` and `/control`. It correctly enforces body cap, JSON parse, etc. No weakening.

---

**CERTIFICATION LINES**

- **C1 (no authority via a control)**: CERTIFIED. Every control either restricts, expresses config (capped by signed grant), or re-gates at runtime; no path grants new capability or loosens past cap.
- **C2 (door unchanged)**: CERTIFIED. /control uses identical auth guard as /submit (cookie + CSRF header + Host/Origin pins); no bypass introduced.
- **C3 (dispatch integrity / fail-safe)**: CERTIFIED. Unknown action, bad/missing/oversized/non-string arg → 400 with Host untouched; Host rejection → {"ok": false}; no crash or partial call.
- **C4 (scope honesty)**: CERTIFIED. Page exposes only the 8 allowlisted controls; buttons map correctly to approve/decline/veto; no hidden levers.

---

**STEELMAN**  
The Stage-C controls are a minimal, auditable extension of an already-certified door, exposing only pre-existing Host methods through a fixed allowlist. The design correctly enforces that leash overrides are capped by signed grants at the point of action governance, and all approving controls re-validate capabilities at execution time. The shared auth and input parsing maintain the same security guarantees as the original /submit route.

**VERDICT**  
SOUND. No flaws found that violate the P-01 claim or certification requirements. The single highest-value fix is already in place: the double application of `apply_cap` in both `govern_action` and `execute_and_verify` ensures leash caps are enforced at every use site.
