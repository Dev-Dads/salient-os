# Surface controls panel: anthropic/claude-opus-4.1

_cost=$0.7921 finish=stop_

I'll perform an adversarial security review of the Stage-C controls, focusing on breaking the P-01 claim that controls can only RESTRICT or EXPRESS config, never GRANT authority.

## FINDINGS

### F1: MISSING EXTRA-KEYS VALIDATION IN /control
**ID:** F1  
**TITLE:** Extra keys in control body not rejected  
**SEVERITY:** LOW  
**LOCATION:** surface.py:~520-530 (_route_control)  
**CONCRETE REQUEST:**
```json
POST /control
Headers: Cookie: sal_session=xxx; X-Sal-Token: yyy
Body: {"action": "pause", "malicious": "data", "__proto__": {"evil": true}}
```
**WHY IT BREAKS A CLAIM:** C3 states "extra keys" should be caught, but the code only validates the expected keys from the spec, not that ONLY those keys exist.
**WHETHER ANOTHER LAYER CATCHES IT:** The Host methods ignore extra args, so no authority is granted.
**FIX:** After extracting required args, verify `set(payload.keys()) - {"action"} - set(keys)` is empty.

### F2: GETATTR DISPATCH IS SAFE
**ID:** F2  
**TITLE:** getattr dispatch properly constrained  
**SEVERITY:** NONE (NOT A BUG)  
**LOCATION:** surface.py:531 (_route_control)  
**ANALYSIS:** `getattr(self._sfc().host, method_name)` uses `method_name` from the FIXED `_CONTROLS` table, never from request data. The `action` string indexes into `_CONTROLS`, which returns a tuple with the method name. No path exists for user input to reach the getattr target.

### F3: LEASH CAP PROPERLY ENFORCED
**ID:** F3  
**TITLE:** Signed cap is hard ceiling for set_leash  
**SEVERITY:** NONE (NOT A BUG)  
**LOCATION:** governance.py:341-344  
**ANALYSIS:** Traced the full path: `set_leash` → `session.leash_overrides[tool]` → `govern_action` applies override THEN `apply_cap(leash, leash_cap(session, tool))`. The cap is always the ceiling. A `proposed`-source action with loosened leash still hits the propose_first floor at line 351-353.

### F4: APPROVE/APPROVE_PROPOSAL RE-GATE PROPERLY
**ID:** F4  
**TITLE:** Approval methods re-gate at execution time  
**SEVERITY:** NONE (NOT A BUG)  
**LOCATION:** host.py:approve/approve_proposal → governance.py:reauthorized_or_denied  
**ANALYSIS:** Both approval paths enqueue work that re-checks capability at the moment of use via `reauthorized_or_denied`. They execute already-permitted actions with fresh authority checks, never grant new capability.

### F5: SHARED _read_json_body PRESERVES /submit SECURITY
**ID:** F5  
**TITLE:** Refactored body reader maintains all checks  
**SEVERITY:** NONE (NOT A BUG)  
**LOCATION:** surface.py:456-489 (_read_json_body)  
**ANALYSIS:** The shared function enforces: Content-Length required and capped, tight body-read timeout, length verification, JSON parse, dict type check. Both /submit and /control call it identically after passing `_guard_authed(check_origin=True)`.

## CERTIFICATION

**C1 (no authority via a control):** CERTIFIED - No control path can grant capability, loosen a leash past the signed cap, set autonomous/intent.source, trigger an unguarded emission, or reach a non-allowlisted Host method.

**C2 (door unchanged):** CERTIFIED - /control uses identical `_guard_authed(check_origin=True)` as /submit; the shared _read_json_body refactor preserves all security checks.

**C3 (dispatch integrity):** NOT-CERTIFIED - Extra keys in the control body are not rejected as specified (though they cause no harm).

**C4 (scope honesty):** CERTIFIED - The page exposes only the 8 allowlisted controls with correct mappings.

## STEELMAN

The Stage-C implementation achieves its core security goal admirably. The fixed dispatch table with method names from constants (never user input) makes authority escalation structurally impossible. The signed cap ceiling on leashes is properly enforced at govern_action, and all approval paths re-gate authority at execution time. The only issue is a minor spec deviation where extra keys aren't explicitly rejected, though they're harmlessly ignored.

## VERDICT

**MINOR_ISSUES** - The implementation is fundamentally sound with one low-severity spec deviation that doesn't compromise security.

**Single highest-value fix:** Add validation to reject control bodies with unexpected keys: `if set(payload.keys()) - {"action"} != set(keys): self._deny(400, "bad request")`
