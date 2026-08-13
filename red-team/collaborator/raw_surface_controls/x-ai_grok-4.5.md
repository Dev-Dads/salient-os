# Surface controls panel: x-ai/grok-4.5

_cost=$0.1227 finish=stop_

# Stage-C Controls — Adversarial Code Review & Certification

## Findings

### F1 / Stale module P-01 docstring (claims “cannot loosen a leash”) / LOW  
**Location:** `collaborator/surface.py:1–28` (module docstring)  
**Concrete request:** n/a (documentation)  
**Why it matters:** Docstring still asserts the Stage-B surface “only ever calls `host.submit` / `host.snapshot`” and “cannot … loosen a leash, or set an autonomy flag.” Stage C deliberately calls `set_leash` / `set_proactivity` / pause. That is the designed express/restrict surface, but the file-level P-01 statement is false and could mis-train a later reviewer.  
**Other layer:** Page footer + `_CONTROLS` comment state the real rule.  
**Fix:** Rewrite the module docstring to match Stage C: allowlisted controls may restrict or express host config (leash capped at the seam); still no grant/mint; still no governance import.

### F2 / `set_leash` accepts any non-empty tool string / LOW  
**Location:** `collaborator/view.py` `set_leash` (~lines with `session.leash_overrides[tool_name] = leash`); reached via `surface.py` `_route_control` → `host.set_leash`  
**Concrete request:**
```http
POST /control HTTP/1.1
Host: 127.0.0.1:<port>
Origin: http://127.0.0.1:<port>
Cookie: sal_session=<valid>
X-Sal-Token: <valid>
Content-Type: application/json
Content-Length: …

{"action":"set_leash","tool":"not_a_real_tool","leash":"act_then_report"}
```
**Why it does *not* break C1:** Override is stored under a junk key; `govern_action` only reads overrides for real `tool.name`, then `apply_cap(leash, leash_cap(...))`. No capability or emission path.  
**Other layer:** Seam cap + unknown-tool deny in `get_tool`.  
**Fix (optional hygiene):** Reject `tool_name not in toolset()` in `view.set_leash` (return `False`).

### F3 / Whitespace-only control args pass surface validation / LOW  
**Location:** `collaborator/surface.py` `_route_control` — `if not isinstance(v, str) or not v or len(v) > _CONTROL_ARG_MAX`  
**Concrete request:**
```json
{"action":"approve","task_id":"   "}
```
**Why it does *not* break C3/C1:** Host lookup fails → `{"ok":false}`; no partial authority change. `not v` is false for `"   "`.  
**Other layer:** Host ID/registry checks.  
**Fix:** Also require `v.strip()` non-empty (and optionally normalize).

### F4 / Body-read timeout not restored on the socket / LOW (nit)  
**Location:** `collaborator/surface.py` `_read_json_body` — `self.connection.settimeout(BODY_READ_TIMEOUT)`  
**Concrete request:** any valid `/submit` or `/control`  
**Why it does *not* weaken the door:** Handler is HTTP/1.0, connection closes after the response; auth already passed. Shared helper does not skip cookie/CSRF/Host/Origin.  
**Other layer:** Handler `timeout`, connection close.  
**Fix:** Save/restore prior socket timeout around the read.

---

**No CRITICAL/HIGH findings.** No path found from a crafted `/control` body to a non-allowlisted Host method, capability grant, leash above signed cap, autonomy/intent.source forge, or unauthenticated/cross-origin control.

---

## Claim certifications

### C1 — CERTIFIED  
`method_name` is taken only from fixed `_CONTROLS` values, never from the request; `getattr(host, method_name)` cannot reach dunders or non-table APIs. `set_leash` only writes `session.leash_overrides`; `govern_action` does `_resolve_leash` (override) **then** `apply_cap(..., leash_cap(...))`, with the same cap re-applied in `execute_and_verify` and the notify_only re-gate in `reauthorized_or_denied`; proposed run_command/egress is still floored to `propose_first`. `set_proactivity` only affects propose surfacing. `approve` / `approve_proposal` require an already-held/PROPOSED item and re-gate on the worker — they do not mint authority.

### C2 — CERTIFIED  
`_route_control` uses the same `_guard_authed(check_origin=True)` as `/submit` (session cookie + `X-Sal-Token` + Host pin + Origin pin + upgrade reject). Shared `_read_json_body` preserves CL-required, 64 KiB cap, short body deadline, and JSON-object checks; it does not bypass auth or relax Host/Origin. Tests pin CSRF/cookie/cross-origin on `/control`.

### C3 — CERTIFIED  
Unknown/non-string/missing `action`, missing/empty/non-string/oversized args → 400 before `getattr`; Host rejection → 200 `{"ok":false}`; unexpected Host exception → 500. Extra JSON keys are ignored. No allowlisted action half-applies an authority write in a way that widens the seam; junk tool/whitespace IDs fail closed at the Host.

### C4 — CERTIFIED  
Page wires exactly the eight table actions (pause/resume, proactivity, per-snapshot leash selects, task approve/decline, proposal approve/veto). Snapshot leashes are **effective** (view applies `apply_cap`), so the UI does not advertise looseness the grant forbids after re-poll. No hidden control surface beyond `_CONTROLS`.

---

## STEELMAN

Stage C really is a thin, fixed allowlist over already-certified Host controls behind the already-certified door: request data never becomes a method name, args are typed/length-capped strings, and the only “loosen” knob is explicitly ceilinged again at every govern/execute/re-gate point. Approve paths enqueue work that re-checks capability (and leash notify_only) at use time rather than trusting hold-time authority. Residual issues are docstring drift and input hygiene, not an authority or door break.

## VERDICT: **SOUND**

No claim-breaking authority, door, or dispatch flaw in the shipped Stage-C path; highest-value fix is **F1 — correct the module docstring** so P-01 text matches the real allowlisted control surface.
