# Red-team (H3, pass=general): x-ai/grok-4.5

_finish=stop seconds=368.1 usage={'prompt_tokens': 29913, 'completion_tokens': 19397, 'total_tokens': 49310, 'cost': 0.1759904, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1759904, 'upstream_inference_prompt_cost': 0.0596084, 'upstream_inference_completions_cost': 0.116382}, 'completion_tokens_details': {'reasoning_tokens': 17988, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / Once-only pristine baseline is mutation-blind (G1 test honesty)
**Severity:** MEDIUM  
**Location:** `tests/hermes_cli/test_salience_consumer.py` :: `test_call_site_precedes_budget_rebuild`; gap vs `agent/turn_context.py` (consumer call site) and `tests/hermes_cli/test_salience_headroom.py` :: `test_no_ratchet_across_busy_turns`

**Concrete trigger (sabotage stays green):**  
Any of:
1. Remove the once-only guard:
```python
agent._salience_operator_iterations = agent.max_iterations  # every turn
agent.max_iterations = _salience_observer.bounded_iterations(
    ..., default=agent._salience_operator_iterations)
```
2. Keep the guard + default line, but re-anchor **after** the `IterationBudget` rebuild:
```python
agent.iteration_budget = IterationBudget(agent.max_iterations)
agent._salience_operator_iterations = agent.max_iterations  # outside pin window
```

Cadence with `compute_headroom=15`, operator 25, saturated busy turns: **25 → 40 → 55 → 70 → …** (no decay).

**Why the suite stays green:**
- Source pin only requires `default=agent._salience_operator_iterations` and bans `default=agent.max_iterations` inside `lines[call-6:rebuild]`. It does **not** require `is None`, and does **not** scan past `IterationBudget(...)`.
- `test_no_ratchet_across_busy_turns` only drives `bounded_iterations(session, floor=25)` with a constant default — it never executes `turn_context.py`.

**Why it matters:** G1’s load-bearing anti-ratchet control is exactly this once-per-agent pristine capture. The PR’s own narrative treats call-site shape as what stops `25→40→55`. That control can be deleted or inverted while every named pin stays green.

**Suggested fix (minimal):** Extend the source-text pin to assert the once-only guard (`is None` / equivalent) **and** that `_salience_operator_iterations` is not assigned again anywhere later in the function; and/or add a thin integration test that instantiates the real call-site block twice across a busy→busy cadence and expects `25→40→40`.

---

### F2 / Produce-path floor can diverge from the consumer’s pristine baseline
**Severity:** LOW  
**Location:** `hermes_cli/observability/salience_observer.py` :: `_close_locked` (budget is `None`), `_operator_budget`; contrast `agent/turn_context.py` (`default=agent._salience_operator_iterations`)

**Concrete trigger:**
1. Process where `agent.max_iterations` / `_salience_operator_iterations` is **500** (e.g. `HERMES_MAX_ITERATIONS` or constructor), but config keys read by `_operator_budget` resolve to **25** (fallback) or a lower `max_turns`.
2. Run one busy turn (≥8 attributed events), `headroom=15`.
3. End session **without** a following turn start (so finalize-on-read never closes the last window).
4. `_close_session` → `_close_locked(window)` with `budget=None` → floor **`_operator_budget()` = 25** → durable directive **40**, not anything in `[500, 515]`.
5. Resume: first consumer apply uses **40** (documented resume carry), i.e. last close used a **different floor source** than every in-process finalize-on-read close.

Symmetric skew the other way (config floor ≫ agent pristine) plus `consume_compute: false` during rollover close, then re-enable consume, can record/apply a budget above `agent_pristine + headroom` without resume — only when those two floors disagree.

**Why it matters:** In-process G1 is preserved on the normal consume-on path (finalize-on-read always wins first). The durable last-turn record and resume path silently use a second floor definition the PR explicitly refused to align (`HERMES_MAX_ITERATIONS` comment). Easy to misread as “operator floor” in audit/resume.

**Suggested fix (minimal):** Thread the same pristine baseline into produce-only closes (session-end/rollover) when known, or document explicitly that produce-path floor is **config-derived only** and may disagree with `agent.max_iterations` under env/constructor override; optional: pass `default` into session-end if the host still has it.

---

### F3 / Box ferry headroom shape ≠ observer contract (digit-strings)
**Severity:** LOW  
**Location:** `box/server.py` :: `_write_hermes_config` vs `salience_observer._compute_headroom`

**Concrete trigger:** Box `settings.yaml` with `compute_headroom: "15"` (digit-string). Ferry writes `0` (pinned). Same value in Hermes config is honored as 15 by the observer.

**Why it matters:** Docs promise digit-string honesty for the knob; the box path silently fail-inerts strings. Surprising “knob does nothing” in the demo rig only.

**Suggested fix (minimal):** Mirror observer parsing (digit-strings honored, bool/float/negative → 0, cap at 1e6) in the ferry.

---

## Steelman

The producer-side design is tight where it counts: one shared `_issue_template`, attention published before interpret with drop-on-publish-failure, events incremented only after the turn-id guard, finalize-on-read flooring on the caller default, and the call site’s pristine baseline correctly breaking the `max_iterations` feedback loop in the shipped tree. Headroom parsing, saturation, quiet-turn empty signal record, and half-up expectations are pinned with real sabotage targets. Default `compute_headroom: 0` really does keep H2 behavior.

## Verdict

**MINOR_ISSUES** — No concrete in-tree runaway/fail-open/A3/A4 production break survived scrutiny; the main miss is that G1’s anti-ratchet call-site guard is under-pinned and can be reintroduced while the suite stays green, plus small floor-source / ferry inconsistencies at the edges.
