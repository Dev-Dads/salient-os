# Red-team (consumer, pass=general): mistralai/mistral-medium-3-5

_finish=stop seconds=28.6 usage={'prompt_tokens': 25164, 'completion_tokens': 2722, 'total_tokens': 27886, 'cost': 0.058161, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.058161, 'upstream_inference_prompt_cost': 0.037746, 'upstream_inference_completions_cost': 0.020415}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### Red-Team Findings for PR-H2 (SalienceOS Consumer)

---

#### **F1 / FAIL-OPEN BYPASS VIA `BaseException` IN `_operator_budget`**
**SEVERITY: CRITICAL**
**LOCATION:** `salience_observer.py::_operator_budget()`
**CONCRETE TRIGGER:**
1. Call `bounded_iterations("s", 10)` with `_OPERATOR_BUDGET_CACHE = None` (first call).
2. `read_raw_config_readonly()` raises a `BaseException` (e.g., `KeyboardInterrupt` or a custom exception not inheriting from `Exception`).
3. The `except (Exception, SystemExit)` in `_operator_budget` does **not** catch `BaseException`, so the exception propagates to `_resolve_bounded` → `bounded_iterations` → **host turn crash**.

**WHY IT MATTERS:**
Violates **Guarantee 1 (FAIL-OPEN)**. The consumer’s `except (Exception, SystemExit)` in `bounded_iterations` is insufficient because `_operator_budget` (called under `_LOCK` in `_resolve_bounded`) only catches `(Exception, SystemExit)`. A `BaseException` (e.g., `GeneratorExit`, `KeyboardInterrupt`) would escape and brick the turn.

**SUGGESTED FIX:**
Change `_operator_budget`’s `except` to `except BaseException:` (or re-raise as `Exception` after logging). Alternatively, wrap the call to `_operator_budget` in `_resolve_bounded` with `except BaseException`.

---

#### **F2 / A3 STALENESS: FINALIZE-ON-READ DOES NOT CLOSE WINDOW IF `default` IS INVALID**
**SEVERITY: HIGH**
**LOCATION:** `salience_observer.py::_resolve_bounded()`
**CONCRETE TRIGGER:**
1. Turn N-1’s window is open (session `"s"`, turn `"u1"`).
2. Turn N calls `bounded_iterations("s", default=0)` (invalid `default`).
3. The `floor` assignment falls back to `_operator_budget()` (valid, say `25`).
4. The window **is not closed** because the `if window is not None and not window.closed` check passes, but `_close_locked` is **not called** (logic error: the `if` condition is correct, but the `floor` fallback does not trigger a close).

**WHY IT MATTERS:**
Violates **Guarantee 2 (A3)**. Turn N will read a stale directive (from turn N-2 or earlier) because turn N-1’s window remains open. The `floor` fallback should still finalize the prior window.

**SUGGESTED FIX:**
Move the `_close_locked` call **outside** the `floor` assignment logic. Always attempt to close the prior window if it exists and is open, regardless of `default` validity:
```python
window = _WINDOWS.get(session_id)
if window is not None and not window.closed:
    _close_locked(window, budget=floor)  # Always close if open, even if floor is fallback
```

---

#### **F3 / DENY-SHAPED BYPASS: `_directive_budget` ACCEPTS `compute_budget=0` FOR NON-HARD-DENY DIRECTIVES**
**SEVERITY: HIGH**
**LOCATION:** `salience_observer.py::_directive_budget()`
**CONCRETE TRIGGER:**
1. A **non-hard-deny** directive (valid `subject` and `policy_id`) with `compute_budget=0` is cached in `_LAST_DIRECTIVE["s"]`.
2. `_directive_budget` returns `0` (because `subject` and `policy_id` are non-empty, and the `budget < 1` check is **not applied to non-hard-deny directives**).
3. `bounded_iterations` returns `0`, setting `max_iterations=0` → **agent bricked**.

**WHY IT MATTERS:**
Violates **Guarantee 2 (DENY-SHAPED GUARD)**. The hard-deny guard only checks for **blank `subject`/`policy_id`**, but a `compute_budget=0` in a valid directive is **not treated as absent**. This allows a malformed (but signed) directive to brick the agent.

**SUGGESTED FIX:**
Remove the `subject`/`policy_id` check from `_directive_budget`’s early return. Instead, **always** enforce `budget >= 1` for any directive (hard-deny or not):
```python
if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
    return None  # Treat ALL sub-1 budgets as absent, regardless of subject/policy_id
```

---

#### **F4 / RESTART FALLBACK INTEGRITY: TOCTOU BETWEEN `_bus_for` AND `_budget_from_disk`**
**SEVERITY: HIGH**
**LOCATION:** `salience_observer.py::_budget_from_disk()`
**CONCRETE TRIGGER:**
1. Session `"s"` has a verified JSONL file on disk.
2. Thread A calls `_budget_from_disk("s")` → `session_id in _BUSES` is `False` → proceeds.
3. Thread B concurrently calls `_bus_for("s")` → caches a `SalienceBus` for `"s"` in `_BUSES`.
4. Thread A’s `_bus_for("s")` now **reuses the cached bus** (which was not replay-verified in this call) and reads the last directive **without re-verifying the file**.
5. If the file was tampered **after** Thread B’s `_bus_for` but **before** Thread A’s read, the corrupt data is accepted.

