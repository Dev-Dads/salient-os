# Red-team (consumer, pass=general): z-ai/glm-5.2

_finish=length seconds=210.2 usage={'prompt_tokens': 23423, 'completion_tokens': 12000, 'total_tokens': 35423, 'cost': 0.04684148, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.04684148, 'upstream_inference_prompt_cost': 0.01780148, 'upstream_inference_completions_cost': 0.02904}, 'completion_tokens_details': {'reasoning_tokens': 12141, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me carefully analyze this code for the specific attack vectors listed.

Let me go through each category systematically.

## 1. FAIL-OPEN / NEVER-BRICK

The consumer `bounded_iterations` catches `(Exception, SystemExit)` and returns `default`. Let me trace the paths:

- `_consume_enabled()` → `salience_enabled()` → catches `(Exception, SystemExit)` → returns False. `_config_flag` catches `(Exception, SystemExit)` → returns False. OK.
- `_resolve_bounded` holds `_LOCK`, calls `_ensure_template_valid`, `_operator_budget`, `_close_locked`, `_LAST_DIRECTIVE.get`, `_budget_from_disk`.
- `_budget_from_disk` calls `get_hermes_home` (could SystemExit), `_bus_for` (could SystemExit from `get_hermes_home`), `open(path)`, `json.loads`, `bus.directives_for`.

Wait - `_budget_from_disk` does `from hermes_constants import get_hermes_home` and then `Path(get_hermes_home())`. If `get_hermes_home` raises SystemExit, that's inside `_resolve_bounded` which is inside the `with _LOCK:` block, which is inside the `try` in `bounded_iterations` that catches `(Exception, SystemExit)`. So SystemExit IS caught. Good.

But wait - there's a subtle issue. The `with _LOCK:` context manager. If an exception is raised inside the `with _LOCK:` block, the lock is released by the context manager's `__exit__`. That's fine - `threading.Lock` releases on any exception. OK.

Now, what about the return value? `bounded_iterations` returns `budget if budget is not None else default`. `_directive_budget` returns either `None` or a positive int (>= 1). So the return is either `default` or a positive int. Can it return < 1? Only if `_directive_budget` returns something < 1, but it checks `budget < 1` → returns None. So no. Can it return a non-int? `_directive_budget` checks `isinstance(budget, int)` and not bool. So no. Good.

But wait - what if `default` itself is < 1? The caller passes `agent.max_iterations`. If `agent.max_iterations` is 0 or negative, `bounded_iterations` returns it as-is (it's an int, not bool). Then `IterationBudget(0)` would brick the agent. But that's the caller's value, and the guarantee says "returns a budget < 1 / non-int that would set max_iterations to 0 and brick the agent" - the consumer is supposed to never return < 1. But if `default` is 0, the consumer returns 0. Is that a violation?

Looking at the guarantee: "must NEVER return a budget < 1 (that would brick the agent at max_iterations 0)." But the consumer returns `default` unchanged when there's no directive. If `default` is 0, it returns 0. However, the consumer's contract is to fail OPEN to `default` - it's not supposed to fix up a bad `default`. The `test_non_int_default_returned_unchanged` test confirms this is intentional for non-int. For int < 1... hmm.

Actually, looking more carefully: `bounded_iterations` first checks `if not isinstance(default, int) or isinstance(default, bool): return default`. So if `default` is 0 (a valid int, not bool), it proceeds. Then `_resolve_bounded` is called with `default=0`. In `_resolve_bounded`, `floor = default if (isinstance(default, int) and not isinstance(default, bool) and default > 0) else _operator_budget()`. So `floor = _operator_budget()` when default is 0. That's used for the policy floor in `_close_locked`. Then if there's no directive, it returns `default` (0). If there IS a directive, it returns the directive's budget (>= 1). So if default is 0 and there's a directive, the agent gets a positive budget. If default is 0 and there's no directive, the agent gets 0. But that's the operator's own value - the consumer didn't make it worse. This seems like intended behavior.

Let me look more carefully at the SystemExit containment. The test `test_bounded_iterations_never_raises_on_broken_home` tests that `get_hermes_home` raising SystemExit is contained. But let me check: does the SystemExit from `get_hermes_home` in `_budget_from_disk` actually get caught?

`bounded_iterations` → `try: ... _resolve_bounded(...) ... except (Exception, SystemExit): return default`. Yes, `_resolve_bounded` calls `_budget_from_disk` which calls `get_hermes_home`. If that raises SystemExit, it propagates up through `_resolve_bounded` (which has no try/except) to `bounded_iterations`'s try/except. Caught. Good.

But wait - `_resolve_bounded` is inside `with _LOCK:`. The SystemExit propagates through the `with` block, which releases the lock, then to `bounded_iterations`'s except. Lock is released. Good.

What about `_ensure_template_valid`? It catches `(Exception, SystemExit)` internally. Good.

What about `_close_locked`? It catches `(Exception, SystemExit)` internally. Good. But if `_close_locked` catches an exception, it doesn't set `_LAST_DIRECTIVE`. So `_LAST_DIRECTIVE.get(session_id)` returns None, and we fall through to `_budget_from_disk`. That's the intended behavior.

Hmm, but there's a subtle issue: if `_close_locked` fails (catches exception), `window.closed` is already set to `True` (it's set before the try block). So the window is marked closed but no directive was emitted. Then `_LAST_DIRECTIVE` is empty, so we go to `_budget_from_disk`. But `_budget_from_disk` checks `if session_id in _BUSES: return None`. If the bus was already created (e.g., by a previous `_record` call), then `_budget_from_disk` returns None, and we get `default`. That's correct - a failed close means no directive, so default.

But what if the bus was NOT yet created? Then `_budget_from_disk` tries to create it via `_bus_for`, which creates the bus and replays. But the window was just closed (failed), so there might be signals on the bus but no directive. `_budget_from_disk` reads the last directive from the file. If there are no directives, `last_subject` is None, returns None → default. If there IS a previous directive (from a prior turn), it reads that. But that's a 2-turns-stale directive! Wait, is this the already-fixed issue (a)?

Let me re-read the already-fixed (a): "the restart fallback used to re-parse the raw JSONL through a possibly-cached (unverified) bus and could apply a 2-turns-stale budget after a failed close — now gated to the COLD path only (`session_id not in _BUSES`) with the value taken from the verified `directives_for`."

So the fix was to gate `_budget_from_disk` to only run when `session_id not in _BUSES`. Let me check: yes, `_budget_from_disk` starts with `if session_id in _BUSES: return None`. So if the bus is cached (which it would be if any signals were published for this session), the disk fallback is skipped. Good.

But what about the case where the bus is NOT cached but there IS a file? That's the cold restart path. The bus gets created, replays+verifies, and then reads the last directive. That's correct.

OK, let me think about a different scenario. What if `_close_locked` fails AFTER `_bus_for` was called (inside `_close_locked`)? Let me trace `_close_locked`:

```python
def _close_locked(window, budget=None):
    if window.closed: return
    window.closed = True
    try:
        if budget is None: budget = _operator_budget()
        policy = issue_policy(...)
        directive = interpret(policy, tuple(window.signals), _POLICY_KEY)
        _bus_for(window.session_id).emit(directive)  # this creates the bus if needed
        _LAST_DIRECTIVE[window.session_id] = directive
    except (Exception, SystemExit):
        logger.warning(...)
```

If `_bus_for` succeeds but `emit` fails, the bus is now in `_BUSES`. The exception is caught. `_LAST_DIRECTIVE` is not set. Then back in `_resolve_bounded`, `_LAST_DIRECTIVE.get(session_id)` is None, so we call `_budget_from_disk`. But `session_id in _BUSES` is True (the bus was just created), so `_budget_from_disk` returns None → default. Good.

What if `_bus_for` itself fails? Then the bus is NOT in `_BUSES`. Exception caught. `_LAST_DIRECTIVE` not set. `_budget_from_disk` called, `session_id not in _BUSES`, so it tries to create the bus again. If the file exists, it replays. If the file doesn't exist, `path.exists()` returns False, returns None → default. If the file exists but is corrupt, replay raises, caught by `bounded_iterations` → default. OK.

Hmm, but there's a subtle issue here. If `_bus_for` fails in `_close_locked` (say, `get_hermes_home` raises SystemExit), that SystemExit is caught by `_close_locked`'s `except (Exception, SystemExit)`. Then `_budget_from_disk` is called, which ALSO calls `get_hermes_home` - which raises SystemExit again. This time it's NOT caught by `_close_locked` (we're not in `_close_locked` anymore), it propagates through `_resolve_bounded` to `bounded_iterations`'s `except (Exception, SystemExit)`. Caught, returns default. OK, still safe.

