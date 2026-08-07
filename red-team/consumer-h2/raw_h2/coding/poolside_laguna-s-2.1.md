# Red-team (consumer, pass=coding): poolside/laguna-s-2.1

_finish=stop seconds=24.8 usage={'prompt_tokens': 26580, 'completion_tokens': 2646, 'total_tokens': 29226, 'cost': 0.00286848, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.00286848, 'upstream_inference_prompt_cost': 0.0023922, 'upstream_inference_completions_cost': 0.00047628}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

# Red-Team Review of PR-H2: SalienceOS Consumer

## Findings

### 1. CRITICAL — `bounded_iterations` returns non-int `default` unchanged, bricking the agent at `max_iterations=0`

**LOCATION:** `salience_observer.py:bounded_iterations` (final return path)

**CONCRETE TRIGGER:**
```python
# default is a float like 3.5 (not a plain int)
agent.max_iterations = 3.5
applied = so.bounded_iterations("s", 3.5)  # returns 3.5 unchanged
agent.max_iterations = applied            # = 3.5
budget = IterationBudget(agent.max_iterations)  # max_total = 3.5
# IterationBudget.consume() compares _used >= max_total; with max_total=3.5,
# _used=4 passes (4 >= 3.5 is False), so the agent runs unbounded.
# OR if default is 0 or negative: max_total=0 → consume() always returns False → agent bricks.
```

**WHY IT MATTERS:** The guarantee explicitly states "must NEVER return a budget < 1 (that would brick the agent at max_iterations 0)." The current code returns `default` unchanged when it's not a plain int, including floats, 0, and negatives. The host then sets `agent.max_iterations = applied` and builds `IterationBudget(agent.max_iterations)`. A float `max_total` breaks the integer comparison in `consume()`, and a `max_total <= 0` bricks the agent entirely.

**SUGGESTED FIX:** Clamp the final return to `max(1, int(default))` when returning `default`, or validate `default > 0` and return `default` only if it's a positive int, otherwise return `max(1, _operator_budget())`.

---

### 2. HIGH — TOCTOU: `_budget_from_disk` reads `bus._directives[-1][1]` after `_bus_for` constructs the bus, but the bus's replay verification happens in `__init__`, creating a window where a concurrent writer could append between verification and read

**LOCATION:** `salience_observer.py:_budget_from_disk`

**CONCRETE TRIGGER:**
1. Process A calls `bounded_iterations("s", 10)` on a cold session.
2. `_budget_from_disk` calls `_bus_for("s")`, which constructs `SalienceBus(path)`.
3. `SalienceBus.__init__` calls `_replay(path)` — verifies the chain up to the current file contents.
4. Between `_replay` completing and `_budget_from_disk` reading `bus._directives[-1][1]`, Process B appends a new (corrupt or tampered) directive to the same JSONL file.
5. Process A reads `directives[-1][1]` — the newly appended, unverified entry.

**WHY IT MATTERS:** The guarantee requires that cold recovery "MUST go through the replay-verifying SalienceBus so a corrupt/tampered tail RAISES (=> default), never feeding an unverified value." The current code verifies the chain at construction time but then reads private state (`_directives[-1][1]`) without re-verifying. A concurrent writer can inject an unverified entry between the verification and the read.

