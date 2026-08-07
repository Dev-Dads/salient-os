# Red-team (consumer, pass=general): moonshotai/kimi-k3

_finish=length seconds=353.2 usage={'prompt_tokens': 23512, 'completion_tokens': 12000, 'total_tokens': 35512, 'cost': 0.250536, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.250536, 'upstream_inference_prompt_cost': 0.070536, 'upstream_inference_completions_cost': 0.18}, 'completion_tokens_details': {'reasoning_tokens': 11997, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me work through this carefully. I need to red-team the consumer `bounded_iterations` and its supporting code.

Let me map out the call flow:

1. `bounded_iterations(session_id, default)`:
   - If `default` is not an int (or is a bool), return default unchanged. Note: `default` could be a float like 3.5 — returned unchanged. OK.
   - try:
     - if not `_consume_enabled()` or not session_id: return default
     - `budget = _resolve_bounded(session_id, default)`
     - return budget if budget is not None else default
   - except (Exception, SystemExit): log, return default.

Note: `bounded_iterations` does NOT catch `KeyboardInterrupt` or `BaseException` — that's intentional (documented).

2. `_consume_enabled()`: calls `salience_enabled()` then `_config_flag("consume_compute", True)`.
   - `salience_enabled()`: checks `_IMPORT_OK`, imports `product_identity.IS_QUORUM_EDITION` catching `(Exception, SystemExit)`, then `_config_flag("enabled", True)`.
   - `_config_flag`: calls `read_raw_config_readonly()` catching `(Exception, SystemExit)`, returns False on failure (fail-closed). Note: for `default=True` flag, a config read failure returns False — meaning consumption is disabled if config unreadable. That's fail-safe (returns default budget). Fine.

3. `_resolve_bounded(session_id, default)`:
   - `with _LOCK:`
     - `_ensure_template_valid()` — calls `_operator_budget()`, `issue_policy`, `verify_policy`. Catches (Exception, SystemExit). But wait — `_operator_budget` reads config; if that raises something not caught... it catches (Exception, SystemExit). OK.
     - compute `floor = default if (isinstance(default, int) and not isinstance(default, bool) and default > 0) else _operator_budget()`.
     - `window = _WINDOWS.get(session_id)`; if window open, `_close_locked(window, budget=floor)`.
     - `directive = _LAST_DIRECTIVE.get(session_id)`; if not None, return `_directive_budget(directive)`.
     - else `return _budget_from_disk(session_id)`.

4. `_close_locked(window, budget)`: sets closed=True, issues policy, interprets, emits to bus via `_bus_for`, caches in `_LAST_DIRECTIVE`. Catches (Exception, SystemExit) — so a failure here leaves window.closed=True but no directive cached. Then `_resolve_bounded` reads `_LAST_DIRECTIVE.get(session_id)` — returns None (assuming empty), then `_budget_from_disk`.

Wait — here's a potential issue. If `_close_locked` fails (e.g., emit raises due to I/O error), then `_LAST_DIRECTIVE` has no entry for the session. Then `_budget_from_disk(session_id)` is called. But `_budget_from_disk` checks `if session_id in _BUSES: return None`. If `_bus_for` was called during `_close_locked` (it is — `_bus_for(window.session_id).emit(directive)`), then the bus IS cached in `_BUSES`, so `_budget_from_disk` returns None. Good — that's the fixed finding (a).

But wait: order of operations in `_close_locked`:
```python
policy = issue_policy(...)
directive = interpret(policy, tuple(window.signals), _POLICY_KEY)
_bus_for(window.session_id).emit(directive)
_LAST_DIRECTIVE[window.session_id] = directive
```
If `_bus_for` raises (e.g., `get_hermes_home` raises SystemExit, mkdir fails, or the bus replay raises ValueError on corrupt file), the except catches it, `_LAST_DIRECTIVE` is NOT updated. But was `_BUSES` updated? In `_bus_for`:
```python
bus = _BUSES.get(session_id)
if bus is None:
    ...
    bus = SalienceBus(str(path))
    _BUSES[session_id] = bus
return bus
```
If `SalienceBus(str(path))` raises (corrupt file replay raises ValueError), then `_BUSES[session_id]` is NOT set. So `session_id not in _BUSES`, and the subsequent `_budget_from_disk` will try again: `path.exists()` — yes; `bus = _bus_for(session_id)` — tries to construct again, replay raises again → ValueError propagates up through `_resolve_bounded` → caught in `bounded_iterations` → returns default. Good, fail-open.

Hmm wait, but actually — there's an interesting point: in `_budget_from_disk`, exceptions from `_bus_for` (the replay) are NOT caught inside `_budget_from_disk`; they propagate to `bounded_iterations`'s except. That's fine (fail-open to default).

But hold on: `_budget_from_disk` is called while holding `_LOCK` (from `_resolve_bounded`). `_bus_for` doesn't acquire the lock. OK, no re-acquire.

Let me check the lock-reentrancy claim: `_LOCK = threading.Lock()` (non-reentrant). `bounded_iterations` → `_resolve_bounded` acquires `_LOCK`. Inside: `_ensure_template_valid` (no lock), `_operator_budget` (no lock), `_close_locked` (no lock — "Caller must hold _LOCK"), `_bus_for` (no lock), `_budget_from_disk` (no lock). So no re-acquire. Good.

But wait — what about `_close_locked` being called from `_open_window` and `_close_session`, which hold `_LOCK`. Fine.

Now, is there a deadlock concern: `_resolve_bounded` holds `_LOCK` while doing disk I/O (replay of potentially large JSONL). Not a deadlock, but blocks all hooks. Performance, maybe LOW. Not a finding per se.

Now let me hunt for the specific guarantees:

**Guarantee 1: FAIL-OPEN / NEVER-BRICK.**

- `bounded_iterations` catches (Exception, SystemExit). What could escape? `BaseException` subclasses other than Exception/SystemExit: `KeyboardInterrupt` (intentionally not caught), `GeneratorExit`. Could any host API raise `GeneratorExit`? Unlikely in normal calls. `MemoryError`? That's an Exception subclass... wait no — `MemoryError` is a subclass of `Exception`. Yes it is. So caught.

Hmm, but there's a subtle one: `budget if budget is not None else default` — `_resolve_bounded` returns `_directive_budget(directive)` which is an int ≥ 1 or None, or `_budget_from_disk` which returns None or `_directive_budget(...)` result. `_directive_budget` checks `isinstance(budget, int) and not isinstance(budget, bool) and budget >= 1`. So budget is a positive int. Never < 1. Good.

But wait — what about the return type of `bounded_iterations` when `default` is not an int? `return default` — e.g. float 3.5 → returns 3.5. Then `agent.max_iterations = 3.5` and `IterationBudget(3.5)`. `consume` compares `self._used >= self.max_total` — works with float. Not bricking. The test asserts `is bad_default`. OK by design.

What if `default` is a valid int but NEGATIVE or zero? `bounded_iterations` doesn't validate that default ≥ 1. It returns default. If default is 0, agent is already bricked independent of the consumer — that's the host's problem, not the consumer's. The consumer's guarantee is "never return a budget < 1" — hmm, the guarantee as stated in the task: "must NEVER return a budget < 1 (that would brick the agent at max_iterations 0)". But if default is 0, `bounded_iterations` returns 0 (the fail-open path returns default unchanged). Is that a violation? The guarantee says "ANY failure, absence... MUST return `default` unchanged" AND "must NEVER return a budget < 1". These conflict if default < 1. But default comes from `agent.max_iterations` which the host controls; if it's 0 the host is already broken. The consumer returning default unchanged when default=0 isn't a new brick. I'd consider this not a real finding — the "never return < 1" guarantee applies to the budget derived from the directive, which `_directive_budget` enforces. Probably LOW at most; and there's no concrete trigger where the consumer makes things worse. Skip or LOW.

Now the key: is there a path where a `BaseException` other than SystemExit escapes? `from hermes_cli.config import read_raw_config_readonly` — import errors are Exception subclasses (ImportError). `from product_identity import IS_QUORUM_EDITION` — caught. `from hermes_constants import get_hermes_home` — inside `_bus_for`, called within `_resolve_bounded` under `bounded_iterations`'s try. SystemExit caught. KeyboardInterrupt intentionally propagates (documented). Fine.

Hmm — one more: `_resolve_bounded` raises... no. `_ensure_template_valid` catches. OK.

Actually wait — there IS something. In `bounded_iterations`, the `try` block: `if not _consume_enabled() or not session_id: return default`. `_consume_enabled` → `_config_flag` catches (Exception, SystemExit) and returns False... So consumption disabled on config error. Fine.

What about `session_id` not being a string? Call site passes `agent.session_id or ""`. `bounded_iterations("s", ...)`. If session_id were, say, bytes, `_session_hash(session_id)` does `session_id.encode("utf-8")` — bytes has no `.encode`... actually bytes HAS `.decode`, not `.encode`? Actually bytes objects do not have `.encode`... wait, yes they do not have encode in py3? Let me think: `b"x".encode` — no, AttributeError. AttributeError is an Exception → caught → default. Fine.

**Guarantee 2: DENY-SHAPED GUARD (A5).**

`_directive_budget(source)`:
- dict: get subject/policy_id/compute_budget.
- object: getattr.
- `if not subject or not policy_id: return None`
- `if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1: return None`
- return budget.

Hard-deny: subject="", policy_id="", compute_budget=0 → `not subject` → None. Good.

What about a dict from disk: `directives_for` returns JSON-round-trip copies, so compute_budget is int or whatever JSON. JSON can't represent bool as int confusion... `True` stays `true`. Check handles bool. What about a payload where compute_budget is a huge int? Returned verbatim — fine, that's Finding D (verbatim).

Hmm — what about a directive where subject is present but `policy_id` missing in dict → None. Good.

Edge: source is a dict but has non-string truthy subject? Whatever, it returns budget if it's a valid int. The guard's purpose is deny-shape detection. Fine.

**Guarantee 3: CONSUMER NOT DECIDER (Finding D).**

`bounded_iterations` returns `budget` verbatim. No clamping. Test pins this (40 > 10 → 40; 3 < 100 → 3). OK.

**Guarantee 4: A3 — TURN N APPLIES TURN N-1.**

Call site: `build_turn_context` mints turn_id at :439-447, then at :491 the consumer runs, then later pre_llm_call at :1054 opens THIS turn's window. So at :491, the open window (if any) is turn N-1's. Finalize-on-read closes it and reads its directive. 

Wait — but is there an issue with the FIRST turn? Turn 1: no window, `_LAST_DIRECTIVE` empty, `_budget_from_disk`. In-session first turn, no file → None → default. Good.

Now, potential staleness: consider the actual event sequence per turn: `build_turn_context` (turn N) → consumer :491 → ... → pre_llm_call :1054 opens turn N's window → tool calls recorded → turn ends → turn N+1's build_turn_context → consumer closes turn N's window.

Hmm, wait. There's a subtlety: `_open_window` also does a rollover close: when turn N+1's pre_llm_call fires, `_open_window` closes turn N's window if still open. But the consumer at turn N+1's :491 already closed it. Idempotent. Fine.

Now here's a question: does the consumer run for EVERY turn? `build_turn_context` — presumably once per turn. The excerpt shows turn_id minted there. OK.

Potential A3 violation hunt: is there a scenario where at :491 the open window is turn N's own (not N-1's)? That would require pre_llm_call of turn N to fire before :491 of turn N. The excerpts show pre_llm_call at :1054, after :491, in the same function. So no.