Let me look at the `open(path, encoding="utf-8")` in `_budget_from_disk`. If the file is deleted between `path.exists()` and `open()`, `open()` raises `FileNotFoundError`, which propagates to `bounded_iterations`'s except. Caught → default. OK, TOCTOU but fail-safe.

Now let me think about whether there's any path where the consumer returns a non-int or < 1...

`_directive_budget` returns either None or an int >= 1. `bounded_iterations` returns `budget if budget is not None else default`. `default` is checked to be an int (not bool) at the top. So the return is either `default` (an int, could be any int including 0 or negative) or a positive int >= 1.

If `default` is negative? `bounded_iterations` checks `isinstance(default, int) and not isinstance(default, bool)` - a negative int passes. Then `_consume_enabled()` is called, then `_resolve_bounded(session_id, default)`. In `_resolve_bounded`, `floor = default if (default > 0) else _operator_budget()`. So floor is `_operator_budget()` (positive). Then if there's a directive, returns the directive's budget (>= 1). If not, returns `default` (negative). So the agent gets a negative max_iterations. But that's the operator's own value - the consumer didn't introduce it. The guarantee says "must NEVER return a budget < 1" but I think this refers to the consumer not INTRODUCING a < 1 budget - it should fail open to default, and if default itself is < 1, that's the caller's problem. The test `test_non_int_default_returned_unchanged` shows the consumer passes through non-int defaults unchanged, so I think passing through a negative int default is consistent.

Actually wait, let me re-read the guarantee: "must NEVER return a budget < 1 (that would brick the agent at max_iterations 0)." This seems absolute. But the consumer returns `default` when there's nothing to apply, and `default` could be 0. However, the consumer can't really fix this - if the operator set max_iterations to 0, the consumer returning 25 would be changing behavior. And the guarantee is about the consumer not BRICKING the agent - if the operator set 0, the operator bricked it. I think this is fine.

## 2. A3 STALENESS

The key question: does `bounded_iterations` at :491 read turn N-1's directive, not turn N's or N-2's?

At :491, the turn_id for turn N has been minted (:439-447) but pre_llm_call hasn't fired yet (:1054). So the only open window is turn N-1's. `_resolve_bounded` calls `_close_locked(window, budget=floor)` which closes turn N-1's window and caches its directive in `_LAST_DIRECTIVE`. Then reads `_LAST_DIRECTIVE.get(session_id)` → turn N-1's directive. Correct.

But wait - what if turn N-1's window was already closed by a previous call? Let me think about the sequence:

