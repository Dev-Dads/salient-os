# Red-team (H3, pass=general): z-ai/glm-5.2

_finish=length seconds=879.5 usage={'prompt_tokens': 27481, 'completion_tokens': 16000, 'total_tokens': 43481, 'cost': 0.1088734, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1088734, 'upstream_inference_prompt_cost': 0.0384734, 'upstream_inference_completions_cost': 0.0704}, 'completion_tokens_details': {'reasoning_tokens': 16000, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me analyze this PR carefully against the stated guarantees.

Let me trace through the key paths:

**G1 - Bounded Escalation / No Ratchet:**

The call site in `turn_context.py`:
```python
if getattr(agent, "_salience_operator_iterations", None) is None:
    agent._salience_operator_iterations = agent.max_iterations
agent.max_iterations = _salience_observer.bounded_iterations(
    agent.session_id or "", default=agent._salience_operator_iterations
)
```

This captures the pristine budget once per agent. The default passed is always the pristine value. Good.

But wait — `_operator_budget()` is called in `_close_locked` when `budget is None` (the produce-path close: rollover and session end). It reads from config, cached once per process. This is the FLOOR for the produce-path close. The consumer's finalize-on-read close passes `floor = default` (the caller's resolved budget). So:

- Rollover close (in `_open_window`): `_close_locked(current)` with `budget=None` → floors at `_operator_budget()` (from config)
- Session end close: `_close_locked(window)` with `budget=None` → floors at `_operator_budget()`
- Finalize-on-read close (in `_resolve_bounded`): `_close_locked(window, budget=floor)` where `floor = default` (the pristine operator budget from the call site)

So the produce-path rollover close uses `_operator_budget()` from config, while the finalize-on-read close uses the caller's `default`. These could differ if the config's `agent.max_iterations` differs from what the host actually resolved. But the call site captures `agent.max_iterations` which was set from config originally, so they should match in normal operation.

Wait, but there's a subtlety. The `_operator_budget()` reads `agent.max_iterations` from config first, then `max_iterations`, then `agent.iteration_budget`, then `agent.max_turns`, then `max_turns`. The call site captures `agent.max_iterations` which is the host's resolved value. If the host resolved `max_iterations` from `max_turns` (as cli.py does), then `agent.max_iterations` might be set to the `max_turns` value, while `_operator_budget()` would find `agent.max_iterations` in config first... 

Actually, let me re-read. The config is the raw config file. `agent.max_iterations` in the config file is what `_operator_budget()` reads. The host's `agent.max_iterations` attribute is set by the host's own resolution chain (cli.py reads `max_turns` and feeds it as `max_iterations`). So if the config has `agent.max_turns: 500` but no `agent.max_iterations`, then:
- `_operator_budget()` would skip `agent.max_iterations` (not in config), skip `max_iterations`, skip `agent.iteration_budget`, find `agent.max_turns: 500` → budget = 500
- The host's `agent.max_iterations` attribute = 500 (set by cli from max_turns)
- The call site captures `agent._salience_operator_iterations = 500`
- The consumer passes `default=500`
- The finalize-on-read close floors at 500

These match. Good.

But what if the config has BOTH `agent.max_iterations: 25` AND `agent.max_turns: 500`? Then:
- `_operator_budget()` finds `agent.max_iterations: 25` first → budget = 25
- The host's cli.py resolves `max_turns: 500` → `agent.max_iterations = 500`
- The call site captures `agent._salience_operator_iterations = 500`
- The consumer passes `default=500`
- The finalize-on-read close floors at 500
- But the ROLLOVER close (in `_open_window`) floors at `_operator_budget() = 25`!

This is a MISMATCH. The rollover close would floor at 25 while the finalize-on-read close floors at 500. The rollover close's directive would be recorded with floor 25, and if the next turn's finalize-on-read doesn't fire (because the window was already closed by rollover), the consumer would read the rollover-close's directive which was floored at 25.

Wait, let me trace more carefully. The sequence:

1. Turn N-1: `pre_llm_call` opens window for turn N-1
2. Turn N-1: events happen
3. Turn N: `bounded_iterations` is called → `_resolve_bounded` → finalize-on-read closes turn N-1's window with `floor = default` (the caller's budget). Then reads the directive.
4. Turn N: `pre_llm_call` opens window for turn N

But what if `bounded_iterations` is NOT called (e.g., consumption disabled)? Then:
3. Turn N: `pre_llm_call` → `_open_window` → sees turn N-1's window still open → `_close_locked(current)` with `budget=None` → floors at `_operator_budget()`

In this case, the rollover close uses `_operator_budget()` from config, which might differ from the host's actual `agent.max_iterations`. But if consumption is disabled, the directive isn't consumed anyway, so this doesn't matter for G1.

Actually wait — consumption could be disabled but production still on. The directive is still recorded with the wrong floor. Then if consumption is later enabled (next process, config change), the resume path reads from disk and gets a directive floored at the wrong value. But that's a config-change-between-processes scenario, which is the documented resume caveat territory.

Let me focus on the within-process case. Within a process, if consumption is on, the finalize-on-read always fires before the rollover. So the rollover close only fires for windows that were already closed by finalize-on-read (idempotent) or when consumption is off. This seems fine.

Actually, let me re-check. Is there a case where the rollover close fires for a window that hasn't been finalize-on-read closed? 

The sequence per turn:
1. `bounded_iterations` (consumer) → finalize-on-read closes prior window
2. `pre_llm_call` → opens new window (may rollover-close prior window if still open)

If `bounded_iterations` is called every turn (which it is, per the call site), then step 1 always closes the prior window before step 2. The rollover in step 2 would find the window already closed (idempotent). So within a process with consumption on, the rollover close is always a no-op. Good.

But what about the VERY FIRST turn? There's no prior window to close. And what about a turn where `bounded_iterations` returns `default` without calling `_resolve_bounded` (e.g., consumption disabled)? Then the rollover close in step 2 would close the prior window with `_operator_budget()`. But if consumption is disabled, the directive isn't consumed. Fine.

OK, so the floor mismatch between `_operator_budget()` and the caller's `default` is only relevant when consumption is off, which means it doesn't affect the applied budget. Not a real issue for G1.

**G3 - A4 Floor Integrity:**