Could there be a scenario where the window open at :491 is turn N-2's (2-stale)? That requires turn N-1's consumer+pre_llm to have never opened a window while turn N-2's window remained open. E.g., turn N-1's pre_llm_call fired with missing session/turn ids → `_open_window` returns early WITHOUT closing the old window! Look:

```python
def _open_window(kwargs):
    session_id, turn_id = _ids(kwargs)
    if not session_id or not turn_id:
        return  # fail-closed attribution
```

If turn N-1's pre_llm_call had empty turn_id or session_id, the old window (turn N-2) stays open. Then at turn N's :491, `_resolve_bounded` closes turn N-2's window and applies its directive — 2-turns-stale... well, it applies the most recent CLOSED-able window's directive. Is that a violation of A3? A3 says "turn N applies turn N-1's directive, nor a 2-turns-stale one". But if turn N-1 produced no window (no ids), there IS no turn N-1 directive. Applying turn N-2's is arguably the best available — "prior turn's recorded decision". Hmm. But also the window-open signal comes from the host; if turn_id is empty at pre_llm_call but non-empty at build_turn_context... At :439-447 turn_id is always minted non-empty. Would pre_llm_call pass the same turn_id? Presumably kwargs carry agent._current_turn_id. Probably fine in practice. This is speculative — no concrete trigger without host behavior beyond the excerpts. LOW at most.

