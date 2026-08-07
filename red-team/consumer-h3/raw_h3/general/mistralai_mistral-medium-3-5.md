# Red-team (H3, pass=general): mistralai/mistral-medium-3-5

_finish=stop seconds=24.0 usage={'prompt_tokens': 29419, 'completion_tokens': 2870, 'total_tokens': 32289, 'cost': 0.0656535, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0656535, 'upstream_inference_prompt_cost': 0.0441285, 'upstream_inference_completions_cost': 0.021525}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### **Red-Team Findings for PR-H3**

After a thorough adversarial review, here are the **concrete defects** that survive the stated guarantees, ordered by severity.

---

---

### **FINDING 1: CRITICAL – ESCALATION / RUNAWAY**
**ID:** `F1-RATCHET-VIA-RESUME`
**SEVERITY:** **CRITICAL**
**LOCATION:** `salience_observer.py::_budget_from_disk` + `bounded_iterations`
**CONCRETE TRIGGER:**
1. Operator sets `salience.compute_headroom: 15`, `agent.max_iterations: 25`.
2. Turn 1: 8 events → directive `40` (25 + 15) recorded to disk.
3. Process restarts (session resumed).
4. Turn 2 (first turn of resumed session): `_budget_from_disk` recovers `40` and promotes it to `_LAST_DIRECTIVE`.
5. `bounded_iterations` returns `40` (no prior turn in this process to finalize-on-read).
6. Turn 2: 8 events → directive `55` (40 + 15) recorded.
7. Next restart: Turn 3 recovers `55` → directive `70` → **unbounded escalation across restarts**.

**WHY IT MATTERS:**
Violates **G1 (BOUNDED ESCALATION)**. The resume caveat is documented, but the guarantee explicitly states *"within a process"* and *"nothing compounds across turns"*. However, **across restarts**, the recovered budget becomes the new floor for the next turn’s window, creating a **ratchet effect** where each resume can climb by `headroom` indefinitely.

**SUGGESTED FIX:**
In `_budget_from_disk`, clamp the recovered budget to `[operator_floor, operator_floor + headroom]` before promoting it to `_LAST_DIRECTIVE`.
```python
recovered_budget = _directive_budget(payload)
if recovered_budget is not None:
    operator_floor = _operator_budget()
    max_budget = operator_floor + _compute_headroom()
    recovered_budget = max(operator_floor, min(recovered_budget, max_budget))
    _LAST_DIRECTIVE[session_id] = payload  # Still store the original payload for audit honesty
    return recovered_budget
```

---

---

### **FINDING 2: HIGH – FAIL-OPEN / NEVER-BRICK**
**ID:** `F2-DIRECTIVE-BUDGET-UNDERFLOW`
**SEVERITY:** **HIGH**
**LOCATION:** `salience_observer.py::_directive_budget`
**CONCRETE TRIGGER:**
A maliciously crafted directive payload with `compute_budget: 0` (or a hard-deny with `compute_budget: 0`) is stored on disk.
On resume, `_budget_from_disk` returns `0` (since `0` is an `int` and not a `bool`).
`bounded_iterations` then returns `0` (fails open to `default` only if `_directive_budget` returns `None`).
**Result:** `agent.max_iterations = 0` → **bricked agent** (infinite loop or immediate termination).

**WHY IT MATTERS:**
Violates **G2 (FAIL-OPEN / NEVER-BRICK)**. The `_directive_budget` function treats `0` as a valid budget, but the consumer must **never** return `< 1`. The hard-deny guard in `_directive_budget` checks for `budget < 1`, but `0` is explicitly allowed to pass (since `0` is a valid `int` and not a `bool`).

**SUGGESTED FIX:**
Modify `_directive_budget` to reject `compute_budget < 1` (not just `<= 0`):
```python
if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
    return None
```

---

---

### **FINDING 3: HIGH – A4 FLOOR INTEGRITY**
**ID:** `F3-OPERATOR-BUDGET-CACHE-POLLUTION`
**SEVERITY:** **HIGH**
**LOCATION:** `salience_observer.py::_operator_budget`
**CONCRETE TRIGGER:**
1. Process starts with `agent.max_iterations: 25` (default).
2. `_operator_budget()` caches `25`.
3. Operator live-edits config to `agent.max_iterations: 50` (without restart).
4. Turn 1: 8 events → directive `25 + headroom` (cached floor `25`).
5. Turn 2: `bounded_iterations` passes `default=50` (new operator budget), but `_resolve_bounded` calls `_operator_budget()` (cached `25`) for the finalize-on-read floor.
6. Result: Turn 2’s directive is floored at `25` (stale cache), not `50`.

**WHY IT MATTERS:**
Violates **G3 (A4 FLOOR INTEGRITY)**. The finalize-on-read floor must be the **pristine operator budget** for the current turn, but the cache is **never invalidated** after first read. This means a config change mid-process will cause the produce-path to use a stale floor, while the consumer’s `default` is fresh.

**SUGGESTED FIX:**
Remove memoization from `_operator_budget` (or invalidate it on config change signals). Since config changes are rare, the performance impact is negligible.
Alternatively, make `_operator_budget` read the config fresh every time (since it’s already guarded by `_LOCK`).

---

---

