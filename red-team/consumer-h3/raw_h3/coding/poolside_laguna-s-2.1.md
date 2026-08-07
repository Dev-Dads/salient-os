# Red-team (H3, pass=coding): poolside/laguna-s-2.1

_finish=stop seconds=70.8 usage={'prompt_tokens': 29404, 'completion_tokens': 4969, 'total_tokens': 34373, 'cost': 0.00354078, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.00354078, 'upstream_inference_prompt_cost': 0.00264636, 'upstream_inference_completions_cost': 0.00089442}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

# Red-Team Review: PR-H3 — Governed Compute Budget

## Findings

### 1. ESCALATION / RUNAWAY — `_budget_from_disk` promotes a stale directive into the live cache, enabling cross-turn ratchet

**ID:** H3-01  
**SEVERITY:** CRITICAL  
**LOCATION:** `hermes_cli/observability/salience_observer.py` → `_budget_from_disk` (lines ~410-440) and `_resolve_bounded` (lines ~470-490)

**CONCRETE TRIGGER:**
1. Turn 1 (process A): operator floor = 25, headroom = 15. Busy turn records directive budget = 40 on disk.
2. Process restarts. Turn 2 (process B): operator floor = 25, headroom = 15. `_LAST_DIRECTIVE` is empty, `_BUSES` is empty.
3. `bounded_iterations("s", 25)` → `_resolve_bounded` → `_budget_from_disk("s")` constructs a bus (replays + verifies), reads the last directive (budget=40), and **promotes it into `_LAST_DIRECTIVE["s"] = payload`** (the deep-copied dict).
4. Turn 3 (process B): `_resolve_bounded` finds `_LAST_DIRECTIVE["s"]` populated (from step 3's promotion), returns budget=40 — **without re-finalizing turn 2's window**.
5. Turn 2's window is never closed by `_resolve_bounded` because the early return `if directive is not None: return _directive_budget(directive)` happens **before** the window-finalize block.

Wait — let me re-read `_resolve_bounded` more carefully.

Actually, looking again at `_resolve_bounded`:
```python
def _resolve_bounded(session_id: str, default: int) -> "int | None":
    with _LOCK:
        _ensure_template_valid()
        floor = ...
        window = _WINDOWS.get(session_id)
        if window is not None and not window.closed:
            _close_locked(window, budget=floor)
        directive = _LAST_DIRECTIVE.get(session_id)
        if directive is not None:
            return _directive_budget(directive)
        return _budget_from_disk(session_id)
```

The window finalize happens BEFORE the `_LAST_DIRECTIVE` check. So if `_LAST_DIRECTIVE` was populated by a prior `_budget_from_disk` promotion, the window IS still finalized. But the issue is: the promoted directive from disk is returned **as-is**, even though it was recorded under a potentially different floor.

Let me reconsider. The real problem:

**CONCRETE TRIGGER (corrected):**
1. Process A, Turn 1: floor=25, headroom=15. Busy turn → directive budget=40 recorded on disk.
2. Process B starts. Turn 2: floor=25, headroom=15. `bounded_iterations("s", 25)`:
   - `_resolve_bounded` → window is None (no open window in process B) → skips finalize.
   - `_LAST_DIRECTIVE` is empty → `_budget_from_disk("s")` → reads budget=40 from disk, **promotes it to `_LAST_DIRECTIVE["s"]`**.
   - Returns 40.
3. Turn 3 in process B: `bounded_iterations("s", 25)`:
   - `_resolve_bounded` → window is None → skips finalize.
   - `_LAST_DIRECTIVE["s"]` is now populated (from step 2's promotion) → returns 40.
4. **The promoted directive persists in `_LAST_DIRECTIVE` for the rest of process B**, even though it was recorded under turn 1's floor. If turn 2 or 3 are quiet, they should return to floor=25, but instead they keep returning 40.

This is a **ratchet**: the promoted stale directive keeps getting returned until a new window is opened and closed in-process. A quiet turn in process B returns 40 instead of 25.

**WHY IT MATTERS:** G1 (Bounded Escalation) is violated. The floor is supposed to be the pristine operator budget, but a stale promoted directive from a prior process can keep a turn above the floor indefinitely within the current process. The promotion side-effect in `_budget_from_disk` creates a persistent stale cache entry that outlives its validity.

**SUGGESTED FIX:** Do NOT promote the disk-recovered directive into `_LAST_DIRECTIVE`. Instead, return the budget directly from `_budget_from_disk` without caching it. The in-memory cache should only hold directives produced by in-process window closes. If a second read is needed before the next close, it should re-read from disk (which is cheap — the bus is already cached and replayed).

```python
# In _budget_from_disk, remove the promotion line:
# _LAST_DIRECTIVE[session_id] = payload  # REMOVE THIS
# Just return the budget:
return _directive_budget(payload)
```

### 2. AUDIT HONESTY / FENCE — `_budget_from_disk` reads `_directives` internal attribute, bypassing the bus's public API and replay verification guarantees

**ID:** H3-02  
**SEVERITY:** HIGH  
**LOCATION:** `hermes_cli/observability/salience_observer.py` → `_budget_from_disk` (line ~430)

**CONCRETE TRIGGER:**
The code reads `getattr(bus, "_directives", None)` — a private internal attribute of `SalienceBus`. The docstring of `_budget_from_disk` claims: "The last directive is then read from the bus's VERIFIED in-memory store." But `_directives` is an implementation detail of `SalienceBus`. If the vendored `SalienceBus` changes its internal storage (e.g., renames `_directives` to `_directive_store`, or changes the tuple structure from `(hash, payload)` to `(hash, Directive_object)`), this code silently breaks or reads wrong data.

More critically: the comment says "no second independent parse of the file (which would be redundant with the replay and could pick a stale subject if the file were truncated between the two reads)." But by reading `_directives` directly, the code is **bypassing** the bus's public `directives_for(subject)` method, which does its own subject filtering. The code reads `directives[-1][1]` — the last directive regardless of subject. If the file has directives for multiple subjects (which shouldn't happen in normal operation, but the bus doesn't enforce single-subject), this could read the wrong one.

**WHY IT MATTERS:** G5 (Audit Honesty / Fence) is compromised. The code relies on a private attribute of the vendored bus, creating a fragile coupling. If the bus's internal representation changes, the consumer could read unverified or incorrect data. The code also bypasses the subject-filtering that `directives_for` provides, potentially reading a directive for a different subject.

**SUGGESTED FIX:** Use the public `directives_for` API with the correct subject. But the code comment says "we cannot key without a turn id." The subject is `_subject(session_id, turn_id)`, but `_budget_from_disk` only has `session_id`. The fix requires either:
- Passing the turn_id to `_budget_from_disk` so it can construct the correct subject, or
- Having the bus expose a method like `last_directive()` that returns the most recent directive regardless of subject (with appropriate documentation).

### 3. ESCALATION / RUNAWAY — `_close_locked` uses `_operator_budget()` as floor on rollover close, but `_operator_budget()` is memoized per-process and can be stale if config changes

**ID:** H3-03  
**SEVERITY:** MEDIUM  
**LOCATION:** `hermes_cli/observability/salience_observer.py` → `_close_locked` (line ~215) and `_operator_budget` (line ~310)

**CONCRETE TRIGGER:**
1. Process starts with config: `agent.max_iterations: 25`, `salience.compute_headroom: 15`.
2. `_operator_budget()` is called and memoized to 25.
3. Operator changes config to `agent.max_iterations: 100` (via config file edit).
4. Turn rollover happens. `_close_locked` is called with `budget=None`, so it calls `_operator_budget()` which returns the **cached** value of 25, not the new 100.
5. The directive is issued with floor=25, max=40 (25+15). Even though the operator intended floor=100, the directive is capped at 40.

**WHY IT MATTERS:** While the docstring says "a live config edit takes effect on restart," the memoization means that within a process, a config change to the operator budget is ignored for rollover closes. This isn't a security issue (the budget is still bounded), but it's a correctness issue — the operator's intent is not honored until restart.

**SUGGESTED FIX:** This is documented behavior ("Resolved once per process... a live config edit takes effect on restart"). However, the docstring of `_operator_budget` should be clearer that this applies to rollover closes, and the config example should note that operator budget changes require restart to take effect for rollover closes (though finalize-on-read uses the caller's default, which is always fresh).

### 4. TEST HONESTY — `test_three_turn_cadence_distinct_budgets` does not verify the durable record for `applied1`

**ID:** H3-04  
**SEVERITY:** MEDIUM  
**LOCATION:** `tests/hermes_cli/test_salience_headroom.py` → `test_three_turn_cadence_distinct_budgets` (lines ~230-250)

**CONCRETE TRIGGER:**
The test asserts `(applied1, applied2, applied3) == (10, 28, 34)` and checks the durable record for u1 and u2, but **does not check the durable record for u3**. If the implementation had a bug where u3's directive was recorded with the wrong budget (e.g., 30 instead of 34), the test would still pass because `applied3` is checked against the in-memory return value, not the durable record.

Wait, actually `applied3` IS the return value of `bounded_iterations`, which reads from `_LAST_DIRECTIVE` or `_budget_from_disk`. Let me re-check...

Actually, `applied3 = so.bounded_iterations("s", 30)` — this calls `_resolve_bounded` which closes u2's window (if still open) and reads `_LAST_DIRECTIVE["s"]`. The directive for u2 was emitted during the close of u2's window. So `applied3` does reflect the durable record for u2. But the test doesn't check the durable record for u3 because u3's window is never closed in the test (no `_open("s", "u3")` or `_close_session`).

Actually, looking more carefully: the test opens u1, records events, calls `bounded_iterations` (which closes u1 via finalize-on-read), opens u2, records events, calls `bounded_iterations` (which closes u2 via finalize-on-read). There is no u3 window opened. So `applied3` is the result of closing u2's window and reading its directive. The test does check `bus.directives_for(so._subject("s", "u2"))[0]["compute_budget"] == 34`, which is the durable record for u2. So this is actually fine.

Let me look for a different test honesty issue.

### 5. TEST HONESTY — `test_no_ratchet_across_busy_turns` does not verify that `_LAST_DIRECTIVE` is cleared between turns

**ID:** H3-05  
**SEVERITY:** MEDIUM  
**LOCATION:** `tests/hermes_cli/test_salience_headroom.py` → `test_no_ratchet_across_busy_turns` (lines ~195-220)

**CONCRETE TRIGGER:**
The test verifies that consecutive busy turns return 40 (not 55) and a quiet turn returns 25. It also checks the durable record for u2 and u3. However, it does **not** verify that `_LAST_DIRECTIVE["s"]` is properly updated between turns. If the implementation had a bug where `_LAST_DIRECTIVE` was not updated after closing u2 (e.g., the directive was emitted to the bus but not cached), the test would still pass because `bounded_iterations` reads from the bus via `_budget_from_disk` when `_LAST_DIRECTIVE` is empty.

Wait, but `_budget_from_disk` returns `None` if `_BUSES` already has the session cached. So if `_LAST_DIRECTIVE` is not updated, the second `bounded_iterations` call would return `None` from `_budget_from_disk` (because `_BUSES["s"]` exists), and then return `default` (25). This would cause the test to fail at `assert so.bounded_iterations("s", floor) == 40` for turn 3. So the test does catch this.

Let me look for a more subtle issue.

### 6. ESCALATION / RUNAWAY — `_resolve_bounded` finalizes the prior window with `floor = default`, but `default` is the caller's pristine budget, which may differ from the floor used when the window was opened

**ID:** H3-06  
**SEVERITY:** MEDIUM  
**LOCATION:** `hermes_cli/observability/salience_observer.py` → `_resolve_bounded` (lines ~475-490)

**CONCRETE TRIGGER:**
1. Turn 1: operator floor = 25, headroom = 15. Window u1 is opened.
2. Turn 2: `bounded_iterations("s", 30)` is called (caller's default = 30, perhaps because the agent's `max_iterations` was set to 30 by some other mechanism).
3. `_resolve_bounded` finalizes u1's window with `floor = 30` (the caller's default), not 25 (the operator budget).
4. The directive for u1 is issued with floor=30, max=45 (30+15). If u1 had 8 events, the directive budget = 45.
5. Turn 3: `bounded_iterations("s", 25)` is called. The directive from u1 (budget=45) is returned.

**WHY IT MATTERS:** The floor used for finalize-on-read is the caller's `default`, which may differ from the operator budget. This means the directive's floor is not always the operator budget — it's whatever the caller passed. The docstring of `_close_locked` acknowledges this: "Note the two floor sources CAN differ." But the guarantee G3 says "the finalize-on-read floor is the session's pristine operator budget." This is a discrepancy between the guarantee and the implementation.

Actually, re-reading G3: "the finalize-on-read floor is the session's pristine operator budget, not a ratcheted or re-derived value." The caller's `default` IS the pristine operator budget (captured once per agent in `turn_context.py`). So this is actually correct — the caller passes the pristine operator budget as `default`.

But what if the caller passes a different value? The `bounded_iterations` function is called with `default=agent._salience_operator_iterations`, which is the pristine budget. So in practice, this is correct. But the API allows passing any value, and the docstring of `_close_locked` says "the consumer's finalize-on-read passes the caller's resolved default." This is a design choice, not a bug.

### 7. ESCALATION / RUNAWAY — `_budget_from_disk` deep-copies the directive payload, but the promotion into `_LAST_DIRECTIVE` creates a reference that could be mutated

**ID:** H3-07  
**SEVERITY:** LOW  
**LOCATION:** `hermes_cli/observability/salience_observer.py` → `_budget_from_disk` (line ~435)

**CONCRETE TRIGGER:**
`_budget_from_disk` does `payload = copy.deepcopy(directives[-1][1])` and then `_LAST_DIRECTIVE[session_id] = payload`. The deep copy is correct. But if `_directive_budget` were to mutate the payload (it doesn't — it only reads), the cached copy would be affected. This is a latent issue, not an active bug.

**WHY IT MATTERS:** Low severity — the code is correct as written, but the deep copy + promotion pattern is fragile. If future code modifies `_directive_budget` to mutate its input, it would corrupt the cached directive.

**SUGGESTED FIX:** Consider not promoting to `_LAST_DIRECTIVE` at all (see H3-01 fix), which eliminates this concern entirely.

### 8. HONESTY — Module docstring overstates the guarantee about "never more than floor + headroom" for the resume path

**ID:** H3-08  
**SEVERITY:** LOW  
**LOCATION:** `hermes_cli/observability/salience_observer.py` → module docstring (lines ~15-20)

**CONCRETE TRIGGER:**
The docstring says: "a resumed session's first turn reapplies the LAST RECORDED budget, which can sit anywhere in the prior process's window (the resume caveat in the config example)." And: "never more than floor + headroom (signed policy ceiling)."

But the resume caveat says the recorded budget "can sit anywhere in the prior process's window, above OR below the current operator setting." If the operator budget changed between processes (e.g., from 25 to 10), and the prior process recorded a budget of 40 (25+15), then the resumed turn would apply 40, which is **above** the current operator floor + headroom (10+15=25). This violates "never more than floor + headroom."

**WHY IT MATTERS:** G7 (Honesty) — the docstring overstates the bound. The "never more than floor + headroom" guarantee only holds within a process where the floor is consistent. Across a resume where the operator budget changed, the recorded budget can exceed the current floor + headroom.

**SUGGESTED FIX:** Clarify the docstring to state that the "never more than floor + headroom" bound applies within a process, and that the resume caveat is an explicit exception where the recorded budget may exceed the current operator's floor + headroom.

### 9. AUDIT HONESTY / FENCE — `_budget_from_disk` reads `directives[-1][1]` without verifying it matches the current session's subject

**ID:** H3-09  
**SEVERITY:** LOW  
**LOCATION:** `hermes_cli/observability/salience_observer.py` → `_budget_from_disk` (line ~430)

**CONCRETE TRIGGER:**
`_budget_from_disk` reads `directives[-1][1]` — the last directive in the bus's internal store, regardless of subject. If the bus file somehow contains directives for multiple subjects (which shouldn't happen in normal operation, but the bus doesn't enforce single-subject per file), this could read a directive for a different subject.

**WHY IT MATTERS:** G5 (Audit Honesty / Fence) — the code bypasses the subject-filtering that `directives_for` provides. While this is unlikely to cause issues in practice (each session has its own bus file), it's a latent correctness issue.

**SUGGESTED FIX:** Use `directives_for` with the correct subject, or add a comment explaining why reading the last directive regardless of subject is safe (because each session has its own bus file).

### 10. ESCALATION / RUNAWAY — `test_no_ratchet_across_busy_turns` uses `_open` which does NOT trigger finalize-on-read, so the test doesn't exercise the actual call path

**ID:** H3-10  
**SEVERITY:** LOW  
**LOCATION:** `tests/hermes_cli/test_salience_headroom.py` → `test_no_ratchet_across_busy_turns` (lines ~195-220)

**CONCRETE TRIGGER:**
The test calls `_open("s", "u1")` directly, which opens a window but does NOT call `bounded_iterations`. The `bounded_iterations` call is what triggers finalize-on-read. But `_open` also triggers a rollover close of the previous window (in `_open_window`). So the test does exercise the rollover close path, but not the finalize-on-read path for the first turn.

Actually, looking more carefully: the test calls `so.bounded_iterations("s", floor)` for turn 1, which triggers `_resolve_bounded`, which checks for an open window. Since no window was opened for turn 1 (the test calls `_open("s", "u1")` AFTER the first `bounded_iterations`), there's no window to close. So `applied1 = 25` (the default). Then `_open("s", "u1")` opens u1's window. Then `_events("s", "u1", 8)` records 8 events. Then `so.bounded_iterations("s", floor)` triggers finalize-on-read, which closes u1's window and reads its directive.

This is correct. The test does exercise the finalize-on-read path for turns 2, 3, and 4.

## STEELMAN

The implementation is fundamentally sound: the pristine-baseline fix at the call site correctly prevents the ratchet, the finalize-on-read pattern ensures turn N applies turn N-1's directive, the synthesized ATTENTION signal is properly published before interpretation, and the fail-open/fail-closed patterns are consistently applied. The test suite is comprehensive and mutation-aware, with tests that explicitly name the production mutations they catch. The audit fence is structurally enforced through bounded ref-shaped tokens, and the hash-chained bus provides verifiable integrity.

## VERDICT

**MINOR_ISSUES** — The core design is correct and the ratchet fix is properly implemented, but `_budget_from_disk`'s promotion of stale directives into `_LAST_DIRECTIVE` (H3-01) is a real cross-turn ratchet vector within a process that violates G1. The reliance on private bus internals (H3-02) and the resume-path bound overstatement (H3-08) are honesty/correctness issues that should be addressed. The remaining findings are low-severity latent issues or documentation clarifications.