More concrete: within `_resolve_bounded`, the finalize-on-read closes whatever window is open for the session and reads `_LAST_DIRECTIVE`. But note the window close sets `_LAST_DIRECTIVE[session_id]` to the directive of THAT window. Then `_resolve_bounded` reads `_LAST_DIRECTIVE.get(session_id)`. Consistent.

But wait — what if `_close_locked` fails (exception caught inside)? Then `_LAST_DIRECTIVE` may hold the directive from an EARLIER turn (e.g., turn N-2's, cached when it closed). Then the consumer reads turn N-2's directive — a 2-stale... no wait. Sequence: turn N-1's window open. Turn N: `_close_locked(window_N-1)` raises internally (caught) → `_LAST_DIRECTIVE` still holds directive from turn N-2 (cached when N-2 closed at N-1's finalize-on-read). Consumer reads N-2's directive → applies a 2-turns-stale budget. Hmm! Is that a real A3 violation?

Wait, let's trace more carefully. `_close_locked` failure mode: `window.closed = True` is set BEFORE the try. So the window is marked closed but no directive emitted/cached. `_LAST_DIRECTIVE` retains its previous value (turn N-2's directive, still cached because nothing cleared it). Then `_resolve_bounded` continues: `directive = _LAST_DIRECTIVE.get(session_id)` → turn N-2's directive → `_directive_budget` → returns N-2's budget. Turn N applies turn N-2's budget. That's 2-turns-stale... 

But wait — when did N-2's directive get cached vs cleared? `_LAST_DIRECTIVE[session_id]` is overwritten on each successful close. At turn N-1's finalize-on-read, N-2's window closed successfully → cached N-2's directive... hold on. Let me redo the indexing.

Turn N's consumer closes turn N-1's window. So:
- Turn 2's consumer closes turn 1's window → caches directive(u1) → applies budget(u1).
- Turn 3's consumer closes turn 2's window → caches directive(u2) → applies budget(u2).

Now suppose at turn 3, `_close_locked(window_u2)` throws inside (e.g., `_bus_for` I/O error, or `interpret` raises). The except in `_close_locked` logs and swallows. `_LAST_DIRECTIVE["s"]` still holds directive(u1) from turn 2's read. `_resolve_bounded` then reads directive(u1) → applies budget(u1) to turn 3. Turn 3 applies turn 1's directive — 2-turns-stale!

Is that the intended behavior? The docstring of `_budget_from_disk` says: "if a bus is already cached but `_LAST_DIRECTIVE` is empty the last close FAILED, and reading a stale on-disk directive would both bypass the replay verification ... and apply a 2-turns-stale budget — so we return None (⇒ default) instead."

So the design explicitly recognizes that a failed close means a stale read is BAD, and guards the disk path. But the in-memory path has the SAME hazard and it's NOT guarded: after a failed close, `_LAST_DIRECTIVE` still holds the PREVIOUS turn's directive, and `_resolve_bounded` happily returns it. That's an inconsistency: the disk-fallback path treats failed-close as "return default", but the in-memory path treats failed-close as "apply the stale directive".

Hmm wait, but is `_LAST_DIRECTIVE` non-empty after a failed close? Yes: the failed close leaves the prior entry. Let me double check `_close_locked` — `_LAST_DIRECTIVE[window.session_id] = directive` only after successful emit. On failure, no write, no pop. So stale entry remains.

Concrete trigger: session "s". Turn 1: `_open_window(u1)`, record, turn 2 consumer: close u1 succeeds, cache directive(u1, budget=7) [say operator budget 7], returns 7. Turn 2's pre_llm opens u2, records signals. Turn 3 consumer: `_close_locked(u2 window)` — make `_bus_for("s").emit` raise (e.g., disk full / permission error on append, or `get_hermes_home` SystemExit). Exception swallowed inside `_close_locked`. Then `_LAST_DIRECTIVE["s"]` = directive(u1) → returns 7 for turn 3 — but turn 2's actual floor/default was, say, 20. Turn 3 gets 7 (stale) instead of failing open to 20.

Hmm, but wait: in v0 the budget is pinned min==max==floor, and floor is the caller's default. directive(u1) has budget = turn 2's default (7 in my example; operator budget actually). The stale application returns turn 1's budget for turn 3. A3 says "nor a 2-turns-stale one". This IS a 2-turns-stale application. But it requires a close failure (partial internal failure, swallowed). Severity: the fail-open guarantee isn't violated (returns a plausible budget), but A3 is: turn N applies turn N-2. And note the asymmetry with the disk path which the reviews explicitly fixed for exactly this hazard. In v0 it's behavior-preserving-ish (echoes operator budget... well, echoes the STALE operator budget — if the operator changed max_iterations between turns, the stale value differs; the `_resolve_bounded` comment even mentions the floor-following-reader caveat).