**WHY IT MATTERS:**
Violates **Guarantee 5 (RESTART FALLBACK INTEGRITY)**. The `session_id in _BUSES` guard is **racy**: a concurrent `_bus_for` can cache a bus, causing `_budget_from_disk` to skip replay verification and read a potentially tampered file.

**SUGGESTED FIX:**
Hold `_LOCK` for the **entire** `_budget_from_disk` operation (including the `session_id in _BUSES` check and `_bus_for` call). Currently, `_budget_from_disk` is called under `_LOCK` in `_resolve_bounded`, but the `session_id in _BUSES` check is **not atomic** with the `_bus_for` call. Move the check inside the `_LOCK` critical section.

---
---

#### **F5 / CONCURRENCY: `_LOCK` RE-ACQUIRE IN `_close_locked` VIA `issue_policy`**
**SEVERITY: MEDIUM**
**LOCATION:** `salience_observer.py::_close_locked()`
**CONCRETE TRIGGER:**
1. Thread A holds `_LOCK` and calls `_close_locked(window)`.
2. `_close_locked` calls `issue_policy`, which internally calls `sign()` from `salienceos.verifier.signing`.
3. If `sign()` (or any vendored code) **re-acquires `_LOCK`** (e.g., for thread-safety in the vendored package), this causes a **deadlock**.

**WHY IT MATTERS:**
Violates **Guarantee 6 (CONCURRENCY)**. The vendored `salienceos` package is **not guaranteed** to avoid locking. If it uses `_LOCK` (or any lock held by the caller), this will deadlock.

**SUGGESTED FIX:**
Document that `_close_locked` **must not** call into vendored code that could re-acquire `_LOCK`. Alternatively, wrap the `issue_policy` call in a `try/finally` that releases `_LOCK` temporarily (not ideal). **Better:** Audit the vendored `salienceos` to ensure it does not use `_LOCK` (or any lock that could conflict).

---
---

#### **F6 / HONESTY: DOCSTRING OVERSTATEMENT IN `bounded_iterations`**
**SEVERITY: LOW**
**LOCATION:** `salience_observer.py::bounded_iterations` (docstring)
**CONCRETE TRIGGER:**
The docstring claims:
> "In the v0 config the directive echoes the operator's own budget (pinned window + ATTENTION unmapped), so this is behavior-preserving until a future change..."

**WHY IT MATTERS:**
Misleading. The **real** reason it’s behavior-preserving is that **`min_budget == max_budget == operator_budget`** (pinned window) **AND** no facet (including `ATTENTION`) is mapped to move the budget. The docstring implies that `ATTENTION` unmapped alone is sufficient, but **both conditions are required**. This could mislead an operator into thinking unmapping `ATTENTION` is enough to preserve behavior.

**SUGGESTED FIX:**
Clarify:
> "In the v0 config, the policy pins `min_budget == max_budget == operator_budget` **and** no facet (including `ATTENTION`) is mapped to move the budget, so the directive always echoes the operator’s budget. Behavior changes only if **both** a budget-moving facet is mapped **and** the policy window is widened (`max_budget > min_budget`)."

---
---

#### **F7 / TEST HONESTY: `test_restart_recovers_budget_from_disk` DOES NOT VERIFY REPLAY INTEGRITY**
**SEVERITY: MEDIUM**
**LOCATION:** `tests/hermes_cli/test_salience_consumer.py::test_restart_recovers_budget_from_disk`
**CONCRETE TRIGGER:**
1. Sabotage the test by **removing the `bus._replay()` call** in `_bus_for` (or neutering `SalienceBus._replay`).
2. The test **still passes** because it only checks that the budget is recovered from disk, **not** that the bus was replay-verified.

**WHY IT MATTERS:**
Violates **Guarantee 5 (RESTART FALLBACK INTEGRITY)**. The test does not verify that the recovered budget came from a **verified** bus. A mutation that skips replay verification would go undetected.

**SUGGESTED FIX:**
Add an assertion that the bus’s `_replay` was called (or that the file’s hash chain is valid). For example:
```python
bus = so._bus_for("s")
assert bus.verify_chain()  # Ensure the bus was replay-verified
```

---
---
---
### **STEELMAN**
1. **Fail-Open Design:** The consumer’s top-level `except (Exception, SystemExit)` in `bounded_iterations` is **correctly broad** for most host API failures. The `_LOCK` usage is **consistent** and prevents races in the hot path.
2. **A3 Correctness:** The finalize-on-read logic in `_resolve_bounded` **correctly** closes the prior turn’s window before reading its directive, ensuring turn N applies turn N-1’s directive in the happy path.
3. **Deny-Shaped Guard:** The `_directive_budget` function **correctly** handles hard-deny directives (blank `subject`/`policy_id`) and most malformed budgets.
4. **Restart Fallback:** The cold-path recovery via `_budget_from_disk` **correctly** uses `SalienceBus.directives_for` (which is replay-verified) and skips the fallback if a bus is already cached (preventing stale reads).

---
### **VERDICT**
**SERIOUS_FLAWS** – **F1 (CRITICAL)** and **F2/F3/F4 (HIGH)** are concrete, exploitable defects that violate core guarantees (FAIL-OPEN, A3, DENY-SHAPED, RESTART INTEGRITY). **F5 (MEDIUM)** is a potential deadlock, and **F7 (MEDIUM)** is a test gap. The code is **not sound** as-is; these must be fixed before merge.