### **FINDING 4: MEDIUM – A3 WITH MOVEMENT**
**ID:** `F4-STALE-DIRECTIVE-ON-FAILED-PUBLISH`
**SEVERITY:** **MEDIUM**
**LOCATION:** `salience_observer.py::_close_locked`
**CONCRETE TRIGGER:**
1. Turn 1: 4 events, headroom `10`, floor `20`.
2. Synthesized ATTENTION signal fails to publish (e.g., disk full).
3. `_close_locked` logs a warning but **still emits a directive** (with `budget = floor + 0 = 20`).
4. Turn 2: `bounded_iterations` reads the directive `20` (from Turn 1’s close).
5. Turn 2: 8 events → directive `30` (20 + 10).
6. **Problem:** Turn 2’s budget is `20` (from Turn 1’s failed ATTENTION), but Turn 1’s **true** budget should have been `20` (floor) because the ATTENTION signal was dropped. However, the directive was still emitted with `budget=20`, which is correct, but the **audit record is inconsistent**: the directive claims `compute_budget=20` but the ATTENTION signal (which would have justified a higher budget) is missing.

**WHY IT MATTERS:**
Violates **G5 (AUDIT HONESTY / FENCE)**. The directive’s `compute_budget` is `20`, but the **absence of the ATTENTION signal** implies the budget should have been `20` anyway. However, the **provenance is misleading**: the directive does not record that the ATTENTION signal was attempted but failed. This is a **fence violation** because the audit record does not honestly reflect the reasoning.

**SUGGESTED FIX:**
If the ATTENTION publish fails, **do not emit a directive at all** (treat it as a failed close). This ensures the consumer falls back to `default`, and the audit record remains consistent (no directive = no movement).

```python
if window.events > 0:
    attention = _signal(...)
    try:
        _bus_for(window.session_id).publish(attention)
        signals.append(attention)
    except Exception:
        logger.warning("salience observer: attention publish failed; "
                       "directive will not be emitted", exc_info=True)
        return  # Skip directive emission entirely
```

---

---
---
### **FINDING 5: MEDIUM – TEST HONESTY**
**ID:** `F5-MUTATION-BLIND-TEST`
**SEVERITY:** **MEDIUM**
**LOCATION:** `tests/hermes_cli/test_salience_headroom.py::test_no_ratchet_across_busy_turns`
**CONCRETE TRIGGER:**
The test asserts:
```python
assert so.bounded_iterations("s", floor) == 40   # turn 3: STILL 40 — no 55
```
But if `_resolve_bounded` were buggy and **reused the prior applied budget** (e.g., `default=40` instead of `default=25`), the test would **still pass** because:
- Turn 2: `bounded_iterations("s", 25)` → `40` (correct).
- Turn 3: `bounded_iterations("s", 25)` → `40` (correct, because the prior directive was `40` and events=8 → `40 + 15 = 55`, but the test does **not** check the directive value, only the returned budget).

**WHY IT MATTERS:**
Violates **G7 (TEST HONESTY)**. The test does not verify the **directive value** on the bus, only the returned budget. A mutation where `_resolve_bounded` floors on `agent.max_iterations` (the ratchet bug) would **still pass** this test because the consumer’s `default` is hardcoded to `floor=25`.

**SUGGESTED FIX:**
Add an assertion to check the directive’s `compute_budget` on the bus:
```python
bus = so._BUSES["s"]
assert bus.directives_for(so._subject("s", "u2"))[0]["compute_budget"] == 40
```

---

---
---
### **FINDING 6: LOW – HONESTY (DOCUMENTATION)**
**ID:** `F6-DOC-OVERSTATE-RESUME-CAVEAT`
**SEVERITY:** **LOW**
**LOCATION:** `cli-config.yaml.example` (lines 1710-1715)
**CONCRETE TRIGGER:**
The prose states:
> "Caveat on resume: the first turn of a resumed session reapplies the LAST RECORDED budget for that session — with headroom on, simply ending on a busy turn is enough (no config change needed): the recorded value can sit anywhere in the prior window, above OR below the current operator setting, until the next turn is finalized."

**WHY IT MATTERS:**
Understates the risk. The resume caveat **does not clarify** that the recovered budget can **exceed the current operator’s floor + headroom** (as in **F1-RATCHET-VIA-RESUME**), creating unbounded escalation across restarts. This is a **documentation gap** that could lead operators to assume the resume behavior is bounded.

**SUGGESTED FIX:**
Add:
> "Note: The recovered budget is **not** clamped to the current operator’s window. If the prior process had a higher floor or headroom, the resumed session may start with a budget above the current `[floor, floor + headroom]` range. This is a known limitation; see ADR 0002."

---

---
---
### **STEELMAN**
1. **The design is sound in-process**: The pristine-baseline fix (F1) correctly prevents intra-process ratcheting, and the finalize-on-read ensures A3 correctness.
2. **Audit honesty is structural**: The synthesized ATTENTION signal is **always** published before interpretation, and the bus enforces ref-shaped tokens. The fence is **not** breached in the happy path.
3. **Fail-open is mostly robust**: The consumer’s `default` fallback and `Exception/SystemExit` containment are well-implemented. The only gap is the `compute_budget: 0` edge case (F2).

---
### **VERDICT**
**SERIOUS_FLAWS** – **F1 (resume ratchet)** and **F2 (budget underflow)** are **critical** and **high-severity** defects that violate core guarantees (G1, G2). **F3 (stale cache)** and **F4 (audit inconsistency)** are **high/medium** and undermine integrity. **F5 (test blindness)** and **F6 (doc gap)** are lower-priority but still real.

**Summary:** PR-H3 is **not ready to merge**—the resume ratchet and budget underflow must be fixed first. The other findings should be addressed before release.