Turn 1: `bounded_iterations("s", 10)` - no window open, no directive → returns 10. Then `pre_llm_call` opens window for turn 1.
Turn 2: `bounded_iterations("s", 20)` - window for turn 1 is open, closes it, caches directive. Returns turn 1's budget. Then `pre_llm_call` opens window for turn 2.
Turn 3: `bounded_iterations("s", 30)` - window for turn 2 is open, closes it, caches directive. Returns turn 2's budget. Then `pre_llm_call` opens window for turn 3.

This is correct - turn N applies turn N-1.

But what if `bounded_iterations` is called twice for the same turn? (e.g., a retry or something). First call closes turn N-1's window, caches directive, returns it. Second call: window is closed (or None), `_LAST_DIRECTIVE` has turn N-1's directive, returns it again. Same result. OK, idempotent.

What if `pre_llm_call` for turn N fires before `bounded_iterations` for turn N? That would open turn N's window, closing turn N-1's window (via the rollover close in `_open_window`). Then `bounded_iterations` would see turn N's window open, close it (with no signals!), and return turn N's directive. That would be wrong - turn N applying turn N's own directive. But the ordering guarantee says `bounded_iterations` at :491 runs BEFORE `pre_llm_call` at :1054. The test `test_call_site_precedes_budget_rebuild` checks adjacency but not ordering relative to pre_llm_call. However, the CONTEXT says this is the ordering, and the code structure supports it. So this is a structural guarantee, not something the consumer can enforce.

Hmm, but what about the case where `bounded_iterations` is called and there's NO open window (first turn, or after a close)? Then `_WINDOWS.get(session_id)` is None or closed. `_close_locked` is not called. `_LAST_DIRECTIVE.get(session_id)` returns the last cached directive (from the previous close). That's turn N-1's directive if the previous turn's window was closed. But when was it closed? By the previous turn's `bounded_iterations` call (finalize-on-read) or by `_open_window`'s rollover close. Either way, it's turn N-1's directive. Correct.

Wait, actually there's a subtlety. Let me trace more carefully:

Turn 1:
- `bounded_iterations("s", 10)`: no window, no directive → 10
- `pre_llm_call`: opens window u1

Turn 2:
- `bounded_iterations("s", 20)`: window u1 is open, close it (budget=20), cache directive for u1. Read directive → u1's budget (20, since v0 echoes). Return 20.
- `pre_llm_call`: opens window u2 (rollover close of u1 is no-op, already closed)

Turn 3:
- `bounded_iterations("s", 30)`: window u2 is open, close it (budget=30), cache directive for u2. Read directive → u2's budget (30). Return 30.
- `pre_llm_call`: opens window u3

This matches the test `test_three_turns_read_prior_not_stale`. Good.

But what about a scenario where the window was closed by `_open_window`'s rollover instead of `bounded_iterations`? That would happen if `pre_llm_call` for turn N+1 fires before `bounded_iterations` for turn N+1. But the ordering says `bounded_iterations` runs first. So in normal operation, `bounded_iterations` always closes the prior window before `pre_llm_call` opens the new one.

But what if the consume kill switch is off? Then `bounded_iterations` returns early without closing. The window stays open. Then `pre_llm_call` opens the new window, and `_open_window`'s rollover close fires, closing the prior window at the operator budget (not the caller's default). That's fine - the consume path is off, so no consumption happens. The produce path keeps its own cadence. The test `test_consume_kill_switch_leaves_budget_and_window_untouched` confirms this.

Now, is there a scenario where `bounded_iterations` reads turn N's own directive? That would require turn N's window to be open AND closed before `bounded_iterations` reads. But `bounded_iterations` is the one that closes it. So it closes it, then reads the cache. The cache is set by `_close_locked`. If the window was turn N's (somehow opened before `bounded_iterations`), then closing it would cache turn N's directive, and `bounded_iterations` would return it. But the ordering guarantee says `pre_llm_call` (which opens the window) runs AFTER `bounded_iterations`. So this can't happen in normal operation.

What about a 2-turns-stale read? That would happen if `_LAST_DIRECTIVE` still has turn N-2's directive when `bounded_iterations` for turn N runs. This would happen if turn N-1's window was never closed (so `_LAST_DIRECTIVE` was never updated). But `bounded_iterations` for turn N closes turn N-1's window (if open), which updates `_LAST_DIRECTIVE`. So `_LAST_DIRECTIVE` has turn N-1's directive. Unless the close fails - but if it fails, `_LAST_DIRECTIVE` is not updated, and we fall through to `_budget_from_disk` which is gated by `session_id not in _BUSES`. If the bus is cached (which it would be if turn N-1 had signals), `_budget_from_disk` returns None → default. So we don't read a stale directive. Good.

But what if turn N-1 had NO signals (no tool calls, no API errors)? Then `_record` was never called, so `_bus_for` was never called, so the bus might not be cached. Wait, but `_close_locked` calls `_bus_for(window.session_id).emit(directive)` - this creates the bus if needed. So if the close succeeds, the bus is cached. If the close fails before `_bus_for`, the bus might not be cached.

Let me think about this: turn N-1 has no signals. `bounded_iterations` for turn N calls `_close_locked(window, budget=floor)`. `_close_locked` sets `window.closed = True`, then calls `issue_policy`, `interpret`, `_bus_for(window.session_id).emit(directive)`. If all succeeds, bus is cached, directive is cached. If `_bus_for` fails (e.g., `get_hermes_home` raises), exception caught, `_LAST_DIRECTIVE` not set. Then `_budget_from_disk` is called. `session_id not in _BUSES` (bus creation failed), so it proceeds. It tries to create the bus again. If `get_hermes_home` fails again, SystemExit propagates to `bounded_iterations` → default. If it succeeds this time, the bus is created, replays the file. If the file has previous directives, it reads the last one. But that's from a PREVIOUS session turn, not turn N-1 (whose close just failed). So it could be turn N-2's or earlier directive. That's a stale read!