Quiet turn (zero events): `_close_locked` with `window.events == 0` → no ATTENTION signal → `signals` is just the mapped signals (which could be non-empty if there were mapped events... wait, no. If `window.events == 0`, there were NO attributed events at all, which means no `_record` calls succeeded, which means `window.signals` is empty. So `signals = list(window.signals)` is empty. Then `interpret(policy, (), _POLICY_KEY)` → budget = `_scale(0.0, floor, floor+H)` = `floor + round_half_up(0.0 * H)` = `floor + 0` = `floor`. Good, exactly the floor.

But wait — what if there were events that incremented `window.events` but whose mapper returned `[]` (read-only tools)? Then `window.events > 0` but `window.signals` could be empty (if all events were unmapped). In that case, the ATTENTION signal IS synthesized (events > 0), and the budget moves. That's correct per the design — unmapped events count toward ATTENTION.

**G4 - A3 with Movement:**

The finalize-on-read in `_resolve_bounded` closes the prior turn's window and reads its directive. The key question: does it read the RIGHT directive?

```python
window = _WINDOWS.get(session_id)
if window is not None and not window.closed:
    _close_locked(window, budget=floor)
directive = _LAST_DIRECTIVE.get(session_id)
if directive is not None:
    return _directive_budget(directive)
return _budget_from_disk(session_id)
```

After `_close_locked` closes the prior window, `_LAST_DIRECTIVE[session_id]` is set to the just-emitted directive. Then it reads that same directive. So turn N applies turn N-1's directive. Correct.

But what about the self-read concern? The call site runs `bounded_iterations` BEFORE `pre_llm_call` opens turn N's window. So at the time of the call, `_WINDOWS[session_id]` is turn N-1's window (still open). The finalize-on-read closes it. Then `pre_llm_call` opens turn N's window. So no self-read. The test `test_call_site_precedes_budget_rebuild` pins this ordering. Good.

**G5 - Audit Honesty / Fence:**

The ATTENTION signal is published to the bus BEFORE being appended to `signals` for interpretation. If publish fails, the signal is NOT appended to `signals`, so it's not interpreted. The directive falls to the floor. The signal is on the bus record if and only if it informed the directive. Good.

But wait — there's a subtle issue. The mapped signals (from `_record`) are published to the bus AND appended to `window.signals`. If a mapped signal's publish fails, it's NOT appended to `window.signals` (the `except` block just logs). But `window.events` is still incremented (before the publish loop). So the events count includes events whose signal publish failed. This means the ATTENTION influence could be based on events that didn't make it to the bus. But ATTENTION itself is published separately at close, so this is fine — the ATTENTION signal's provenance says `events:<n>` where n is the attributed count, and the ATTENTION signal is published at close. The mapped signals that failed to publish are just lost (not on the bus, not interpreted). This seems correct.

Actually, let me re-read `_record`:
```python
window.events += 1  # every ATTRIBUTED event counts toward ATTENTION (PR-H3)
for signal in mapper(kwargs, window.subject):
    try:
        self_bus = _bus_for(session_id)
        self_bus.publish(signal)
        window.signals.append(signal)
    except Exception:
        logger.warning(...)
```

So `window.events` is incremented BEFORE the publish loop. If the bus is down, events still count. The ATTENTION signal at close will reflect the full event count, but the individual mapped signals won't be on the bus. The ATTENTION signal IS published at close (separate publish). If the bus is still down at close, the ATTENTION publish also fails, and the directive falls to the floor. So the audit record would have NO signals and a floor directive. That's honest — the record shows nothing moved because nothing could be recorded.

But what if the bus is up at close but was down during `_record`? Then the mapped signals failed to publish (not on the bus, not in `window.signals`), but `window.events` was still incremented. At close, the ATTENTION signal is published (bus is now up), and it's interpreted. The directive moves. The audit record has the ATTENTION signal but NOT the mapped signals that drove the event count. Is this a fence violation? The ATTENTION signal's provenance says `events:4`, but the bus record doesn't show the 4 individual events that were counted. 

Hmm, but the ATTENTION signal is the synthesized signal — it's not claiming to represent the individual signals, it's representing the COUNT of attributed activity. The count is a fact (4 events happened), and the ATTENTION signal is on the record. The individual mapped signals that failed to publish are just lost — but they're not what moves the budget; the ATTENTION signal is. So this seems honest: the signal that moved the budget (ATTENTION) IS on the record.

Actually, I think this is fine. The mapped signals (VERIFICATION, RISK, MEMORY) don't move the compute budget — only ATTENTION does. And ATTENTION is synthesized at close. So even if the mapped signals are lost, the budget-moving signal is on the record.

**Now let me look for the harder issues...**

**Feedback loop through `_operator_budget()`:**

`_operator_budget()` reads from config, cached once per process. It does NOT read `agent.max_iterations` (the runtime attribute). It reads the CONFIG file's `agent.max_iterations`. The call site captures `agent.max_iterations` (runtime) as the pristine baseline. These are different sources. Could the config's `agent.max_iterations` be polluted by a previously applied value?

No — the config file is not mutated by the runtime. The runtime assigns `agent.max_iterations` (an attribute on the agent object), not the config file. So `_operator_budget()` reads the pristine config value. Good.

But wait — the box ferry (`_write_hermes_config` in `server.py`) writes:
```python
cfg = {
    "salience": {...},
    "agent": {"max_iterations": MAX_ITERS},
}
```

Where does `MAX_ITERS` come from? It's a constant in `server.py`. Let me check... it's not shown in the material, but it's likely from `box/settings.yaml`'s `agent.max_iterations: 25`. The ferry writes this to the config file once. So `_operator_budget()` would read 25 from config. And the host's `agent.max_iterations` would also be 25 (from the config the ferry wrote). These match. Good.

**The `_budget_from_disk` promotion:**

```python
_LAST_DIRECTIVE[session_id] = payload
return _directive_budget(payload)
```

This promotes a disk-recovered directive into the in-memory cache. The directive was recorded in a PRIOR process with a potentially different floor. The resume caveat documents this. But let me check: could this promoted directive's budget exceed the CURRENT process's operator + headroom?

The directive's `compute_budget` was clamped to `[floor_prior, floor_prior + headroom_prior]` in the prior process. In the current process, the operator budget and headroom might be different. The consumer applies the budget VERBATIM (no re-clamp, G6). So if the prior process had floor=25, headroom=15, and recorded budget=40, and the current process has floor=10, headroom=0, the consumer would apply 40 — which exceeds the current floor + headroom = 10.

But this is the documented resume caveat: "the first turn of a resumed session reapplies the LAST RECORDED budget for that session — with headroom on, simply ending on a busy turn is enough." And G1 says "The ONLY cross-restart carry is the documented resume caveat." So this is in scope and documented. Not a finding.

But wait — G1 says "Within a process the applied budget never exceeds operator + headroom." The resume caveat is explicitly carved out. So this is fine.

**Arithmetic edge cases:**

`influence = min(1.0, window.events / _ATTENTION_SATURATION_EVENTS)`

`_ATTENTION_SATURATION_EVENTS = 8`. `window.events` is an int >= 0. So `events / 8` is a float. `min(1.0, ...)` caps it. Then `_scale(frac, floor, floor+H)` = `floor + _round_half_up(frac * H)` = `floor + int(frac * H + 0.5)`.

For `events = 0`: frac = 0.0, budget = floor + int(0.0 + 0.5) = floor + 0 = floor. ✓
For `events = 8`: frac = 1.0, budget = floor + int(H + 0.5) = floor + H (for integer H). ✓
For `events = 4, H = 5`: frac = 0.5, budget = floor + int(2.5 + 0.5) = floor + 3. ✓ (matches test)
For `events = 4, H = 6`: frac = 0.5, budget = floor + int(3.0 + 0.5) = floor + 3. But the test `test_budget_saturates_at_floor_plus_headroom` uses 50 events and H=6, expecting 16 = 10 + 6. That's frac=1.0, budget = 10 + int(6.0 + 0.5) = 10 + 6 = 16. ✓

What about `events = 4, H = 1`? frac = 0.5, budget = floor + int(0.5 + 0.5) = floor + 1. That's the ceiling. So 4 events out of 8 already maxes out a headroom of 1. Seems fine.

What about floating point precision? `events / 8` for events=7: 0.875. `0.875 * 8 = 7.0`. `int(7.0 + 0.5) = 7`. Budget = floor + 7. Test expects 17 for floor=10, H=8. ✓

For events=3: 3/8 = 0.375. 0.375 * 10 = 3.75. int(3.75 + 0.5) = int(4.25) = 4. Budget = 10 + 4 = 14. The test `test_one_attention_signal_per_window_on_bus` uses 3 events with H=10 but doesn't assert the budget, just the signal. OK.

I don't see arithmetic issues.

**Now let me look for the REALLY hard stuff...**

**Double-counting / window splitting:**

Could a single turn's activity be counted twice? The `_Window` is keyed by `session_id` in `_WINDOWS`. When a new turn opens, the old window is closed. Events are only counted against the open window with matching turn_id. So no double-counting across turns.

Could the ATTENTION signal be emitted twice? `_close_locked` is idempotent (`if window.closed: return`). So even if called twice (finalize-on-read + rollover), the second call is a no-op. Good.

**The `_resolve_bounded` floor derivation:**

```python
floor = default if (isinstance(default, int) and not isinstance(default, bool)
                    and default > 0) else _operator_budget()
```

If `default` is a valid positive int, `floor = default`. Otherwise, `floor = _operator_budget()`. The call site always passes `default=agent._salience_operator_iterations`, which is the pristine budget. If the pristine budget is somehow < 1 (e.g., misconfigured), the call site would pass it, `bounded_iterations` would return it untouched (the `if not isinstance(default, int) or isinstance(default, bool) or default < 1: return default` guard), and `_resolve_bounded` would never be called. So the `else _operator_budget()` branch in `_resolve_bounded` is only reachable if someone calls `_resolve_bounded` directly with a bad default. In normal operation, `floor = default = pristine budget`. Good.

But wait — what if the pristine budget is valid but the operator config changed between process start and the first finalize? `_operator_budget()` is cached once per process. The pristine budget is captured once per agent. If the config was read at agent creation (giving `agent.max_iterations = 25`), and then the config file was edited to `agent.max_iterations = 50` before the first finalize, then:
- `agent._salience_operator_iterations = 25` (captured at agent creation)
- `_operator_budget()` = 25 (cached at first finalize, reads the EDITED config... wait, no. `_operator_budget()` reads the config file. If the config was edited to 50, `_operator_budget()` would read 50 on its first call. But the pristine budget was captured as 25.

Actually, `_operator_budget()` is called in `_close_locked` when `budget is None` (produce-path close). The finalize-on-read close passes `budget = floor = default = 25` (the pristine budget). So the finalize-on-read close floors at 25, not 50. The produce-path close (rollover, session end) would floor at `_operator_budget() = 50` (if that's what the config says now). But as we established, within a process with consumption on, the rollover close is a no-op (finalize-on-read already closed the window). So this mismatch doesn't affect the applied budget.

This is a config-edit-during-process scenario, which is documented as "a live config edit takes effect on restart." Fine.

**Test honesty — let me look for mutation-blind tests...**

`test_no_ratchet_across_busy_turns`: 
```
floor = 25
assert so.bounded_iterations("s", floor) == 25   # turn 1: nothing recorded
_open("s", "u1")
_events("s", "u1", 8)                            # busy
assert so.bounded_iterations("s", floor) == 40   # turn 2: floor + 15
_open("s", "u2")
_events("s", "u2", 8)                            # busy again
assert so.bounded_iterations("s", floor) == 40   # turn 3: STILL 40 — no 55
_open("s", "u3")                                 # quiet
assert so.bounded_iterations("s", floor) == 25   # turn 4: decays to floor
```

Turn 1: no prior window → `_resolve_bounded` finds no window, no directive → returns None → default 25. ✓
Turn 2: opens u1, 8 events. `bounded_iterations("s", 25)` → finalize-on-read closes u1 at floor 25, headroom 15, 8 events saturated → 25 + 15 = 40. Reads directive 40. Returns 40. ✓
Turn 3: opens u2, 8 events. `bounded_iterations("s", 25)` → finalize-on-read closes u2 at floor 25, 8 events → 40. Reads directive 40. Returns 40. ✓ (not 55 — no ratchet because default is always 25, not the prior 40)
Turn 4: opens u3, 0 events. `bounded_iterations("s", 25)` → finalize-on-read closes u3 at floor 25, 0 events → 25. Reads directive 25. Returns 25. ✓

This test is correct and would catch a ratchet. If the default were `agent.max_iterations` (which would be 40 after turn 2), turn 3 would floor at 40 + 15 = 55, and the test would fail. Good.

But wait — in the test, the test passes `floor` (25) as the default every time. The test is simulating the call site's behavior (passing the pristine budget every turn). But the test doesn't actually use the call site — it calls `bounded_iterations` directly. So the test is testing the consumer's behavior given the call site's contract. The call site itself is pinned by `test_call_site_precedes_budget_rebuild`. Together, they cover the ratchet. Good.

`test_three_turn_cadence_distinct_budgets`:
```
applied1 = so.bounded_iterations("s", 10)      # nothing recorded yet
_open("s", "u1")
_events("s", "u1", 8)                          # saturated
applied2 = so.bounded_iterations("s", 20)      # closes u1 at floor 20 ⇒ 28
_open("s", "u2")
_events("s", "u2", 4)                          # half
applied3 = so.bounded_iterations("s", 30)      # closes u2 at floor 30 ⇒ 34
assert (applied1, applied2, applied3) == (10, 28, 34)
```

Turn 1: no window, no directive → 10 (default). ✓
Turn 2: u1 has 8 events, saturated. Close at floor 20, headroom 8 → 20 + 8 = 28. Read 28. ✓
Turn 3: u2 has 4 events, half. Close at floor 30, headroom 8 → 30 + int(0.5 * 8 + 0.5) = 30 + int(4.5) = 30 + 4 = 34. Read 34. ✓

But note: the floor changes each turn (10, 20, 30) because the test passes different defaults. This tests that the finalize-on-read uses the caller's default as the floor, not the operator budget. Good.

But there's a subtlety: `applied2 = 28` is the budget for turn 2 (reading turn 1's directive). But turn 1 had no events (no window was even opened for turn 1 — `bounded_iterations` was called without `_open` first). So there's no directive for turn 1. `applied1 = 10` (default). Then `_open("s", "u1")` opens u1's window. `bounded_iterations("s", 20)` closes u1 at floor 20. But wait — u1 is the CURRENT turn's window, not the prior turn's. The test opens u1, records events, then calls `bounded_iterations` which closes u1 and reads its directive. This is a SELF-READ — the consumer is reading the CURRENT turn's directive, not the prior turn's.

Wait, no. Let me re-trace. The test doesn't simulate the real turn sequence. In the real host:
1. `bounded_iterations` (consumer) — closes prior window, reads prior directive
2. `pre_llm_call` — opens current window
3. events happen
4. next turn: back to step 1

In the test:
1. `bounded_iterations("s", 10)` — no window open, no directive → 10
2. `_open("s", "u1")` — opens u1
3. `_events("s", "u1", 8)` — records 8 events
4. `bounded_iterations("s", 20)` — closes u1 (finalize-on-read), reads u1's directive → 28

So step 4 is closing the CURRENT turn's window (u1) and reading its directive. In the real host, the consumer runs BEFORE the window opens, so it would close the PRIOR turn's window. The test is simulating a different ordering — it opens the window, records events, then calls the consumer. This means the consumer is reading the CURRENT turn's directive, not the prior turn's.

Is this a problem? The test is testing the consumer's mechanics (close + read), not the ordering. The ordering is pinned by `test_call_site_precedes_budget_rebuild`. But the test's expected values (28, 34) are correct for the mechanics: close u1 at floor 20 → 28, close u2 at floor 30 → 34. The test would catch a stale read (if the consumer read the PRIOR directive instead of the just-closed one, it would get 10 or None, not 28).

Actually, this IS testing a self-read scenario, and it's EXPECTED to work. In the real host, this wouldn't happen because the consumer runs before the window opens. But the test is deliberately testing the close-then-read mechanics. The test's comment says "Turn N applies turn N-1's recorded directive" but the test sequence actually has the consumer closing the CURRENT turn's window and reading it. This is a test honesty issue — the test's name and comment say "turn N applies turn N-1's" but the test actually demonstrates "turn N closes its own window and reads it."

Hmm, but is this actually a defect? The test is testing the `_resolve_bounded` mechanics, not the full turn sequence. The full turn sequence is tested by `test_e2e_headroom_moves_iteration_budget`. Let me check that test:

```python
lifecycle.invoke_hook("pre_llm_call", ...)  # opens turn-1 window
for i, ...: model_tools._emit_post_tool_call_hook(...)  # 4 events
applied = so.bounded_iterations(session_id, 25)  # closes turn-1, reads directive
assert applied == 33
```

This is also a self-read! The consumer closes the CURRENT turn's window (turn-1) and reads its directive. In the real host, the consumer would run at the START of turn 2, closing turn 1's window. But in this test, the consumer runs at the END of turn 1 (after the events). So the consumer is reading turn 1's own directive, which is what turn 2 should read.

Wait, but in the real host, the consumer runs at the START of each turn. So:
- Turn 1 start: `bounded_iterations` — no prior window, no directive → default
- Turn 1: `pre_llm_call` opens window, events happen
- Turn 2 start: `bounded_iterations` — closes turn 1's window, reads turn 1's directive → applies to turn 2

The e2e test simulates this by calling `bounded_iterations` AFTER the events (which is equivalent to turn 2's start). The window for turn 1 is still open (no rollover happened), and the consumer closes it and reads it. This is correct — it's the finalize-on-read behavior.

So the test sequence IS correct for the real turn sequence, just with the consumer call at the end of the turn instead of the start of the next turn. The mechanics are the same: the consumer closes the prior turn's window and reads its directive. The test just doesn't simulate the next turn's `pre_llm_call`.

OK, so the tests are honest. Let me keep looking...

**The `_budget_from_disk` path and the `_BUSES` check:**

```python
def _budget_from_disk(session_id: str) -> "int | None":
    if session_id in _BUSES:
        return None
    ...
    bus = _bus_for(session_id)  # constructs ⇒ replay + verify
    directives = getattr(bus, "_directives", None)
    if not directives:
        return None
    payload = copy.deepcopy(directives[-1][1])
    _LAST_DIRECTIVE[session_id] = payload
    return _directive_budget(payload)
```

After `_bus_for(session_id)`, `session_id` is now in `_BUSES`. So the `if session_id in _BUSES: return None` guard at the top means this function only runs once per restart. If it's called again, the bus is cached, and it returns None. The promoted directive in `_LAST_DIRECTIVE` would be found by the caller (`_resolve_bounded` checks `_LAST_DIRECTIVE` first). Good.

But there's a subtle issue: `_budget_from_disk` calls `_bus_for(session_id)` which adds the bus to `_BUSES`. Then it reads `bus._directives[-1][1]` — the LAST directive in the bus, regardless of subject. This is the last directive for ANY subject (any turn). In a single-session bus, all directives have the same session hash but different turn subjects. The last directive is the most recent turn's. That's correct for the resume case — you want the last recorded turn's directive.

But what if the bus has directives from multiple sessions? No — the bus is per-session (file named by session hash). So all directives are for the same session. The last one is the most recent turn. Good.

**Now let me look at the box ferry more carefully...**

```python
def _write_hermes_config() -> None:
    ...
    raw_headroom = salience.get("compute_headroom", 0)
    headroom = (raw_headroom
                if isinstance(raw_headroom, int)
                and not isinstance(raw_headroom, bool)
                and raw_headroom >= 0 else 0)
    cfg = {
        "salience": {
            ...
            "compute_headroom": headroom,
        },
        "agent": {"max_iterations": MAX_ITERS},
    }
```

The ferry sanitizes headroom but does NOT cap it at `_MAX_HEADROOM`. If the box sets `compute_headroom: 2000000` (above 1e6), the ferry passes it through (it's a valid int >= 0), and the observer's `_compute_headroom()` caps it at 1e6. So the effective headroom is 1e6, not 2e6. The box's prose says "budget moves between 25 and 40" (headroom 15), so this is just a config-value issue, not a code defect. The observer's cap handles it. Fine.

But wait — the ferry does NOT pass through digit strings. If the box's `settings.yaml` has `compute_headroom: "15"`, the ferry's `isinstance(raw_headroom, int)` check would fail (it's a string), and headroom would be 0. The observer would then read 0 from the config the ferry wrote. But the observer's own parser DOES honor digit strings. So the ferry is MORE restrictive than the observer. Is this a defect?

The box's `settings.yaml` has `compute_headroom: 15` (an int), so this is fine in practice. The ferry's restrictiveness is a defense-in-depth measure (don't launder values the observer would reject). But the observer WOULD accept `"15"` — the ferry rejects it. This is a minor inconsistency but not a defect — the ferry is more conservative, which is safe.

Actually, wait. The ferry's comment says "Mirror the observer's shape contract instead of int()-coercing: the ferry must not launder values (bool True, floats) the observer's fail-inert parser would reject into ints it would honor." But the observer's parser DOES honor digit strings, and the ferry does NOT. So the ferry is NOT exactly mirroring the observer's shape contract — it's stricter on strings. This is a minor honesty issue in the comment but not a functional defect (being stricter is safe).

Hmm, but is it actually a problem? If someone sets `compute_headroom: "15"` in the box's `settings.yaml`, the ferry writes `compute_headroom: 0` to the hermes config, and the observer reads 0. The user's intent (headroom 15) is lost. But the box's `settings.yaml` example shows `compute_headroom: 15` (unquoted int), so this is only an issue if someone quotes it. LOW at most.

**Let me look for the really hard escalation paths...**

**Path: `_operator_budget()` reads `agent.max_iterations` from config, which could be the GOVERNED value if something writes back to config.**

Does anything in this PR write to the config file? The box ferry writes once at startup. The observer and consumer never write to config. So `_operator_budget()` always reads the pristine config value. No feedback loop through config. Good.

**Path: `_salience_operator_iterations` captured from `agent.max_iterations`, which could already be governed.**

```python
if getattr(agent, "_salience_operator_iterations", None) is None:
    agent._salience_operator_iterations = agent.max_iterations
```

This runs on the FIRST turn. On the first turn, `agent.max_iterations` is the host's initial value (from config/cli resolution). It hasn't been governed yet (the consumer hasn't run yet — this IS the consumer's call site). So the pristine value is captured before any governance. Good.

But what if the agent is re-created mid-session? Or what if `agent.max_iterations` was already modified by something else before the first turn? The call site is in `turn_context.py` which runs at the start of each turn. The first time it runs, `agent.max_iterations` is the host's initial value. Unless something else modifies it before the first turn... but that would be outside this PR's scope.

**Path: The `_LAST_DIRECTIVE` cache survives across turns within a process and could be stale.**

`_LAST_DIRECTIVE[session_id]` is updated by `_close_locked` on every close. The consumer reads it in `_resolve_bounded`. If a close fails, `_LAST_DIRECTIVE` is popped (the `except` block in `_close_locked`). So a failed close doesn't leave a stale directive. Good.

But what if a close succeeds with a directive from turn N-1, and then turn N's window is opened and closed (rollover) before the consumer reads? No — the consumer runs before `pre_llm_call` (pinned by the source-text test). So the consumer reads the directive before the next window opens. Good.

**Path: The `_budget_from_disk` promotion could promote a directive from a DIFFERENT session.**

No — the bus is per-session (file named by session hash). `_budget_from_disk` reads from the session-specific bus. Good.

**Let me look at the `_close_locked` exception handling more carefully...**

```python
try:
    if budget is None:
        budget = _operator_budget()
    signals = list(window.signals)
    if window.events > 0:
        attention = _signal(...)
        try:
            _bus_for(window.session_id).publish(attention)
            signals.append(attention)
        except Exception:
            logger.warning(...)
    policy = _issue_template(window.subject, budget)
    directive = interpret(policy, tuple(signals), _POLICY_KEY)
    _bus_for(window.session_id).emit(directive)
    _LAST_DIRECTIVE[window.session_id] = directive
except (Exception, SystemExit):
    _LAST_DIRECTIVE.pop(window.session_id, None)
    logger.warning(...)
```

If `_bus_for(window.session_id).emit(directive)` fails, the directive was computed but not recorded. The `except` block pops `_LAST_DIRECTIVE`. But the directive was already computed by `interpret` — it's just not on the bus. The consumer would fall back to default. Good — fail open.

But what if `emit` partially succeeds (writes to the file but raises)? The bus's `_append` method writes to the file and then updates the in-memory state. If the file write fails, the in-memory state is not updated (the write is before the append). Actually, looking at the bus's `_append`:

```python
def _append(self, entry, stored):
    entry_hash = digest(entry)
    entry = {**entry, "hash": entry_hash}
    (self._signals if kind == "signal" else self._directives).append((entry_hash, obj))
    self._entries.append(entry)
    self._head = entry_hash
    if self._path is not None:
        with open(self._path, "a", ...) as fh:
            fh.write(...)
    return entry_hash
```

The in-memory state is updated BEFORE the file write. So if the file write fails, the in-memory state has the entry but the file doesn't. On a later reopen, the file wouldn't have this entry. But within the process, the in-memory state is consistent. The `_LAST_DIRECTIVE` would be set (the emit returned normally from the in-memory perspective... wait, no. If the file write raises, the exception propagates out of `_append`, out of `emit`, and is caught by the `except (Exception, SystemExit)` in `_close_locked`. So `_LAST_DIRECTIVE` is popped. But the bus's in-memory state already has the entry. This is a minor inconsistency — the bus thinks it has the directive, but `_LAST_DIRECTIVE` doesn't. On the next read, `_LAST_DIRECTIVE` is empty, so the consumer tries `_budget_from_disk`, which finds the bus is cached (`session_id in _BUSES` → True) and returns None. So the consumer falls back to default. The bus has a phantom entry that's not consumed. This is safe (fail open) but slightly inconsistent. Not a real issue.

Actually wait — the `_append` method updates in-memory state BEFORE writing to disk. If the disk write fails, the in-memory state is AHEAD of the disk. On a later reopen (new process), the disk wouldn't have the last entry. But within the process, the bus's `verify_chain()` would still pass (the in-memory chain is consistent). This is a vendored-internals issue (out of scope).

**Let me look at one more thing: the `test_finalize_on_read_floor_is_caller_default` test.**

```python
_use_config(monkeypatch, tmp_path,
            _cfg(headroom=10, agent={"max_iterations": 25}))
_open("s", "u1")
_events("s", "u1", 8)
assert so.bounded_iterations("s", 12) == 22
```

Config has `agent.max_iterations: 25`. The caller passes `default=12`. The finalize-on-read should floor at 12 (the caller's default), not 25 (the config). 8 events saturate → 12 + 10 = 22. The test asserts 22. If the floor were derived from `_operator_budget()` (25), the result would be 25 + 10 = 35. So the test catches the wrong-floor mutation. Good.

But wait — `_operator_budget()` reads `agent.max_iterations` from config (25). The test passes `default=12`. The `_resolve_bounded` code:
```python
floor = default if (isinstance(default, int) and not isinstance(default, bool)
                    and default > 0) else _operator_budget()
```
`default=12` is a valid positive int, so `floor=12`. The close uses floor 12. The directive is 22. The consumer reads 22. ✓

If someone changed the code to `floor = _operator_budget()`, the floor would be 25, the directive would be 35, and the test would fail (22 ≠ 35). Good — the test is mutation-sensitive.

**Now let me look for something the internal reviews might have missed...**

**The `_compute_headroom` memoization and the `_issue_template` call:**

`_issue_template` calls `_compute_headroom()` every time it's called. `_compute_headroom()` is memoized. But `_issue_template` is called from `_close_locked` (every close) and `_ensure_template_valid` (once). The memoization means the headroom is resolved once per process. If the config changes mid-process, the headroom doesn't change. This is documented ("a live config edit takes effect on restart"). Fine.

**The `_operator_budget()` cache and the `_compute_headroom()` cache are both cleared by `_reset_for_tests`.** Good — no cross-test contamination.

**Let me look at the `test_headroom_parsing_table` parametrize for `10**400`:**

```python
(10**400, 10**6),             # capped: an absurd int must not overflow the float scaling
```

`10**400` is a Python int (arbitrary precision). `_compute_headroom()` checks `isinstance(value, int)` → True, `value >= 0` → True, so `headroom = 10**400`. Then `headroom = min(headroom, _MAX_HEADROOM)` = `min(10**400, 10**6)` = `10**6`. Good — the cap works.

But wait — `10**400` is a huge int. `min(10**400, 10**6)` works fine in Python (arbitrary precision ints). The cap is applied AFTER the int is accepted. So no overflow. Good.

But what about `10**9`? The test expects `10**6` (capped). `min(10**9, 10**6)` = `10**6`. ✓

**Let me look at the box ferry's `MAX_ITERS`:**

The ferry writes `"agent": {"max_iterations": MAX_ITERS}`. `MAX_ITERS` is not defined in the shown material. It's likely a constant from `server.py`. The box's `settings.yaml` has `agent.max_iterations: 25`. The ferry probably reads this and writes it to the hermes config. So `_operator_budget()` would read 25 from the config. And the host's `agent.max_iterations` would also be 25. These match. Good.

But what if `MAX_ITERS` is something else? Without seeing its definition, I can't be sure. But this is outside the PR's diff (the ferry is shown for context). LOW at most.

**Let me look for a really subtle issue...**

**The `_resolve_bounded` finalize-on-read closes the prior window with `floor = default`. But `default` is the CURRENT turn's budget, not the prior turn's.**

The comment in `_resolve_bounded` acknowledges this: "closing turn N-1's window here floors its DURABLE directive at turn N's budget (window [floor, floor + headroom]), so if the operator budget changed between turns the record is floored to the reader's value."

This means the prior turn's directive is floored at the CURRENT turn's budget, not the prior turn's. If the operator budget changed between turns (e.g., config edit mid-process — but that's documented as taking effect on restart), the prior turn's directive would be floored at the wrong value. But within a process, the operator budget is constant (cached), and the call site passes the same pristine budget every turn. So `default` is the same every turn. No issue in practice.

But wait — what if the call site's `default` changes between turns? The call site passes `agent._salience_operator_iterations`, which is captured once. So it's constant. Good.

**The `_close_locked` with `budget=None` (produce-path close) vs. `budget=floor` (finalize-on-read close):**

The produce-path close (rollover, session end) uses `_operator_budget()` as the floor. The finalize-on-read close uses the caller's `default`. These should be the same in normal operation (both are the operator budget). But they're derived differently:
- `_operator_budget()` reads from config
- `default` is `agent._salience_operator_iterations`, captured from `agent.max_iterations` at agent creation

If the host's `agent.max_iterations` was set from a source OTHER than the config's `agent.max_iterations` (e.g., from `max_turns`, or from an env override, or from a CLI flag), these could differ. The call site comment says "cli feeds the agent's max_iterations from max_turns." So if the config has `agent.max_turns: 500` but no `agent.max_iterations`, then:
- `_operator_budget()` skips `agent.max_iterations` (not in config), finds `agent.max_turns: 500` → 500
- The host's `agent.max_iterations` = 500 (from cli resolution)
- `agent._salience_operator_iterations` = 500
- `default` = 500

These match. Good.

But if the config has `agent.max_iterations: 25` AND `agent.max_turns: 500`, and cli.py uses `max_turns` to set `agent.max_iterations`:
- `_operator_budget()` finds `agent.max_iterations: 25` first → 25
- The host's `agent.max_iterations` = 500 (cli used `max_turns`)
- `agent._salience_operator_iterations` = 500
- `default` = 500

MISMATCH! The produce-path close floors at 25, the finalize-on-read close floors at 500.

But as we established, within a process with consumption on, the produce-path close (rollover) is a no-op (finalize-on-read already closed the window). So the mismatch doesn't affect the applied budget. The only case where the rollover close fires is when consumption is off (and the directive isn't consumed) or on session end (and the session is over).

Wait — session end. `_close_session` calls `_close_locked(window)` with `budget=None`. If the window was already closed by finalize-on-read, this is a no-op. If the window is still open (consumption was off), it closes with `_operator_budget()` as the floor. The directive is recorded but not consumed (session is over). On resume, the directive is read from disk. If the floor was 25 (from `_operator_budget()`) but the current process's operator budget is 500, the resumed directive (25 + headroom) could be below the current floor. The consumer applies it verbatim (no re-clamp, G6). So the resumed turn could get a budget BELOW the current operator floor.

But G1 says "Within a process the applied budget never exceeds operator + headroom and never falls below the operator floor." The resume caveat says "the first turn of a resumed session reapplies the LAST RECORDED budget." So the resumed budget could be below the current floor. Is this a G1 violation?

G1 says "Within a process" — the resume is across processes. And G1 says "The ONLY cross-restart carry is the documented resume caveat." So this is within the documented caveat. Not a finding.

But wait — the resume caveat says "the recorded value can sit anywhere in the prior window, above OR below the current operator setting." So a below-floor resume is documented. Fine.

**Let me look at one more potential issue: the `_record` function's bus handling.**

```python
def _record(kwargs: dict, mapper) -> None:
    ...
    with _LOCK:
        window = _WINDOWS.get(session_id)
        if window is None or window.closed or window.turn_id != turn_id:
            return
        window.events += 1
        for signal in mapper(kwargs, window.subject):
            try:
                self_bus = _bus_for(session_id)
                self_bus.publish(signal)
                window.signals.append(signal)
            except Exception:
                logger.warning(...)
```

`_bus_for(session_id)` is called inside the `_LOCK`. `_bus_for` creates a `SalienceBus` which does file I/O (replay on open). This is under `_LOCK`, so it's serialized. But it could be slow (replaying a large file). This is a performance issue, not a correctness issue. And it only happens once per session (the bus is cached). Fine.

**Let me look at the `_close_locked` bus calls.**

`_close_locked` calls `_bus_for(window.session_id)` twice: once for the ATTENTION publish, once for the directive emit. Both are under `_LOCK` (the caller holds `_LOCK`). The bus is cached after the first call. Fine.

But what if the ATTENTION publish succeeds but the directive emit fails? The ATTENTION signal is on the bus, but the directive is not. The `_LAST_DIRECTIVE` is popped. The consumer falls back to default. The bus has an ATTENTION signal without a corresponding directive. Is this a fence violation? No — the signal is on the record (it happened), and the directive just wasn't emitted (the close failed). The consumer doesn't read the signal; it reads the directive. So the signal is just an orphaned record. Not a correctness issue.

**Let me look at the `test_attention_publish_failure_falls_back_to_floor` test more carefully.**

```python
class _PublishFails:
    def publish(self, signal):
        raise RuntimeError("publish down")
    def emit(self, directive):
        return real_bus.emit(directive)

monkeypatch.setattr(so, "_bus_for", lambda sid: _PublishFails(), raising=False)
assert so.bounded_iterations("s", 10) == 10          # not 15
assert so._LAST_DIRECTIVE["s"] is not None           # the close itself succeeded
```

The test monkeypatches `_bus_for` to return a `_PublishFails` bus. But `_record` also calls `_bus_for` — if `_record` is called after the monkeypatch, it would get the `_PublishFails` bus and the signal publish would fail. But in this test, `_events` is called BEFORE the monkeypatch. So `_record` uses the real bus. Then the monkeypatch is applied, and `bounded_iterations` calls `_close_locked`, which calls `_bus_for` (now `_PublishFails`). The ATTENTION publish fails, so the signal is dropped. The directive emit calls `_PublishFails.emit`, which delegates to `real_bus.emit`. So the directive IS emitted to the real bus. `_LAST_DIRECTIVE` is set. The budget is 10 (floor, no ATTENTION). ✓

But wait — `_close_locked` calls `_bus_for(window.session_id)` for the ATTENTION publish. The monkeypatched `_bus_for` returns `_PublishFails()`. The ATTENTION publish raises. Then `_close_locked` calls `_bus_for(window.session_id)` again for the directive emit. The monkeypatched `_bus_for` returns a NEW `_PublishFails()` instance. Its `emit` delegates to `real_bus.emit(directive)`. But `real_bus` was captured before the monkeypatch: `real_bus = so._bus_for("s")`. So `real_bus` is the actual bus. The directive is emitted to the real bus. ✓

But there's a subtlety: the `_PublishFails.emit` calls `real_bus.emit(directive)`. This emits to the real bus, which updates the real bus's in-memory state AND writes to the file. But the `_LAST_DIRECTIVE` is set to the directive object (not the bus's payload). The consumer reads `_LAST_DIRECTIVE` and calls `_directive_budget(directive)`. The directive is a `Directive` object (from `interpret`). `_directive_budget` reads `getattr(source, "compute_budget", None)`. So it reads the `Directive` object's `compute_budget`. This is 10 (floor, no ATTENTION). ✓

The test is correct and mutation-sensitive. If the ATTENTION signal were interpreted without being published (i.e., appended to `signals` despite the publish failure), the budget would be 15 (10 + int(0.5 * 10 + 0.5) = 10 + 5 = 15), and the test would fail (10 ≠ 15). Good.

**Let me look for something really subtle...**

**The `_signal` function and `Facet.ATTENTION`:**

```python
attention = _signal(
    window.subject,
    Facet.ATTENTION,
    min(1.0, window.events / _ATTENTION_SATURATION_EVENTS),
    _ref("attention", "events:" + str(window.events)),
)
```

`Facet.ATTENTION` is the string `"attention"`. `_signal` creates a `SalienceSignal` with `facet="attention"`. The `valid_signal` check in the bus verifies `s.facet` is a bounded string. `"attention"` is 9 chars, well under 128. Good.

`_ref("attention", "events:" + str(window.events))` creates a tuple `("attention", "events:N")`. For large N (e.g., 1000000), `"events:1000000"` is 14 chars, under 128. For very large N (e.g., 10**100), `"events:" + str(10**100)` would be a very long string. But `_ref` truncates each token to `MAX_TOKEN_LEN` (128). So the provenance would be `("attention", "events:1000000...0"[:128])`. The truncation would make the provenance ambiguous (different large counts could truncate to the same string). But this is a provenance display issue, not a correctness issue — the budget is computed from `window.events`, not from the provenance string. And `window.events` is an int, not a string. The provenance is just for the audit record. LOW at most.

Actually, can `window.events` be that large? Each event is a tool call or API error. In a single turn, there could be thousands of tool calls, but not millions. And the influence saturates at 8 events. So the provenance string is at most `"events:10000"` or so. Not an issue.

**Let me look at the `test_e2e_headroom_moves_iteration_budget` test:**

```python
for i, (name, status, err) in enumerate([
    ("write_file", "success", ""), ("read_file", "success", ""),
    ("edit_file", "success", ""), ("run_shell", "error", "tool_error"),
]):
    model_tools._emit_post_tool_call_hook(
        function_name=name, function_args={}, result={},
        session_id=session_id, task_id=task_id, turn_id=turn_id,
        tool_call_id=f"c{i}", status=status,
        **({"error_type": err, "error_message": "boom"} if err else {}),
    )
applied = so.bounded_iterations(session_id, 25)
assert applied == 33
```

4 events: write_file (MEMORY), read_file (unmapped), edit_file (MEMORY), run_shell error (VERIFICATION). All 4 increment `window.events`. 4/8 = 0.5. floor 25, headroom 15. 25 + int(0.5 * 15 + 0.5) = 25 + int(8.0) = 25 + 8 = 33. ✓

Wait, `int(0.5 * 15 + 0.5) = int(7.5 + 0.5) = int(8.0) = 8`. So 25 + 8 = 33. ✓

But the test comment says "influence 0.5 ⇒ 25 + int(7.5 + 0.5) = 33". `0.5 * 15 = 7.5`. `int(7.5 + 0.5) = int(8.0) = 8`. `25 + 8 = 33`. ✓

**Let me look for a test that's mutation-blind...**

`test_quiet_turn_stays_exactly_at_floor`:
```python
_open("s", "u1")
assert so.bounded_iterations("s", 10) == 10
bus = so._BUSES["s"]
assert bus.directives_for(so._subject("s", "u1"))[0]["compute_budget"] == 10
assert list(bus.signals_for(so._subject("s", "u1"))) == []
```

If the ATTENTION synthesis condition were changed from `window.events > 0` to `window.events >= 0`, an ATTENTION signal with influence 0.0 would be synthesized. The budget would be `10 + int(0.0 * 15 + 0.5) = 10 + 0 = 10`. So the budget assertion would still pass (10 == 10). But the signals assertion would fail (there would be an ATTENTION signal in the list). So the test catches this mutation via the signals assertion. Good — the test is NOT mutation-blind for this mutation.

But what if the mutation is `window.events > 0` → `window.events > 1`? Then a single event would not synthesize ATTENTION. The budget would be 10 (floor). But the test has zero events, so it wouldn't catch this. The `test_budget_moves_and_rounds_half_up` test (4 events) would catch it: if ATTENTION is not synthesized for 4 events, the budget would be 10 (floor), not 13. So the mutation is caught by a different test. Good.

**Let me look at `test_saturation_boundary_exact`:**

```python
_open("a", "u1")
_events("a", "u1", 7)
assert so.bounded_iterations("a", 10) == 17
so._reset_for_tests()
_use_config(monkeypatch, tmp_path, _cfg(headroom=8))
_open("b", "u1")
_events("b", "u1", 8)
assert so.bounded_iterations("b", 10) == 18
```

7 events, headroom 8: 7/8 = 0.875. 10 + int(0.875 * 8 + 0.5) = 10 + int(7.0 + 0.5) = 10 + 7 = 17. ✓
8 events, headroom 8: 8/8 = 1.0. 10 + int(1.0 * 8 + 0.5) = 10 + int(8.5) = 10 + 8 = 18. ✓

If `_ATTENTION_SATURATION_EVENTS` were 7 instead of 8: 7 events → 7/7 = 1.0 → 10 + 8 = 18 ≠ 17. Test fails. ✓
If it were 9: 7/9 = 0.777... → 10 + int(0.777... * 8 + 0.5) = 10 + int(6.22 + 0.5) = 10 + 6 = 16 ≠ 17. Test fails. ✓
If it were 8 but the rounding were banker's: 0.875 * 8 = 7.0, no half issue. 1.0 * 8 = 8.0, no half issue. So this test doesn't catch banker's rounding. But `test_budget_moves_and_rounds_half_up` does (0.5 * 5 = 2.5, half-up = 3, banker's = 2). ✓

**Let me look at the `test_operator_budget_precedence_table`:**

```python
({"agent": {"max_iterations": 7, "max_turns": 40}}, 7),
```

Config has both `agent.max_iterations: 7` and `agent.max_turns: 40`. The precedence gives `max_iterations` first → 7. This is the rig key winning over the host key. Good for back-compat.

But this means the produce-path close (rollover, session end) floors at 7, while the host's `agent.max_iterations` might be 40 (if cli uses `max_turns`). The call site captures `agent.max_iterations = 40` (or 7, depending on how the host resolves). If the host uses `max_iterations` from config (7), then `agent._salience_operator_iterations = 7`, and both paths floor at 7. If the host uses `max_turns` (40), then `agent._salience_operator_iterations = 40`, and the finalize-on-read floors at 40 while the rollover floors at 7. But as we established, the rollover is a no-op when consumption is on. So this mismatch doesn't affect the applied budget.

Hmm, but this is a real config scenario (both keys present). The produce-path close would record a directive floored at 7, while the finalize-on-read close would record a directive floored at 40. If consumption is on, only the finalize-on-read directive is consumed. If consumption is off, the rollover directive (floored at 7) is recorded but not consumed. On resume, the directive (floored at 7) is read and applied verbatim. The current process's operator budget might be 40, so the resumed budget (7 + headroom) could be below 40. But this is the documented resume caveat. Not a finding.

**OK, I think I've been thorough. Let me look for one more thing: the `_ensure_template_valid` and the probe.**

```python
def _ensure_template_valid() -> None:
    global _TEMPLATE_VALIDATED
    if _TEMPLATE_VALIDATED is not None:
        return
    try:
        policy = _issue_template("salience.template.probe", _operator_budget())
        _TEMPLATE_VALIDATED = bool(verify_policy(policy, _POLICY_KEY))
    except (Exception, SystemExit):
        _TEMPLATE_VALIDATED = False
    if not _TEMPLATE_VALIDATED:
        logger.error(...)
```

This runs on the first consume call. It validates the template through `verify_policy`. If the template is invalid, it logs an error and sets `_TEMPLATE_VALIDATED = False`. Subsequent calls return early. The consumer still runs — it just gets a hard-deny directive (because `interpret` calls `verify_policy` and fails). The deny-shaped guard in `_directive_budget` returns None, and the consumer falls back to default. Good — fail open.

But wait — `_ensure_template_valid` calls `_issue_template` which calls `_compute_headroom()`. This caches the headroom. And `_operator_budget()` caches the budget. So the first consume call caches both. If the config changes after the first consume, the caches are stale. But this is documented ("a live config edit takes effect on restart"). Fine.

**Let me look at one final thing: the `test_template_probe_validates_widened_shape` test's second part.**

```python
so._reset_for_tests()
_use_config(monkeypatch, tmp_path, _cfg())
monkeypatch.setattr(so, "_compute_headroom", lambda: -1, raising=False)
with caplog.at_level("ERROR"):
    so._ensure_template_valid()
assert so._TEMPLATE_VALIDATED is False
assert any("template" in r.message for r in caplog.records)
```

This monkeypatches `_compute_headroom` to return -1. Then `_issue_template` would call `_compute_headroom()` → -1. So `max_budget = floor + (-1) = floor - 1`. `min_budget = floor`. So `min_budget > max_budget`, which fails `verify_policy`'s `0 <= min_budget <= max_budget` check. `_TEMPLATE_VALIDATED = False`. The error is logged. ✓

But wait — `_compute_headroom` is monkeypatched to return -1. But `_compute_headroom` is memoized — the monkeypatch replaces the function, so the memoization doesn't apply. The monkeypatched function returns -1 directly. So `_issue_template` gets -1 from `_compute_headroom()`. Good — the test works.

But in production, `_compute_headroom` never returns -1 (it clamps to 0). So this test is testing a hypothetical construction regression (e.g., someone edits `_compute_headroom` to return a negative). The test is valid for that scenario. Good.

**OK, I think I've been very thorough. Let me now look for the ONE thing I might have missed...**

Let me re-read the `_resolve_bounded` function one more time:

```python
def _resolve_bounded(session_id: str, default: int) -> "int | None":
    with _LOCK:
        _ensure_template_valid()
        floor = default if (isinstance(default, int) and not isinstance(default, bool)
                            and default > 0) else _operator_budget()
        window = _WINDOWS.get(session_id)
        if window is not None and not window.closed:
            _close_locked(window, budget=floor)
        directive = _LAST_DIRECTIVE.get(session_id)
        if directive is not None:
            return _directive_budget(directive)
        return _budget_from_disk(session_id)
```

After `_close_locked(window, budget=floor)`, `_LAST_DIRECTIVE[session_id]` is set to the just-emitted directive. Then `directive = _LAST_DIRECTIVE.get(session_id)` reads it. So the consumer reads the directive that was JUST emitted by the finalize-on-read close. This is turn N-1's directive (the window that was just closed). Turn N applies turn N-1's directive. ✓

But what if `_close_locked` fails? The `except` block pops `_LAST_DIRECTIVE[session_id]`. Then `directive = _LAST_DIRECTIVE.get(session_id)` returns None. The consumer tries `_budget_from_disk`. If the bus is cached (it was created during the failed close), `_budget_from_disk` returns None. The consumer falls back to default. Good — fail open.

But what if the bus was NOT cached before the failed close? The `_close_locked` might have created the bus (via `_bus_for`) before failing. Then `_budget_from_disk` finds the bus cached and returns None. Good.

But what if `_close_locked` fails BEFORE creating the bus? Then `_budget_from_disk` would try to create the bus (replay from disk). If there's a prior directive on disk, it would be promoted. This is a stale directive from a prior process. The consumer would apply it. Is this correct?

The scenario: turn N-1's window is open. Turn N's consumer calls `_resolve_bounded`. The finalize-on-read close fails (e.g., `interpret` raises). `_LAST_DIRECTIVE` is popped. The consumer tries `_budget_from_disk`. The bus is not cached (first call in this process). `_budget_from_disk` creates the bus, replays, and finds a prior directive from a prior process. It promotes it and returns its budget. The consumer applies this budget.

Is this correct? The consumer is applying a directive from a PRIOR PROCESS (not turn N-1). This is the resume caveat — the first turn after a restart reapplies the last recorded budget. But in this scenario, it's not a restart — it's a failed close within the same process. The consumer is falling back to a disk directive because the in-memory close failed. This is arguably correct (fail open to the last recorded budget) but it's applying a potentially stale budget.

Actually, let me think about this more carefully. If the close fails, it means something went wrong with the policy/interpret/emit. The prior turn's directive is not recorded. The consumer falls back to... the last SUCCESSFULLY recorded directive, which could be from many turns ago (or a prior process). This is better than nothing (it's a recorded decision), but it could be stale.

But G4 says "turn N applies the most recently RECORDED turn's directive (normally N-1)." If turn N-1's close failed, the most recently RECORDED turn is N-2 (or earlier). So applying N-2's directive is correct per G4. The consumer is doing the right thing — it's applying the most recently RECORDED directive, which is the one on disk.

But wait — is the on-disk directive from N-2 or from a prior process? If this is the first turn after a restart, and N-1's close fails, the on-disk directive is from the prior process. The consumer applies it. This is the resume caveat. Fine.

If this is NOT the first turn after a restart (the process has been running), and N-1's close fails, the on-disk directive is from N-2 (the last successful close). The consumer applies N-2's directive. This is correct per G4 (most recently recorded). Fine.

But there's a subtlety: `_budget_from_disk` checks `if session_id in _BUSES: return None`. If the bus was cached during a prior successful close (N-2), then `_BUSES[session_id]` exists. So `_budget_from_disk` returns None. The consumer falls back to default. So the consumer does NOT apply N-2's directive — it falls back to default.

Wait, but `_LAST_DIRECTIVE[session_id]` was set during N-2's close. Then N-1's close fails and pops `_LAST_DIRECTIVE[session_id]`. Now `_LAST_DIRECTIVE` is empty. The consumer tries `_budget_from_disk`, which finds `_BUSES[session_id]` cached and returns None. The consumer falls back to default.

So the consumer applies the DEFAULT, not N-2's directive. Is this correct? The most recently RECORDED directive is N-2's (on the bus). But the consumer can't read it because `_budget_from_disk` short-circuits when the bus is cached. The comment in `_budget_from_disk` explains: "In-process, the authority is `_LAST_DIRECTIVE`; if a bus is already cached but `_LAST_DIRECTIVE` is empty the last close FAILED, and reading a stale on-disk directive would both bypass the replay verification and apply a 2-turns-stale budget — so we return None (⇒ default) instead."

So the design choice is: if the in-memory cache is empty but the bus is cached (meaning a close failed in-process), fall back to default rather than reading a stale on-disk directive. This is conservative and correct — a failed close means the prior turn's directive is unreliable, so don't apply a stale one. Good.

But this means the consumer applies the DEFAULT (operator budget) when a close fails. This is fail-open. G2 says "any failure => the caller's default." ✓

**OK, I think I've been extremely thorough. Let me now look for one final thing: the `_close_locked` exception handling and the `_LAST_DIRECTIVE` pop.**

```python
except (Exception, SystemExit):
    _LAST_DIRECTIVE.pop(window.session_id, None)
    logger.warning("salience observer: window finalize failed", exc_info=True)
```

This pops `_LAST_DIRECTIVE[session_id]`. But what if the close partially succeeded — e.g., the ATTENTION signal was published but the directive emit failed? The ATTENTION signal is on the bus, but the directive is not. `_LAST_DIRECTIVE` is popped. The consumer falls back to default. The bus has an orphaned ATTENTION signal. Is this a problem?

No — the ATTENTION signal is on the record (it happened), but no directive was emitted. The consumer doesn't read signals. The next close would emit a new directive (with its own ATTENTION signal). The orphaned signal is just extra audit data. Not a correctness issue.

**Let me look at one more potential issue: the `_close_locked` and the `_bus_for` call.**

In `_close_locked`, `_bus
