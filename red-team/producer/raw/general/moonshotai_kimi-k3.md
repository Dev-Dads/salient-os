# Red-team (producer, pass=general): moonshotai/kimi-k3

_finish=length seconds=671.9 usage={'prompt_tokens': 15778, 'completion_tokens': 12000, 'total_tokens': 27778, 'cost': 0.227334, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.227334, 'upstream_inference_prompt_cost': 0.047334, 'upstream_inference_completions_cost': 0.18}, 'completion_tokens_details': {'reasoning_tokens': 12000, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me carefully analyze this code for concrete defects under the stated guarantees. The task is to red-team PR-H1, a produce-only observer wiring SalienceOS into quorum-agent.

Let me go through the attack surfaces in order:

## 1. PRODUCE-ONLY / NEVER-CRASH-THE-HOST

The containment layers:
- `_safe_observe` in `observability/__init__.py` catches `except Exception`
- `observe_lifecycle` in salience_observer catches `except Exception`
- `invoke_hook` in lifecycle.py catches `except Exception`

The hunt: BaseException/SystemExit/KeyboardInterrupt paths from host APIs the observer calls:
- `read_raw_config_readonly` (config read) — called in `_config_flag` and `_operator_budget`
- `get_hermes_home` — called in `_bus_for`
- `mkdir`/`open` — in `_bus_for` and `SalienceBus`
- salienceos calls — `issue_policy`, `interpret`, `SalienceBus.publish/emit`

Let me check each:

**`_config_flag`**: 
```python
try:
    from hermes_cli.config import read_raw_config_readonly
    cfg = read_raw_config_readonly() or {}
except Exception:
    return False
```
If `read_raw_config_readonly` raises `SystemExit` (like `get_config_value` did), this escapes. But the material says the fix was to use `read_raw_config_readonly` instead of `get_config_value`. We don't see the implementation of `read_raw_config_readonly` — it's "readonly" so presumably doesn't sys.exit. But we can't verify. Hmm. The prompt says the already-fixed item is `get_config_value` calling `sys.exit()`. The new function `read_raw_config_readonly` is not shown. Can I claim it might sys.exit? That would be speculative — no concrete trigger. Unless... the name suggests it's a programmatic read. I should not report without concrete evidence. Actually wait — the instruction says "hunt specifically for a BaseException/SystemExit/KeyboardInterrupt path from a host API the observer calls (config read, get_hermes_home, mkdir/open, the salienceos calls) that escapes those guards — beyond the already-fixed get_config_value."

Let me think about what could raise BaseException:

1. **`os.urandom(32)` at module import** — `_POLICY_KEY = os.urandom(32)` at module level. If this fails... it's at import time, and the import of the module happens inside `observe_lifecycle` in `__init__.py` (`from . import relay_shared_metrics, salience_observer`). If the import raises, `_safe_observe`... wait, no. The import is inside `observe_lifecycle` function body in `__init__.py`, which is called from `_safe_observe`? No wait:

```python
def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    from . import relay_shared_metrics, salience_observer
    _safe_observe(relay_shared_metrics.observe_lifecycle, hook_name, kwargs)
    _safe_observe(salience_observer.observe_lifecycle, hook_name, kwargs)
```

The import `from . import relay_shared_metrics, salience_observer` is OUTSIDE `_safe_observe`. If importing `salience_observer` raises (e.g., `os.urandom` fails — extremely unlikely; or some other module-level code), then `observe_lifecycle` raises... but it's called from `lifecycle.invoke_hook`:

```python
try:
    from hermes_cli.observability import observe_lifecycle
    observe_lifecycle(hook_name, **kwargs)
except Exception:
    logger.warning(...)
```

So `invoke_hook` catches Exception around the whole `observe_lifecycle` call. An ImportError (Exception subclass) would be caught there. But a BaseException during import? `os.urandom` doesn't raise SystemExit. KeyboardInterrupt during import would escape — but KeyboardInterrupt can happen anywhere anytime; that's not a concrete defect.

Actually, wait — there's a subtlety with module-level import failure: if `salience_observer` import fails partway, the module may be left in `sys.modules` in a partially initialized state or removed. But again, caught by invoke_hook's except Exception.

2. **`hashlib.sha256`** — doesn't raise BaseException.

3. **`get_hermes_home()`** — unknown implementation. Could it sys.exit? It's from `hermes_constants`. Typically returns a path. Not shown. Speculative.

4. **`Path.mkdir(parents=True, exist_ok=True)`** — raises OSError subclasses (Exception). Caught.

5. **`open(path, "a")`** in bus `_append` — OSError. Caught by observer's try/except in `_record` and `_close_locked`.

6. **`SalienceBus(path)` constructor** — `_replay` raises ValueError on corrupt file. That's an Exception, caught in `_record`'s loop try/except and `_close_locked`'s try/except. Wait — but in `_bus_for`, the exception propagates to the caller. Callers: `_record` (inside try/except Exception around `self_bus = _bus_for(session_id); self_bus.publish(signal)`) — yes it's inside the try. `_close_locked` — `_bus_for(window.session_id).emit(directive)` is inside the try/except Exception. OK.

Hmm wait, actually there's something interesting in `_record`:

```python
for signal in mapper(kwargs, window.subject):
    try:
        self_bus = _bus_for(session_id)
        self_bus.publish(signal)
        window.signals.append(signal)
    except Exception:
        logger.warning(...)
```

If `_bus_for` raises (e.g., corrupt bus file → ValueError from `_replay`), the exception is caught, logged, and... the loop continues to the next signal, calling `_bus_for` again, which raises again (since `_BUSES[session_id]` was never set — the assignment `_BUSES[session_id] = bus` happens after `SalienceBus(str(path))` succeeds). So each signal retries the bus open, fails again. That's just wasted I/O, not a crash. Fine.

But wait — there's a worse issue: if the bus file is corrupt, `_replay` raises ValueError every time, and the observer logs a warning per signal per turn forever. Log spam, but not a crash. Also the directive emit in `_close_locked` will fail the same way. The window gets closed, signals lost. Fail-dark, acceptable for produce-only.

7. **Memory exhaustion via `window.signals`** — unbounded list growth within a turn? A turn with many tool calls accumulates signals in `window.signals` (in-memory list) AND on the bus. How many tool calls per turn? Bounded by max_iterations presumably. Each signal is small. Probably fine. But what about a turn that never closes? If `pre_llm_call` is called with the same turn_id repeatedly... no, window stays open, signals accumulate. Tool calls per turn are bounded by the agent loop. OK.

8. **`_BUSES` growth without session close**: `_bus_for` is called from `_record` only when a window exists, and from `_close_locked`. Windows are keyed by session_id. If sessions never emit `on_session_end/finalize/reset`, `_WINDOWS` and `_BUSES` grow. But that's the host's contract — the already-fixed leak was about freeing on session close. If the host never closes sessions, that's a host issue. Hmm, but is there a path where a bus is created for a session without a window? `_record` checks window first, then `_bus_for`. `_close_locked` uses `window.session_id`. So buses only exist for sessions with (or that had) windows. Windows are created in `_open_window` only with valid ids. OK.

Wait, actually there's a subtle one: in `_open_window`, when a new turn arrives, `_close_locked(current)` is called, which calls `_bus_for(window.session_id)` — creating a bus even if NO signals were ever recorded (e.g., a turn with zero tool calls). So every turn emits a directive, creating a bus file per session. That's by design (one directive per turn).

9. **`_subject` includes raw `turn_id`** — the subject is `(hash[:16] + ":" + turn_id)[:128]`. The turn_id is NOT hashed! Guarantee 3 says "The raw session_id must never reach the durable record — both the subject token and the bus filename hash it." Only session_id needs hashing. turn_id is presumably not sensitive. But wait — is turn_id bounded? `turn_id` comes from the host. If turn_id is huge (e.g., a megabyte string), `_subject` truncates to 128. OK. But what about `_ids`: `str(kwargs.get("turn_id") or "")` — no bound, but it's only used for comparison and subject construction (truncated). Fine.

Hmm, but wait: what if `turn_id` contains something sensitive? Not in scope — guarantee is about session_id.

10. **Hash truncation to 16 hex chars** — 64 bits. Collision across sessions would merge subjects... but buses are per-session (full hash filename), and windows are keyed by raw session_id. The subject is only used within a session's bus. A collision in the 16-char prefix between two different sessions doesn't matter because they're on different bus files. Within a session, same prefix always. OK.

11. **`_session_hash` filename** — full sha256 hexdigest. Fine.

12. **THE BIG ONE — let me look at `_open_window` more carefully:**

```python
def _open_window(kwargs: dict) -> None:
    session_id, turn_id = _ids(kwargs)
    if not session_id or not turn_id:
        return
    task_id = str(kwargs.get("task_id") or "")
    with _LOCK:
        current = _WINDOWS.get(session_id)
        if current is not None and not current.closed and current.turn_id != turn_id:
            _close_locked(current)
        if current is None or current.closed or current.turn_id != turn_id:
            _WINDOWS[session_id] = _Window(...)
```

Wait — what if `current is not None and not current.closed and current.turn_id == turn_id`? Then no new window, keep accumulating. Fine.

What if `current is not None and current.closed and current.turn_id == turn_id`? Condition `current is None or current.closed or ...` → True → new window created for the same turn_id. When can a window be closed but still in `_WINDOWS`? `_close_locked` is called from `_open_window` (previous window, different turn — stays in dict until replaced) and from `_close_session` (which POPS from `_WINDOWS` first). So after `_open_window` closes the previous window, it immediately replaces it (since `current.closed` → condition true). So a closed window never lingers in `_WINDOWS`... except: in `_open_window`, if `_close_locked(current)` runs and then the second `if` — `current.closed` is now True → creates new window. OK so closed windows are always replaced immediately. And `_close_session` pops. So `_WINDOWS` never holds a closed window. Fine.

13. **A3 ordering**: "Turn N's window is finalized before turn N+1 accumulates." In `_open_window`, the previous window is closed (directive emitted) before the new window is created. Good. But — the directive for turn N is emitted to the bus while holding `_LOCK`. `interpret` and `emit` do file I/O under the lock. Single-threaded contract OK.

14. **Concurrency**: All `_WINDOWS`/`_BUSES` access under `_LOCK`. Let me verify: `_open_window` — yes. `_record` — yes. `_close_session` — yes. `_close_locked` — called with lock held (from `_open_window` and `_close_session`). `_bus_for` — called from `_record` (lock held) and `_close_locked` (lock held). `_operator_budget` — called from `_close_locked` (lock held). `_reset_for_tests` — takes lock. 

Hmm wait — is there a deadlock risk? `_LOCK` is a `threading.Lock` (non-reentrant). `_close_session` acquires `_LOCK`, calls `_close_locked`, which calls `_bus_for` and `_operator_budget` — none of these acquire `_LOCK` again. `_open_window` acquires, calls `_close_locked` — no re-acquire. OK, no deadlock.

But — `_reset_for_tests` acquires `_LOCK` and clears. Fine.

15. **The `interpret` call**: `interpret(policy, tuple(window.signals), _POLICY_KEY)`. We don't see `interpret`'s signature in the material (it's in the vendored package, not shown except signal/policy/bus). The observer passes the policy, signals tuple, and key. Presumably correct.

16. **Now the seam — guarantee 6**: "Adding the observer must NOT change invoke_hook's return value, the dispatch to relay_shared_metrics, or the EFFECT of any hook."

Look at `observability/__init__.py`:

```python
def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    from . import relay_shared_metrics, salience_observer
    _safe_observe(relay_shared_metrics.observe_lifecycle, hook_name, kwargs)
    _safe_observe(salience_observer.observe_lifecycle, hook_name, kwargs)
```

`invoke_hook` returns `plugins.invoke_hook(hook_name, **kwargs)` — observer results don't affect it. Good.

`handles_hook`:
```python
return (
    relay_shared_metrics.handles_hook(hook_name)
    or salience_observer.handles_hook(hook_name)
)
```

Enabling salience flips `has_hook` True for `post_tool_call` and `api_request_error` — expected and acceptable per the brief ("firing a previously-dead observational hook... is expected and acceptable").

BUT — `pre_llm_call` is in `SALIENCE_HANDLED_HOOKS`! So `handles_hook("pre_llm_call")` returns True when salience is enabled. Was `pre_llm_call` previously handled by relay_shared_metrics or plugins? The brief says: "Enabling salience flips has_hook True only for the observational post_tool_call and api_request_error emitters (previously dead by default); pre_verify and transform_tool_result stay unhandled; **pre_llm_call's context-injecting return is consumed from PLUGINS only, never from the observer.**"

Hmm, so the claim is that pre_llm_call's return is consumed from plugins only. Look at the emit site in turn_context.py:

```python
_pre_results = _invoke_hook("pre_llm_call", ...)
```

`invoke_hook` returns `plugins.invoke_hook(...)` results — the observer's return (None) is not included. So the context-injection consumption is from plugins only. The observer's `observe_lifecycle` returns None and its result is discarded by `lifecycle.invoke_hook` (which calls `observe_lifecycle` for side effects only, then returns plugins' results). So even though the observer "handles" pre_llm_call, its return value never reaches the `_pre_results`. Good.

But wait — does `has_hook("pre_llm_call")` gate anything at the pre_llm_call emit site? Looking at the emit site: it calls `_invoke_hook` directly without checking `has_hook` first. So no gating change there.

Hmm, but here's a question: does `has_hook("pre_llm_call")` get used elsewhere to decide whether to emit? Not shown. The brief explicitly says flipping has_hook True for pre_llm_call... wait, it says "flips has_hook True only for the observational post_tool_call and api_request_error emitters". But `SALIENCE_HANDLED_HOOKS` includes `pre_llm_call`, `on_session_end`, `on_session_finalize`, `on_session_reset` too! So enabling salience ALSO flips `has_hook` True for `pre_llm_call`, `on_session_end`, `on_session_finalize`, `on_session_reset` (assuming relay/plugins don't already handle them).

Is that a guarantee violation? Guarantee 6 says: "Enabling salience flips has_hook True only for the observational post_tool_call and api_request_error emitters (previously dead by default)". This is a statement about the expected effect. If `has_hook("pre_llm_call")` flips True and some emit site gates on it and changes behavior based on the hook's return... The pre_llm_call emit site shown doesn't gate on has_hook. But are there OTHER emit sites that gate on `has_hook("pre_llm_call")` or `has_hook("on_session_end")` etc. and change behavior? Not shown in the material.

Hmm, wait. Let me re-read: "pre_llm_call's context-injecting return is consumed from PLUGINS only, never from the observer." This is the guarantee. The mechanism: `lifecycle.invoke_hook` returns only `plugins.invoke_hook(...)` results. Let me double check lifecycle.py:

```python
def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    try:
        from hermes_cli.observability import observe_lifecycle
        observe_lifecycle(hook_name, **kwargs)
    except Exception:
        logger.warning("Built-in observability hook failed", exc_info=True)
    from hermes_cli import plugins
    return plugins.invoke_hook(hook_name, **kwargs)
```

Yes — observer results are discarded; return value is purely plugins'. So the guarantee holds at this seam.

But hold on — what about `has_hook` gating at OTHER emit sites for the session hooks? E.g., if `on_session_end` emitter gates on `has_hook("on_session_end")` and previously it was dead (no plugins), now enabling salience makes it fire. Is firing it "changing the EFFECT of a hook"? The brief says firing a previously-dead observational hook is "expected and acceptable" — but only mentions post_tool_call and api_request_error. Hmm, it says "Enabling salience flips has_hook True only for the observational post_tool_call and api_request_error emitters (previously dead by default)". The word "only" — if in fact it also flips pre_llm_call, on_session_end, on_session_finalize, on_session_reset, then the statement in the guarantee is inaccurate, but is it a DEFECT? The guarantee's core is: don't change invoke_hook's return, don't change relay dispatch, don't change hook EFFECTS. Firing dead observational hooks is acceptable. pre_llm_call is NOT purely observational — it's context-injecting! If some code path checks `has_hook("pre_llm_call")` to decide whether to build the messages list or do expensive work, flipping it could change behavior. But the shown emit site doesn't gate. 

Actually wait — there's something more subtle. Look at the pre_llm_call emit site again:

```python
_pre_results = _invoke_hook("pre_llm_call", session_id=..., ..., conversation_history=list(messages), ...)
```

The observer receives `user_message=original_user_message` and `conversation_history=list(messages)` — THE FULL PROMPT AND HISTORY! Does the observer put any of that on the bus? `_open_window` only extracts session_id, turn_id, task_id. So no. The audit fence holds. But the kwargs contain the full conversation — the observer ignores them. Fine.

17. **Now the AUDIT FENCE (attack surface 3)**: "any input (tool_name/status/provider/args) that puts unbounded or non-ref content on the bus, OR makes a produced signal FAIL valid_signal."

Look at `_map_tool_call`:
```python
tool_name = str(kwargs.get("tool_name") or "")
status = str(kwargs.get("status") or "")
error_type = str(kwargs.get("error_type") or "")
...
provenance = _ref("tool:" + tool_name, "status:" + status)
```

`_ref` truncates each to 128 chars and drops empties:
```python
def _ref(*parts: str) -> tuple:
    out = []
    for part in parts:
        token = str(part)[:MAX_TOKEN_LEN]
        if token:
            out.append(token)
    return tuple(out[:16])
```

So provenance tokens are bounded to 128. But — `"tool:" + tool_name` — if tool_name is exactly at the boundary... truncated to 128 total. Fine.

**KEY QUESTION: can a produced signal FAIL valid_signal?**

`valid_signal` requires:
- `type(s) is SalienceSignal` — yes, constructed directly.
- `_ref_token(s.subsystem_id)` — SUBSYSTEM_ID = "quorum.observer", fine.
- `_ref_token(s.subject)` — subject from `_subject()`, bounded to 128, non-empty (hash prefix is 16 chars + ":" + turn_id; turn_id non-empty since `_ids` checks). Wait — `_subject` returns `(hash[:16] + ":" + turn_id)[:128]`. Non-empty always. Fine.
- `_ref_token(s.facet)` — Facet constants, fine.
- `_unit(s.influence)` — 0.7, 0.6, 0.4, 0.5, 0.8 — all in [0,1]. Fine.
- `_unit(s.confidence)` — 1.0. Fine.
- `isinstance(s.provenance, tuple)` — `_ref` returns tuple. Fine.
- `len(s.provenance) <= 16` — `_ref` caps at 16. Fine.
- `all(_ref_token(p) for p in s.provenance)` — each token is `str(part)[:128]`, non-empty (empties dropped). Fine.

Hmm wait — what if `tool_name` is not a string but something whose `str()` raises? `str()` on arbitrary objects can raise if `__str__` is broken — but that's an Exception, caught. What if `str(part)` returns a non-str? `__str__` must return str or TypeError. Fine.

What about `status` being bytes? `str(b"error")` = `"b'error'"` — bounded. Fine.

What about influence values: `_map_api_error` — `influence = 0.5 if retryable is True else 0.8`. Fine.

So produced signals always pass valid_signal? Let me check the subject more carefully. `_subject(session_id, turn_id)`:

```python
return (_session_hash(session_id)[:16] + ":" + turn_id)[:MAX_TOKEN_LEN]
```

`_session_hash` returns hexdigest (64 chars), [:16] → 16 chars. + ":" + turn_id, truncated to 128. Non-empty. OK.

Hmm, but wait — what about `SalienceSignal(SUBSYSTEM_ID, subject, facet, influence, 1.0, provenance)` — positional args: subsystem_id, subject, facet, influence, confidence, provenance. Matches the dataclass field order. Good.

18. **Now FAIL-CLOSED ATTRIBUTION (attack surface 2)**: "a concrete sequence of hook calls that records a signal with no open window, a closed window, a mismatched turn_id, or cross-session/cross-turn."

`_record` checks: window exists, not closed, turn_id matches. Windows keyed by session_id. 

Hmm — what about the SAME turn_id across DIFFERENT sessions? Windows are keyed by session_id, so session A's window and session B's window are separate. A post_tool_call with session_id=B, turn_id=X records against B's window if turn matches. Fine.

What about turn_id REUSE within a session? If turn "u1" is finalized (window closed and replaced by "u2"), then a late post_tool_call with turn_id="u1" arrives → window.turn_id is "u2" ≠ "u1" → dropped. Good.

But here's a scenario: turn "u1" window closed by `_close_session` (popped). Then `pre_llm_call` with turn_id="u1" AGAIN (retry? resume?). New window created for "u1". New signals recorded. New directive emitted on close. Two directives for the same subject on the bus. Is that a violation? A3 says turn N finalized before N+1. Reopening a turn... The host presumably doesn't reuse turn_ids. Not a concrete defect without evidence of reuse.

**Cross-session signal leakage via `_record`**: none — keyed lookup.

Hmm, wait. Let me look at `_open_window` again for a subtle bug:

```python
current = _WINDOWS.get(session_id)
if current is not None and not current.closed and current.turn_id != turn_id:
    _close_locked(current)
if current is None or current.closed or current.turn_id != turn_id:
    _WINDOWS[session_id] = _Window(session_id, turn_id, task_id, _subject(session_id, turn_id))
```

Scenario: `pre_llm_call` for turn u1. Then `pre_llm_call` for turn u1 AGAIN (duplicate, e.g., retry within same turn — actually pre_llm_call fires before EVERY LLM call, and a turn may involve multiple LLM calls!).

WAIT. This is important. `pre_llm_call` — "pre" every LLM call. In an agent loop, within ONE turn there are typically MANY LLM calls (one per iteration: LLM → tool calls → LLM → ...). Does `pre_llm_call` fire once per turn or once per LLM call? The name suggests per LLM call! The emit site is in `turn_context.py` — "pre_llm_call (context injected into user message...)". Hmm, if it fires per LLM call with the SAME turn_id, then `_open_window` with same turn_id → keeps existing window. Fine — idempotent.

But what if turn_id changes per LLM call within what the host considers... no, turn_id is the turn identifier. If the host passes a different turn_id per LLM call, then each pre_llm_call would finalize the previous "turn" — emitting a directive per LLM call rather than per turn. That would violate "one directive per turn" but it's a host-semantics question we can't resolve from the material. The tests use distinct turn_ids per turn. Can't flag concretely.

19. **The `on_session_reset` case**: `_close_session` pops window and bus, emitting final directive. If a session is reset and then CONTINUES (reset ≠ end?), subsequent pre_llm_call reopens a window and `_bus_for` RE-CREATES the bus — reopening the same JSONL file and replaying. That works (bus replays). OK.

20. **Now, the freed-bus-late-hook claim in `_close_session`:**

```python
_WINDOWS.pop(session_id, None)
if window is not None and not window.closed:
    _close_locked(window)
_BUSES.pop(session_id, None)
```

Comment says "A late hook for this session now hits _record's 'no window' guard and is dropped, so freeing here is safe."

But wait — there's a subtle issue: `_close_locked(window)` calls `_bus_for(window.session_id)` which may CREATE a bus (if none existed — e.g., a turn with no signals). Then after `_close_locked` returns, `_BUSES.pop(session_id, None)` removes it. Fine.

But what if a LATE `pre_llm_call` arrives for the session after close (session reuse? late event)? `_open_window` creates a new window; `_record` → `_bus_for` re-creates the bus (replays file). So a late hook doesn't crash; it just reopens. The test `test_session_close_frees_registries` asserts a late `_record` (without reopening) is dropped. OK.

21. **Resource: unbounded growth on long-lived host.** `_WINDOWS` and `_BUSES` are freed on session close. But what if `on_session_end/finalize/reset` never fires for a session (host crash, or a host that doesn't emit these)? Then entries leak. But that's the host contract; the already-fixed finding was the leak, now freed on close. Is there a NEW leak? 

Hmm — `_bus_for` creates a bus file on disk per session: `<home>/salience/<hash>.jsonl`. Files are never deleted! On a long-lived host with many sessions, the `salience/` directory grows unboundedly ON DISK. The guarantee 9 says "no per-session leak on a long-lived host (freed on session close)" — that's about in-memory registries. Disk growth is inherent to an audit log (append-only durable record). Probably by design — it's an audit bus. Not a defect per the guarantees.

22. **Now let me hunt the BaseException paths harder.**

The three containment layers all catch `except Exception`. What host APIs does the observer call that could raise BaseException?

- `read_raw_config_readonly()` — unknown impl. The fixed bug was `get_config_value` calling `sys.exit()`. Does `read_raw_config_readonly` call `sys.exit` internally? Name suggests a clean programmatic read. We can't see it. Hmm. The brief says "beyond the already-fixed get_config_value" — implying there might be another. Let me think about what's visible.

- `get_hermes_home()` — from `hermes_constants`. Unknown. Could it raise SystemExit? Constants modules usually compute a path. Speculative.

- `Path.mkdir` / `open` — OSError only.

- `hashlib.sha256` — no.

- `os.urandom` — module level, no.

- `salienceos` calls: `issue_policy` → `sign(...)` — vendored, reviewed. `interpret` — vendored. `SalienceBus.publish/emit` — raise TypeError (Exception). `_replay` raises ValueError. All Exception subclasses.

- `str(kwargs.get(...))` — `__str__` can raise arbitrary exceptions, including... well, a malicious `__str__` could raise SystemExit! But who controls kwargs? The host emit sites pass strings or None. `model_tools._emit_post_tool_call_hook` passes `session_id or ""` etc. — strings. `run_agent` passes `self.session_id or ""`. `turn_context` passes `agent.session_id`. These are host-controlled strings, not attacker-controlled objects with evil `__str__`. Not concrete.

- **`logger.warning(..., exc_info=True)`** — logging can raise? Logging handles its own errors by default (raiseExceptions=False in production for handlers, but `logging.raiseExceptions` is True in development... actually handler errors print to stderr, don't raise, when raiseExceptions... no wait: `logging.raiseExceptions` controls whether handler exceptions are raised — default is True, which means... hmm, actually in Python, `Handler.handle` catches exceptions in `emit` only if `raiseExceptions` is False? Let me recall: `Handler.handleError` is called when emit raises; if `logging.raiseExceptions` is True (default), it prints a traceback to stderr but does NOT re-raise. So logging doesn't propagate. Fine.

Hmm, what about **`_config_flag`'s import**: `from hermes_cli.config import read_raw_config_readonly` — if `hermes_cli.config` module has module-level code that raises SystemExit on import... The import happens inside `try/except Exception`. SystemExit during import would escape `_config_flag` → `salience_enabled` → `handles_hook` → `observability.handles_hook` → `lifecycle.has_hook`:

```python
def has_hook(hook_name: str) -> bool:
    try:
        from hermes_cli.observability import handles_hook
        if handles_hook(hook_name):
            return True
    except Exception:
        logger.warning(...)
    from hermes_cli import plugins
    return plugins.has_hook(hook_name)
```

`except Exception` doesn't catch SystemExit. So IF `hermes_cli.config` import raises SystemExit, it escapes `has_hook` → `_emit_post_tool_call_hook`:

```python
try:
    from hermes_cli.lifecycle import has_hook, invoke_hook
    if not has_hook("post_tool_call"):
        return
    ...
except Exception as _hook_err:
    logger.debug(...)
```

Also `except Exception` — SystemExit escapes to the tool dispatch path! That would break the host. BUT this requires `hermes_cli.config` import to raise SystemExit, which is speculative — the module is already imported elsewhere in the host almost certainly (config is core). Once in sys.modules, the import is a dict lookup. Not concrete.

What about `read_raw_config_readonly` itself raising SystemExit? It's the "programmatic read path" — the fix for exactly this class of bug. Claiming it also sys.exits without evidence is speculative. The brief explicitly says the get_config_value fix is accepted; I shouldn't re-report it or invent that its replacement has the same bug without evidence.

Hmm, but wait — actually, let me reconsider. Is there any VISIBLE code path in the material that raises BaseException? Let me scan again...

`sys.exit` — not present. `KeyboardInterrupt` — only from signals (anywhere). `SystemExit` — not present in shown code. `GeneratorExit` — no generators. `StopIteration`/`StopAsyncIteration` — those are Exceptions anyway (well, StopIteration is Exception).

Actually — hold on. Let me look at `_safe_observe` and the dispatch again:

```python
def _safe_observe(callback: Any, hook_name: str, kwargs: dict[str, Any]) -> None:
    try:
        callback(hook_name, **kwargs)
    except Exception:
        ...
```

`callback(hook_name, **kwargs)` — if kwargs contains keys that clash... `salience_observer.observe_lifecycle(hook_name, **kwargs)` — signature is `(hook_name: str, **kwargs)`. If the host emits a hook with a kwarg NAMED `hook_name`! Then `callback(hook_name, hook_name=...)` → TypeError "got multiple values for argument 'hook_name'". That's an Exception → caught. Just goes dark. Not a crash. But wait — could a kwarg named `hook_name` cause the observer to receive the WRONG hook_name? No — TypeError, caught.

Hmm, but actually there's a subtler one: `relay_shared_metrics.observe_lifecycle` is called FIRST with the same kwargs. Not our concern.

23. **Now the SEAM (attack surface 4)**: "any way enabling salience changes invoke_hook's return value, the relay dispatch, hook ordering, or the EFFECT of a hook."

`observe_lifecycle` in `__init__.py` calls relay first, then salience. Order: relay before salience — was relay always first? Before this PR, presumably `observe_lifecycle` only called relay. Now it also calls salience after. Relay dispatch unchanged (still called, still first, still via _safe_observe). 

Hmm wait — one subtle thing: `from . import relay_shared_metrics, salience_observer` — this import now imports `salience_observer` module, which at MODULE LEVEL does `os.urandom(32)` and tries importing salienceos. If the salienceos import has side effects or is slow... it's vendored, fine. But here's a thought: if `salience_observer` module import FAILS (raises Exception at import time — e.g., `os.urandom` unavailable on some platform? No...), then `observe_lifecycle` raises BEFORE calling `_safe_observe(relay_shared_metrics...)` — wait no:

```python
def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    from . import relay_shared_metrics, salience_observer
    _safe_observe(relay_shared_metrics.observe_lifecycle, hook_name, kwargs)
    _safe_observe(salience_observer.observe_lifecycle, hook_name, kwargs)
```

If `from . import ... salience_observer` raises (module-level exception during first import), then `observe_lifecycle` raises before relay is dispatched! The caller `lifecycle.invoke_hook` catches it (`except Exception`) — but RELAY_SHARED_METRICS NEVER GETS CALLED for that hook! That changes the relay dispatch — a SEAM violation!

But wait — what could make the salience_observer module import fail? Module-level code: imports (hashlib, logging, os, threading, typing — all safe), the salienceos import (guarded by try/except Exception → _IMPORT_OK=False), `_POLICY_KEY = os.urandom(32)`. `os.urandom` can raise... on some platforms if the entropy source fails? CPython docs: "On a Unix-like system, ... if getrandom() fails... " Actually `os.urandom` can raise `NotImplementedError`? Hmm, historically it could raise if no entropy source. Extremely unlikely. Also — the salienceos import guard catches `except Exception`, but what if importing salienceos raises a BaseException (e.g., the vendored package does something at import that raises SystemExit)? Vendored code is reviewed; out of scope.

Also: once imported successfully, subsequent imports are sys.modules lookups. The failure window is first import only. And if the first import fails partway, Python removes the partial module from sys.modules, so EVERY subsequent `from . import salience_observer` retries and re-raises — meaning relay_shared_metrics would be permanently starved IF the import consistently fails. But again — concrete trigger? `os.urandom` failure is not realistic. The salienceos import is guarded. Hmm.

Actually wait — there IS a realistic import-failure: `from salienceos.interpreter import Facet, SalienceSignal, interpret, issue_policy` — if the vendored package exists but `interpret` or `issue_policy` is NOT exported from `salienceos.interpreter` (e.g., they live in submodules `salienceos.interpreter.policy` / wherever and `__init__` doesn't re-export), that's an ImportError → caught by the guard → `_IMPORT_OK = False`. Fine, handled.

What about `from salienceos.interpreter.bus import SalienceBus` — same guard. Fine.

So the import-failure-starves-relay path requires a module-level BaseException or os.urandom failure — not concrete. LOW at most. Hmm. Actually, is it worth mentioning as a robustness note? The ordering issue (import outside _safe_observe, before relay dispatch) is a real structural fragility: ANY exception during salience_observer import prevents relay dispatch for that hook (caught upstream, but relay starved). But since invoke_hook catches it and the import is retried each time... relay would be starved for EVERY hook if the import deterministically fails. The realistic deterministic import failure mode is guarded (salienceos). What remains: `os.urandom(32)`. I'll note it as LOW.

Hmm wait, actually, let me reconsider — is there something that makes import fail deterministically? `_POLICY_KEY = os.urandom(32)` — no. Logging — no. OK, LOW.

24. **Now let me look at `handles_hook` being called on the hot path and doing config I/O:**

`salience_enabled()` → `_config_flag("enabled", True)` → `read_raw_config_readonly()` — A DISK READ (config file parse) on EVERY `handles_hook` call! And `handles_hook` is called from `has_hook` at every emit site — `post_tool_call` per tool call, `api_request_error` per error, etc. And `observe_lifecycle` also calls `handles_hook` per event. So every tool call now does (at least) two config file reads (has_hook + observe_lifecycle→handles_hook). This is a PERFORMANCE issue, not a correctness one. The guarantees don't cover performance directly... "Never changes what the agent does" — performance degradation could count? The brief's attack surfaces don't include performance except resource growth. Config reads are cached by the config module maybe (`read_raw_config_readonly` might cache). Unknown. Not a guarantee violation per se. Skip or LOW.

Hmm, wait — actually there's a correctness angle: `_config_flag` failing closed means if the config read transiently fails (e.g., disk hiccup), salience goes dark mid-session — windows already open stay open, but `handles_hook` returns False so `observe_lifecycle` returns early and... `_close_session` never runs for the final flush! The window leaks until... `_reset_for_tests`? No, in production it leaks forever (session in `_WINDOWS`/`_BUSES` forever if the gate closes before on_session_end). But that's a transient config failure scenario. Also if the operator flips the kill switch mid-session, the open window is never finalized and the registries leak. Minor. The guarantee 5 says explicit false → OFF. Going dark mid-session with a leak is a minor robustness issue. LOW.

25. **TEST HONESTY (attack surface 6)**: "any guarantee above whose test would stay green if the code were sabotaged."

Let me examine the tests:

a) `test_records_only_against_matching_open_window` — asserts wrong-turn drop and matching record. Mutation: delete turn_id check → count becomes 2 → red. Good. But note: it calls `so._record` DIRECTLY, bypassing the gate. Fine for unit test.

b) `test_no_ids_no_window` — asserts `_WINDOWS == {}` after empty ids. Good.

c) `test_subject_hashes_session_and_is_bounded` — checks raw session not in subject, bounded, deterministic. Good.

d) `test_close_emits_one_directive_and_is_idempotent` — calls `_close_session` twice, asserts 1 directive. Wait — the second `_close_session` pops nothing (already popped), so of course no second directive. Does this test actually test `_close_locked` idempotency? `_close_locked` has `if window.closed: return`. The test never calls `_close_locked` twice on the same window. The idempotency claim is weakly tested but the behavior (one directive per window) is tested. OK-ish.

e) `test_new_turn_finalizes_previous` — good, asserts directive for u1 after opening u2.