Wait, but is this realistic? `_bus_for` fails in `_close_locked` but succeeds in `_budget_from_disk`? That would require `get_hermes_home` to be flaky. In practice, if `get_hermes_home` fails once, it'll fail again. But it's not impossible (e.g., a race condition on a config file). However, this is a very edge case. And even if it happens, the stale directive's budget is applied verbatim - it's a valid budget >= 1. It's not a brick, just a stale value. And the guarantee says "a 2-turns-stale one" is wrong. So this IS a potential A3 violation, but it requires a very specific failure mode (bus creation fails in `_close_locked` but succeeds in `_budget_from_disk`).

Actually, let me re-examine. If `_close_locked` fails, `window.closed` is True. Then `_LAST_DIRECTIVE.get(session_id)` is None (not set). Then `_budget_from_disk(session_id)` is called. It checks `if session_id in _BUSES: return None`. If the bus was NOT created (because `_bus_for` failed), then `session_id not in _BUSES`, so it proceeds. It calls `_bus_for(session_id)` again. If this succeeds, the bus is created and replays. Then it reads the last directive from the file. If the file has directives from previous turns, it returns the last one's budget. But the current turn's (N-1) directive was NOT emitted (the close failed), so the last directive on disk is from turn N-2 or earlier. That's a 2-turns-stale read!

But wait - is this the already-fixed issue (a)? Let me re-read: "the restart fallback used to re-parse the raw JSONL through a possibly-cached (unverified) bus and could apply a 2-turns-stale budget after a failed close — now gated to the COLD path only (`session_id not in _BUSES`)."

The fix gates to the cold path (`session_id not in _BUSES`). But in my scenario, the bus is NOT in `_BUSES` (creation failed), so the gate doesn't help - it proceeds to the disk path. The fix assumes that if the bus is not cached, it's a cold restart. But a failed bus creation also results in no cached bus, and it's NOT a cold restart.

Hmm, but is this realistic? If `_bus_for` fails in `_close_locked`, it's because `get_hermes_home` or `SalienceBus()` raised. If `get_hermes_home` raises SystemExit, it's caught by `_close_locked`. Then `_budget_from_disk` calls `get_hermes_home` again - if it raises again, SystemExit propagates to `bounded_iterations` → default. So the stale read only happens if `get_hermes_home` fails once then succeeds. That's a TOCTOU-like flaky failure. Very unlikely but not impossible.

Actually, there's another case: `SalienceBus(path)` raises (e.g., corrupt file). Then the bus is not cached. `_close_locked` catches the exception. Then `_budget_from_disk` calls `_bus_for` again, which calls `SalienceBus(path)` again, which raises again. This propagates to `bounded_iterations` → default. So a corrupt file doesn't cause a stale read - it causes a fail-closed. Good.

What about `directory.mkdir(parents=True, exist_ok=True)` failing? If it fails in `_close_locked`'s `_bus_for`, exception caught. Then in `_budget_from_disk`, `_bus_for` is called again, `mkdir` fails again, exception propagates → default. OK.

So the only scenario for a stale read is: `_bus_for` fails in `_close_locked` for a transient reason, then succeeds in `_budget_from_disk`. This is extremely unlikely. And even then, the file would have to have a previous directive. Let me think about whether this is worth reporting...

Actually, I realize there's a more fundamental issue. The `_budget_from_disk` function reads the last directive from the file by scanning for `kind == "directive"` entries. But it reads the file directly with `open(path)` and `json.loads`, NOT through the bus's verified store. It then uses the bus's `directives_for` to get the verified copy. But the `last_subject` is determined by the direct file read, not the bus. So if the file has a corrupt non-tail directive (wrong hash but valid JSON), the direct read would still parse it and extract its subject. Then `bus.directives_for(last_subject)` would... wait, the bus construction would raise on the corrupt entry during replay. So the bus is never created, and the exception propagates → default. OK.

But what if the corrupt entry is the LAST directive entry? The direct read would parse it (valid JSON, wrong hash) and extract its subject. Then `_bus_for` would replay and raise on the corrupt entry. Exception propagates → default. OK.

What if the file has a valid directive at the end, but a corrupt signal entry before it? The bus replay would raise on the corrupt signal. Exception propagates → default. OK, fail-closed.

Hmm, what about the TOCTOU between the direct file read and the bus construction? The direct read scans the file to find `last_subject`. Then `_bus_for` constructs the bus, which replays the file. If the file is modified between the read and the bus construction... but they're both under `_LOCK`, so no concurrent process can modify the file through the observer. An external process could, but that's out of scope (consistent malicious rewrite).

Actually wait, there IS a TOCTOU within `_budget_from_disk` itself. It reads the file directly to find `last_subject`, then constructs the bus (which replays+verifies), then calls `bus.directives_for(last_subject)`. The direct read and the bus construction are two separate reads of the same file. If the file is truncated between them (by another process, or by a crash), the direct read might find a subject that the bus replay doesn't see. Then `directives_for(last_subject)` returns an empty tuple, and `_directive_budget` is called with... wait, `directives[-1]` would raise IndexError on an empty tuple. Let me check:

```python
directives = bus.directives_for(last_subject)  # verified copies, oldest first
return _directive_budget(directives[-1]) if directives else None
```

If `directives` is empty, it returns None → default. OK, safe.

But what if the file is EXTENDED between the direct read and the bus construction? The direct read finds `last_subject` from the old file. The bus replays the new file, which has more entries. `directives_for(last_subject)` returns directives for that subject, which might include new ones. The last one is returned. This is... actually fine, it's just a newer directive for the same subject. Not a staleness issue.

OK, I think the TOCTOU in `_budget_from_disk` is benign in practice. The direct read is just to find the subject; the bus provides the verified value. If they disagree, the bus wins (or returns empty → default).

