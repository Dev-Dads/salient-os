# Red-team (control seam): x-ai/grok-4.5

_finish=stop seconds=273.7 usage={'prompt_tokens': 14236, 'completion_tokens': 14909, 'total_tokens': 29145, 'cost': 0.1177084, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1177084, 'upstream_inference_prompt_cost': 0.0282544, 'upstream_inference_completions_cost': 0.089454}, 'completion_tokens_details': {'reasoning_tokens': 12911, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / Defensive `effective_stakes` clamp inflates `achieved_level` → false FULL clearance
- **Severity:** HIGH
- **Location:** `salienceos/control/govern.py` — `decide()`
- **Concrete trigger:**
```python
from salienceos.control.govern import decide
from salienceos.control.outcome import FULL
from salienceos.interpreter import AdaptationEligibility, Directive, Reconfigure
from salienceos.verifier import Stakes, Status, Verdict

d = Directive(
    subject="act-1", policy_id="p", compute_budget=100, verification_depth=0,
    retention_class="working", routing_hint="", adaptation_eligibility=AdaptationEligibility.NONE,
    allowed_capabilities=(), reconfigure=Reconfigure.BETWEEN_TURN,
    interpreter_version="test", reasons=(),
)
# One-source VERIFIED (what LOW/NORMAL composer rigor actually produces),
# paired with a HIGH envelope floor and a *lowered* effective_stakes.
v = Verdict(status=Status.VERIFIED, reasons=(), composer_version="test")
o = decide(d, "act-1", Stakes.HIGH, v, Stakes.LOW)
assert o.effective_stakes is Stakes.HIGH   # clamp "repaired" the floor
assert o.achieved_level == FULL            # reinterpreted as two-source!
assert o.cleared is True                   # HIGH-stakes action cleared with no two-source evidence
```
  (This is exactly the input shape of `test_decide_clamps_a_lowered_effective_stakes`, which asserts only the clamp and never checks `cleared`/`achieved_level`.)
- **Why it matters:** Invariant 2 (fail-closed clearance) and the HIGH/CRITICAL = two-source bar. `achieved_level()` treats `VERIFIED + (HIGH|CRITICAL)` as FULL. The “defense in depth” line `effective_stakes = max_stakes(envelope_stakes, effective_stakes)` **upgrades** a caller-supplied under-stated stakes *before* that interpretation, so a 1-source `VERIFIED` is laundered into FULL and clears a HIGH/CRITICAL floor. Without the clamp, `achieved=INDEPENDENT < required=FULL` and clearance would correctly deny. Buggy caller / wrong subsystem is in-scope; `decide` is the exported pure spine.
- **Suggested fix (minimal):** Do not re-interpret the verdict after upgrading stakes. If `effective_stakes` is below the envelope floor, **fail closed** (deny `cleared` / append a reason) rather than clamping then trusting `achieved_level`. Optionally record the clamp only on the output field after achievement is computed from the caller-supplied (pre-clamp) value, or require strict equality with `max_stakes(envelope_stakes, escalate)` from `govern`.

---

### F2 / `decide()` does not bind `Verdict` to envelope/action identity
- **Severity:** HIGH
- **Location:** `salienceos/control/govern.py` — `decide()` (and `Verdict` in `salienceos/verifier/verdict.py`, which carries no `envelope_id`)
- **Concrete trigger:**
```python
# verdict_A obtained from a successful LOW/NORMAL verification of action A
# (or simply Verdict(status=Status.VERIFIED, ...))
o = decide(
    directive(subject="act-B", depth=0),  # matches B
    "act-B",                              # envelope id B
    Stakes.HIGH,
    verdict_A,                            # evidence/result for A, not B
    Stakes.HIGH,
)
assert o.cleared  # True — no check that verdict is about act-B
```
- **Why it matters:** Invariants 2 and 3. Subject/envelope matching only pairs `directive.subject` with `envelope_id`. A misfiring subsystem can reuse an unrelated `VERIFIED` (or pair the wrong receipt path’s verdict) and obtain clearance/adaptation for a different action. `govern()` happens to thread one attempt correctly; the pure gate does not enforce it.
- **Suggested fix (minimal):** Stamp `envelope_id` (and ideally effective stakes) onto `Verdict` at compose time; in `decide`, require `verdict.envelope_id == envelope_id` (and stakes agreement) or deny.

---

### F3 / Caller-raised `effective_stakes` above real verification rigor also false-clears FULL
- **Severity:** MEDIUM
- **Location:** `salienceos/control/govern.py` — `decide()` + `achieved_level()`
- **Concrete trigger:**
```python
# VERIFIED produced under LOW/NORMAL (one world source), but caller claims HIGH rigor
o = decide(directive(depth=3), "act-1", Stakes.LOW, VERIFIED, Stakes.HIGH)
# effective = max(LOW, HIGH) = HIGH → achieved FULL ≥ required FULL → cleared
assert o.cleared is True
```
- **Why it matters:** Same root as F1, opposite direction: invariant 1 allows salience to escalate scrutiny, but `effective_stakes` is also the *evidence* that two-source rigor was applied. Overstating it awards FULL without a two-source verdict. `govern()` does not do this; direct `decide()` does.
- **Suggested fix (minimal):** Same as F1/F2 — bind stakes-at-compose onto the verdict; `decide` should trust verdict metadata, not a free `effective_stakes` parameter, for `achieved_level`.

---

### F4 / Invalid stakes values crash instead of fail-closed deny
- **Severity:** LOW
- **Location:** `salienceos/verifier/envelope.py` — `max_stakes()`; called from `decide()` / `Verifier.verify()`
- **Concrete trigger:** `decide(d, "act-1", Stakes.NORMAL, VERIFIED, "high")` or `max_stakes(Stakes.NORMAL, "high")` → `ValueError` from `STAKES_ORDER.index(...)`.
- **Why it matters:** Buggy caller should get `cleared=False`, not an exception that may abort a higher-level handler into an undefined path. `_stakes_floor` already fail-closes unknowns to FULL; `max_stakes` does not.
- **Suggested fix (minimal):** In `max_stakes` / `decide`, on non-`Stakes` / non-`None` input, treat as unknown and deny (or coerce to strictest and force `cleared=False`).

---

### F5 / Binding ignores `policy_id`
- **Severity:** LOW
- **Location:** `salienceos/control/govern.py` — `decide()` (`bound = ...`)
- **Concrete trigger:** `directive.subject == envelope_id == "act-1"` but `directive.policy_id = "p-A"` while envelope was issued under `"p-B"`.
- **Why it matters:** Mixed authority if envelope IDs ever collide across policies; weaker than subject mismatch fail-closed.
- **Suggested fix (minimal):** Require `directive.policy_id == envelope.policy_id` when `govern` has the envelope (pass policy_id into `decide`).

---

### Not counted as defects (checked)
- `govern()` path: `escalate_to` + `max_stakes` is upward-only; `escalate_to=None` is a true no-op; e2e “cannot lower / must raise” behavior holds.
- Adaptation gate requires `Status.VERIFIED` (not merely `cleared` / `INTEGRITY_ATTESTED`) — invariant 3 holds on the intended path.
- `required = max(depth, _stakes_floor(envelope_stakes))` correctly prevents a low-depth directive from clearing a HIGH-signed envelope when the verdict is honest.
- LOW vs NORMAL composer rigor is identical (`_required_sources`); only FULL→HIGH escalation is required for a total depth↔stakes↔level story; that mapping is consistent for `govern`.
- `decide()` is pure (local rebind only; no I/O).

---

## STEELMAN
The seam’s production spine (`govern` → `verify(..., escalate_to=...)` → `decide`) correctly implements upward-only escalation, floors required clearance by **both** directive depth and policy-signed envelope stakes, and seals adaptation behind a real `VERIFIED`. The verifier change is behavior-preserving when `escalate_to` is omitted, and the tests pin the important “salience cannot lower / FULL must lift NORMAL→HIGH” cases end-to-end.

## VERDICT
**SERIOUS_FLAWS** — `govern()` is sound, but the exported pure gate `decide()` can award HIGH/FULL clearance (and thus adaptation when eligibility is set) from a one-source `VERIFIED` whenever `effective_stakes` / envelope floor are desynchronized from the verdict; the defensive clamp in F1 actively creates a false-clear path the tests do not block.