**SUGGESTED FIX:** After reading `directives[-1][1]`, call `bus.verify_chain()` again before using the value, or use the public `directives_for()` API with a subject lookup (though the comment notes this isn't available without a turn id).

---

### 3. HIGH — `_directive_budget` accepts `bool` as a valid `compute_budget` via the `isinstance(budget, int)` check, since `bool` is a subclass of `int` in Python

**LOCATION:** `salience_observer.py:_directive_budget`

**CONCRETE TRIGGER:**
```python
# A directive with compute_budget=True (which is == 1 in Python)
directive = {"subject": "s", "policy_id": "p", "compute_budget": True}
budget = so._directive_budget(directive)  # returns True (== 1)
# bounded_iterations returns True, which is then assigned to agent.max_iterations
# IterationBudget(True) → max_total = 1 (works by accident, but semantically wrong)
```

**WHY IT MATTERS:** The code has `if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1: return None`. However, `isinstance(True, int)` is `True` in Python, and `isinstance(True, bool)` is also `True`. The check `isinstance(budget, bool)` correctly excludes booleans. Wait — let me re-read... Actually the check IS correct: `isinstance(budget, bool)` returns True for booleans, so `not isinstance(budget, int) or isinstance(budget, bool)` evaluates to `False or True = True`, causing it to return None. This is actually handled correctly.

Let me re-examine... The condition is: `if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1: return None`. For `budget = True`: `not isinstance(True, int)` = `not True` = `False`; `isinstance(True, bool)` = `True`. So `False or True or ...` = `True` → returns None. This is correct.

However, there's a subtle issue: what about `budget = 1` (a legitimate int)? `not isinstance(1, int)` = `False`; `isinstance(1, bool)` = `False`; `1 < 1` = `False`. So `False or False or False` = `False` → does NOT return None, returns 1. This is correct.

Actually, this finding is INVALID — the bool check is correctly placed. Let me look for a different issue.

---

### 3. MEDIUM — `_directive_budget` does not validate that `compute_budget` is a positive integer when reading from a `Directive` object (only from dict)

**LOCATION:** `salience_observer.py:_directive_budget`

**CONCRETE TRIGGER:**
```python
# A Directive object with compute_budget = -5 (should be treated as malformed/absent)
class FakeDirective:
    subject = "s"
    policy_id = "p"
    compute_budget = -5

directive = FakeDirective()
budget = so._directive_budget(directive)  # returns -5, not None!
# bounded_iterations returns -5 → agent.max_iterations = -5
# IterationBudget(-5) → max_total = -5 → consume() always returns False → agent bricks
```

**WHY IT MATTERS:** The deny-shaped guard (A5) states "a non-int / bool / sub-1 budget is malformed" and must be treated as absent. For dict sources, the code checks `budget < 1`. But for object sources (the `else` branch), it only checks `isinstance(budget, int)` and `isinstance(budget, bool)` — it does NOT check `budget < 1`. A `Directive` object with a negative `compute_budget` would pass through and potentially brick the agent.

**SUGGESTED FIX:** Add `budget < 1` check to the object branch as well, or unify the logic to check all conditions regardless of source type.

---

### 4. MEDIUM — `_resolve_bounded` closes the prior window with `floor` (this turn's default), but if `default` is a non-int, `floor` falls back to `_operator_budget()`, which may differ from what the caller expects

**LOCATION:** `salience_observer.py:_resolve_bounded`

**CONCRETE TRIGGER:**
```python
# default is a string "10" (not a plain int)
agent.max_iterations = "10"  # somehow set to a string
applied = so.bounded_iterations("s", "10")
# In _resolve_bounded: floor = default if (isinstance(default, int)...) else _operator_budget()
# Since "10" is not an int, floor = _operator_budget() = 25 (the _DEFAULT_BUDGET)
# The window is closed with budget=25, not 10
# Then _directive_budget returns 25 (from the directive)
# bounded_iterations returns 25, not "10"
# agent.max_iterations = 25 (changed from "10" to 25!)
```

**WHY IT MATTERS:** The guarantee says "fails OPEN to `default` unchanged." But when `default` is not a plain int, the code falls back to `_operator_budget()` for the floor, which may produce a different value. The caller's `default` is not preserved.

**SUGGESTED FIX:** When `default` is not a valid int, return `default` unchanged immediately (before entering `_resolve_bounded`), rather than falling back to `_operator_budget()`.

---

### 5. LOW — `_budget_from_disk` creates a bus via `_bus_for` even when the file doesn't exist, because the `path.exists()` check is done before `_bus_for` is called, but `_bus_for` itself doesn't check existence

**LOCATION:** `salience_observer.py:_budget_from_disk`

**CONCRETE TRIGGER:**
```python
# File doesn't exist, but _budget_from_disk is called
# path.exists() returns False → returns None (correct)
# But if there's a race: file is created between path.exists() and _bus_for()
# _bus_for creates a SalienceBus with a path that now exists
# SalienceBus.__init__ calls _replay(path) which reads the file
# If the file was just created and is empty or partially written, _replay may behave unexpectedly
```

**WHY IT MATTERS:** This is a minor race condition. The `path.exists()` check and the `_bus_for` call are not atomic. In practice, this is unlikely to cause issues since an empty file would result in an empty `_directives` list, and the code handles that case. But it's a TOCTOU issue.

**SUGGESTED FIX:** Move the existence check inside `_bus_for` or handle the case where the file is empty/partial in `_budget_from_disk`.

---

### 6. LOW — Test `test_call_site_precedes_budget_rebuild` reads source code statically, which is fragile and may not catch actual reordering

**LOCATION:** `tests/hermes_cli/test_salience_consumer.py:test_call_site_precedes_budget_rebuild`

**CONCRETE TRIGGER:**
If the source code is reformatted (e.g., line breaks change, comments are added/removed), the static line-number-based assertions may break even if the logical ordering is preserved. Conversely, if the code is reordered in a way that preserves line proximity but changes semantics (e.g., the consumer call is moved into a conditional block), the test may still pass.

**WHY IT MATTERS:** The test relies on static source code analysis rather than runtime behavior verification. This makes it brittle and potentially misleading.

**SUGGESTED FIX:** Consider a runtime-based test that actually executes the turn context building and verifies the ordering, or make the static test more robust by searching for semantic patterns rather than exact line positions.

---

## STEELMAN

The consumer implementation demonstrates strong security awareness with comprehensive fail-open handling, proper deny-shaped directive treatment, and careful attention to the A3 turn-ordering guarantee. The deep-copy of cached directives, the cold-path-only disk recovery, and the idempotent finalize-on-read close are all well-designed. The test suite is thorough, covering deny-shaped directives, restart recovery, tamper detection, and kill-switch behavior with specific mutation tests.

## VERDICT

**SERIOUS_FLAWS** — The critical finding (returning non-int defaults unchanged, potentially bricking the agent) and the medium finding (missing `< 1` check for object sources) represent real risks that could violate the core "NEVER-BRICK" guarantee. The TOCTOU issue in `_budget_from_disk` is also concerning for the integrity guarantee. These need to be addressed before the consumer can be considered safe for production use.
