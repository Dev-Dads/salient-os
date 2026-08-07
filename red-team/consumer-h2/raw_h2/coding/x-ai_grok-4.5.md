# Red-team (consumer, pass=coding): x-ai/grok-4.5

_finish=stop seconds=370.5 usage={'prompt_tokens': 27539, 'completion_tokens': 21002, 'total_tokens': 48541, 'cost': 0.1808724, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1808724, 'upstream_inference_prompt_cost': 0.0548604, 'upstream_inference_completions_cost': 0.126012}, 'completion_tokens_details': {'reasoning_tokens': 19153, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / A3 production ordering (consumer vs `pre_llm_call`) is not pinned / TEST HONESTY
- **SEVERITY:** MEDIUM
- **LOCATION:** `tests/hermes_cli/test_salience_consumer.py` (`test_call_site_precedes_budget_rebuild`, `test_three_turns_read_prior_not_stale`); production order claimed in `agent/turn_context.py` `:491` vs `:1054`
- **CONCRETE TRIGGER (mutation):** Move the `bounded_iterations(...)` assignment in `build_turn_context` to *after* the `pre_llm_call` dispatch (or any hook that calls `_open_window` for the just-minted `turn_id`).
  1. `pre_llm_call` opens turn **N** (rollover closes **N-1** into `_LAST_DIRECTIVE` with produce-side `_operator_budget()`, not the consumer floor).
  2. Consumer runs, sees turn **N**’s window still open, finalize-on-read closes **N** (empty / partial signals), caches **N**’s own directive.
  3. Turn **N** applies turn **N**’s directive (A3 broken: self-read, or N-2 if you only delete finalize-on-read and rely on rollover timing).
- **WHY IT MATTERS:** A3 is load-bearing and is guaranteed only by call-site order relative to window-open. `test_call_site_precedes_budget_rebuild` only checks adjacency to `IterationBudget(...)` (≤12 lines, no `max_iterations` reassignment). `test_three_turns_*` hard-codes the correct cadence inside the test harness and never reads `turn_context.py` for `pre_llm_call`. **All consumer tests stay GREEN** under the reorder; production silently violates “turn N applies turn N-1”.
- **SUGGESTED FIX (minimal):** Extend the structural test to assert `bounded_iterations` / `agent.max_iterations =` appears *before* the `pre_llm_call` observe/dispatch site in `turn_context.py` (same style as the IterationBudget adjacency check).

### F2 / `test_restart_recovers_budget_from_disk` is mutation-blind to “ignore disk, use operator budget”
- **SEVERITY:** LOW (partially mitigated)
- **LOCATION:** `tests/hermes_cli/test_salience_consumer.py::test_restart_recovers_budget_from_disk` vs `_budget_from_disk`
- **CONCRETE TRIGGER (mutation):** In `_budget_from_disk`, replace the verified tail read with `return _operator_budget()` (or any path that returns the configured iteration budget without touching the JSONL).
- **WHY IT MATTERS:** Fixture sets `agent.max_iterations: 7` and the persisted directive is also 7 (produce floor). Assert `== 7` stays **GREEN** even though restart integrity (grok-F8) was sabotaged. `test_cold_recovery_reads_newest_directive` (budgets 5 then 9) and the corrupt-tail tests *do* cover real disk/verify behaviour — this one test alone does not.
- **SUGGESTED FIX:** Record/persist a budget that cannot equal config/default (e.g. emit 9 with config 7 / default 10), then assert recovery == 9.

### F3 / Non-positive `default` + open window does not fail open to `default`
- **SEVERITY:** LOW (out-of-contract input, but contradicts the absolute “never change a bad default” reading of fail-open)
- **LOCATION:** `salience_observer.bounded_iterations` → `_resolve_bounded` floor selection → `_close_locked`
- **CONCRETE TRIGGER:**
  1. `_open("s","u1")` (window open, not closed).
  2. `bounded_iterations("s", 0)` (or `-1`).
  3. `default > 0` is false ⇒ `floor = _operator_budget()` (e.g. 25).
  4. Finalize-on-read emits/caches directive with `compute_budget == 25`.
  5. Return value is **25**, not `0`.
- **WHY IT MATTERS:** Docstring admits non-positive `default` is out of contract, and A5 still prevents returning `< 1` from a *directive*. But the consumer actively *replaces* a non-positive host budget with a manufactured operator floor whenever a window is open — not “return `default` unchanged on everything”. Unlikely in production if `max_iterations` is always positive.
- **SUGGESTED FIX:** If `default` is not a positive plain int, return `default` immediately (same early-exit as the non-int branch), and do not finalize-on-read.

### F4 / “Behavior-preserving in v0” understates cold-recovery after operator budget change
- **SEVERITY:** LOW (honesty)
- **LOCATION:** Module docstring / `cli-config.yaml.example` salience block; behaviour in `_budget_from_disk` + `bounded_iterations`
- **CONCRETE TRIGGER:**
  1. Session `s` runs with `max_iterations=7`, directive `7` persisted, process clears in-memory state.
  2. Operator changes host budget to `100`.
  3. `bounded_iterations("s", 100)` → **7** (verified tail), not 100.
- **WHY IT MATTERS:** Live steady-state v0 *is* preserving (pinned window + unmapped ATTENTION + floor=`default`). Restart fallback is intentionally *not* — and is tested that way — but the operator-facing text says consumption “echoes the operator’s own budget” / is “behavior-preserving” without the resume/config-change caveat. Can surprise operators who bump iterations and resume a session.
- **SUGGESTED FIX:** One sentence in the config comment + module docstring: cold resume reapplies the last *recorded* budget, which may differ from the current operator setting until a new turn is finalized.

---

### Already hunted; no surviving finding above LOW
- **Fail-open / never-brick:** `except (Exception, SystemExit)` on the consumer and on `_close_locked` / gates; `_directive_budget` rejects hard-deny, non-int, bool, sub-1; no path returns a directive budget `< 1`. KeyboardInterrupt intentionally not caught (as documented). No `BaseException` escape from the listed host APIs beyond that.
- **Re-clamp / Finding D:** return is verbatim `budget` or `default`; tests for above/below default are meaningful.
- **Restart integrity:** cold path only when `session_id not in _BUSES`; value taken from post-replay `bus._directives` (getattr fail-open); deepcopy + promote; warm+empty-cache skips disk; corrupt/non-tail tamper covered.
- **Concurrency:** single non-reentrant `_LOCK` acquired once in `_resolve_bounded`; `_close_locked` / `_bus_for` / `_budget_from_disk` / `_operator_budget` / `_ensure_template_valid` do **not** re-enter `_LOCK` — no deadlock on the finalize-on-read path. `_LAST_DIRECTIVE` popped in `_close_session` with `_BUSES`.
- **Deny-shaped:** withhold markers consumed; no re-derive.

---

## STEELMAN
The consumer is carefully shaped as a fail-open, kill-switched reader: deny/malformed budgets become `default`, finalize-on-read is idempotent under one lock, and cold resume goes through `SalienceBus` replay rather than a second parse. Prior review items (warm-bus guard, failed-close pop, deepcopy promote, no re-clamp, verbatim apply) are implemented coherently and backed by several mutation-aware tests. For a v0 behaviour-preserving wire-up, the production logic is tight.

## VERDICT
**MINOR_ISSUES** — Production fail-open / A3 / integrity logic looks sound; the main gap is **test/honesty**: A3’s dependence on “consumer before `pre_llm_call`” is not structurally enforced, so a call-site reorder would break the guarantee while the suite stays green.