f) `test_emitted_directive_binds_operator_budget` — config returns max_iterations 7, asserts compute_budget == 7. Note: `_operator_budget` is memoized via `_OPERATOR_BUDGET_CACHE`, and `_reset_for_tests` clears it (autouse fixture). Good — otherwise test pollution. 

g) `test_e2e_through_real_tool_dispatch` — the big one. Uses `lifecycle.invoke_hook("pre_llm_call", ...)` and `model_tools._emit_post_tool_call_hook(...)`. Asserts facets == ["memory", "verification"], one directive, chain verifies, session_id not in subject.

Wait — there's a potential issue with the E2E: `lifecycle.invoke_hook` also calls `plugins.invoke_hook`. In the test environment, are there plugins registered that might interfere? Probably not. Also `relay_shared_metrics.observe_lifecycle` gets called — does it do anything with these hooks in the test env? Unknown, presumably inert or its own thing.

Hmm, also: `model_tools._emit_post_tool_call_hook` with `status=None` default derives fields — the test passes explicit status. Fine.

Now — TEST HONESTY issues:

**Issue A**: In `test_e2e_through_real_tool_dispatch`, the gate is forced open by monkeypatching `so.salience_enabled`. But `lifecycle.has_hook` → `observability.handles_hook` → `salience_observer.handles_hook` → `salience_enabled()` — the monkeypatched `so.salience_enabled` is looked up as a module attribute at call time (`salience_enabled()` called inside `handles_hook` refers to the module global). `monkeypatch.setattr(so, "salience_enabled", lambda: True)` replaces the module global, and `handles_hook` calls `salience_enabled()` — which resolves via module globals → the patch. Good, genuinely wired.

