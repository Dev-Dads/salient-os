# Red-team (H3, pass=coding): x-ai/grok-4.5

_finish=stop seconds=648.8 usage={'prompt_tokens': 30326, 'completion_tokens': 35818, 'total_tokens': 66144, 'cost': 0.2753424, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.2753424, 'upstream_inference_prompt_cost': 0.0604344, 'upstream_inference_completions_cost': 0.214908}, 'completion_tokens_details': {'reasoning_tokens': 34369, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / Capture RHS not mutation-pinned by the cited source-text test / LOW / `tests/hermes_cli/test_salience_consumer.py` · `test_call_site_precedes_budget_rebuild` + `agent/turn_context.py` (consumer call site)

**Concrete trigger (sabotage):** Change only the capture RHS:
```python
if getattr(agent, "_salience_operator_iterations", None) is None:
    agent._salience_operator_iterations = 25  # was agent.max_iterations
```
CONTEXT’s pin list is: exactly one assignment, `is-None` guard, ban of `default=agent.max_iterations`. It does **not** require the RHS to be `agent.max_iterations`.

**Why it matters:** With a real operator budget of 500 (e.g. `max_turns: 500`) and `compute_headroom: 15`, production floors every window at 25 and ceilings at 40. Quiet turns apply 25 — **below the true operator floor** (G1/G3). Headroom tests all pass their own `default=` into `bounded_iterations` and never execute this line; the source-text test stays green.

**Suggested fix:** Assert the capture line contains `= agent.max_iterations` (and not a literal). Optionally add one integration assert that a turn_context-shaped call site with `agent.max_iterations = 500` finalizes at floor 500.

---

### F2 / Attention `publish` catches `Exception` only; `SystemExit` fails the whole finalize / LOW / `hermes_cli/observability/salience_observer.py` · `_close_locked`

**Concrete trigger:** `SalienceBus.publish` (or `_bus_for`) raises `SystemExit` during synthesized ATTENTION publish (hostile/odd host I/O helper).

**Why it matters:** Mapped-signal publish and attention publish both use `except Exception`. For attention, `SystemExit` bypasses the soft-drop path, hits the outer `except (Exception, SystemExit)`, pops `_LAST_DIRECTIVE`, and skips `emit` — fail-open to default (no brick), but **stricter** than the documented “failed publish drops the signal; directive falls to the floor” path (which still emits a floor directive). Audit loses the whole turn decision, not just ATTENTION. Contained by `bounded_iterations`, so G2 holds; G5’s “drop signal, still close” story is incomplete for `SystemExit`.

**Suggested fix:** Mirror the outer policy on the attention publish arm:
```python
except (Exception, SystemExit):
    logger.warning(...)
```
so attention soft-drops consistently and the floor directive still emits.

---

### F3 / Resume caveat understates ceiling miss when headroom shrinks / LOW (honesty) / `cli-config.yaml.example` (salience block) vs G1

**Concrete trigger:**
1. Process A: `compute_headroom: 15`, floor 25, busy turn → durable `compute_budget: 40`.
2. Process B: `compute_headroom: 0` (or lower), same session file, cold start.
3. First `bounded_iterations(session, 25)` → `_budget_from_disk` → **40**.

**Why it matters:** G1’s only cross-restart exception is the resume caveat. Config text says the replayed value may sit “above OR below the **current operator setting**,” not that it may sit **above the current `floor + headroom` ceiling** (here 25). Behavior matches “prior process’s window,” but the operator-facing caveat undersells ceiling violation after a headroom reduction (still one turn, then decay).

**Suggested fix:** One clause: replayed budget may exceed the *current* `floor + compute_headroom` until the next finalize if headroom/floor changed.

---

### F4 / `or agent.max_iterations` fallback would defeat the no-ratchet pin without redding the source test / LOW (test honesty) / `test_call_site_precedes_budget_rebuild`

**Concrete trigger (sabotage):**
```python
agent.max_iterations = _salience_observer.bounded_iterations(
    agent.session_id or "",
    default=agent._salience_operator_iterations or agent.max_iterations,
)
```
Positive pin (`default=agent._salience_operator_iterations`) holds; negative pin (`default=agent.max_iterations` not in block) holds because `default=` does not prefix the second term.

**Why it matters:** If the baseline is ever `0`/`None` (bad host value or partial init), the `or` re-anchors to live `agent.max_iterations` and reintroduces compounding once headroom > 0. Unlikely with a healthy positive baseline; still a mutation-blind gap on the load-bearing pin.

**Suggested fix:** Ban `agent.max_iterations` anywhere in the default expression (e.g. regex on the `bounded_iterations` call), not only the `default=agent.max_iterations` substring.

---

*Not reported (already fixed / out of scope / no concrete trigger):* pristine-baseline ratchet (a), misattributed events (b), headroom 1e6 cap (c), box ferry parse (d), quiet-turn empty signals (e), doc resume/within-process wording already present in module docstring (f), produce-path vs live floor (g), uncapped headroom below 1e6, coarse activity proxy, ADR 0001/0002, vendored interpret/bus internals, KeyboardInterrupt non-catch.

---

## STEELMAN

The ratchet fix is the right shape: one pristine baseline, consumer default always that baseline, window always `[operator, operator + H]`, with a source-text pin plus a 25→40→40→25 durable-record test. Audit fencing for synthesized ATTENTION (publish-before-interpret, drop on publish failure, ref-shaped `("attention","events:N")`, confidence 1.0) is consistent and tested, including inert-vs-raise distinguishability for bad headroom. Finalize-on-read flooring at the caller default, shared `_issue_template` for probe and producer, and fail-inert headroom parsing are coherent with G2–G5.

## VERDICT

**MINOR_ISSUES** — No concrete in-process escalation above `operator + headroom`, no fail-open brick in the changed paths, and A3/A4/audit behavior match the guarantees under normal turn ordering; remaining gaps are low-severity test-pin holes and honesty/edge asymmetries, not a broken governor.
