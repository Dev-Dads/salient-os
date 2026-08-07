# Red-team (H3, pass=general): moonshotai/kimi-k3

_finish=length seconds=223.5 usage={'prompt_tokens': 27597, 'completion_tokens': 16000, 'total_tokens': 43597, 'cost': 0.4841865, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.4841865, 'upstream_inference_prompt_cost': 0.1241865, 'upstream_inference_completions_cost': 0.36}, 'completion_tokens_details': {'reasoning_tokens': 16826, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me carefully analyze this PR-H3 implementation for concrete defects. The task is to find what two internal reviews missed, focusing on:

1. ESCALATION / RUNAWAY paths
2. FAIL-OPEN / NEVER-BRICK
3. A4 FLOOR INTEGRITY
4. A3 WITH MOVEMENT (stale/self-read directives)
5. AUDIT HONESTY / FENCE
6. HONESTY (docs)
7. TEST HONESTY (mutation-blind tests)

Let me trace through the code carefully.

### The ratchet fix analysis

The call site in `agent/turn_context.py`:

```python
try:
    from hermes_cli.observability import salience_observer as _salience_observer
    if getattr(agent, "_salience_operator_iterations", None) is None:
        agent._salience_operator_iterations = agent.max_iterations
    agent.max_iterations = _salience_observer.bounded_iterations(
        agent.session_id or "", default=agent._salience_operator_iterations
    )
except Exception:
    logger.debug("salience consumer skipped", exc_info=True)
agent.iteration_budget = IterationBudget(agent.max_iterations)
```

The pristine value is captured ONCE per agent: `if getattr(agent, "_salience_operator_iterations", None) is None`. 

**Potential issue 1: What if the operator legitimately changes `agent.max_iterations` mid-session?** The pristine value is captured once and never updated. If the operator raises their budget from 25 to 100 mid-session (e.g., via a config command that sets `agent.max_iterations = 100`), the pristine stays 25, and the consumer will keep flooring at 25 — so `agent.max_iterations` gets overwritten back to 25-40 range, silently discarding the operator's change. Actually wait — is there a path where the operator can change max_iterations mid-session? Possibly via a `/config` command or similar. The consumer would then stomp it. But is this in scope? The guarantee G1 says "the applied budget never exceeds operator + headroom and never falls below the operator floor". If the operator changes their budget mid-session, the applied budget would fall below the NEW operator floor. Hmm, but this might be considered an edge case. Actually, more importantly: the capture-once means if the FIRST turn's `agent.max_iterations` was somehow already governed... no wait, it's captured before the first application.

Actually wait — there's a subtler issue. When is this code run? "Called once at turn start, immediately before the host rebuilds its IterationBudget". The capture happens on the FIRST turn. On the first turn, `agent.max_iterations` is the operator's configured value (nothing has governed yet). So pristine = operator value. Good.

But what about **session resume within the same agent object**? If `agent.max_iterations` was mutated by a previous session's governance... Actually the agent object persists across turns of a session. On resume (new process), a new agent is constructed with the operator's config value, so pristine = operator value again. OK.

But here's a real one: **what if `agent.max_iterations` is legitimately mutated by something else between the capture and a later turn?** E.g., a `/set max_iterations 100` command. Then pristine (25) stomps it back to ≤40 on the next turn. The operator's explicit change is silently discarded. Is that a violation of G1? "never falls below the operator floor" — the operator floor is now 100, but applied is 25-40. Hmm. But is there such a mutation path in the host? We don't have visibility into the rest of turn_context.py. This is speculative without a concrete trigger. Let me note it but it may be LOW/MEDIUM.

**Potential issue 2: The `getattr(agent, "_salience_operator_iterations", None) is None` check.** What if `agent.max_iterations` is 0 or negative at capture time? Then pristine = 0, and `bounded_iterations(session_id, default=0)` returns 0 untouched (out of contract). Then `agent.max_iterations = 0`... wait no. Let's trace: pristine = 0. `bounded_iterations` with default=0: `if not isinstance(default, int) or isinstance(default, bool) or default < 1: return default` → returns 0. So `agent.max_iterations = 0`. Then `IterationBudget(0)`. That's the host's own bug though — the consumer deliberately doesn't paper over it. Documented. OK.

**Potential issue 3: What if the first capture happens when `agent.max_iterations` was ALREADY governed by a previous turn in the same process but different agent?** No — `_salience_operator_iterations` is per-agent attribute. New agent → fresh capture. OK.

Hmm wait, actually there's a subtle one: the capture happens inside the try. If the import fails (stock build), the whole block is skipped — fine. But what if `bounded_iterations` raises SystemExit? It's declared to catch (Exception, SystemExit) internally... but the call-site catch is only `except Exception`. If `bounded_iterations` itself has a bug and raises SystemExit, it escapes the call-site guard and crashes the turn. But `bounded_iterations` catches `(Exception, SystemExit)` internally, so it shouldn't propagate. The import could raise SystemExit though — `from hermes_cli.observability import salience_observer` — if that module import triggers a SystemExit (e.g., config helper at import time)... the call site only catches Exception. Hmm, but this is the H2 call site shape, mostly unchanged. The PR changed the default passing. The comment says "the surrounding try guards the import itself". A SystemExit from the import would escape. But is that new in this PR? No. Out of scope-ish (H2 behavior). Skip.

### The `_resolve_bounded` floor derivation

```python
floor = default if (isinstance(default, int) and not isinstance(default, bool)
                    and default > 0) else _operator_budget()
window = _WINDOWS.get(session_id)
if window is not None and not window.closed:
    _close_locked(window, budget=floor)
```

The finalize-on-read floors the PRIOR turn's window at THIS turn's default (the pristine operator budget). Good — that's the A4 floor.

**Potential issue 4: The window closed at finalize-on-read is turn N-1's window, floored at turn N's pristine default. But the directive produced is then read and applied to turn N. So turn N's budget = f(turn N-1's events, turn N's floor). That's the design.**

But wait — what about the rollover close path (`_open_window` closing the previous window with `budget=None` → `_operator_budget()`)? When does that happen vs. finalize-on-read? 

Sequence in production (per the ordering context): turn N starts → consumer call site runs `bounded_iterations` (finalize-on-read closes turn N-1's window at pristine floor, reads directive, applies) → ... → `pre_llm_call` fires → `_open_window` for turn N (window turn N-1 already closed, so no rollover close; new window created).

So in the normal flow, every close is a finalize-on-read close at the pristine floor. The rollover close (budget=None → `_operator_budget()`) only happens if a turn's window is still open when the NEXT turn's `pre_llm_call` fires WITHOUT the consumer running in between — e.g., if consumption is kill-switched (`consume_compute: false`) but production is on. In that case the floor is `_operator_budget()` — the config-derived value, not the pristine agent value. Those can differ! `_operator_budget()` reads config keys `agent.max_iterations`, `max_iterations`, `agent.iteration_budget`, `agent.max_turns`, `max_turns`, fallback 25. The pristine value is `agent.max_iterations` the host actually resolved — which may come from `HERMES_MAX_ITERATIONS` env override (deliberately not read by `_operator_budget` — documented), or CLI args, or platform-specific resolution.

So with `consume_compute: false`, the recorded directives are floored at `_operator_budget()` (config-derived). Then if the operator later flips `consume_compute: true`... wait, config is read once per process for headroom, but `_config_flag("consume_compute", True)` is read EVERY call (not memoized — `_config_flag` reads config each time). Actually `_consume_enabled()` calls `_config_flag("consume_compute", True)` which calls `read_raw_config_readonly()` fresh each time. So the kill switch is live-read. Hmm, but that's a runtime toggle.

The scenario: env override `HERMES_MAX_ITERATIONS=500`, config has `agent.max_iterations: 25`. Host resolves max_iterations=500 (env wins). Pristine = 500. With consume on: finalize-on-read floors at 500, window [500, 500+H]. Fine. With consume off: rollover close floors at `_operator_budget()` = 25 (config). Recorded directive = 25-ish. But consume is off so nothing reads it... until a resume: new process, consume on, `_budget_from_disk` recovers the last recorded directive = 25+H-ish, applies it → turn 1 of resumed session gets 25+H instead of 500. That's the documented resume caveat though ("can sit anywhere in the prior window"). But the floor integrity issue: the recorded floor was 25, not the operator's real 500. The docstring for `_operator_budget` acknowledges: "The HERMES_MAX_ITERATIONS env override is deliberately not read here: this floor only covers rollover / session-end closes, and the consumer's finalize-on-read floors at the caller's fully-resolved default anyway." So it's documented. OK, out of scope-ish.

**Potential issue 5: `_close_locked` idempotency and the `budget` parameter.** First close wins. If finalize-on-read closes window N-1 at floor=pristine, then later `_close_session` or rollover tries to close again — `window.closed` is True, no-op. Fine.

### The double-count / window-split hunt

`_record` increments `window.events += 1` AFTER the turn-id guard. Good (already-fixed finding b).

`_close_locked`: synthesizes ONE attention signal if `window.events > 0`. Publishes it, appends to signals, interprets. 

**Potential issue 6: What if `_close_locked` is called, the attention publish succeeds, but `interpret` or `emit` throws?** Then `_LAST_DIRECTIVE.pop` and log. The attention signal IS on the bus (published), but no directive. The window is marked closed. No double-count (window won't be re-closed). But the bus now has an attention signal with no directive — audit record has a signal that informed nothing. Not a fence violation per se. And the consumer falls to default. Fine.

**Potential issue 7: Can a window be closed TWICE with different budgets?** `window.closed = True` is set first thing. No.

**Potential issue 8: The `_open_window` rollover: `if current is not None and not current.closed and current.turn_id != turn_id: _close_locked(current)`. Then `if current is None or current.closed or current.turn_id != turn_id: create new`. Note: if `current.turn_id == turn_id` and not closed, keep the existing window (same turn, multiple pre_llm_calls — e.g., retries within a turn). Events accumulate. Fine — one window per turn.

But wait — what if the SAME turn_id gets a pre_llm_call, events, then the consumer's finalize-on-read closes it (because... hmm, when would finalize-on-read close the CURRENT turn's window?). The ordering pin says the consumer runs BEFORE pre_llm_call opens turn N's window. So at consumer time, the open window is turn N-1's. Finalize-on-read closes it. Then pre_llm_call opens turn N. Then within turn N, multiple pre_llm_calls (one per LLM call in the iteration loop!) — wait, pre_llm_call fires before EVERY LLM call, not once per turn? Let me check.

Looking at turn_context.py lines 1070-1092: "Plugin hook: pre_llm_call (context injected into user message...)". This is in the turn setup path. Hmm, but the name suggests it fires before each LLM call. If the agent loop calls the LLM multiple times per turn (iterations!), does pre_llm_call fire each time with the SAME turn_id? Then `_open_window` with same turn_id keeps the same window. Events accumulate across the turn. Fine.

But what if turn_id changes per ITERATION (e.g., turn_id = f"{session}-{turn}-{iter}")? Then each iteration would roll over the window... but then the consumer's finalize-on-read at next turn start would close the LAST iteration's window only. Hmm, we don't have visibility into how turn_id is generated. The tests use "u1", "u2" per turn. The H2 design presumably established turn_id semantics. Out of scope (H1/H2 behavior).

**Potential issue 9: THE KEY ONE — let me look at `_resolve_bounded` more carefully.**

```python
with _LOCK:
    _ensure_template_valid()
    floor = default if (...) else _operator_budget()
    window = _WINDOWS.get(session_id)
    if window is not None and not window.closed:
        _close_locked(window, budget=floor)
    directive = _LAST_DIRECTIVE.get(session_id)
    if directive is not None:
        return _directive_budget(directive)
    return _budget_from_disk(session_id)
```

Turn N calls this. It closes the open window (turn N-1's) at floor=default (pristine). Reads `_LAST_DIRECTIVE[session_id]` — which `_close_locked` just set to turn N-1's directive. Applies it. Correct: turn N applies turn N-1's directive.

**But what if turn N-1's window was ALREADY closed (e.g., by session-end or a prior finalize-on-read)?** Then `_LAST_DIRECTIVE` holds... whatever the last close produced. If turn N-1 closed at rollover (consume was off) and then consume turns on... edge cases.

**Potential issue 10: THE SELF-READ / STALE-READ with movement.** Consider: turn N-1 aborts before opening its window (documented in bounded_iterations docstring: "a turn that aborts before opening its window records nothing, so an earlier turn may be the latest"). Then at turn N, `_WINDOWS.get(session_id)` is None (or turn N-2's closed window). `_LAST_DIRECTIVE` holds turn N-2's directive. Turn N applies turn N-2's budget. That's documented ("normally that is the immediately prior turn, but..."). OK.

**Potential issue 11: Now the REAL hunt — the finalize-on-read close uses floor = THIS turn's default. The directive recorded for turn N-1 is floored at turn N's pristine budget. Since pristine is captured once and constant per agent, floor is the same every turn. Window [pristine, pristine+H]. Applied budget ∈ [pristine, pristine+H]. G1 satisfied within a process.**

Wait, but there's a subtle cross-session issue with the pristine capture: `_salience_operator_iterations` is per-AGENT, but `_LAST_DIRECTIVE`, `_WINDOWS`, `_BUSES` are per-SESSION module globals. If ONE agent serves MULTIPLE sessions sequentially (session reset → new session_id, same agent object), then: session A's turns govern with pristine=25. Session A ends (on_session_end frees everything). New session B same agent: pristine already captured = 25 (fine, same operator). Turn 1 of session B: `_WINDOWS` empty, `_LAST_DIRECTIVE` empty (freed), `_BUSES` empty → `_budget_from_disk(session_B)` → no file → None → default 25. Fine.

But what if session B is a RESUME of session A (same session_id, same agent, after in-process reset)? on_session_reset frees everything including `_LAST_DIRECTIVE`. Then turn 1: `_budget_from_disk` — `session_id in _BUSES`? No (freed). So it opens the bus from disk, replays, recovers last directive = e.g. 40 (busy last turn). Applies 40. Documented resume caveat. OK.

**Potential issue 12: `_budget_from_disk` promotion and the floor.** The recovered directive (say 40, from a window [25,40]) is applied verbatim to turn 1 of the resumed process. G1 says "The ONLY cross-restart carry is the documented resume caveat (first resumed turn reapplies the last RECORDED budget)." Documented. OK.

But WAIT — what about `_budget_from_disk` when the LAST directive on disk is a HARD DENY or from a different config era? `_directive_budget` guards deny-shaped (subject/policy_id present, budget ≥ 1). A recorded deny has subject="" → treated absent → None → default. Fine.

**Potential issue 13: What if the recorded directive on disk has budget 40 but the CURRENT process's operator budget is 10 (operator lowered config between processes)?** Resume applies 40 > operator 10 + headroom? If headroom is now 0 (pinned), applied 40 exceeds operator + headroom = 10. G1 says "Within a process the applied budget never exceeds operator + headroom" — the resume caveat is explicitly the only cross-restart carry, and the config example documents "the recorded value can sit anywhere in the prior window, above OR below the current operator setting". Documented. OK.

### Now the arithmetic edge cases

`influence = min(1.0, events / 8)`. events is int. For events=1..7: fractions. `_scale(frac, lo, hi) = lo + int(frac * (hi - lo) + 0.5)`.

Float precision: `frac * (hi - lo)` — for large hi-lo (up to 1e6), frac like 1/8=0.125 exactly representable, 3/8=0.375, 7/8=0.875 — all exact in binary (denominators are powers of 2!). events/8 for events 0..8: 0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0 — ALL exactly representable in IEEE754. So frac * headroom for headroom up to 1e6: 0.125 * 1e6 = 125000.0 exact? 1e6 = 2^6 * 15625, and 0.125 = 2^-3, product = 125000 exactly. Any dyadic rational times an integer up to 2^53 is exact if the product fits. frac = k/8, headroom ≤ 1e6 < 2^20, so frac*headroom = k*headroom/8 < 2^23 — exact. +0.5 exact. int() exact. So no float drift at the saturation constant 8. Nice — the tests' expected values hold. No finding there.

But wait — `_aggregate` computes the confidence-weighted mean: `agg[facet] = sum(s.influence * s.confidence) / weight`. With ONE attention signal, confidence 1.0: agg = influence * 1.0 / 1.0 = influence. Exact. But if there are OTHER attention signals... the observer is the sole attention publisher (comment says so). The mapped signals are VERIFICATION/RISK/MEMORY only. So agg[ATTENTION] = the synthesized influence exactly. OK.

Hmm wait, actually — could there be MULTIPLE synthesized attention signals in one window's signal list? `_close_locked` synthesizes one and appends to `signals` (a local list copy). The window's `signals` list only has mapped signals. One synthesis per close, one close per window. OK.

**Potential issue 14: The attention signal is published to the bus AND included in the interpret list. But the bus publish happens BEFORE interpret. If the SAME window is closed... no, idempotent. OK.**

**Potential issue 15: `_signal` constructs `SalienceSignal(SUBSYSTEM_ID, subject, facet, influence, 1.0, provenance)`. influence = min(1.0, events/8) ∈ (0, 1]. For events ≥ 1, influence ≥ 0.125 > 0. valid_signal requires _unit(influence): 0 ≤ x ≤ 1. OK. confidence 1.0 OK. Provenance ("attention", "events:<n>") — both ≤ 128 chars. n can be huge (events unbounded)? "events:123456789" — still short. OK. Subject ≤ 128 by _subject construction. OK.**

### The publish-failure path

```python
try:
    _bus_for(window.session_id).publish(attention)
    signals.append(attention)
except Exception:
    logger.warning(...)
```

If publish fails, signal dropped → interpret without attention → floor. Good (G5). But note: `_bus_for` itself could throw (mkdir fails, replay fails) — caught by the same except. Good.

But WAIT — the publish in `_record` for mapped signals: if a mapped signal's publish fails, it's not in `window.signals`, but `window.events` was still incremented. So the attention count includes events whose signals failed to publish. The docstring says "including events whose signal publish failed — it measures attributed activity, not recorded signals." Documented, deliberate. OK.

**Potential issue 16: Partial publish failure of the ATTENTION signal specifically — what if publish SUCCEEDS but appends a signal that interpret then drops?** valid_signal passes (constructed valid). OK.

### The `_close_locked` exception path

```python
except (Exception, SystemExit):
    _LAST_DIRECTIVE.pop(window.session_id, None)
    logger.warning(...)
```

If `_operator_budget()` throws... it can't (contained). If `_issue_template` throws (issue_policy raising?) — contained. If `interpret` throws — contained. If `emit` throws (bus I/O error) — contained, and `_LAST_DIRECTIVE` popped → consumer falls to default. Good.

BUT: the window is marked `closed = True` even on failure. So a failed close never retries. The turn's directive is absent → next turn applies default. Fail-open. Good.

Hmm, wait — there's something. `_close_locked` sets `window.closed = True` BEFORE the try. If the close fails, the window is closed, no directive. Then `_open_window` for the next turn: `current.closed` → create new window. Fine.

### The consumer read path — G6 says zero diff. Skip mostly.

### Now — the hunt for OTHER feedback paths (the prompt's hint list)

"any OTHER feedback path that re-anchors the floor to a governed output (getattr defaults, resume paths, the box's own bounded_iterations call in server.py, _budget_from_disk promotion)"

**The box's server.py!** Let me look. `_write_hermes_config` writes `agent.max_iterations: MAX_ITERS` (25) and `compute_headroom` (15, mirrored). The box runs the host with this config. The host's agent resolves max_iterations=25. Pristine=25. Window [25,40]. Fine.

But the prompt mentions "the box's own bounded_iterations call in server.py" — the material only shows `_write_hermes_config` in server.py lines 142-165. Maybe there's another call elsewhere not shown. Can't analyze what's not shown. The ferry mirrors the shape contract (fixed finding d). One thing: the ferry writes `compute_headroom` as int ≥ 0, but does NOT cap at 1e6! `headroom = raw_headroom if isinstance(...) and raw_headroom >= 0 else 0`. If box settings.yaml has `compute_headroom: 10**9`, the ferry writes 10^9 to the hermes config. Then `_compute_headroom()` caps at 1e6. So the observer's cap still applies. Fine — the cap is at the observer, ferry doesn't need it.

Hmm wait, actually — does the ferry's mirror accept a digit-string? `isinstance(raw_headroom, int)` — a string "15" in box settings.yaml → not int → 0. But the observer honors digit-strings. So the box ferry is STRICTER than the observer. The box settings.yaml comment says "compute_headroom: 15". If a box operator writes "15" (quoted), the ferry coerces to 0 (inert) while a direct hermes config would honor it. Inconsistent but fail-inert direction — safe. Honesty issue at most, minor. Actually the ferry comment says "Mirror the observer's shape contract instead of int()-coercing" — but it does NOT mirror the full contract (no digit-strings, no cap). The observer's contract: bool→0, negative→0, float→0, garbage→0, digit-string→honored, capped 1e6. The ferry: non-int→0 (including digit-strings), no cap (relies on observer cap). The ferry UNDER-honors digit-strings. Is that a defect? The ferry's job is to produce a clean hermes config; writing 0 for a quoted string is fail-inert. The box settings.yaml documents `compute_headroom: 15` (unquoted). Minor honesty gap at most. LOW.

**`_budget_from_disk` promotion:** promotes the recovered payload dict into `_LAST_DIRECTIVE[session_id]`. Then a subsequent read in the same process returns `_directive_budget(payload)` — the SAME recovered value. Fine. But: the promoted payload is a deep copy. `_directive_budget` reads subject/policy_id/compute_budget. Fine.

Hmm — wait. Here's a subtle one. `_budget_from_disk` recovers `directives[-1]` — the LAST directive on the bus, regardless of subject/turn. On a cold resume, that's the last recorded turn's directive. Applied to turn 1. Documented. But what about the FLOOR integrity of that recorded directive? It was floored at the PRIOR process's pristine (or `_operator_budget()` if rollover-closed). If the prior process had a different operator budget... documented resume caveat. OK.

**getattr defaults hunt:** `getattr(agent, "_salience_operator_iterations", None) is None` — capture-once. What if `agent.max_iterations` at capture time is ALREADY a governed value? When could that be? The capture happens on the first turn the consumer runs. If the agent object was created and `max_iterations` set from config — pristine. But what if the agent is LONG-LIVED and a PREVIOUS session's governance mutated `agent.max_iterations`, and `_salience_operator_iterations` was never set because... no — if governance ran before, the attribute was set then. The attribute is set on the FIRST consumer run, before any governance. Unless... the attribute gets DELETED somehow (del agent._salience_operator_iterations)? No path shown. 

Hmm, what about agent RESET paths — `on_session_reset`? Does the host reset `agent.max_iterations` on session reset but not `_salience_operator_iterations`? If the operator changes config and resets the session, the agent might re-resolve max_iterations=100, but `_salience_operator_iterations` stays 25 → consumer stomps to ≤40. Same as the mid-session config change issue. The capture-once-per-agent is the documented design ("captured once per agent"). The honesty question: does any doc tell the operator that changing max_iterations mid-agent-lifetime won't take effect while consume is on? The module docstring says "within a process, never fewer than the operator's own budget (A4 floor)". If the operator raises their budget mid-process, the applied budget CAN be fewer than the operator's (new) budget. That's an overstatement... but "operator's own budget" arguably means the captured pristine. Hmm. This is a genuine gap but needs a concrete trigger: a host path that mutates agent.max_iterations mid-session. We don't have evidence of such a path in the material. The box fixes it at 25 via config. CLI `/config` commands? Not shown. I'll flag it as MEDIUM-LOW with the caveat.

Actually wait — there IS a concrete path visible in the material! Look at the call site again:

```python
if getattr(agent, "_salience_operator_iterations", None) is None:
    agent._salience_operator_iterations = agent.max_iterations
agent.max_iterations = _salience_observer.bounded_iterations(
    agent.session_id or "", default=agent._salience_operator_iterations
)
```

The pristine is captured from `agent.max_iterations` at FIRST consumer run. But WHEN does the first consumer run happen relative to agent construction? "Called once at turn start, immediately before the host rebuilds its IterationBudget". On turn 1, agent.max_iterations = operator config value. Capture = operator value. ✓.

BUT — what if the FIRST turn's consumer run happens when `agent.max_iterations` was already mutated by the RESUME path? On resume (same process? no — resume is a new process typically). In a new process, new agent, max_iterations from config. ✓.

Hmm, what about `HERMES_MAX_ITERATIONS` env? The host resolves max_iterations honoring the env override (per `_operator_budget` docstring: "cli feeds the agent's max_iterations from max_turns" and env override exists). Pristine captures the RESOLVED value (env honored). ✓ — actually good, the pristine is more accurate than `_operator_budget()`.

### A3 with movement — the stale-read hunt

Now let me think REALLY carefully about the turn sequence. The ordering: consumer (turn start) → pre_llm_call (window open for turn N) → tool calls (events) → ... → turn N+1 start: consumer closes turn N's window at floor=pristine, reads directive, applies.

Test `test_three_turn_cadence_distinct_budgets` models this. ✓.

**What about the FIRST turn after enabling consume?** Turn 1: no window, no directive → default. ✓.

**What if a turn has NO pre_llm_call but the consumer still runs?** E.g., a turn that aborts early. Consumer runs at turn start (before any LLM call). If the turn aborts before pre_llm_call... wait, the consumer runs BEFORE pre_llm_call per the ordering pin. So sequence: consumer (closes prior window, applies) → turn aborts before pre_llm_call → no window opened for this turn. Next turn: consumer runs → `_WINDOWS.get(session_id)` → None (or the prior closed window... wait, `_WINDOWS[session_id]` still holds the CLOSED window from 2 turns ago — `_open_window` replaces it only when a new window opens; `_close_session` pops it). So `_WINDOWS.get` returns the closed window → `not window.closed` False → no close. `_LAST_DIRECTIVE` holds the directive from 2 turns ago → applied. Documented ("a turn that aborts before opening its window records nothing, so an earlier turn may be the latest"). ✓ honest docs.

**THE SELF-READ:** Could the consumer ever close and read the CURRENT turn's OWN window? Only if pre_llm_call for turn N fires BEFORE the consumer call site in turn N. The source-text test pins `call < pre_llm_call` — the FIRST occurrence of `"pre_llm_call"` in the file. Hmm — the test finds `next(k for k, ln in enumerate(lines) if '"pre_llm_call"' in ln)` — the first line containing `"pre_llm_call"` ANYWHERE in turn_context.py. If there's an earlier mention (e.g., in a comment or a different dispatch) before the consumer call... the test asserts call < pre_llm_call, so if the first mention were before, the test would RED. It passes, so the first mention is after. But is the first mention the actual dispatch for THIS turn? The material shows the dispatch at lines 1070-1092, consumer at 480-515. ✓. But wait — is the consumer call site at 480 in the SAME function/flow as the pre_llm_call at 1070? If they're in different functions (e.g., 480 in `prepare_turn` and 1070 in `run_llm_loop`), the ordering within a turn depends on call order, not source order. The source-text test only pins SOURCE order. Hmm — but the test comment acknowledges it's a structural adjacency guard, "a full-turn harness is out of scope". The e2e test (`test_e2e_headroom_moves_iteration_budget`) goes through the real dispatch chain but calls `so.bounded_iterations` directly, not through turn_context. So the ACTUAL turn-level ordering (consumer before window-open, per turn) is pinned only by source-text adjacency within one file. If turn_context.py has the consumer in an early function and pre_llm_call dispatch in a later-called function... source order wouldn't guarantee runtime order. But we can't see the full file. The test is honest about its limits. Not a finding without evidence.

**Now — a REAL potential A3 issue: the finalize-on-read close floors turn N-1's directive at turn N's pristine. Since pristine is constant, fine. But what about the VERY FIRST finalize-on-read in a process where `_operator_budget` differs from pristine?** floor = default (pristine) if valid. Always pristine. ✓ consistent.

**What about `bounded_iterations` called with a session_id but the window belongs to a DIFFERENT turn because turn_ids are reused?** E.g., turn_id "1" every session... within a session, turn_ids unique presumably. Skip.

### The multi-session / gateway hunt

`_WINDOWS`, `_BUSES`, `_LAST_DIRECTIVE` keyed by session_id. `_OPERATOR_BUDGET_CACHE` and `_HEADROOM_CACHE` are PROCESS-global. Two concurrent sessions (gateway) with different operator budgets? The host is single-agent per process presumably; the box is single-session. The bus contract is single-threaded, serialized under _LOCK. Multi-session in one process shares the same operator budget (same config). ✓.

But — `_close_session` pops `_BUSES[session_id]` and `_LAST_DIRECTIVE[session_id]`. If a LATE hook for that session arrives after close... `_record` drops (no window). ✓. But what if a late `bounded_iterations(session_id)` arrives after `_close_session`? `_WINDOWS` empty, `_LAST_DIRECTIVE` empty, `_BUSES` empty → `_budget_from_disk` → reopens bus from disk, replays, recovers last directive → applies it. Post-close read recovers from disk — documented in `_close_session` comment ("A post-close read recovers from disk if ever needed"). ✓.

Hmm wait — actually there's a subtle issue here. `_close_session` is called on `on_session_end`. In the host, does the consumer call site run AFTER on_session_end for the last turn? No — consumer runs at turn START. Session end is after the last turn. ✓.

### The `_ensure_template_valid` probe

```python
policy = _issue_template("salience.template.probe", _operator_budget())
_TEMPLATE_VALIDATED = bool(verify_policy(policy, _POLICY_KEY))
```

Goes through `_issue_template` → `_compute_headroom()`. If headroom config is bad → 0 → pinned → verifies. If headroom is huge → capped 1e6 → floor + 1e6 → verifies (min ≤ max). ✓. The probe runs on FIRST consume, under _LOCK. It memoizes `_operator_budget()` as a side effect — fine.

Test `test_template_probe_validates_widened_shape` part (b): monkeypatches `so._compute_headroom` to return -1 → `_issue_template` produces min=25, max=24 → verify_policy False (min > max) → `_TEMPLATE_VALIDATED` False → error logged. ✓ good test.

But WAIT — the probe uses `_operator_budget()` as floor. If `_operator_budget()` returns... always ≥ 1 (config values must be > 0, fallback 25). ✓.

### Test honesty hunt

Now let me look for mutation-blind tests.

**`test_no_ratchet_across_busy_turns`:** passes `floor=25` as default every turn. Asserts 25→40→40→25. This tests the OBSERVER math, but the actual no-ratchet property depends on the CALL SITE passing pristine. The test comment acknowledges: "The call-site shape itself is pinned by test_call_site_precedes_budget_rebuild." The source-text pin asserts `default=agent._salience_operator_iterations` in the block and bans `default=agent.max_iterations`. 

Mutation hunt on the source-text pin: if the call site were mutated to capture pristine AFTER assignment... e.g.:

```python
agent.max_iterations = _salience_observer.bounded_iterations(...)
if getattr(agent, "_salience_operator_iterations", None) is None:
    agent._salience_operator_iterations = agent.max_iterations
```

Then pristine = governed value → ratchet. Would the source-text test catch it? The test finds `call` = first line with "bounded_iterations(" AND "agent.max_iterations". Then `block = lines[call-6:rebuild]`; asserts "default=agent._salience_operator_iterations" in block. In the mutated version, the call line is `agent.max_iterations = _salience_observer.bounded_iterations(` and the next line `agent.session_id or "", default=agent._salience_operator_iterations` — hmm, the mutation I wrote still passes default=agent._salience_operator_iterations but captures it AFTER. The test's `between` check: `assert not any("agent.max_iterations =" in ln for ln in between)` — between call+1 and rebuild. In my mutation, the capture line `agent._salience_operator_iterations = agent.max_iterations` is between... it contains "agent.max_iterations" but does it contain "agent.max_iterations ="? The line is `agent._salience_operator_iterations = agent.max_iterations` — substring "agent.max_iterations =" requires "agent.max_iterations" followed by " =". Here it's "= agent.max_iterations" at end of line — no " =" after. So the between-check passes. And "default=agent._salience_operator_iterations" still in block. So the source-text test stays GREEN under this capture-order mutation → the ratchet returns and no test reds? Wait — would `test_no_ratchet_across_busy_turns` catch it? That test calls `so.bounded_iterations("s", floor)` directly with a constant floor — it does NOT exercise the call-site capture logic at all. So yes: the capture-order mutation (capture pristine AFTER the governed assignment) is INVISIBLE to both tests. Hmm, but is that mutation realistic? It's a specific reorder. The test honesty section asks: "any guarantee above whose test stays GREEN when its production line is sabotaged (mutation-blind...)". G1's no-compound guarantee: the production line `if getattr(...) is None: agent._salience_operator_iterations = agent.max_iterations` placed BEFORE the assignment is load-bearing. Moving it after the assignment reintroduces the ratchet, and the source-text pin (which only checks the default= token and the between-lines) stays green, and the cadence test bypasses the call site. That's a legitimate test-blindness finding. Severity: the guarantee is G1 (no compounding). The blind spot is real but requires a specific mutation. I'd rate MEDIUM as a test-honesty finding.

Actually wait, let me double-check the between-check more carefully. The test:

```python
call = next(i for i, ln in enumerate(lines) if "bounded_iterations(" in ln and "agent.max_iterations" in ln)
rebuild = next(j for j, ln in enumerate(lines) if j > call and "IterationBudget(agent.max_iterations)" in ln)
assert rebuild - call <= 12
between = lines[call + 1:rebuild]
assert not any("agent.max_iterations =" in ln for ln in between)
```

In the mutated order:
```python
try:
    from ... import ...
    agent.max_iterations = _salience_observer.bounded_iterations(   # <- call line
        agent.session_id or "", default=agent._salience_operator_iterations
    )
    if getattr(agent, "_salience_operator_iterations", None) is None:
        agent._salience_operator_iterations = agent.max_iterations
except Exception:
    ...
agent.iteration_budget = IterationBudget(agent.max_iterations)   # <- rebuild
```
between = lines after call, before rebuild: includes `agent._salience_operator_iterations = agent.max_iterations`. Check: does `"agent.max_iterations ="` appear as substring? The line: `        agent._salience_operator_iterations = agent.max_iterations`. Substrings: "agent.max_iterations" appears at the end, followed by nothing (end of line). "agent.max_iterations =" needs " ="" after. Not present. ✓ test stays green. Also `block = lines[call-6:rebuild]` contains "default=agent._salience_operator_iterations" ✓ and "default=agent.max_iterations" not present ✓. So YES — green under the ratchet mutation. Confirmed test-blindness finding. Also note the capture line could alternatively be mutated to `agent._salience_operator_iterations = agent.max_iterations` capturing post-value... same thing.

Hmm, but hold on — would the ratchet actually manifest? Turn 1: pristine unset → capture happens AFTER assignment in mutated code. Turn 1 assignment: bounded_iterations returns default... but default=agent._salience_operator_iterations which is UNSET on turn 1 → AttributeError! `getattr(agent, "_salience_operator_iterations", None)` in the default expression — no wait, the mutated code passes `default=agent._salience_operator_iterations` directly (attribute access, not getattr) → AttributeError on turn 1 → caught by `except Exception` → skipped → no governance at all, and `agent.max_iterations` unchanged... wait no, the assignment line itself: `agent.max_iterations = bounded_iterations(agent.session_id or "", default=agent._salience_operator_iterations)` — evaluating the default argument raises AttributeError BEFORE bounded_iterations is called → caught → max_iterations unchanged. So turn 1: no governance. Turn 2: attribute still unset (capture line never reached because the exception happened before it... wait in my mutation the capture is AFTER the assignment, so turn 1 raises at the assignment, capture never runs, turn 2 same → governance permanently disabled, fail-open. Hmm, so that specific mutation self-neuters (fail-open, no ratchet). 

Let me construct the dangerous mutation properly: keep capture-first but capture from the WRONG source... Actually the realistic sabotage the test claims to pin is `default=agent.max_iterations` (banned explicitly ✓ caught). The capture-order one self-neuters. What about capturing pristine but UPDATING it when max_iterations changes? E.g. someone "fixes" the stale-pristine issue by re-capturing each turn: `agent._salience_operator_iterations = agent.max_iterations` unconditionally BEFORE the assignment — then default = last turn's governed value → ratchet 25→40→40... wait: turn 2: pristine = 40 (turn 1's governed... turn 1 applied 25). Hmm: turn 1: pristine=25, applied 25 (nothing recorded). Turn 2: re-capture pristine = agent.max_iterations = 25 → applied 40 (busy turn 1). Turn 3: re-capture = 40 → floor 40 → applied 40+15=55? Window [40, 55], saturated → 55. Turn 4: re-capture 55 → 70. RATCHET. Does the source test catch unconditional re-capture? The line `agent._salience_operator_iterations = agent.max_iterations` placed BEFORE the call: `block = lines[call-6:rebuild]` — the banned token is "default=agent.max_iterations" — not present. The between-check only covers lines AFTER call. So unconditional re-capture before the call → test GREEN, ratchet LIVE. And `test_no_ratchet_across_busy_turns` bypasses the call site → GREEN. So the no-compound guarantee (G1) is pinned only against the specific `default=agent.max_iterations` regression, not against re-capture mutations. This is a legitimate MEDIUM test-honesty finding with a concrete mutation. Good.

Actually, hmm, wait. Let me reconsider: is unconditional re-capture a "sabotage of a production line"? The production line is `if getattr(agent, "_salience_operator_iterations", None) is None:`. Deleting the guard (making it unconditional) is a one-line mutation. Result: ratchet. Tests green. Yes, valid finding.

### Now the biggest hunt: is there a REAL ratchet/feedback path in the CURRENT code?

The pristine is captured once per agent from `agent.max_iterations`. The assignment feeds governed values into `agent.max_iterations`. The capture is guarded by `is None`. So within an agent's lifetime, pristine never changes. ✓ no ratchet in-process.

BUT — cross-RESTART within the same... no, new process → new agent → fresh capture from config. ✓.

What about the box? server.py writes config with MAX_ITERS=25 fixed. Agent captures 25. ✓.

Hmm, what about this: `agent._salience_operator_iterations` — could the host's own code persist/restore agent attributes (e.g., session serialization that pickles agent state including the governed max_iterations AND _salience_operator_iterations)? If agent state is pickled after governance and restored, pristine restores correctly (it was captured pre-governance). ✓. If ONLY max_iterations is restored (governed value) into a NEW agent without the pristine attribute... then capture = governed value → floor anchored to a governed output! Is there such a restore path? Not shown in the material. Speculative. LOW at most.

### The `_operator_budget` chain — pollution hunt

`_operator_budget()` reads config keys in order: agent.max_iterations, max_iterations, agent.iteration_budget, agent.max_turns, max_turns. These are CONFIG values, never governed outputs. The governed value lives only on `agent.max_iterations` (runtime attribute), not written back to config. Is there any path where the governed `agent.max_iterations` gets PERSISTED to config.yaml? If the host has a "save current settings" command... not shown. Speculative.

Hmm wait — the box! server.py `_write_hermes_config` writes `agent.max_iterations: MAX_ITERS` (a constant 25), not the governed value. ✓.

### The finalize-on-read floor vs. the recorded directive — G3

"a zero-event window's directive is EXACTLY the floor; the finalize-on-read floor is the session's pristine operator budget". 

In `_resolve_bounded`, floor = default if valid else `_operator_budget()`. The call site always passes pristine (valid positive int, assuming operator config sane). ✓.

But hmm — what if pristine is valid but the WINDOW being closed belongs to a session whose... no, floor is per-call. ✓.

**Zero-event window via finalize-on-read:** `_close_locked(window, budget=floor)`: events==0 → no attention → interpret with mapped signals only (none) → budget = _scale(0.0, floor, floor+H) = floor + int(0.5)?? WAIT. `_scale(frac, lo, hi) = lo + _round_half_up(frac * (hi - lo))` = lo + int(0 * span + 0.5) = lo + int(0.5) = lo + 0 = lo. ✓ floor exactly. Phew — int(0.5) = 0. ✓.

Test `test_quiet_turn_stays_exactly_at_floor` asserts 10 and empty signal record. ✓. Mutation check: `if window.events > 0` mutated to `>= 0` → attention with influence 0.0 published → agg: weight = 1.0 > 0 → agg[ATTENTION] = 0.0 → budget = floor still! Budget assertion stays green BUT the signal-record assertion (`== []`) reds. ✓ caught (this was fixed finding e).

### The influence aggregation with confidence

One attention signal, confidence 1.0. agg = influence. ✓. But what if a mapped signal... no mapped signal uses ATTENTION facet. ✓ sole publisher.

### `_record` — events increment and the mapper

```python
window.events += 1
for signal in mapper(kwargs, window.subject):
```

If the MAPPER raises (e.g., `_map_tool_call` on weird kwargs — it str()s everything, shouldn't raise)... if it did, events already incremented but the exception propagates out of `_record` → caught by `observe_lifecycle`'s (Exception, SystemExit) → logged. Event counted, no signals. Consistent with "attributed activity" semantics. ✓.

### Double-count via `_close_locked` publish + `_record` publish

The attention signal is published once in `_close_locked`. The mapped signals were published in `_record`. `signals = list(window.signals)` + attention. interpret sees each mapped signal once + attention once. The BUS also has each once. ✓ no double-count.

BUT WAIT — what about signals published to the bus by `_record` for window turn N-1, and then the window rolls... no. Each window's signals list is its own. ✓.

**Hmm, here's one: the attention signal is published to the bus but NOT appended to `window.signals`. So if `_close_locked` is somehow re-entered... it's idempotent (closed flag). ✓. But the bus record: signals_for(subject) returns mapped + attention. Test `test_one_attention_signal_per_window_on_bus` asserts exactly 1 attention. ✓.**

### The resume path and `_budget_from_disk` — a REAL issue?

```python
if session_id in _BUSES:
    return None
```

In-process authority is `_LAST_DIRECTIVE`. If `_LAST_DIRECTIVE` empty but bus cached → last close FAILED → return None → default. ✓ (documented).

Cold path: constructs bus (replay+verify), reads `directives[-1][1]` — the LAST directive. Deep-copies, promotes, returns budget.

**Issue: the last directive on disk might be from a turn whose window was floored at a STALE operator budget (config changed between processes). Documented resume caveat. ✓.**

**Issue: `directives[-1]` is the last directive REGARDLESS of subject. If the bus has directives from multiple... one session = one subject per turn, many turns. Last = latest turn. ✓.**

**Issue: what if the last directive on disk is a HARD DENY (subject="")? `_directive_budget` → subject falsy → None → default. ✓.**

### Now — G2 never-brick, applied budget < 1 hunt

`bounded_iterations` returns: default (validated ≥ 1) or recorded budget (validated ≥ 1 by `_directive_budget`) — verbatim, never re-clamped (G6). Recorded budget ∈ [floor, floor+H] where floor ≥ 1. ✓ ≥ 1.

The call site: `agent.max_iterations = bounded_iterations(...)`. If bounded_iterations somehow returns 0... it can't (default ≥ 1 checked; recorded ≥ 1 checked). ✓.

`IterationBudget(agent.max_iterations)` — outside the try. If max_iterations is valid, fine.

### The `_looks_off` / `_config_flag` for consume_compute — live read each call. Fine.

### HONESTY hunt — docs

Module docstring: "within a process, never fewer than the operator's own budget (A4 floor) and never more than floor + headroom (signed policy ceiling); a resumed session's first turn reapplies the LAST RECORDED budget, which can sit anywhere in the prior process's window".

Hmm — "never more than floor + headroom" within a process: applied = recorded ∈ [floor, floor+H] where floor = pristine. ✓. But the RESUME caveat: first resumed turn can apply up to prior floor + prior H. If current pristine < prior floor... documented ("above OR below the current operator setting" in config example). ✓.

Config example: "next = floor + round_half_up(min(1, events/8) * headroom)". ✓ matches. "saturating at 8 events" ✓. "Bad values (bool, negative, float, garbage) are treated as 0" ✓. "a plain int >= 0 or a quoted digit-string like "15" is honored (capped at 1,000,000)" ✓. "Resolved once per process" ✓.

Hmm — "The budget is never below the floor (a quiet turn changes nothing)". "a quiet turn changes nothing" — a quiet turn yields the floor; if the previous turn was busy (applied 40) and this turn is quiet... the NEXT turn decays to 25. "changes nothing" is loose phrasing — a quiet turn's directive = floor, which may LOWER the applied budget back to floor. Actually "a quiet turn changes nothing" could be misread as "the budget stays where it was". The accurate statement: a quiet turn's directive sits exactly at the floor. Minor wording. The module docstring says "quiet turns decay back to the operator floor" (turn_context comment) — that's accurate. The config's "(a quiet turn changes nothing)" — hmm, it means "changes nothing relative to the operator's own setting" i.e. no boost. Borderline. LOW.

Box settings.yaml: "the transparency panel shows the real movement between the floor and floor + headroom" — is there a transparency panel? Not in material. Can't verify. Skip.

Box settings: "It DOES override max_iterations, so compute is governed on its own channel — and with `compute_headroom` below, a busy turn genuinely raises the next turn's budget above the floor." ✓ accurate.

"compute_headroom: 15 # extra iterations a busy turn can buy for the NEXT turn (budget moves between 25 and 40; 0 = pinned/inert)" ✓.

Module docstring: "so a busy turn can buy the NEXT turn up to ``compute_headroom`` extra iterations". ✓.

`_Window` docstring: "including events whose mapper returned nothing (e.g. a read-only tool call) and events whose signal publish failed". ✓ matches code.

`bounded_iterations` docstring: "With the default salience.compute_headroom: 0 the directive echoes the operator's own budget (pinned window) and this is behavior-preserving". Hmm — "echoes the operator's own budget": with headroom 0, directive = floor = pristine default passed by call site. Applied = pristine = operator budget. ✓.

"never below that floor, never above floor + H (the signed policy window is the cap)". ✓.

The module docstring's produce-only claim: "The observer half never feeds back into what the agent does" — well, now the ATTENTION signal is derived from activity and DOES affect the next turn's budget via the consumer. But the docstring distinguishes observer (records) vs consumer (applies). The feedback loop activity→budget is the WHOLE POINT of the PR and is documented. ✓.

### Now let me hunt the SATURATION / influence round-trip once more

`_ATTENTION_SATURATION_EVENTS = 8`. influence = min(1.0, events/8). 

Test `test_saturation_boundary_exact`: n=7, headroom 8, floor 10: 7/8=0.875, 0.875*8=7.0, int(7.5)=7 → 17 ✓. n=8: 1.0*8=8, int(8.5)=8 → 18 ✓.

`test_budget_moves_and_rounds_half_up`: 4 events, headroom 5: 0.5*5=2.5, int(3.0)=3 → 13 ✓ (banker's would give 12 — pinned ✓).

Float exactness: 0.875*8 = 7.0 exactly (0.875 = 7/8 exact; 7/8*8 = 7 exact). int(7.0+0.5)=7 ✓. 0.5*5=2.5 exact. ✓.

What about headroom values where frac*headroom is NOT exact? frac is always k/8 (dyadic). k/8 * H for any int H: k*H/8 — k*H ≤ 8*1e6 = 8e6 < 2^53, and division by 8 is exact in binary floating point (power of 2). So frac*H is ALWAYS exact. +0.5 exact (value < 2^53). int() exact. NO float drift possible within the cap. The cap (1e6) makes this airtight. ✓ good design.

Hmm wait — is it though? k/8 where k ∈ {1..7}: 1/8=0.125 exact, 3/8=0.375 exact, 5/8=0.625, 7/8=0.875 — all dyadic ✓. events/8 for events > 8 → min(1.0, ...) → 1.0 ✓. So influence ∈ {0.125, 0.25, ..., 1.0} all dyadic ✓. And agg = influence (single signal, conf 1.0): sum(influence*1.0)/1.0 — exact ✓.

### The `_subject` hashing — turn_id aliasing. Fixed already (hashed not truncated). ✓.

### Let me reconsider `_open_window` rollover close and the floor used there

Rollover close passes budget=None → `_operator_budget()`. When does rollover close happen in production? Only if pre_llm_call for turn N+1 fires while turn N's window is still open — i.e., the consumer did NOT run between turns (consume_compute off, or salience disabled mid-session, or bounded_iterations returned early... wait, bounded_iterations with consume off returns default WITHOUT finalizing (the `if not _consume_enabled() or not session_id: return default` is before `_resolve_bounded`). So with consume off, windows close at rollover with `_operator_budget()` floor. Recorded directives floored at config-derived budget. If consume is later enabled (live config read!) — `_config_flag` reads config EVERY call, not memoized. So an operator could toggle consume_compute mid-process by editing config.yaml! Then the next consume reads a directive floored at `_operator_budget()` (possibly ≠ pristine). E.g., env override HERMES_MAX_ITERATIONS=500, config agent.max_iterations=25: pristine=500, but rollover closes floored at 25. Toggle consume on → applied = 25+H-ish < pristine floor 500! G1 says "never falls below the operator floor" within a process. VIOLATION? Hmm — but is toggling consume mid-process a supported flow? `_config_flag` live-reads, so yes it's possible. But wait — with consume off, does the host still apply bounded_iterations? It returns default=pristine → max_iterations=pristine=500. Then consume toggled on: next turn, `_resolve_bounded`: window open (turn N-1's, still open because consume was off... wait no — with consume off, `bounded_iterations` returns before `_resolve_bounded`, so windows are NOT closed at turn start; they close at ROLLOVER (next pre_llm_call) with `_operator_budget()` floor=25. So when consume turns on, `_LAST_DIRECTIVE` holds a directive floored at 25 (window [25, 25+H]). Applied: up to 25+H. If H=15: 40 < 500. The applied budget (40) falls below the operator's real floor (500). G1 violation within a process!

Hmm, but hold on — is this realistic? The env override + mid-process config toggle + consume flip. The `_operator_budget` docstring explicitly says: "The HERMES_MAX_ITERATIONS env override is deliberately not read here: this floor only covers rollover / session-end closes, and the consumer's finalize-on-read floors at the caller's fully-resolved default anyway." So they KNEW rollover closes use a different floor. The scenario requires consume off → on mid-process. Is that in scope? The kill switch is documented as live (`_config_flag` reads fresh). Actually is it? The config example says "Resolved once per process (like the operator budget); a live config edit takes effect on restart" — that's about COMPUTE_HEADROOM. For the kill switches, no such statement — `_config_flag` live-reads. Hmm, but actually does it? `read_raw_config_readonly()` — reads the file each time? "readonly" suggests a fresh read. So kill switches are live. 

But actually — wait. Even WITHOUT toggling: consume on from the start. Turn 1: consumer runs (nothing to close), pre_llm_call opens window u1. Turn 2: consumer closes u1 at floor=pristine ✓. So with consume ON, rollover closes only happen if... the consumer's `_resolve_bounded` runs at EVERY turn start (consume on). Windows always closed by finalize-on-read before the next pre_llm_call. Rollover close only for the LAST turn at session end (`_close_session` → `_close_locked(window)` budget=None → `_operator_budget()` floor) — but that directive is never consumed in-process (session over); it could be consumed on RESUME via `_budget_from_disk`. So: session ends with a busy turn; session-end close floors at `_operator_budget()` (config-derived, possibly 25 while real budget 500 via env). Resume: new process, pristine=500 (env), turn 1 applies recorded 25+H... wait no — recorded directive from session-end close: window [25, 25+H], saturated → 25+H=40. Applied 40 < pristine 500. The resume caveat documents "can sit anywhere in the prior window, above OR below the current operator setting" — but here it's below the CURRENT operator setting because the PRIOR close used the WRONG floor (config 25 vs real 500), not because of a config change. The docstring for `_operator_budget` acknowledges the env blind spot and accepts it ("deliberately"). Is this a finding? The PR description item 4 says `_operator_budget` now reads agent.max_turns/max_turns "the host's REAL operator chain" — but NOT the env override (deliberate, documented). The session-end close floor can be wrong relative to the real budget when the env override is the source of truth. Documented as deliberate. Hmm. The guarantee G3: "the finalize-on-read floor is the session's pristine operator budget" — that's about finalize-on-read, which IS pristine. Session-end close floor is `_operator_budget()` — a documented exception. I'd call this out only as a borderline/note. Actually the prompt says hunt "every floor derivation for pollution by a previously-applied value" — `_operator_budget()` is config-derived, NOT polluted by applied values. ✓ no pollution. The env blind spot is documented. Skip or LOW.

### Now, a deeper A3 hunt — the "two windows" scenario

Can `_WINDOWS[session_id]` at consumer time be a window from turn N-1 that was opened but the turn had ZERO events, while turn N-2 was busy? Turn N: closes N-1 (quiet) → directive = floor → applied floor. ✓ correct semantics (most recent recorded turn).

### The `_LAST_DIRECTIVE` write timing

`_close_locked` writes `_LAST_DIRECTIVE[session_id] = directive` AFTER successful emit. ✓. On failure pops. ✓.

### Subtle: `_close_locked` publishes attention BEFORE `_issue_template`/`interpret`. If interpret hard-denies (template regression — but probe would have caught... the probe runs on first CONSUME, but produce-path closes (rollover/session-end) NEVER run the probe! If the template is incoherent (construction regression), produce closes hard-deny and record deny directives; the consumer's deny-guard treats them as absent → default. Fail-open ✓. And the probe logs loudly on first consume ✓.

### `_ensure_template_valid` runs INSIDE `_resolve_bounded` under _LOCK, calling `_issue_template` → `_compute_headroom()` (memoized, reads config under lock first time) and `_operator_budget()` (memoized). ✓ no lock issues (no re-acquisition).

### Deadlock hunt: `_resolve_bounded` holds _LOCK for the whole read-modify-read including `_budget_from_disk` which does FILE I/O (replay). A concurrent hook blocks on _LOCK during I/O. Single-threaded contract; the lock serializes. Slow but safe. ✓ (H2 behavior anyway).

### Now — TEST HONESTY deep hunt

`test_budget_moves_and_rounds_half_up`: 2 write_file (mapped MEMORY) + 2 read_file (unmapped) = 4 events. Asserts 13. Mutation: counting only mapped signals (events=2 → influence 0.25 → 10+int(1.25+0.5)=10+1=11) → red ✓. Mutation: int() truncation → 12 → red ✓. Mutation: banker's round → 12 → red ✓. Good.

`test_finalize_on_read_floor_is_caller_default`: config agent.max_iterations=25, headroom 10, default=12, 8 events → 22. "Kills: deriving the floor from _operator_budget() on the read path" → would give 35 → red ✓. Good.

`test_three_turn_cadence_distinct_budgets`: asserts (10, 28, 34) and durable records 28, 34. Kills deleting finalize-on-read: then u1 closes at rollover (when _open("s","u2") is called) with budget=None → `_operator_budget()` = 25 (config agent.max_iterations=25) → saturated → 33. applied2 would be... wait, without finalize-on-read, at applied2 time u1 is still OPEN → `_LAST_DIRECTIVE` empty → `_budget_from_disk`? session in _BUSES (bus created by _record publishes) → return None → default 20. So applied2=20 ≠ 28 → red ✓.

`test_rollover_close_floors_at_operator_max_turns`: config agent.max_turns=40, headroom 8, 8 events, rollover close → 48. Kills the max_turns blind spot (would be 25+8=33) ✓.

`test_operator_budget_precedence_table`: includes `({"agent": {"max_iterations": 7, "max_turns": 40}}, 7)` — rig key wins ✓. `({"agent": {"max_turns": True}}, 25)` — bool rejected ✓. `({"agent": {"max_turns": 0}}, 25)` — zero rejected ✓. Good.

Hmm — one missing case: `agent.max_turns` as a FLOAT (40.5)? `isinstance(node, int)` rejects → falls through to `max_turns` root → not present → 25. Fine, fail-safe. And negative? `node > 0` rejects ✓.

`test_one_attention_signal_per_window_on_bus`: asserts len==1, influence==3/8, confidence 1.0, provenance exactly ("attention","events:3"), tokens ≤ 128. Mutation: per-event attention → 3 → red ✓. Mutation: provenance with tool name → red ✓. Good.

`test_attention_publish_failure_falls_back_to_floor`: monkeypatches `_bus_for` to return a proxy whose publish raises but emit delegates. Asserts applied==10 (floor) and `_LAST_DIRECTIVE["s"] is not None`. 

WAIT — mutation check: if the code interpreted the attention WITHOUT publishing (the fence violation), applied would be 15 → red ✓. If the close CRASHED on publish failure → `_LAST_DIRECTIVE` empty → default 10 → applied==10 green BUT `_LAST_DIRECTIVE["s"]` → KeyError → red ✓. Good test.

Hmm, but subtle: the proxy's `publish` raises for ALL signals — including the mapped signals from `_record`! `_record_tool` with default tool_name="write_file"... the test uses `_events("s", "u1", 4)` → write_file → mapped MEMORY signal → `_record` calls `_bus_for(session_id).publish(signal)` — but wait, `_bus_for` is monkeypatched AFTER `_events`? Let me re-read:

```python
_open("s", "u1")
_events("s", "u1", 4)
real_bus = so._bus_for("s")
class _PublishFails: ...
monkeypatch.setattr(so, "_bus_for", lambda sid: _PublishFails(), raising=False)
assert so.bounded_iterations("s", 10) == 10
```

Events recorded BEFORE the monkeypatch → mapped signals published to real bus, window.signals has 4 MEMORY signals. Then publish fails only for the ATTENTION signal at close. interpret with 4 MEMORY signals, no ATTENTION → budget = floor 10 ✓. applied==10 ✓. And the directive emit succeeds via real_bus. `_LAST_DIRECTIVE` set ✓. Good.

But hmm — what does the MEMORY aggregation do? retention only, not budget ✓.

`test_bad_headroom_full_path_stays_inert`: bad values, asserts 10 and durable directive 10. Distinguishes inert from swallowed raise ✓ (fixed finding e).

`test_e2e_headroom_moves_iteration_budget`: real dispatch chain. 4 events (write, read, edit, run_shell error) → influence 0.5, headroom 15, floor 25 → 25 + int(7.5+0.5) = 33 ✓. Asserts IterationBudget(33).max_total == 33. Good.

Hmm — wait, `run_shell` with error → VERIFICATION signal. edit_file → MEMORY. write_file → MEMORY. read_file → unmapped. 4 events ✓.

`test_headroom_parsing_table`: includes (10**400, 10**6) — the absurd int capped ✓. (" 15 ", 15) whitespace ✓. (3.5, 0) float ✓. (True, 0) bool ✓. Good.

Hmm — `("-15", ...)`? Not in the table! `int("-15".strip())` = -15 → `parsed if parsed >= 0 else 0` → 0 ✓ handled but untested. Minor test gap, behavior correct. Also `"+15"`? int("+15")=15 honored. Fine.

What about `"15.5"`? int() raises ValueError → 0 ✓. `"0x15"`? ValueError → 0 ✓.

`test_template_probe_validates_widened_shape` (b): monkeypatches `_compute_headroom` to -1 → min 25 > max 24 → verify False → error logged. But WAIT — `_issue_template` computes `floor + _compute_headroom()` = 25 + (-1) = 24 < 25 → verify_policy's `0 <= min <= max` fails ✓. Good.

### The conftest gate

Suite-wide: `salience_enabled` → False, `_reset_for_tests()` per test. The headroom tests opt back in via `_use_config` monkeypatching `so.salience_enabled` → True. ✓.

Hmm — `_use_config` monkeypatches `so.salience_enabled` but NOT `_consume_enabled`... `_consume_enabled` calls `salience_enabled()` (monkeypatched True) and `_config_flag("consume_compute", True)` → reads config via `read_raw_config_readonly` (monkeypatched to return cfg) → salience block present, consume_compute absent → default True ✓.

### Now let me look for the REALLY subtle stuff — the things two reviews missed.

**1. The `_record` events increment vs. the `_close_locked` attention synthesis — the publish ordering on the bus.** The attention signal is published at CLOSE, AFTER all of the window's mapped signals. On the durable record, the attention signal appears after the turn's signals, right before the directive. Fine for audit.

**2. `_close_locked` — the attention signal's subject.** `window.subject` — same as policy subject ✓ (interpret filters by subject match).

**3. What if `window.events > 0` but ALL mapped signal publishes failed AND attention publish succeeds?** signals = [attention] → budget moves ✓ on record ✓.

**4. THE BIG ONE — let me re-examine `_resolve_bounded`'s floor choice when `default` is invalid:**

```python
floor = default if (isinstance(default, int) and not isinstance(default, bool) and default > 0) else _operator_budget()
```

But `bounded_iterations` returns EARLY for invalid default (`return default` before `_resolve_bounded`). So `_resolve_bounded` only ever sees valid default. The else branch is dead code defensively. ✓ harmless.

**5. Cross-session pristine: one agent, session ends (on_session_end frees registries), NEW session same agent.** Pristine already captured ✓ same operator ✓. Turn 1 of new session: no window/directive/bus → disk → no file (new session hash) → None → default=pristine ✓.

**6. What if `agent.session_id` is None at consumer time?** `agent.session_id or ""` → "" → `bounded_iterations("", default)` → `if not _consume_enabled() or not session_id: return default` ✓ no-op.

**7. `_budget_from_disk` — the promoted payload and SUBSEQUENT finalize-on-read.** Cold resume turn 1: `_resolve_bounded`: window None; `_LAST_DIRECTIVE` empty → `_budget_from_disk` → recovers 40, promotes to `_LAST_DIRECTIVE["s"] = payload`. Applied 40 ✓ (resume caveat). Turn 1's window opens (pre_llm_call), events. Turn 2: `_resolve_bounded`: closes turn 1's window at floor=pristine → `_LAST_DIRECTIVE` OVERWRITTEN with turn 1's directive ✓. Applied turn 1's directive ✓. No stale promotion linger ✓.

BUT — what if turn 1 (resumed) ABORTS