**Issue B**: `test_only_mapped_hooks_are_handled(home)` — asserts `handles_hook("pre_verify") is False` and `transform_tool_result` False. Good for seam guarantee.

**Issue C**: Is there a test that `invoke_hook`'s RETURN VALUE is unchanged (guarantee 6)? The E2E doesn't assert anything about `invoke_hook` returning plugins' results unchanged. No test covers "pre_llm_call's context-injecting return is consumed from PLUGINS only, never from the observer." If someone sabotaged `lifecycle.invoke_hook` to append observer results to the return... but that's host code, not this PR. The PR's seam code is `observability/__init__.py` — `observe_lifecycle` returns None and its result is discarded by `lifecycle.invoke_hook`. Could a mutation in THIS PR's code change invoke_hook's return? `observe_lifecycle` in `__init__.py` doesn't return anything; `lifecycle.invoke_hook` ignores it. There's no mutation within the PR's files that changes the return... unless `salience_observer.observe_lifecycle` returned something and `__init__.observe_lifecycle` returned it — but lifecycle.py ignores it anyway. So the guarantee is structurally enforced by lifecycle.py (host, shown). No test needed? The guarantee says the seam must not change invoke_hook's return — that's lifecycle.py's behavior, which is shown and correct. A test would be nice but its absence isn't a dishonest test; it's a missing test. The attack surface asks for tests that "would stay green if the code were sabotaged." Missing tests aren't green-staying tests per se, but the spirit includes untested guarantees. Hmm.