Let me move on.

## 3. DENY-SHAPED / NO-RE-CLAMP

`_directive_budget` checks:
- `not subject or not policy_id` → None (hard-deny has blank subject/policy_id)
- `not isinstance(budget, int) or isinstance(budget, bool) or budget < 1` → None

This correctly handles:
- Hard-deny: blank subject/policy_id, budget 0 → None → default ✓
- Malformed: non-int, bool, < 1 → None → default ✓

The budget is applied verbatim - no re-clamp. The tests `test_no_reclamp_directive_below_default` and `test_no_reclamp_directive_above_default` confirm this. Good.

But wait - what about a directive with a valid subject/policy_id but `compute_budget` that exceeds `max_budget` of the policy? Can that happen? The interpreter clamps `compute_budget` to `[min_budget, max_budget]`. So the recorded budget is always within the policy window. The consumer applies it verbatim. No re-clamp. Good.

What about a directive from a DIFFERENT policy (not the observer's v0 policy)? The consumer doesn't check the policy_id. It just reads `compute_budget`. If someone writes a directive with a different policy_id but a valid subject and a huge compute_budget, the consumer would apply it. But the directive comes from the bus, which only contains directives emitted by the observer's `_close_locked`. So this can't happen in normal operation. On the disk fallback, the file is hash-chained, so a tampered directive would be caught by replay. OK.

## 4. RESTART-FALLBACK INTEGRITY

I covered most of this above. The key question: is there a path where `_budget_from_disk` returns an unverified value?

`_budget_from_disk`:
1. Checks `session_id in _BUSES` → return None (skip)
2. Checks `path.exists()` → return None if no file
3. Calls `_bus_for(session_id)` → constructs bus, replays+verifies, raises on corrupt
4. Reads file directly to find `last_subject`
5. Calls `bus.directives_for(last_subject)` → verified copies
6. Returns `_directive_budget(directives[-1])`

The value comes from `bus.directives_for`, which returns copies from the bus's verified `_directives` store. The bus was constructed with replay+verify. So the value is verified. Good.

But step 4 reads the file directly. Could `last_subject` be manipulated? If an attacker adds a fake directive entry with valid JSON but wrong hash at the end of the file, the direct read would parse it and extract its subject. But the bus replay would raise on the wrong hash. So the bus is never constructed, exception propagates → default. Good.

If the attacker modifies the last directive's subject (keeping the hash valid)... they can't, because the hash covers the payload. Changing the subject changes the hash. So the replay would catch it. Good.

What about a TOCTOU between step 2 (`path.exists()`) and step 3 (`_bus_for`)? If the file is deleted between exists() and bus construction, `SalienceBus(path)` with a non-existent path... let me check: `SalienceBus.__init__` checks `os.path.exists(path)`. If the file doesn't exist, it doesn't replay. The bus is empty. Then step 4 opens the file and gets FileNotFoundError. This propagates → default. OK, safe.

What about a TOCTOU between step 4 (direct read) and step 5 (bus.directives_for)? The bus is already constructed and verified at step 3. Steps 4 and 5 use the in-memory bus state, not the file. So no TOCTOU. Wait, step 4 reads the FILE, not the bus. But step 5 uses the BUS. If the file changed between step 3 (bus construction) and step 4 (direct read), the direct read might find a different `last_subject` than what the bus has. But `directives_for(last_subject)` on the bus would return directives for that subject from the bus's verified store. If the bus doesn't have that subject (because the file was truncated after bus construction), it returns empty → None → default. If the bus has that subject, it returns the verified directives. So the value is still verified. OK.

Hmm, actually I realize there's a potential issue: the direct file read in step 4 is redundant. The bus already has all the directives in memory. Why not just iterate over `bus._directives` to find the last one? The direct file read is a second parse of the file, which:
1. Doesn't verify hashes (but the bus already did)
2. Could disagree with the bus's verified store (TOCTOU)

But the `last_subject` from the direct read is only used as a key into the bus's verified store. If the direct read finds a subject that the bus doesn't have (TOCTOU), `directives_for` returns empty → None → default. If it finds a subject the bus has, the value is verified. So the direct read can't cause an unverified value to be returned. It can only cause a wrong subject to be queried, which results in empty → default. Not a security issue, just a potential staleness issue (if the file was truncated, the direct read might find an older subject that the bus has, and return an older directive). But that's the tail-truncation case, which is explicitly out of scope (ADR 0001).

Actually, wait. Let me think about this more carefully. The direct read scans ALL lines in the file for `kind == "directive"` and takes the LAST one's subject. The bus has all directives in memory. Why not just use the bus? The answer is probably that `directives_for` is subject-keyed, and the bus doesn't expose a "get all directives" API. But the bus has `_directives` as an internal field. The consumer could iterate over `bus._directives` to find the last one. But that's reaching into the bus's internals, which is out of scope (vendored code).

Actually, the consumer could call `bus.directives_for` for each subject, but it doesn't know the subjects without reading the file. So the direct file read is to find the last subject. This is a bit awkward but not a security issue.

Let me check: is there a way to get the last directive from the bus without knowing the subject? Looking at the bus API: `signals_for(subject)`, `directives_for(subject)`, `head()`, `verify_chain()`. No "get all directives" or "get last directive" API. So the consumer has to know the subject. The direct file read is the only way to find it. OK, this is a design limitation of the bus API, not a bug in the consumer.

But there IS a subtle issue: the direct file read finds the last directive entry's subject. But what if the last directive entry in the file has a different subject than the last directive in the bus's `_directives` list? This could happen if... no, the bus replays the file in order, so the last directive in the file is the last directive in the bus. Unless the file was modified between bus construction and the direct read (TOCTOU). But as I discussed, this only affects which subject is queried, not whether the value is verified.

OK, I'm satisfied that the restart fallback is sound.

## 5. CONCURRENCY / RESOURCE

`_LOCK` is a `threading.Lock()` (non-reentrant). Let me check for re-entrant acquisition:

`bounded_iterations` → `_resolve_bounded` (acquires `_LOCK`) → calls:
- `_ensure_template_valid` (no lock acquisition)
- `_operator_budget` (no lock acquisition)
- `_close_locked` (no lock acquisition, but calls `_bus_for` and `_operator_budget`)
- `_LAST_DIRECTIVE.get` (no lock)
- `_budget_from_disk` (no lock, but calls `_bus_for`)

`_bus_for` doesn't acquire `_LOCK`. Good.
`_operator_budget` doesn't acquire `_LOCK`. Good.
`_close_locked` doesn't acquire `_LOCK`. Good.
`_ensure_template_valid` doesn't acquire `_LOCK`. Good.

So no re-entrant acquisition. Good.

But wait - `_close_locked` calls `_bus_for`, which calls `SalienceBus(path)`. `SalienceBus.__init__` calls `_replay`, which does file I/O. This is all under `_LOCK`. So file I/O is serialized. That's fine for correctness but could be a performance issue (blocking other sessions). But that's a design choice, not a bug.

What about `_open_window` and `_record`? They also acquire `_LOCK`. So all registry access is serialized. Good.

What about `observe_lifecycle`? It calls `_open_window`, `_record`, or `_close_session`, all of which acquire `_LOCK`. But `observe_lifecycle` itself doesn't acquire `_LOCK`. So there's no re-entrant issue there either.

`_close_session` acquires `_LOCK` and calls `_close_locked` (no lock). Good.

Now, is there a deadlock scenario? `_LOCK` is a simple lock, non-reentrant. If any code path acquires `_LOCK` and then calls something that acquires `_LOCK` again, it would deadlock. I've checked all paths under `_LOCK` and none of them acquire `_LOCK` again. Good.

Resource: `_LAST_DIRECTIVE` is freed on session close (`_close_session` pops it). Good. `_BUSES` is also freed. `_WINDOWS` is also freed. No leak.

But what about `_OPERATOR_BUDGET_CACHE`? It's never freed on session close. But it's a single int, not per-session. It's freed on `_reset_for_tests`. In production, it's cached once per process. No leak.

`_TEMPLATE_VALIDATED`? Same - single bool, not per-session. No leak.

## 6. HONESTY

Let me check the docstrings and config text for overstatements.

The module docstring says: "It is wired live but INERT in v0: the produce policy pins `min_budget == max_budget == operator budget` and ATTENTION is unmapped, so the directive always echoes the operator's own budget — the consumer is behavior-preserving by construction."

Is this accurate? The policy pins `min_budget == max_budget == budget` (where budget is the operator budget or the caller's default). ATTENTION is not in the signal map (only RISK, VERIFICATION, MEMORY are mapped). So `agg.get(Facet.ATTENTION, 0.0)` is always 0.0. `_scale(0.0, min, max) = min + round_half_up(0.0 * (max - min)) = min`. Since `min == max`, `compute_budget = min = max = budget`. So yes, the directive always echoes the budget. Accurate.

The config comment says: "In this v0 config the directive echoes the operator's own configured budget, so consumption is behavior-preserving." Accurate.

The `bounded_iterations` docstring says: "In the v0 config the directive echoes the operator's own budget (pinned window + ATTENTION unmapped), so this is behavior-preserving until a future change widens the policy window and maps a budget-moving facet." Accurate.

Does any text overstate what the consumer does? The docstring says "Bound this turn's iteration budget by the directive recorded for the PRIOR turn." This is accurate - it applies the directive's budget. It doesn't claim to move the budget.

Does any text understate the kill-switch defaults? The config says "both default ON." The code: `_config_flag("enabled", True)` and `_config_flag("consume_compute", True)`. Accurate.

Hmm, one thing: the `_consume_enabled` docstring says "default ON as of PR-H2." The config comment says "both default ON in the Quorum Edition." Accurate.

Let me check the `_ensure_template_valid` docstring: "this is NOT config validation. No template knob is config-wired yet." Accurate - only `enabled` and `consume_compute` are config-wired.

I don't see any honesty issues.

## 7. TEST HONESTY

Let me check if any test would stay GREEN if the corresponding production line were sabotaged.

### test_applies_recorded_budget_verbatim
Seeds `_LAST_DIRECTIVE["s"]` directly, then calls `bounded_iterations("s", 10)`. If the `return budget if budget is not None else default` line were changed to `return default`, this test would RED (expects 7, gets 10). If `_directive_budget` were changed to always return None, this test would RED. Good.

But if the `_resolve_bounded` function were changed to always return None (skipping the cache read), this test would RED (expects 7, gets 10). Good.

### test_no_reclamp_directive_below_default
Seeds `_LAST_DIRECTIVE["s"]` with budget 3, default 100. If a `min(budget, default)` clamp were added, this would RED (expects 3, gets 3... wait, min(3, 100) = 3, so it would still pass!). Hmm, no - the test says "directive says 3 where config/default would say 100 ⇒ the recorded 3 wins." `min(3, 100) = 3`. So a `min(budget, default)` clamp would NOT change the result here. The test doesn't actually catch a down-clamp when the directive is BELOW the default!

Wait, let me re-read: `test_no_reclamp_directive_below_default` has budget 3, default 100. `min(3, 100) = 3`. So a `min` clamp is a no-op here. The test would stay GREEN. But `test_no_reclamp_directive_above_default` has budget 40, default 10. `min(40, 10) = 10`. So a `min` clamp WOULD be caught by this test. OK, so the below-default test is redundant (it doesn't catch anything the above-default test doesn't), but it's not wrong - it just doesn't add coverage.

Actually wait, what about a `max(budget, default)` clamp (up-clamp)? `test_no_reclamp_directive_below_default`: `max(3, 100) = 100`. Expects 3, gets 100. RED. Good. `test_no_reclamp_directive_above_default`: `max(40, 10) = 40`. Expects 40, gets 40. GREEN. So the below-default test catches an up-clamp, and the above-default test catches a down-clamp. Together they cover both directions. OK, good.

### test_finalize_on_read_closes_prior_window
Opens window u1, records a signal, calls `bounded_iterations("s", 10)`. Asserts window is closed, directive is on bus, applied == 10. If `_close_locked` were removed from `_resolve_bounded`, the window would stay open, no directive would be cached, and `bounded_iterations` would return 10 (default, since no directive). The test asserts `so._WINDOWS["s"].closed is True` - this would RED. Good.

But wait - if `_close_locked` were removed, `_LAST_DIRECTIVE` would be empty, and `_budget_from_disk` would be called. If the bus is already cached (from `_record`), `_budget_from_disk` returns None → default 10. So `applied == 10` would still pass. But `so._WINDOWS["s"].closed is True` would RED (window not closed). And `len(so._bus_for("s").directives_for(...)) == 1` would RED (no directive emitted). Good, the test catches this.

### test_three_turns_read_prior_not_stale
This is the key A3 test. Let me trace:
- `bounded_iterations("s", 10)` - no window → 10
- `_open("s", "u1")`, `_record_write("s", "u1")`
- `bounded_iterations("s", 20)` - closes u1, reads u1's directive → 20
- `_open("s", "u2")`, `_record_write("s", "u2")`
- `bounded_iterations("s", 30)` - closes u2, reads u2's directive → 30

Asserts `(10, 20, 30)`. If finalize-on-read were removed:
- `bounded_iterations("s", 10)` - no window → 10
- `_open("s", "u1")`, `_record_write("s", "u1")`
- `bounded_iterations("s", 20)` - no close, no directive → 10 (default? wait, default is 20)

Hmm, wait. If finalize-on-read is removed, `_resolve_bounded` doesn't close the window. `_LAST_DIRECTIVE` is empty. `_budget_from_disk` is called. If the bus is cached (from `_record_write`), `session_id in _BUSES` → return None → default 20. So `applied2 = 20`. Then `_open("s", "u2")` - rollover close of u1 at operator budget (25, since `_operator_budget()` returns `_DEFAULT_BUDGET` = 25 under this fixture's config which has no `agent.max_iterations`). Wait, the config is `{"salience": {"enabled": True}}` - no `agent.max_iterations`. So `_operator_budget()` returns `_DEFAULT_BUDGET` = 25. So u1 is closed at budget 25. Then `bounded_iterations("s", 30)` - u2 is open, no close (finalize-on-read removed), `_LAST_DIRECTIVE` is empty (u1 was closed by rollover, not by `_close_locked` in `_resolve_bounded`... wait, the rollover close in `_open_window` calls `_close_locked(current)` which DOES set `_LAST_DIRECTIVE`. So after `_open("s", "u2")`, `_LAST_DIRECTIVE["s"]` has u1's directive (budget 25). Then `bounded_iterations("s", 30)` - no close (u2 not closed), reads `_LAST_DIRECTIVE` → u1's budget 25. So `applied3 = 25`. The test asserts `applied3 == 30`, gets 25. RED. Good.

Wait, but the test comment says "Deleting finalize-on-read makes turn 3 read u1 — which _open("u2")'s rollover closed at the operator budget (25, the _DEFAULT_BUDGET under this fixture's config) — so turn 3 returns 25, not 30, and reds." Yes, that matches my analysis. Good.

But what if the sabotage is different? What if `_close_locked` is called but with the WRONG window? E.g., closing the current turn's window instead of the prior turn's. But the code reads `_WINDOWS.get(session_id)` which is the CURRENT open window (turn N-1's, since turn N's hasn't opened yet). So it closes the right window. If someone changed it to, say, skip the close when the window is not closed... that's the same as removing finalize-on-read. The test catches it.

What if someone changed `_resolve_bounded` to close the window but NOT update `_LAST_DIRECTIVE`? Then `_LAST_DIRECTIVE` would be empty (or stale), and `_budget_from_disk` would be called. If the bus is cached, returns None → default. So `applied2 = 20` (default), `applied3 = 30` (default). The test asserts `(10, 20, 30)` - these match the defaults! So the test would stay GREEN!

Wait, that's a problem. If `_close_locked` successfully closes the window and emits the directive to the bus, but `_LAST_DIRECTIVE` is not updated (e.g., the line `_LAST_DIRECTIVE[window.session_id] = directive` is removed), then:
- `applied2 = 20` (default, since `_LAST_DIRECTIVE` is empty and bus is cached → `_budget_from_disk` returns None)
- But the test asserts `applied2 == 20` - which is the default! So it passes!
- `applied3 = 30` (default) - test asserts `applied3 == 30` - passes!

The test would stay GREEN even though the consumer is returning the default instead of the recorded directive! Because in v0, the directive echoes the default (since `min == max == floor == default`). So the test can't distinguish between "applied the directive" and "returned the default" - they're the same value!

This is a fundamental test honesty issue. The A3 test uses distinct defaults (10, 20, 30) to make the A3 property observable. But since the directive's budget equals the default (v0 behavior-preserving), the test can't tell if the directive was actually read or the default was returned. The test only verifies A3 when finalize-on-read is REMOVED (causing a stale read), not when the cache read is bypassed.

Let me verify: in `test_three_turns_read_prior_not_stale`:
- `applied2 = bounded_iterations("s", 20)` - if finalize-on-read works, closes u1 at floor 20, directive budget = 20, returns 20. If `_LAST_DIRECTIVE` is not updated, returns default 20. Same result.
- `applied3 = bounded_iterations("s", 30)` - if finalize-on-read works, closes u2 at floor 30, directive budget = 30, returns 30. If `_LAST_DIRECTIVE` is not updated, returns default 30. Same result.

So the test is VACUOUS with respect to the cache read - it can't distinguish "read the directive" from "return the default" because they're the same value in v0. The test only catches the case where finalize-on-read is removed entirely (causing a stale read from a previous turn's rollover close at a different budget).

This is a real test honesty issue. The test claims to verify "turn N applies turn N-1's directive" but it actually only verifies "turn N doesn't apply a stale directive." It can't verify that the directive is actually READ and APPLIED, because the directive's value equals the default.

Hmm, but is this inherent to v0 being behavior-preserving? The CONTEXT says: "Flag any docstring/config text that hides or overstates this" and "DO flag if... A3/A4 are therefore untestable as written." So this IS something I should flag.

Actually, let me re-read the CONTEXT: "the fact that v0 is intentionally behavior-preserving (that is by design, not a bug — but DO flag if it is misdescribed or if A3/A4 are therefore untestable as written)."

So the question is: are A3/A4 untestable as written? Let me think about A4 specifically.

A4 says: "The finalize-on-read close binds the policy floor to the caller-passed `default`." The test `test_three_turns_read_prior_not_stale` asserts `bus.directives_for(so._subject("s", "u1"))[0]["compute_budget"] == 20` and `bus.directives_for(so._subject("s", "u2"))[0]["compute_budget"] == 30`. These are the directives emitted by the finalize-on-read close, with floor = default (20 and 30 respectively). If the floor were the operator budget (25) instead of the default, the directive would have budget 25, and the assertion would RED. So A4 IS testable - the directive's budget reflects the floor, which is the default.

But A3 - "turn N applies turn N-1's directive" - is only testable in the negative (stale detection). The positive (actually reading and applying the directive) is untestable because the directive's value equals the default. The test can't distinguish "read directive (== 20)" from "return default (== 20)."

Wait, but the test `test_applies_recorded_budget_verbatim` seeds `_LAST_DIRECTIVE` with a directive of budget 7 and default 10. This DOES distinguish - 7 ≠ 10. So the positive case IS testable, just not in the A3 sequence test. The A3 sequence test can't use a non-default directive because the directive is generated by the close, which uses the default as the floor.

Hmm, but `test_applies_recorded_budget_verbatim` seeds `_LAST_DIRECTIVE` directly, bypassing the finalize-on-read wiring. So it tests `_directive_budget` and the cache read, but NOT the finalize-on-read path. The CONTEXT says: "tests that only seed _LAST_DIRECTIVE directly and thus never exercise the real finalize-on-read wiring" - this is exactly the issue called out in attack vector 7.

So the test suite has a gap: no test exercises the finalize-on-read wiring with a directive whose budget differs from the default. This means the "read the directive and apply it" part of A3 is untestable in the current suite. The A3 sequence test only catches stale reads (by removing finalize-on-read), not missed reads.

Let me think about what mutation would expose this. If someone changed `_resolve_bounded` to:
```python
directive = _LAST_DIRECTIVE.get(session_id)
if directive is not None:
    return None  # SABOTAGE: ignore the cache, return default
return _budget_from_disk(session_id)
```

Then:
- `test_applies_recorded_budget_verbatim`: seeds `_LAST_DIRECTIVE["s"]` directly, calls `bounded_iterations("s", 10)`. `_resolve_bounded` returns None (sabotage). `bounded_iterations` returns default 10. Test expects 7. RED. Good, this test catches it.

Wait, so `test_applies_recorded_budget_verbatim` DOES catch this sabotage. Because it seeds a directive with budget 7 and default 10, and the sabotage returns 10 ≠ 7. So the positive case IS tested, just not through the finalize-on-read wiring.

But the issue is: `test_applies_recorded_budget_verbatim` seeds `_LAST_DIRECTIVE` directly. It doesn't test the finalize-on-read path. If someone changed `_close_locked` to not update `_LAST_DIRECTIVE`, `test_applies_recorded_budget_verbatim` would still pass (it seeds the cache directly). And `test_three_turns_read_prior_not_stale` would also pass (because the directive value equals the default). So the sabotage "_close_locked doesn't update _LAST_DIRECTIVE" would go undetected.

But wait - if `_close_locked` doesn't update `_LAST_DIRECTIVE`, then in `test_three_turns_read_prior_not_stale`:
- `applied2 = bounded_iterations("s", 20)`: `_close_locked` closes u1 but doesn't cache. `_LAST_DIRECTIVE` is empty. `_budget_from_disk("s")`: `session_id in _BUSES` (bus was created by `_record_write`) → return None. `bounded_iterations` returns default 20. Test expects 20. PASS.
- `applied3 = bounded_iterations("s", 30)`: `_close_locked` closes u2 but doesn't cache. `_LAST_DIRECTIVE` is empty. `_budget_from_disk("s")`: `session_id in _BUSES` → return None. `bounded_iterations` returns default 30. Test expects 30. PASS.
- `bus.directives_for(so._subject("s", "u1"))[0]["compute_budget"] == 20`: The directive WAS emitted to the bus (just not cached in `_LAST_DIRECTIVE`). So this passes. PASS.
- `bus.directives_for(so._subject("s", "u2"))[0]["compute_budget"] == 30`: Same. PASS.

So the ENTIRE test stays GREEN! The sabotage "_close_locked doesn't