Actually, wait. Let me reconsider: is this truly reachable? `_close_locked` exceptions: `issue_policy` (unlikely to raise), `interpret` (pure, shouldn't raise), `_bus_for(...).emit(directive)` — file I/O (open/append) can raise OSError; `get_hermes_home` could SystemExit; `SalienceBus(path)` replay could raise ValueError if the file got corrupted mid-session. All caught by `_close_locked`'s except. So yes, reachable, though only on I/O failure. The trigger is concrete: chmod the salience dir read-only mid-session, or corrupt the JSONL file externally mid-session (then `emit`... no, emit just appends; replay only happens on construction. If bus already cached, emit appends — OSError on append if disk full). Fine, concrete enough: monkeypatch or actual I/O failure.

Actually, even simpler: in the same-process flow, the FIRST finalize-on-read close of the session fails (turn 1's window close at turn 2 fails). Then `_LAST_DIRECTIVE` is empty → `_budget_from_disk`: is session in `_BUSES`? `_bus_for` was called in `_close_locked` — if `_bus_for` itself succeeded but `emit` failed, `_BUSES["s"]` is cached → `_budget_from_disk` returns None → default. Good. If `_bus_for` failed (not cached), `_budget_from_disk` tries to construct again → raises → caught by bounded_iterations → default. Good. So the first-close-failure path is fine. The problem is specifically a close failure on turn ≥ 2 with a previous directive still cached. 

Suggested fix: on close failure, pop `_LAST_DIRECTIVE[session_id]` (or have `_close_locked` return success/failure and `_resolve_bounded` treat failure as absent). Minimal: in `_close_locked`'s except, `_LAST_DIRECTIVE.pop(window.session_id, None)`. Hmm, but careful: `_close_locked` is also called from `_open_window` rollover and `_close_session`. Popping on failure there is also consistent (a failed close shouldn't leave a consumable stale directive). That fix is minimal and matches the disk-path rationale.

Severity: This is a real A3 staleness path but requires an internal I/O failure, and the value applied is a previously-valid, policy-clamped budget (bounded, never < 1, never crash). In v0 it's the operator's own (possibly stale) budget — harm is limited; but as the guarantee states A3 as a hard guarantee ("nor a 2-turns-stale one"), and the fix is trivial, I'd call it MEDIUM (or HIGH-ish?). The task says "a 2-turns-stale one" is a guarantee violation. Given it needs an internal failure to trigger, MEDIUM seems right. Let me also check: does any test cover it? No test simulates a failed close with a populated `_LAST_DIRECTIVE`. `test_restart_fallback_skipped_when_bus_cached` covers the analogous disk path. So also a test-gap.

**Guarantee 5: A4 — operator-budget binding.** `floor = default if sane else _operator_budget()`. Passed to `_close_locked(window, budget=floor)`. The directive's min==max==floor. In v0 echoes default. The docstrings are heavy with honesty. The config example says behavior-preserving. Fine. Check `_ensure_template_valid` docstring — says probe. Honest per finding (d).

Hmm wait, one thing in `_close_locked`: the produce-side closes pass budget=None → `_operator_budget()`. Fine.

But consider: at turn N's :491, the finalize-on-read closes turn N-1's window with floor = turn N's default. If the operator changed max_iterations between turn N-1 and N, the DURABLE record for turn N-1 says turn N's budget. Acknowledged in the comment ("harmless ... deferred"). OK.

**Guarantee 6: RESTART FALLBACK INTEGRITY.**

`_budget_from_disk`:
- `if session_id in _BUSES: return None` — cold path only.
- path = get_hermes_home()/salience/hash.jsonl; if not exists → None (no spurious bus/file creation — good; test asserts the salience dir doesn't even exist after).
- `bus = _bus_for(session_id)` — constructs → `SalienceBus(path)` → `_replay` verifies whole chain; raises on corruption. Caught by `bounded_iterations` → default. Good.
- Then it RE-READS the file with `open(path)` + `json.loads` per line to find the last directive's subject, then `bus.directives_for(last_subject)` and takes `directives[-1]`.

TOCTOU hunt: between `SalienceBus(path)` construction (verify) and `open(path)` re-read, the file could change. `_LOCK` is process-local; another PROCESS could rewrite the file between verify and read. Then `last_subject` comes from the NEW (unverified) content, but the budget comes from `directives_for(last_subject)` — which reads the VERIFIED in-memory store. If the subject from the tampered file isn't in the verified store → empty tuple → None → default. If the subject IS in the store (attacker rewrites the file but keeps a subject name — subjects are hashes, guessable? subject = hash(session)[:16] + ":" + turn_id; the attacker writing the file knows its content), they'd get directives from the verified store anyway. So the VALUE always comes from the verified store. The only unverified input is `last_subject` used as a LOOKUP KEY, never returned. So no unverified value can be returned. That's actually sound design... but wait.

Hmm, one wrinkle: the re-read loop `json.loads(line)` — if the file was modified between verify and read to contain invalid JSON, `json.loads` raises → propagates → caught by bounded_iterations → default. Fail-open. OK.

But there's a subtle correctness issue in the "last directive" selection: `_budget_from_disk` finds the last directive LINE's subject in the file, then takes `directives[-1]` for that subject — the last directive FOR THAT SUBJECT, not the last directive overall. If the last directive line in the file has subject X, then directives_for(X)[-1] IS the last directive line for X — which is the last directive line overall (since the last directive line overall has subject X). Wait: the last directive line overall has subject X (by construction of last_subject). directives_for(X) returns all directives with subject X, oldest first; the last one is the most recent directive with subject X. Is the last directive-with-subject-X necessarily the last directive overall? The last directive overall has subject = last_subject = X. So yes, it IS in directives_for(X), and it's the most recent one with subject X (nothing after it). So directives[-1] == last directive overall. Consistent. OK good.

But hold on — why re-read the file at all? Because the in-memory `bus._directives` doesn't preserve... it does preserve order. `directives_for` filters by subject. The consumer needs "the last directive overall" but the bus API only exposes per-subject queries. So the file re-read finds the last subject. This is a bit ugly but sound, as analyzed.

Now — is there a TOCTOU where the re-read finds a subject from a TAMPERED file that maps to a directive in the verified store that's NOT the actual last? Scenario: another process appends a valid-looking line? Another process can't produce valid chain entries without... actually the chain is just SHA-256 digests, no key (hash chain, not HMAC — `digest(base)`). Anyone can append a well-formed chained line! The integrity scope explicitly excludes "consistent malicious rewrite" (ADR 0001). But a same-instant append between verify and read: the appended line would be verified-or-nothing at NEXT open, but our re-read reads it NOW without verification. `last_subject` could be from the attacker's line → lookup in verified store → subject likely absent → None → default. If attacker uses an existing subject string → returns that subject's verified directive — a real recorded value, just maybe not the latest. Impact: applies an older-but-verified budget. Marginal, requires cross-process race with a malicious writer; ADR 0001 excludes consistent malicious rewrite; a race-based append is arguably within "the file can be maliciously rewritten" exclusion. LOW.

What about same-process TOCTOU? `_LOCK` held throughout; all in-process bus access is under `_LOCK`. Single-threaded contract respected. OK.

One more restart-fallback check: `_budget_from_disk` constructs the bus via `_bus_for`, which CACHES it in `_BUSES` and also does `directory.mkdir(parents=True, exist_ok=True)` — but we checked `path.exists()` first, so directory exists. No spurious file creation: `SalienceBus.__init__` with existing path only reads. OK. But note: `_bus_for` caches the bus — so a later `_record`/`emit` in this fresh process appends to the verified chain. Good (that's the point of replay).

Hmm wait — there's a subtle one: on the cold path, `_budget_from_disk` constructs and caches a bus for the session EVEN IF the session is long dead. `bounded_iterations` is called per turn with the current session_id, so the session is alive. Fine.

But: what if the cold path is hit and the replay raises (corrupt file)? `_bus_for` raised BEFORE `_BUSES[session_id] = bus`? Look at `_bus_for`:

```python
bus = SalienceBus(str(path))
_BUSES[session_id] = bus
```

`SalienceBus.__init__` calls `self._replay(path)` which raises → constructor raises → `_BUSES` not updated. So every subsequent call re-attempts replay and raises again → default every time. Consistent fail-closed. Good. (Also means a corrupt file permanently poisons the produce path for that session in this process — every `_record` call will try `_bus_for`, raise, and... wait, `_record` catches Exception around publish — `_bus_for` is inside the try in `_record`. Yes: `try: self_bus = _bus_for(session_id); self_bus.publish(signal)... except Exception: log`. So produce goes dark for that session, per design "fail closed". OK.)

**Guarantee 7: CONCURRENCY.**

Single non-reentrant `_LOCK`. `_resolve_bounded` holds it and calls `_close_locked`, `_bus_for`, `_budget_from_disk`, `_operator_budget`, `_ensure_template_valid` — none acquire `_LOCK`. Verified above. No deadlock.

But wait — `_ensure_template_valid` calls `verify_policy` and `issue_policy` — no lock. OK.

Is there any path where `bounded_iterations` is called re-entrantly from within a hook while `_LOCK` is held? The consumer is called from `build_turn_context`, not from within observer hooks. Host-side concurrency of turns on the same session: `_resolve_bounded` holds the lock for the whole read-modify-read, so concurrent hooks block. Fine.

Potential blocking: `_resolve_bounded` holds `_LOCK` during file replay (cold start) — could be slow I/O while holding the lock that the produce hot path needs. Performance concern only; LOW.

`_LAST_DIRECTIVE` freed on session close: yes, `_close_session` pops it. Test pins it. Good.

Unbounded growth: `_WINDOWS`, `_BUSES`, `_LAST_DIRECTIVE` all per-session, freed on close. If session close events never fire (host crash), in-process growth only for live sessions. OK.

**Guarantee 8: KILL SWITCHES.**

`_consume_enabled` = `salience_enabled()` AND `_config_flag("consume_compute", True)`. Default ON both. Config example documents both default ON. Honest.

Hmm — `_config_flag` fail-CLOSED on unreadable config (returns False). For `consume_compute` default True, an unreadable config DISABLES consumption. Fail-safe direction (returns default budget). Fine, and arguably good.

Wait, actually let me look at `_config_flag` again:

```python
try:
    from hermes_cli.config import read_raw_config_readonly
    cfg = read_raw_config_readonly() or {}
except (Exception, SystemExit):
    return False
```

"unreadable config ⇒ fail-closed" — for the `enabled` flag, fail-closed = off = inert. Good.

But note: `_consume_enabled` → `salience_enabled()` → imports product_identity — caught (Exception, SystemExit) → False. Good.

**Now the call site (turn_context.py :491).**

```python
try:
    from hermes_cli.observability import salience_observer as _salience_observer
    agent.max_iterations = _salience_observer.bounded_iterations(
        agent.session_id or "", default=agent.max_iterations
    )
```

The excerpt cuts off at the assignment — presumably followed by `except Exception: pass` or similar, then `agent.iteration_budget = IterationBudget(agent.max_iterations)`. The excerpt shows the try but not the except. The comment says "Fails open: the observer never raises". If the import itself fails (ImportError) — is there an except? The material cuts at line ~502. `bounded_iterations` never raises (except KeyboardInterrupt). The import could raise ImportError — is it caught? We can't see the except clause. Hmm. The visible code shows `try:` with the import inside. If there's no except, an ImportError would crash build_turn_context. But presumably there is one below the cut. Can't verify; the excerpt ends. I shouldn't invent. Actually the excerpt label says "lines 438-502" and shows through the assignment — the except may be at :502+. Not reportable without evidence.

Now — a REAL call-site issue hunt: `agent.max_iterations = bounded_iterations(...)`. In v0, the directive echoes the operator budget — but WHICH operator budget? The floor used in `_close_locked` is turn N's `default` = `agent.max_iterations` at call time. First turn: no directive → returns default. Turn 2: closes turn 1's window with floor = turn 2's default → directive budget == turn 2's default → returns it → `agent.max_iterations` unchanged. Behavior-preserving. ✓.

But wait — there's the produce-side rollover close path too. `_open_window` at turn N's pre_llm_call closes turn N-1's window IF still open — but the consumer at :491 already closed it. So produce close uses budget=None → `_operator_budget()` only when the consumer didn't run (e.g., consume_compute off, or non-Quorum... if subsystem off, hooks don't fire). With consume_compute off: windows close at rollover with floor=_operator_budget() (config-read, memoized). Not consumed. Fine.

Now `test_three_turns_read_prior_not_stale` asserts `(10, 20, 30)` and per-subject budgets 20/30. This pins A3+A4. Decent.

**TEST HONESTY hunt (item 7).**

Let me check each test for mutation-blindness:

1. `test_applies_recorded_budget_verbatim` — seeds `_LAST_DIRECTIVE` directly. Never exercises finalize-on-read. But other tests do that. Mutation: `_directive_budget` returning budget+1? Then ==7 fails. OK.

2. `test_no_reclamp_directive_above_default` — pins no `min(budget, default)`. Good (this was the added test, finding b).

3. `test_hard_deny_directive_falls_back_to_default` — seeds `interpret(None, ...)`. Good.

4. `test_finalize_on_read_closes_prior_window` — opens u1, records, calls bounded_iterations("s", 10), asserts window closed, one directive on bus, applied == 10. Mutation check: if finalize-on-read were DELETED, then `_LAST_DIRECTIVE` empty → `_budget_from_disk`: session in _BUSES? `_record` → `_bus_for` cached the bus (publish). So `_budget_from_disk` returns None → applied == 10 == default. The assertion `applied == 10` would STILL PASS (default is 10)! But the other assertions (`_WINDOWS["s"].closed is True`, `directives_for(...) == 1`) would fail. So the mutation is caught by those. OK.

But here's a subtle one: in this test, `applied == 10` — the directive budget equals floor == default == 10. So this test CANNOT distinguish "applied the directive" from "returned default"! If `_directive_budget` were sabotaged to always return None, this test stays GREEN (applied==10==default, window closed, directive on bus). But `test_applies_recorded_budget_verbatim` (budget 7, default 10) catches that. So across the suite it's covered.

5. `test_three_turns_read_prior_not_stale` — the meaty A3 test. applied == (10, 20, 30). Distinct defaults make staleness observable. Let's verify the claimed mutation: delete finalize-on-read (`_close_locked` call in `_resolve_bounded`). Then turn 2's read: `_LAST_DIRECTIVE` empty (u1 not closed) → `_budget_from_disk` → session in _BUSES (bus cached by _record) → None → applied2 = 20 (default). ✓ still 20. Then `_open("s","u2")` rollover-closes u1 with budget=None → `_operator_budget()` → config is `{"salience": {"enabled": True}}` → no max_iterations → `_DEFAULT_BUDGET` = 25. So directive(u1) budget 25, cached in `_LAST_DIRECTIVE`. Turn 3's read (no finalize): reads directive(u1) → 25 ≠ 30 → reds. ✓. Also `directives_for(u1)[0]["compute_budget"] == 20` would fail (25). So the test genuinely pins finalize-on-read. Good.

Hmm — but wait: is there a mutation that keeps this green while breaking A3? E.g., sabotage `_resolve_bounded` to close the window but read `_LAST_DIRECTIVE` BEFORE closing (read-before-close)? Then turn 2 reads empty → disk → None → 20 (default) — same. Turn 3: `_LAST_DIRECTIVE` has directive(u1) written at turn 2's close... wait order: if we swap to read-then-close: turn 2: read `_LAST_DIRECTIVE` (empty) → 20; then close u1 at floor 20 → cache directive(u1, 20). Turn 3: read directive(u1) → 20 ≠ 30 → reds. Good, caught.

What about a subtler mutation: close with `budget=None` (operator budget) instead of floor in `_resolve_bounded`? Then directive budgets = 25 (operator default under fixture config), applied2 = 25 ≠ 20 → reds. Caught. 

But NOTE: `test_three_turns` runs under `home` fixture whose config has NO agent.max_iterations → operator budget 25. The A4 pinning relies on defaults 10/20/30 differing from 25. Good.

6. Restart tests. `test_restart_recovers_budget_from_disk`: config has agent.max_iterations=7. Open u, record, `_close_session` → closes window with budget=None → `_operator_budget()` = 7 → directive budget 7 persisted. Reset. `bounded_iterations("s", 10)` → no window, `_LAST_DIRECTIVE` empty → `_budget_from_disk`: session not in _BUSES (reset) → file exists → construct bus (replays OK) → re-read file → last_subject → directives_for → 7. Returns 7. ✓.

Mutation hunt: if `_budget_from_disk` were sabotaged to take the value from the UNVERIFIED re-parse (`entry["payload"]["compute_budget"]` directly) instead of `directives_for` — the restart tests stay GREEN (value is the same!). The tamper tests: `test_restart_nontail_tamper_fails_closed` — with the sabotage, the bus CONSTRUCTION still raises (replay verify) before any value is read... unless the sabotage also bypasses bus construction. The sabotage "read the budget from my own json parse, ignoring the bus" — if it replaces `_bus_for` + directives_for with pure file parse, then the non-tail tamper (valid JSON, wrong hash) would be ACCEPTED → returns 7 → test expects 10 → reds. OK so that mutation is caught by the tamper test. 

But here's a REAL test-honesty gap candidate: the current implementation reads `last_subject` from the unverified re-parse. A mutation: "take subject from unverified parse" — that IS the current code, and it's sound because the value comes from the verified store. But consider a mutation where `_budget_from_disk` returns `_directive_budget` of the LAST LINE'S PAYLOAD parsed from the file rather than `directives_for`: covered above — caught by tamper test. OK.

Now `test_restart_corrupt_tail_fails_closed_to_default` — appends invalid JSON. Bus replay raises → default. But ALSO the observer's own `json.loads` would raise. The test can't distinguish WHICH json parse caught it — that was finding (c), and they added the non-tail test for the real gate. But wait — with the non-tail wrong-hash test: the file is valid JSON everywhere; observer's re-parse succeeds; only bus replay raises. The bus construction happens BEFORE the re-parse (`bus = _bus_for(...)` then `open(path)`). So replay raises first. ✓ genuinely pins the bus verify.

Hmm — one thing: in `test_restart_nontail_tamper_fails_closed`, they set `entry["hash"] = "0"*64` on a non-tail directive line. Replay checks `digest(base) == e["hash"]` → fails → raises. ✓. But ALSO `e["prev"] == prev` for the NEXT line would fail (chain discontinuity) — either way raises. Fine.

Mutation: "neuter the replay integrity check" (make `_replay` not raise) → then `_budget_from_disk` proceeds: re-read finds last directive subject (u2's subject) → `directives_for(u2_subject)` — the tampered bus stored directives... if replay doesn't raise, `_directives` includes the tampered non-tail entry and the tail. directives_for(u2)[-1] budget 7 → returns 7 ≠ 10 → reds. ✓ (as the test docstring claims).

7. `test_restart_fallback_skipped_when_bus_cached` — pins the `_BUSES` guard. ✓ (this was fix (a)).

8. `test_consume_kill_switch_leaves_budget_and_window_untouched` — consume_compute false → default AND window NOT closed. Mutation: `_consume_enabled` ignoring consume_compute → then finalize-on-read would close the window → `_WINDOWS["s"].closed is False` fails → reds. ✓. Also budget: default 10 anyway (echo). The window-closed assertion carries the weight. ✓.

9. `test_non_int_default_returned_unchanged` — parametrize "x", True, None, 3.5 → `is bad_default`. Pins the early return. Note `bounded_iterations` checks `not isinstance(default, int) or isinstance(default, bool)` → returns default. True is a bool → returned. ✓.

Hmm wait — what about `default` being an int-like such as `2**63`? Fine.

10. `test_applied_value_propagates_into_iteration_budget` — constructs IterationBudget(applied) manually. Doesn't exercise the real call site; the structural test (`test_call_site_precedes_budget_rebuild`) does source grepping. `rebuild - call <= 12` and no reassignment between. Weak but something. Mutation: move the consumer call AFTER the rebuild → `rebuild` found after `call`... if consumer moved below rebuild, `next(j for j ... if j > call ...)` finds the rebuild... wait if consumer is after rebuild, then looking for rebuild at index > call would find... the NEXT occurrence? There's presumably one occurrence. `next` would raise StopIteration → test errors → red. OK. Mutation: insert `agent.max_iterations = <default>` between → `between` check reds. OK.

11. `test_consumer_cache_freed_on_session_close` — pins the pop. ✓.

12. `test_template_validation_flagged_but_consumption_survives` — patches `so.verify_policy` to False. `_ensure_template_valid` uses module-level `verify_policy` → False → logs error, `_TEMPLATE_VALIDATED = False`. The real close uses `interpret` which internally calls `verify_policy` imported inside `salienceos.interpreter.interpreter` — NOT the patched module attr. So the close succeeds. Asserts applied==10, window closed, flag False, log record. ✓ honest.

Hmm wait — subtle: `_ensure_template_valid` catches (Exception, SystemExit) and sets False. With verify_policy → False, `_TEMPLATE_VALIDATED = bool(False)` = False → logs error. ✓.

But does any test pin that `_ensure_template_valid` runs UNDER `_LOCK` (no deadlock)? If `_ensure_template_valid` tried to acquire `_LOCK` → deadlock → test hangs (not red, but timeout). Eh.

13. `test_bounded_iterations_never_raises_on_broken_home` — `get_hermes_home` raises SystemExit; no window/directive; `_budget_from_disk` → path construction calls get_hermes_home → SystemExit → caught by bounded_iterations → default. ✓ pins the SystemExit containment on the disk path.

Wait — actually, `_budget_from_disk` first checks `if session_id in _BUSES: return None` — empty after reset → proceeds → `from hermes_constants import get_hermes_home` — the test monkeypatches `hermes_constants.get_hermes_home`. The import inside the function gets the patched attr. ✓. SystemExit propagates: `_budget_from_disk` doesn't catch; `_resolve_bounded` doesn't catch; `bounded_iterations` catches (Exception, SystemExit). ✓ GREEN, and a regression to `except Exception` only would red it (SystemExit escapes → test errors). ✓ good.

Now — are there gaps where a sabotaged line keeps ALL tests green? The big one I found: the failed-close stale-cache path (finding above). Sabotage frame: after a failed `_close_locked`, stale `_LAST_DIRECTIVE` is applied — no test simulates a close failure at all. Also the design docstring for `_budget_from_disk` explicitly says the failed-close stale-read is bad, but only guards the disk path. So: production behavior arguably wrong + zero test coverage. That's my primary finding.

Let me also double-check the `_record` function for a weird bug:

```python
for signal in mapper(kwargs, window.subject):
    try:
        self_bus = _bus_for(session_id)
        self_bus.publish(signal)
        window.signals.append(signal)
    except Exception:
        logger.warning(...)
```

`self_bus` — odd name, fine. If publish fails, signal not appended to window.signals → interpret won't see it. Fine (goes dark).

One more consumer-misuse-of-vendored-API check: `_close_locked` emits directive then caches. `_bus_for(window.session_id).emit(directive)` — emit requires Directive type and shape; `interpret` returns a proper Directive. ✓.

`_budget_from_disk` uses `bus.directives_for(last_subject)` — returns tuple of dict copies. `_directive_budget(dict)` handles dict shape. The payload's compute_budget is an int (JSON round-trip). subject non-empty for real directives; hard-deny directives have blank subject → `last_subject` would be... if the last directive line is a hard-deny (subject ""), then `last_subject` = "" → falsy → `if not last_subject: return None` → default. ✓ nice — deny-shaped on disk → absent.

But WAIT — what if the last directive line has subject "" and there was an EARLIER valid directive? `last_subject` = "" → return None → default. Correct per A5 (most recent turn hard-denied → treat as absent → default). ✓.

What if the JSONL has signals but no directives (e.g., window never closed before process died)? `last_subject` stays None → None → default. ✓.

Now let me look for the "directive is not None → return _directive_budget(directive)" path again in `_resolve_bounded`:

```python
directive = _LAST_DIRECTIVE.get(session_id)
if directive is not None:
    return _directive_budget(directive)
return _budget_from_disk(session_id)
```

If the cached directive is deny-shaped → `_directive_budget` returns None → `bounded_iterations` returns default. ✓. But NOTE: it does NOT fall through to disk when the cached directive is deny-shaped — correct (in-memory authority).

Now, hmm, another A3 angle: THE CALL SITE runs at :491 for turn N. But `_resolve_bounded` closes "the open window" — which is turn N-1's — but ONLY if `window.turn_id != ` ... no, it closes whatever's open for the session. What if the open window at :491 is actually turn N's own because some path opened it early? The excerpts show pre_llm_call at :1054 — after. But is `build_turn_context` the only window opener? `_open_window` fires on pre_llm_call hook only. OK.

What about turn N's :491 running when turn N-1's pre_llm_call NEVER fired (e.g., turn N-1 errored before :1054)? Then the open window is turn N-2's (still open, since rollover close only happens in `_open_window` or close_session). Turn N's consumer closes turn N-2's window and applies its directive — 2-stale? Well, turn N-1 had no window/directive at all, so "turn N-1's directive" doesn't exist; applying the most recent available is defensible. The guarantee says "nor a 2-turns-stale one" — but with no turn N-1 window, there's nothing else to apply. This is inherent to the fail-closed attribution design. Not a defect — edge case semantics. LOW/skip.

Now the concurrency claim about `_operator_budget` "only safe under _LOCK": `_operator_budget` is called from `_close_locked` (always under lock per contract) and `_ensure_template_valid` (under lock via _resolve_bounded) and... anywhere else? Grep: `_close_locked` (budget None), `_resolve_bounded` floor fallback, `_ensure_template_valid`. All under _LOCK. ✓.

`_reset_for_tests` acquires _LOCK — fine.

Is there a path where `bounded_iterations` is called while the calling thread already holds `_LOCK`? The consumer call site is in turn_context, unrelated to observer internals. Unless some host code calls `bounded_iterations` from within a lifecycle hook that the observer processes... `observe_lifecycle` → `_open_window` holds `_LOCK` briefly; if any host hook invoked `bounded_iterations` synchronously... no evidence. Skip.

Deadlock via `SalienceBus` constructor while holding `_LOCK`: replay is synchronous I/O — no locks. OK.

Now, guarantee 1 deeper hunt — "returns a budget < 1 / non-int that would set max_iterations to 0":

`_directive_budget` enforces ≥1 int. `_budget_from_disk` returns `_directive_budget(...)` or None. `_resolve_bounded` returns those. `bounded_iterations` returns budget-or-default. Default could be 0 or negative if `agent.max_iterations` were 0 — but that's pre-existing host breakage; and the "never return <1" guarantee is about the DERIVED budget. Hmm, but strictly: "must NEVER return a budget < 1 (that would brick the agent at max_iterations 0)". If default is 0, the function returns 0 — but the agent was ALREADY at max_iterations 0 before the call. The consumer didn't brick it. Not a finding.

What about `default=True` (bool)? Returned unchanged → `agent.max_iterations = True` → IterationBudget(True) → `used >= True`... works as 1. Whatever — host passes int.

What about the recorded budget being an enormous int (e.g., 10**9 from a future widened policy)? Returned verbatim — Finding D says verbatim. By design.

OK now, let me also check `_ensure_template_valid`'s claim "Caller holds _LOCK (reads the operator budget cache)" — it does call `_operator_budget`. It's called only from `_resolve_bounded` under lock. ✓.

One more: `_consume_enabled` is called OUTSIDE the lock in `bounded_iterations` (before `_resolve_bounded`). It reads config — thread-safe? Config read is read-only; fine.

Race: between `_consume_enabled` (outside lock) and `_resolve_bounded` — kill switch flipped mid-call; benign.

Now — the `_looks_off` function: `value is None` → True (off). For `_config_flag`, missing key returns default before `_looks_off` is called (`key not in salience → return default`). If key present with value None → off. For `enabled` default True: `salience: {enabled: null}` → off. Deliberate ("clearly-off"). OK.

Hmm, `isinstance(value, int) and not isinstance(value, bool)` → `value == 0` off; `enabled: 2` → on. Fine.

Now the honesty review (item 6): docstrings... The module docstring says consumer "is wired live but INERT in v0 ... behavior-preserving by construction." Config example: "the directive echoes the operator's own configured budget, so consumption is behavior-preserving". Accurate given min==max==floor and floor==default at consume time. Hmm — is it EXACTLY behavior-preserving? The directive echoes the floor which is `default` (this turn's resolved max_iterations) — so `agent.max_iterations` is reassigned to itself. Behavior-preserving. ✓ unless the stale-cache bug fires (then it echoes an OLDER budget — could actually CHANGE behavior in v0 if the operator changed max_iterations mid-session! Wait — floor is turn N's default; the stale directive carries turn N-2's floor. If operator budget constant, stale == current. If changed at turn N-1... the stale directive has the value from when it was closed = turn N-1's default... hmm, actually the stale directive was closed at turn N-1's :491 with floor = turn N-1's default. Turn N applies it. If the operator changed budget at turn N (default_N ≠ default_N-1), turn N gets default_N-1 — off by one turn, only when close failed. Minor but real. Consistent with my MEDIUM.)

Now let me also examine: is `_close_locked`'s swallow of SystemExit OK? Yes, consistent.

Let me reconsider the failed-close path once more to make sure the stale directive is actually READ (not skipped):

```python
window = _WINDOWS.get(session_id)
if window is not None and not window.closed:
    _close_locked(window, budget=floor)
directive = _LAST_DIRECTIVE.get(session_id)
if directive is not None:
    return _directive_budget(directive)
```

`_close_locked` sets `window.closed = True` first, then try/except. On failure: `_LAST_DIRECTIVE` unchanged (stale from previous turn's successful close). `directive` = stale → `_directive_budget(stale)` → valid budget → returned. CONFIRMED. Turn N applies turn N-2's directive. Wait, indexing again: at turn N's :491, the open window is turn N-1's. The stale cache entry is from turn N-1's :491 (which closed turn N-2's window). So the stale directive is turn N-2's. Turn N applies turn N-2 → 2