**Issue D — the big one**: Look at `test_unreadable_config_fails_closed` and `_config_flag`:

```python
def _config_flag(key: str, default: bool) -> bool:
    try:
        from hermes_cli.config import read_raw_config_readonly
        cfg = read_raw_config_readonly() or {}
    except Exception:
        return False
```

The test monkeypatches `hermes_config.read_raw_config_readonly` to raise. But `_config_flag` does `from hermes_cli.config import read_raw_config_readonly` — a FROM-import INSIDE the function. At call time, this imports the name from the module — monkeypatch.setattr(hermes_config, "read_raw_config_readonly", _boom) sets the module attribute, and the from-import retrieves the current attribute → the boom function. So the test genuinely exercises it. Good.

BUT — `_operator_budget` also does `from hermes_cli.config import read_raw_config_readonly`. Same pattern. Fine.

**Issue E**: `test_kill_switch_disables` — patches config to `{"salience": {"enabled": False}}`, asserts `salience_enabled() is False`. `_config_flag("enabled", True)`: salience dict present, "enabled" in salience → `return salience.get("enabled") is not False` → `False is not False` → False. Good.

Wait, actually — there's a subtle bug in `_config_flag`! Consider `salience: {"enabled": 0}` or `{"enabled": "false"}` (string) or `{"enabled": None}`. `salience.get("enabled") is not False` — `0 is not False` → True (0 is not the False singleton... wait, `0 is False`? No — `False == 0` but `False is not 0`; they're different objects. Small int caching: `False` is a singleton bool, `0` is an int. `0 is False` → False). So `enabled: 0` → treated as ON. `enabled: "false"` → ON. `enabled: None` → ON. Is that a defect? The guarantee: "explicit false => OFF". YAML `enabled: false` parses to Python `False`. `enabled: 0` in YAML is int 0 — is that an "explicit false"? Arguably the operator wrote something falsy. The strict `is not False` means only literal `False` disables. This is a deliberate strict reading ("explicit false"). Hmm — but consider YAML `enabled: off` → parses to... in YAML 1.1, `off` → False! In YAML 1.2, `off` is a string "off". Depends on parser. If the config parser is YAML 1.1-ish (PyYAML safe_load: `off` → False? PyYAML implements YAML 1.1 where `off`/`no`/`n` → False... actually PyYAML's SafeLoader resolves `off` to False? Let me recall: PyYAML bool resolution includes `on/off/yes/no/true/false` (1.1). Yes, PyYAML SafeLoader parses `off` → False, `no` → False.) So `enabled: off` → False → disabled. Good.

But `enabled: "false"` (quoted string) → "false" is not False → ON. An operator writing `"false"` with quotes gets ON. Edge case, debatable. The guarantee says "explicit false => OFF" — a quoted "false" string is arguably explicit. LOW at most. Actually, the strict `is not False` is defensible as fail-safe design (only unambiguous off). I'll skip or LOW.

Hmm wait, actually there's a more interesting direction: what about non-dict `salience`? `salience: true` (bool)? `isinstance(salience, dict)` False → return default (True) → ON. Fine.

**Issue F — `_config_flag` returning non-bool**: `return salience.get(key) is not False` — always bool. And `salience_enabled` returns `_config_flag(...)` — bool. OK.

**Issue G**: Now, a REAL test-honesty check — `test_e2e_through_real_tool_dispatch` asserts `facets == ["memory", "verification"]`. The two tool calls: `write_file` success → MEMORY; `run_shell` error → VERIFICATION. Sorted → ["memory", "verification"]. Good. And `len(bus.directives_for(subject)) == 1`. And `bus.verify_chain() is True`. And `session_id not in subject`.

Would this test stay green under sabotage? E.g., if `_record` dropped the turn_id check — E2E uses matching turn_ids, so it'd stay green — but the unit test `test_records_only_against_matching_open_window` covers that. If the subject stopped hashing — `session_id not in subject` reds. If the bus filename stopped hashing — `_bus_file` computes the expected path via `so._session_hash` — CIRCULAR! `_bus_file(tmp_path, session_id)` uses `so._session_hash(session_id)` to compute the expected filename. If someone sabotaged the filename to use the RAW session_id, then `path.exists()`... wait, the test computes `path = _bus_file(home, session_id)` = hash-based path, and asserts `path.exists()`. If the code used raw session_id for the filename, the hash-based path wouldn't exist → red. OK not circular for the filename. But if someone sabotaged `_session_hash` itself to return the raw session id... then `_bus_file` would compute raw-based path, the code would write raw-based path, `path.exists()` green, and `session_id not in subject` — subject uses `_session_hash(session_id)[:16]` = raw[:16] — `session_id` = "sess-e2e" (8 chars) — is "sess-e2e" in subject? subject = "sess-e2e"[:16]... wait `_session_hash("sess-e2e")` sabotaged to return "sess-e2e", [:16] = "sess-e2e", subject = "sess-e2e:turn-1". `"sess-e2e" in subject` → True → assert fails → red. OK. And `test_subject_hashes_session_and_is_bounded` uses a real sha256 expectation? It asserts `"super-secret-session" not in subject` — if `_session_hash` returned identity, subject = "super-secret-sess..."[:16]+":turn-9" → contains "super-secret-session"? [:16] of "super-secret-session" = "super-secret-ses" — the full string "super-secret-session" (20 chars) is NOT in "super-secret-ses:turn-9". So that assert would stay GREEN under identity-hash sabotage! But `test_subject_hashes_session_and_is_bounded` also asserts `subject.endswith(":turn-9")` — green. Hmm, so an identity-hash sabotage: does ANY test red? E2E: `session_id not in subject` — "sess-e2e" in "sess-e2e:turn-1" → True → RED. Good, E2E catches it. OK.

But hold on — is there a test asserting the subject equals a KNOWN-GOOD sha256 prefix? No — all tests use `so._session_hash` or `so._subject` to compute expectations, which is somewhat circular: if `_session_hash` used md5 instead of sha256, no test would red. But the guarantee is "hashed, one-way" not specifically sha256. Fine.

**Issue H — the conftest disable and `handles_hook`**: conftest patches `salience_enabled` to False suite-wide. The observer's tests opt back in. Fine.

**Issue I**: `test_closed_gate_produces_nothing_through_dispatch` — forces gate closed, drives real dispatch, asserts no windows and no salience dir. Good. But note: `_force_gate(monkeypatch, tmp_path, False)` patches `so.salience_enabled` to `lambda: False`. Then `lifecycle.has_hook("post_tool_call")` → relay or salience handles? relay_shared_metrics.handles_hook("post_tool_call") — unknown; if relay handles post_tool_call, has_hook would be True regardless! The test asserts `lifecycle.has_hook("post_tool_call") is False` — so in the test env relay must not handle it. OK, presumably true. This test would red if the observer ignored the gate. Good.

26. **Now let me think harder about guarantee 2 (fail-closed attribution) — cross-session/cross-turn.**

Scenario: Session A open window turn u1. Session B open window turn u1. post_tool_call(session_id=A, turn_id=u1) → records to A. Fine.

Scenario: `_open_window` for session A turn u1; `_open_window` for session A turn u2 (finalizes u1); now a late post_tool_call(session A, turn u1) → dropped (window is u2). Good.

Scenario: TWO hosts/processes sharing the same bus file? Single-process test rig. Out of scope.

Scenario: `turn_id` collision across sessions — windows keyed by session, subjects include session hash. Fine.

Hmm, what about the **`_record` mapper receiving `window.subject`** — the subject is bound at window open. Signals recorded against the window's subject. Directive uses same subject. Consistent.

27. **Guarantee 7 (A3)**: "Turn N's window is finalized (its directive emitted) before turn N+1 accumulates." In `_open_window`, `_close_locked(current)` runs BEFORE creating the new window — under the same lock. Good. But — what finalizes the LAST turn of a session if neither on_session_end/finalize/reset fires? The directive for the last turn is emitted only on session close or next turn. If the session just ends without those hooks (host crash), no directive — acceptable (produce-only, crash).

Wait, actually — there's a subtle A3 violation path: `_close_locked` emits the directive via `_bus_for(window.session_id).emit(directive)`. If `emit` raises (caught), the directive is LOST but the window is marked closed. Turn N's directive never emitted, turn N+1 proceeds. Fail-dark, not an ordering violation. Fine.

28. **Guarantee 8 (A4)**: min==max==operator budget. `_operator_budget` reads config paths `("agent", "max_iterations"), ("max_iterations",), ("agent", "iteration_budget")`. Memoized. `issue_policy(..., budget, budget, ...)
