# Red-team (producer, pass=general): z-ai/glm-5.2

_finish=length seconds=114.1 usage={'prompt_tokens': 15621, 'completion_tokens': 12000, 'total_tokens': 27621, 'cost': 0.0746694, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0746694, 'upstream_inference_prompt_cost': 0.0218694, 'upstream_inference_completions_cost': 0.0528}, 'completion_tokens_details': {'reasoning_tokens': 12383, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me carefully analyze this code for concrete defects under the stated guarantees.

Let me go through each attack vector:

**1. PRODUCE-ONLY / NEVER-CRASH-THE-HOST (BaseException escape)**

The containment layers:
- `_safe_observe` in `__init__.py`: `except Exception`
- `observe_lifecycle` in `salience_observer.py`: `except Exception`
- `invoke_hook` in `lifecycle.py`: `except Exception`

I need to find a path where a `BaseException` (like `SystemExit`, `KeyboardInterrupt`, `GeneratorExit`) escapes these guards.

Let me trace the calls the observer makes:
- `read_raw_config_readonly()` - already fixed (uses programmatic read)
- `get_hermes_home()` - called in `_bus_for`
- `directory.mkdir()` - called in `_bus_for`
- `SalienceBus(str(path))` - constructor, which calls `_replay` if file exists
- `SalienceBus.publish()` - calls `valid_signal`, `digest`, `open()`, `json.dumps`
- `SalienceBus.emit()` - similar
- `issue_policy()` - calls `sign()`
- `interpret()` - vendored

The `_replay` method in `SalienceBus.__init__` can raise `ValueError` - but that's an `Exception`, caught by the observer's `except Exception` in `_record` and `_close_locked`.

Wait, let me look more carefully. In `_bus_for`, the `SalienceBus(str(path))` constructor is called. If the file exists and is corrupt, `_replay` raises `ValueError`. Where is `_bus_for` called?

1. In `_record`: inside the `with _LOCK:` block, inside a `try/except Exception` - so `ValueError` is caught. Good.
2. In `_close_locked`: inside a `try/except Exception` - caught. Good.

But wait - `_bus_for` is also called directly in tests, but that's test code.

Let me look at `_bus_for` more carefully. It does:
```python
directory = Path(get_hermes_home()) / "salience"
directory.mkdir(parents=True, exist_ok=True)
path = directory / (_session_hash(session_id) + ".jsonl")
bus = SalienceBus(str(path))
```

If `get_hermes_home()` raises a `SystemExit` or `KeyboardInterrupt`... but that would be unusual. Let me check if `get_hermes_home` could do that. We don't have the source, but the context says the already-fixed item was `get_config_value()` calling `sys.exit()`. `get_hermes_home` is a different function.

Actually, let me think about `os.urandom(32)` at module level - `_POLICY_KEY = os.urandom(32)`. This runs at import time. If it raises... but `os.urandom` is very unlikely to raise `BaseException`. And the import itself is guarded by the `try/except Exception` around the import of salienceos.

Wait, actually `_POLICY_KEY = os.urandom(32)` is at module level in `salience_observer.py`, not inside the try/except. If `os.urandom` raises something... but that's extremely unlikely and would happen at import time, not during a hook call.

Let me look at the `SalienceBus._append` method. It does `open(self._path, "a", encoding="utf-8")` and `json.dumps(entry, sort_keys=True)`. If `json.dumps` encounters something it can't serialize... but the entries are carefully constructed dicts with simple types. `asdict(signal)` produces a dict with string/float/tuple values - all JSON-serializable.

Hmm, what about `digest()`? We don't have its source, but it's from the vendored code. If `digest` raises a `BaseException`... but that's vendored internals, out of scope.

Let me think about `KeyboardInterrupt` specifically. If the user hits Ctrl+C while the observer is in the middle of `_bus_for` (e.g., during `mkdir` or `open`), the `KeyboardInterrupt` would propagate up through `_record` or `_close_locked`. Both have `except Exception` which doesn't catch `KeyboardInterrupt`. Then it would propagate to `observe_lifecycle` which has `except Exception` - still doesn't catch it. Then to `_safe_observe` in `__init__.py` - `except Exception` - still doesn't catch it. Then to `invoke_hook` in `lifecycle.py` - `except Exception` - still doesn't catch it.

But wait - `KeyboardInterrupt` during a hook is a user-initiated interrupt. The guarantee says "never lets an exception (including a BaseException like SystemExit) reach the host." But `KeyboardInterrupt` is a user action to kill the process - is that really "reaching the host" in a meaningful sense? The host would also be interrupted. This seems like it's not really a defect - if the user hits Ctrl+C, the whole process should stop.

Let me focus on `SystemExit` specifically. Is there any call path where `SystemExit` could be raised?

- `read_raw_config_readonly()` - already fixed, uses programmatic read
- `get_hermes_home()` - could this call `sys.exit()`? We don't have the source. The context says the author already caught and fixed `get_config_value()` which calls `sys.exit()`. But `get_hermes_home()` is a different function. Let me check if there's any indication...

Actually, looking at the conftest, `get_hermes_home` is monkeypatched to return a tmp_path. In production, it likely reads an env var or config. It's unlikely to call `sys.exit()`.

Let me look at `SalienceBus.__init__` more carefully. It calls `os.path.exists(path)` and then `_replay(path)` if the file exists. `_replay` calls `open(path, encoding="utf-8")`. If the file is corrupt, it raises `ValueError` (an `Exception`). That's caught.

What about `json.loads` in `_replay`? It raises `json.JSONDecodeError` which is a subclass of `ValueError` - an `Exception`. Caught.

Hmm, let me look at the `sign` and `signature_valid` functions in the vendored code. We don't have their source, but `verify_policy` catches `TypeError` and `ValueError` from `signature_valid`. The `sign` function is called in `issue_policy` which is called from `_close_locked` inside a `try/except Exception`. So even if `sign` raises something unusual, it would be caught as long as it's an `Exception`.

Let me think about `interpret()`. It's called from `_close_locked` inside `try/except Exception`. So any `Exception` is caught.

OK, I think the BaseException path is hard to trigger concretely. Let me move on.

**2. FAIL-CLOSED ATTRIBUTION**

Let me trace the window lifecycle:

1. `pre_llm_call` → `_open_window(kwargs)`: opens a window for (session_id, turn_id)
2. `post_tool_call` → `_record(kwargs, _map_tool_call)`: records against matching window
3. `api_request_error` → `_record(kwargs, _map_api_error)`: records against matching window
4. `on_session_end/finalize/reset` → `_close_session(kwargs)`: closes and frees

In `_open_window`:
- Gets session_id, turn_id from kwargs
- If either is empty, returns (no window)
- If current window exists and is for a different turn, closes it first
- Creates new window if needed

In `_record`:
- Gets session_id, turn_id from kwargs
- If either empty, returns
- Gets window from _WINDOWS[session_id]
- If window is None, closed, or turn_id doesn't match, returns (drop)
- Otherwise, publishes signals

This looks correct. Let me think about edge cases...

What if `on_session_reset` is called? It calls `_close_session`, which pops the window and bus. But what if a `post_tool_call` comes after `on_session_reset` for the same session? The window would be gone, so `_record` would find no window and drop. That's correct.

What about cross-session? The window is keyed by session_id in _WINDOWS. A signal for session "a" can't be recorded against session "b"'s window because `_record` looks up `_WINDOWS.get(session_id)` with the session_id from the signal's kwargs. Correct.

What about the subject? The subject is `_subject(session_id, turn_id)` = `hash(session_id)[:16] + ":" + turn_id`. The window stores this subject, and signals are created with this subject. So signals for a window always carry the correct subject. Correct.

Wait, let me look at `_record` more carefully:

```python
def _record(kwargs: dict, mapper) -> None:
    session_id, turn_id = _ids(kwargs)
    if not session_id or not turn_id:
        return
    with _LOCK:
        window = _WINDOWS.get(session_id)
        if window is None or window.closed or window.turn_id != turn_id:
            return  # no matching open window ⇒ drop (fail-closed)
        for signal in mapper(kwargs, window.subject):
            try:
                self_bus = _bus_for(session_id)
                self_bus.publish(signal)
                window.signals.append(signal)
            except Exception:
                logger.warning("salience observer: publish failed", exc_info=True)
```

The mapper creates signals with `window.subject`. The `publish` call validates the signal with `valid_signal`. If `valid_signal` returns False, `publish` raises `TypeError`, which is caught by the `except Exception`. The signal is not appended to `window.signals` (because the append is after publish). So a signal that fails `valid_signal` is dropped with a log message. That's correct behavior per the guarantees.

But wait - could a signal FAIL `valid_signal`? Let me check what the mapper produces:

`_signal(subject, facet, influence, provenance)` creates:
```python
SalienceSignal(SUBSYSTEM_ID, subject, facet, influence, 1.0, provenance)
```

- `SUBSYSTEM_ID = "quorum.observer"` - bounded string, OK
- `subject` - from `_subject()`, bounded to MAX_TOKEN_LEN, OK
- `facet` - from `Facet.VERIFICATION` etc., which are short strings like "verification", OK
- `influence` - 0.4, 0.6, 0.7, 0.5, 0.8 - all in [0,1], OK
- `confidence` - 1.0, OK
- `provenance` - from `_ref()`, which bounds each token to MAX_TOKEN_LEN and limits to 16 refs, OK

So `valid_signal` should always pass for these signals. Good.

But wait - what about `_ref`? Let me look:

```python
def _ref(*parts: str) -> tuple:
    out = []
    for part in parts:
        token = str(part)[:MAX_TOKEN_LEN]
        if token:
            out.append(token)
    return tuple(out[:16])
```

This truncates each token to MAX_TOKEN_LEN and limits to 16 refs. `MAX_PROVENANCE_REFS` is 16 in the vendored code. So `len(provenance) <= 16` is satisfied. And each token is `0 < len(token) <= MAX_TOKEN_LEN`. But wait - what if `str(part)` is empty? Then `token` would be `""`, and `if token:` would be False, so it wouldn't be added. Good.

But what if `part` is not a string? `str(part)` converts it. So `str(None)` = `"None"`, which is a non-empty string. That's fine - it's a bounded ref token.

Actually, wait. Let me look at `_map_tool_call`:

```python
provenance = _ref("tool:" + tool_name, "status:" + status)
```

`tool_name = str(kwargs.get("tool_name") or "")` - so if tool_name is a very long string, `"tool:" + tool_name` could be very long, but `_ref` truncates to MAX_TOKEN_LEN. OK.

What about `status`? Same thing. OK.

Let me look at `_map_api_error`:

```python
provenance = _ref("api_error", "provider:" + str(kwargs.get("provider") or ""))
```

Same - bounded by `_ref`. OK.

So I don't see a way to make a signal fail `valid_signal`. The audit fence seems solid.

**3. AUDIT FENCE**

Can any input put unbounded or non-ref content on the bus?

The signals are created by the mappers, which use `_ref` for provenance and bounded strings for other fields. The subject is bounded. The facet is a constant. The influence is a constant. The confidence is 1.0.

The directive is created by `interpret()`, which is vendored code. The directive payload is validated by `_valid_directive_shape` in `emit()`. So even if `interpret` produced something weird, `emit` would reject it.

But wait - what if `interpret` raises an exception? That's caught by `_close_locked`'s `try/except Exception`. The directive wouldn't be emitted. OK.

What about the `args` and `result` fields in `post_tool_call` kwargs? The observer receives them but never puts them on the bus. `_map_tool_call` only uses `tool_name`, `status`, and `error_type`. It never touches `args` or `result`. Good.

What about `user_message` and `conversation_history` in `pre_llm_call`? The observer receives them but `_open_window` only uses `session_id`, `turn_id`, and `task_id`. Good.

**4. SEAM SAFETY**

Does enabling salience change `invoke_hook`'s return value?

`invoke_hook` calls `observe_lifecycle` (which returns None) and then `plugins.invoke_hook` (which returns a list). The observer's `observe_lifecycle` returns None. So the return value of `invoke_hook` is always from `plugins.invoke_hook`. Enabling salience doesn't change this. Good.

Does enabling salience change the relay dispatch? `observe_lifecycle` calls both `relay_shared_metrics.observe_lifecycle` and `salience_observer.observe_lifecycle`. They're independent. Enabling salience doesn't affect the relay. Good.

Does enabling salience change hook ordering? `observe_lifecycle` calls relay first, then salience. This ordering is fixed regardless of whether salience is enabled. And `invoke_hook` calls `observe_lifecycle` first, then `plugins.invoke_hook`. This is also fixed. Good.

Does enabling salience change the EFFECT of a hook? The observer only records; it doesn't modify any state that affects the agent. The `pre_llm_call` hook in `turn_context.py` uses `_pre_results` from `invoke_hook`, which returns the results of `plugins.invoke_hook`. The observer's `observe_lifecycle` returns None and doesn't contribute to the return value. So the observer can't change the effect. Good.

Wait, actually let me re-read `invoke_hook`:

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

`observe_lifecycle` is called for its side effects, and its return value is discarded. `invoke_hook` returns `plugins.invoke_hook(...)`. So the observer can't affect the return value. Good.

But wait - `has_hook` is also affected:

```python
def has_hook(hook_name: str) -> bool:
    try:
        from hermes_cli.observability import handles_hook
        if handles_hook(hook_name):
            return True
    except Exception:
        logger.warning("Unable to inspect built-in observability hooks", exc_info=True)
    from hermes_cli import plugins
    return plugins.has_hook(hook_name)
```

When salience is enabled, `handles_hook("post_tool_call")` returns True, so `has_hook("post_tool_call")` returns True. This means `_emit_post_tool_call_hook` will proceed to emit the hook when it previously would have no-op'd (if no plugin registered for it). This is explicitly called out in the guarantees as acceptable: "Enabling salience flips has_hook True only for the observational post_tool_call and api_request_error emitters (previously dead by default)."

But does this change the EFFECT of the hook? The emitter calls `invoke_hook("post_tool_call", ...)`, which calls `observe_lifecycle` (records the signal) and then `plugins.invoke_hook` (which returns [] if no plugin). The return value is discarded by the emitter. So the effect is just that the observer records a signal. That's produce-only. Good.

Actually, wait. Let me think about this more carefully. The `has_hook` gate in `_emit_post_tool_call_hook` controls whether the result fields are derived:

```python
if not has_hook("post_tool_call"):
    return
if status is None:
    status, error_type, error_message = _tool_result_observer_fields(result)
```

When salience is enabled, `has_hook("post_tool_call")` returns True, so the emitter proceeds to derive status fields and call `invoke_hook`. The `invoke_hook` call passes `args=function_args` and `result=result` to the observer. The observer receives these but never puts them on the bus. So the audit fence holds.

But is there a performance concern? The result-field derivation (`_tool_result_observer_fields(result)`) now runs when it previously wouldn't have. This is a cost, not a correctness issue. And it's called out as expected behavior.

**5. CONCURRENCY / RESOURCE**

All access to `_WINDOWS`, `_BUSES`, and `_OPERATOR_BUDGET_CACHE` is under `_LOCK`. Let me verify:

- `_open_window`: `with _LOCK:` - yes
- `_record`: `with _LOCK:` - yes
- `_close_session`: `with _LOCK:` - yes
- `_close_locked`: called from `_open_window` (under `_LOCK`) and `_close_session` (under `_LOCK`) - yes
- `_bus_for`: called from `_record` (under `_LOCK`) and `_close_locked` (under `_LOCK`) - yes
- `_operator_budget`: called from `_close_locked` (under `_LOCK`) - yes
- `_reset_for_tests`: `with _LOCK:` - yes

Wait, but `_bus_for` does file I/O (`mkdir`, `SalienceBus` constructor which may read a file) while holding `_LOCK`. This means that if one session's bus file is slow to read (e.g., a large corrupt file), it blocks all other sessions' hook processing. This is a performance concern but not a correctness defect per se. And the single-threaded bus contract says all access is serialized under `_LOCK`, so this is by design.

Is there a deadlock risk? `_LOCK` is a single `threading.Lock()`, not a reentrant lock. Are there any calls that try to acquire `_LOCK` while already holding it?

- `_open_window` acquires `_LOCK`, then calls `_close_locked` (which doesn't acquire `_LOCK`) and `_bus_for` (which doesn't acquire `_LOCK`). No re-entrancy.
- `_close_session` acquires `_LOCK`, then calls `_close_locked` (no `_LOCK`) and `_bus_for` (no `_LOCK`). No re-entrancy.
- `_record` acquires `_LOCK`, then calls `_bus_for` (no `_LOCK`). No re-entrancy.

No deadlock. Good.

Resource: `_WINDOWS` and `_BUSES` are freed on session close (`_close_session` pops both). The budget cache is process-global and never freed (except by `_reset_for_tests`), but it's a single int, so no leak. Good.

But wait - what if `on_session_end`/`on_session_finalize`/`on_session_reset` is never called? Then the window and bus would leak. But that's a host behavior issue, not an observer defect. The observer can't control whether the host calls these hooks.

Actually, let me think about `on_session_reset`. If a session is reset, `_close_session` is called, which pops the window and bus. But what if the session continues after reset? The next `pre_llm_call` would open a new window. That's correct.

**6. TEST HONESTY**

Let me look at the tests for mutation-blindness:

`test_records_only_against_matching_open_window`: The comment says "deleting the turn_id check in _record makes the final count 2 and reds this test." Let me verify. If the turn_id check is deleted, the wrong-turn signal would be recorded, making the count 2 instead of 1. Yes, this test would catch that mutation. Good.

`test_no_ids_no_window`: Opens windows with empty session_id and empty turn_id, asserts `_WINDOWS == {}`. If the empty-id checks in `_open_window` were deleted, the windows would be created and this test would fail. Good.

`test_close_emits_one_directive_and_is_idempotent`: Calls `_close_session` twice, asserts one directive. If `_close_locked` weren't idempotent, it would emit two directives. But wait - the second `_close_session` pops the window from `_WINDOWS` (returns None since it was already popped), so `_close_locked` isn't called the second time. The idempotency of `_close_locked` itself (the `if window.closed: return` check) isn't actually tested here because the second call doesn't reach it. Let me re-read...

```python
def _close_session(kwargs: dict) -> None:
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return
    with _LOCK:
        window = _WINDOWS.pop(session_id, None)
        if window is not None and not window.closed:
            _close_locked(window)
        _BUSES.pop(session_id, None)
```

First call: pops window, window is not None and not closed, calls `_close_locked` which sets `window.closed = True` and emits directive. Pops bus.

Second call: pops window (None, already popped), doesn't call `_close_locked`. Pops bus (None, already popped).

So the test is actually testing that `_close_session` is idempotent (via the pop), not that `_close_locked` is idempotent. If `_close_locked`'s `if window.closed: return` were deleted, this test would still pass because `_close_locked` is never called twice. This is a mutation-blind spot.

But is this a real concern? The only way `_close_locked` could be called twice on the same window is if there's a code path that does so. Looking at the code, `_close_locked` is called from:
1. `_open_window` (when a new turn finalizes the previous one)
2. `_close_session` (when the session ends)

In `_open_window`, it's called on `current` which is the existing window. After `_close_locked`, `current.closed = True`. Then a new window is created. So `_close_locked` won't be called on `current` again.

In `_close_session`, the window is popped, so it can't be called again.

So `_close_locked`'s idempotency check is defensive but not reachable in normal operation. The test doesn't actually test it. But this is a minor test-honesty issue, not a correctness defect.

Let me look at `test_session_close_frees_registries`: Asserts `"s" not in so._WINDOWS` and `"s" not in so._BUSES` after `_close_session`. If the `_BUSES.pop` were deleted, the second assertion would fail. Good. If the `_WINDOWS.pop` were deleted... well, `_WINDOWS.pop(session_id, None)` is the first thing in the `with _LOCK` block. If it were deleted, `window` would be undefined (NameError), which would be caught by... wait, `_close_session` doesn't have a try/except. The exception would propagate to `observe_lifecycle`'s `except Exception`. So the session wouldn't be freed, and the test would fail. Good.

Actually wait, let me re-read `_close_session`:

```python
def _close_session(kwargs: dict) -> None:
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return
    with _LOCK:
        window = _WINDOWS.pop(session_id, None)
        if window is not None and not window.closed:
            _close_locked(window)
        _BUSES.pop(session_id, None)
```

No try/except here. But `observe_lifecycle` has one:

```python
def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    if not handles_hook(hook_name):
        return
    try:
        ...
        elif hook_name in ("on_session_end", "on_session_finalize", "on_session_reset"):
            _close_session(kwargs)
    except Exception:
        logger.warning("salience observer hook failed: %s", hook_name, exc_info=True)
```

So if `_close_session` raises, it's caught. But the test calls `_close_session` directly, not through `observe_lifecycle`. So the test would see the exception. OK.

Let me look at `test_emitted_directive_binds_operator_budget`: Sets `max_iterations: 7` in config, asserts `directive["compute_budget"] == 7`. If the `_operator_budget` function were deleted or returned the default, the assertion would fail. Good.

But wait - the test monkeypatches `read_raw_config_readonly` but doesn't monkeypatch `salience_enabled`. It uses... let me check. The test doesn't use the `home` fixture. It uses `monkeypatch` and `tmp_path` directly. It monkeypatches `get_hermes_home` and `read_raw_config_readonly` but doesn't force the gate open. So `salience_enabled()` would use the real gate, which is patched by conftest to return False. But the test calls `_open_window`, `_record`, and `_close_session` directly, not through `observe_lifecycle`. These functions don't check `salience_enabled()`. So the gate doesn't matter for these direct calls. Good.

Actually, wait. The `_reset_state` fixture calls `_reset_for_tests()` before and after. And the conftest patches `salience_enabled` to return False. But the test calls internal functions directly, bypassing the gate. So the test is testing the internals, not the gated path. That's fine for testing the budget binding.

Let me look at `test_e2e_through_real_tool_dispatch`: This test uses the `home` fixture (which forces the gate open) and drives through the real dispatch chain. It calls `lifecycle.invoke_hook("pre_llm_call", ...)` and `model_tools._emit_post_tool_call_hook(...)`. This tests the full path from emitter to observer. Good.

But wait - the test asserts `lifecycle.has_hook("post_tool_call") is True`. This is the gate check. If `handles_hook` returned False for `post_tool_call`, the emitter would no-op and the test would fail (no signals recorded). Good.

Let me check `test_closed_gate_produces_nothing_through_dispatch`: Forces the gate closed, asserts `has_hook("post_tool_call") is False`, and asserts nothing was written. If the gate check in `handles_hook` were deleted, `has_hook` would return True, the emitter would fire, and signals might be recorded. But the test asserts `so._WINDOWS == {}` and no salience directory exists. If the gate were bypassed, `_open_window` would create a window, and `_WINDOWS` would not be empty. So this test would catch that mutation. Good.

Now let me think about what the tests might miss...

The `test_close_emits_one_directive_and_is_idempotent` test - as I noted, it doesn't actually test `_close_locked`'s idempotency. But is that a real concern? The idempotency check is defensive. If it were removed, would anything break? In normal operation, `_close_locked` is never called twice on the same window. So removing the check wouldn't change behavior. The test is testing the wrong thing (it tests `_close_session`'s idempotency via pop, not `_close_locked`'s). This is a minor test-honesty issue.

Let me look for other issues...

Actually, let me re-examine the `_open_window` function more carefully:

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
            _WINDOWS[session_id] = _Window(
                session_id, turn_id, task_id, _subject(session_id, turn_id)
            )
```

What if `current` is not None, not closed, and `current.turn_id == turn_id`? Then neither branch fires, and the existing window stays. This is correct - same turn, no need to reopen.

What if `current` is not None, not closed, and `current.turn_id != turn_id`? Then `_close_locked(current)` is called (sets `current.closed = True`, emits directive). Then the second `if` checks: `current.closed` is True, so a new window is created. Correct.

What if `current` is not None and `current.closed`? Then the first `if` fails (because `not current.closed` is False). The second `if` checks: `current.closed` is True, so a new window is created. Correct.

What if `current` is None? First `if` fails. Second `if`: `current is None` is True, so new window created. Correct.

OK, this looks correct.

Now let me think about a subtle issue. In `_open_window`, when a new turn finalizes the previous one, `_close_locked(current)` is called. This calls `_bus_for(window.session_id)` to emit the directive. But `_bus_for` creates the bus if it doesn't exist. So the bus is created during `_open_window` (inside `_close_locked`). Then when `_record` is called for the new turn, `_bus_for` returns the existing bus. This is fine.

But what if `_close_locked` fails (exception caught)? The window is still marked `closed = True` (that happens before the try/except). The directive is not emitted. The bus may or may not exist (depending on where the exception occurred). The new window is created. Signals for the new turn are recorded. The previous turn's directive is lost. Is this a problem?

The guarantee says "Turn N's window is finalized (its directive emitted) before turn N+1 accumulates." If the directive emission fails, the window is still closed (so N+1 can accumulate), but the directive is lost. This is a graceful degradation - the observer goes dark for that turn but doesn't block the next turn. Is this a violation of A3?

Hmm, A3 says "Turn N's window is finalized (its directive emitted) before turn N+1 accumulates." If the directive isn't emitted, is the window "finalized"? The code sets `window.closed = True` before trying to emit, so from the window's perspective, it's finalized. But the directive wasn't emitted. This is a gray area. I'd say this is acceptable - the observer is produce-only and should go dark rather than block the host. The alternative (not closing the window if emission fails) would mean N+1's signals might be attributed to N's window, which is worse.

Actually, wait. Let me re-read `_close_locked`:

```python
def _close_locked(window: _Window) -> None:
    if window.closed:
        return
    window.closed = True
    try:
        budget = _operator_budget()
        policy = issue_policy(...)
        directive = interpret(policy, tuple(window.signals), _POLICY_KEY)
        _bus_for(window.session_id).emit(directive)
    except Exception:
        logger.warning("salience observer: window finalize failed", exc_info=True)
```

`window.closed = True` is set before the try/except. So even if emission fails, the window is closed. This means:
1. The directive for this turn is lost (not emitted).
2. The signals for this turn were already published to the bus (in `_record`), so they're on the bus but without a corresponding directive.
3. The next turn can proceed.

Is this a problem? The signals are on the bus but the directive is missing. The bus chain is still valid (signals were published with correct prev hashes). The missing directive means the audit record is incomplete for that turn. But the observer is produce-only and this is a graceful failure. I'd say this is acceptable.

Let me now think about whether there's a way to make `_bus_for` raise a `BaseException`...

`_bus_for` does:
1. `from pathlib import Path` - import, unlikely to raise
2. `from hermes_constants import get_hermes_home` - import, unlikely to raise
3. `Path(get_hermes_home()) / "salience"` - if `get_hermes_home()` returns something weird, `Path()` might raise `TypeError` (an Exception)
4. `directory.mkdir(parents=True, exist_ok=True)` - might raise `OSError` (an Exception) if the path is too long or permission denied
5. `directory / (_session_hash(session_id) + ".jsonl")` - string concatenation, unlikely to raise
6. `SalienceBus(str(path))` - constructor, might raise `ValueError` from `_replay` (an Exception)

All of these are `Exception` subclasses, caught by the surrounding try/except. I don't see a `BaseException` path here.

What about `os.urandom(32)` at module level? If the system's entropy source is unavailable, `os.urandom` might raise `NotImplementedError` on some platforms. But that's an `Exception`, and it happens at import time, not during a hook call. The import of `salience_observer` would fail, but... actually, the import of `salience_observer` happens in `observe_lifecycle` in `__init__.py`:

```python
def observe_lifecycle(hook_name: str, **kwargs: Any) -> None:
    from . import relay_shared_metrics, salience_observer
    _safe_observe(relay_shared_metrics.observe_lifecycle, hook_name, kwargs)
    _safe_observe(salience_observer.observe_lifecycle, hook_name, kwargs)
```

If the import of `salience_observer` fails (because `os.urandom` raised), the `from . import ... salience_observer` would raise. This is inside `observe_lifecycle` which is called from `invoke_hook`:

```python
def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    try:
        from hermes_cli.observability import observe_lifecycle
        observe_lifecycle(hook_name, **kwargs)
    except Exception:
        logger.warning("Built-in observability hook failed", exc_info=True)
    ...
```

So the `Exception` would be caught. But `os.urandom` raising `NotImplementedError` is an `Exception`, so it's caught. And this would only happen once (at first import), after which the module would be in `sys.modules` (possibly partially initialized). Actually, if the module raises during initialization, Python marks it as failed and subsequent imports would re-raise. But the `from . import` is inside a function, so it's re-executed each time. And the `try/except Exception` in `invoke_hook` would catch it each time. So the host wouldn't crash, but the observer would be permanently dark. That's acceptable.

Hmm, but what if `os.urandom` raises something that's NOT an `Exception`? On CPython, `os.urandom` can raise `ValueError` if the random source is not available. That's an `Exception`. I don't think it raises `BaseException` subclasses.

OK, I'm not finding a concrete `BaseException` escape path. Let me look more carefully...

Actually, let me reconsider. The `_POLICY_KEY = os.urandom(32)` is at module level. If it succeeds, great. If it fails, the module import fails, and the `except Exception` in `invoke_hook` catches it. But what about the `_IMPORT_OK` flag? The `try/except` around the salienceos import sets `_IMPORT_OK = False` if the import fails. But `os.urandom(32)` is AFTER the try/except, so if `os.urandom` fails, it's not caught by the import try/except. It would propagate up to the caller. But as I said, `invoke_hook` catches it.

Wait, actually, let me re-read the module structure:

```python
try:
    from salienceos.interpreter import Facet, SalienceSignal, interpret, issue_policy
    from salienceos.interpreter.bus import SalienceBus
    _IMPORT_OK = True
except Exception:
    _IMPORT_OK = False
    logger.warning(...)

# ... constants and module-level code ...

_POLICY_KEY = os.urandom(32)
```

If the salienceos import fails, `_IMPORT_OK = False`, but the module continues to execute. `_POLICY_KEY = os.urandom(32)` still runs. If `os.urandom` succeeds, the module is fully loaded with `_IMPORT_OK = False`. Then `salience_enabled()` returns False (because `_IMPORT_OK` is False), and `handles_hook` returns False for all hooks. The observer is inert. Good.

If `os.urandom` fails, the module import fails. But as I said, `invoke_hook` catches the `Exception`.

OK, I'm convinced there's no `BaseException` escape from the observer's code paths. The only remaining concern would be if a vendored function (like `sign`, `interpret`, `digest`) raises a `BaseException`, but that's out of scope (vendored internals).

Let me now look for more subtle issues...

**Hashed identity leak:**

The subject is `_session_hash(session_id)[:16] + ":" + turn_id`. The bus filename is `_session_hash(session_id) + ".jsonl"`. The raw session_id is never written to the bus. Let me verify...

In `_record`, the signal is created with `window.subject` (which is the hashed subject). The signal is published to the bus. The bus stores `asdict(signal)`, which includes `subsystem_id`, `subject`, `facet`, `influence`, `confidence`, `provenance`. None of these contain the raw session_id.

In `_close_locked`, the policy is issued with `window.subject` (hashed). The directive is emitted to the bus. The directive payload includes `subject`, `policy_id`, etc. None contain the raw session_id.

The bus filename is `_session_hash(session_id) + ".jsonl"`. The raw session_id is not in the filename. Good.

But wait - the `_BUSES` and `_WINDOWS` dicts are keyed by the raw `session_id` in memory. These are process-local and not durable. The guarantee says "the raw session_id must never reach the durable record." The in-memory dicts are not durable. So this is fine.

Actually, let me check if the raw session_id could leak through provenance. In `_map_tool_call`:

```python
provenance = _ref("tool:" + tool_name, "status:" + status)
```

`tool_name` and `status` come from kwargs, not from session_id. In `_map_api_error`:

```python
provenance = _ref("api_error", "provider:" + str(kwargs.get("provider") or ""))
```

`provider` comes from kwargs, not from session_id. So no leak. Good.

**Turn_id in subject:**

The subject is `hash(session_id)[:16] + ":" + turn_id`. The `turn_id` is included in plaintext in the subject. Is this a concern? The guarantee says "the raw session_id must never reach the durable record." It doesn't say anything about turn_id. And the turn_id is needed for attribution (matching signals to windows). So this is fine.

But wait - could the `turn_id` be a sensitive value? In some systems, the turn_id might contain user data. But that's a host concern, not an observer concern. The observer just uses what the host provides.

**Audit fence - could tool_name or status be unbounded?**

`_ref` truncates each token to `MAX_TOKEN_LEN` (128). So even if `tool_name` is 10MB, the provenance token `"tool:" + tool_name` is truncated to 128 chars. Good.

But what about the `subject`? It's `_subject(session_id, turn_id)` = `(_session_hash(session_id)[:16] + ":" + turn_id)[:MAX_TOKEN_LEN]`. So it's bounded to 128 chars. Good.

What about `SUBSYSTEM_ID = "quorum.observer"`? It's a constant, 15 chars. Good.

What about `facet`? It's a constant from `Facet.VERIFICATION` etc. Short strings. Good.

**Could a signal fail `valid_signal`?**

Let me check each field:
- `subsystem_id = "quorum.observer"` - `_ref_token` checks `isinstance(x, str) and 0 < len(x) <= MAX_TOKEN_LEN`. "quorum.observer" is 15 chars. OK.
- `subject` - bounded to 128 chars, non-empty (hash is 16 chars + ":" + turn_id, at least 18 chars if turn_id is 1 char). OK.
- `facet` - "verification", "risk", "memory" - all short non-empty strings. OK.
- `influence` - 0.4, 0.6, 0.7, 0.5, 0.8 - all in [0,1]. OK.
- `confidence` - 1.0 - in [0,1]. OK.
- `provenance` - from `_ref()`, each token is non-empty and <= 128 chars, and there are at most 16 tokens. OK.

So `valid_signal` should always pass. No signal should fail. Good.

But wait - what if `turn_id` is empty? Then `_subject` returns `hash[:16] + ":"` = 17 chars. But `_open_window` checks `if not session_id or not turn_id: return`, so a window with empty turn_id is never created. And `_record` also checks for empty turn_id. So the subject always has a non-empty turn_id component. Good.

What if `turn_id` is very long? The subject is truncated to 128 chars. But the subject is `hash[:16] + ":" + turn_id`, truncated to 128. So `turn_id` contributes at most 111 chars. The subject is still a valid ref token (non-empty, <= 128). Good.

**Concurrency - is `_LOCK` sufficient?**

All access to `_WINDOWS`, `_BUSES`, and `_OPERATOR_BUDGET_CACHE` is under `_LOCK`. The `SalienceBus` instances are stored in `_BUSES` and accessed under `_LOCK`. The `SalienceBus` is single-threaded by contract, and all access is serialized under `_LOCK`. So the single-threaded contract is satisfied. Good.

But wait - in the test `test_records_only_against_matching_open_window`, the test calls `so._bus_for("s").signals_for(subject)` outside of any lock. This is test code, not production code. In production, all bus access is under `_LOCK`. But in tests, direct access to `_bus_for` bypasses the lock. This is a test-honesty issue - the tests access the bus in a way that production code doesn't. But it's not a production defect.

Actually, looking more carefully at the test, `so._bus_for("s")` returns the bus from `_BUSES`, and `.signals_for(subject)` reads from the bus's internal `_signals` list. This is a read operation, and in the test, there's no concurrent access, so it's fine.

**Let me look for any issue with the `_operator_budget` function:**

```python
def _operator_budget() -> int:
    global _OPERATOR_BUDGET_CACHE
    if _OPERATOR_BUDGET_CACHE is not None:
        return _OPERATOR_BUDGET_CACHE
    budget = _DEFAULT_BUDGET
    try:
        from hermes_cli.config import read_raw_config_readonly
        cfg = read_raw_config_readonly() or {}
    except Exception:
        cfg = {}
    for path in (("agent", "max_iterations"), ("max_iterations",), ("agent", "iteration_budget")):
        node: Any = cfg
        for part in path:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                node = None
                break
        if isinstance(node, int) and not isinstance(node, bool) and node > 0:
            budget = node
            break
    _OPERATOR_BUDGET_CACHE = budget
    return budget
```

This is called from `_close_locked` which is under `_LOCK`. The config read is inside a `try/except Exception`. The path traversal is safe (checks `isinstance(node, dict)`). The budget is validated as a positive int (not bool). Good.

But wait - what if `read_raw_config_readonly()` raises a `BaseException`? The `except Exception` wouldn't catch it. But `read_raw_config_readonly` is the same function used in `_config_flag`, which also has `except Exception`. If it could raise `BaseException`, that would be a problem. But we don't have its source, and the context says the `get_config_value` (which calls `sys.exit()`) issue was already fixed by using the programmatic read path. `read_raw_config_readonly` is presumably the programmatic read path that doesn't call `sys.exit()`. So this should be safe.

Actually, let me re-read the context: "The author already caught and fixed one such path: get_config_value() calls sys.exit() on a missing key, a SystemExit that would sail past `except Exception` — now uses the programmatic read." So `read_raw_config_readonly` is the programmatic read that doesn't call `sys.exit()`. Good.

**Let me look at the `pre_llm_call` hook more carefully:**

In `turn_context.py`:
```python
_pre_results = _invoke_hook(
    "pre_llm_call",
    session_id=agent.session_id,
    task_id=effective_task_id,
    turn_id=turn_id,
    user_message=original_user_message,
    conversation_history=list(messages),
    ...
)
```

The observer's `observe_lifecycle` for `pre_llm_call` calls `_open_window(kwargs)`. This opens a window. The `user_message` and `conversation_history` are in kwargs but are never used by the observer. Good - no prompt content reaches the bus.

But wait - `invoke_hook` returns `plugins.invoke_hook(...)`. The `_pre_results` from plugins might include context injection. The observer doesn't contribute to this return value. So the observer doesn't change what the agent does. Good.

**Let me look for a subtle seam issue:**

When salience is enabled, `has_hook("post_tool_call")` returns True (from `handles_hook`). This causes `_emit_post_tool_call_hook` to proceed with the hook emission. The hook emission calls `invoke_hook("post_tool_call", ...)`, which calls `observe_lifecycle` (records signal) and `plugins.invoke_hook` (returns plugin results). The emitter discards the return value. So the only effect is that the observer records a signal. This is produce-only. Good.

But what if a plugin also handles `post_tool_call`? Then `plugins.invoke_hook` would return plugin results. The observer doesn't affect this. Good.

What about `api_request_error`? Same analysis. The emitter in `run_agent.py` checks `has_hook("api_request_error")` and calls `invoke_hook`. The observer records a signal. The return value is discarded (the emitter is a void function). Good.

**Let me look for a resource issue:**

The `_BUSES` dict stores one `SalienceBus` per session. Each `SalienceBus` holds `_signals`, `_directives`, and `_entries` lists in memory. On a long-lived host serving many sessions, if sessions are not properly closed (no `on_session_end`/`on_session_finalize`/`on_session_reset`), the buses would accumulate. But the guarantee says "freed on session close," and the host is expected to call these hooks. If it doesn't, that's a host bug, not an observer bug.

But wait - what about the bus files on disk? Each session creates a file under `<hermes_home>/salience/<hash>.jsonl`. These files are never deleted by the observer. On a long-lived host, these would accumulate indefinitely. Is this a resource issue?

The guarantee says "no per-session leak on a long-lived host (freed on session close)." This refers to in-memory state, not disk files. The disk files are the durable audit record, which is the point. So this is by design, not a leak.

**Let me look at the test for `test_new_turn_finalizes_previous`:**

```python
def test_new_turn_finalizes_previous(home):
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u1"})
    so._record({"session_id": "s", "turn_id": "u1", "tool_name": "write_file",
                "status": "ok"}, so._map_tool_call)
    so._open_window({"session_id": "s", "task_id": "t", "turn_id": "u2"})
    assert len(so._bus_for("s").directives_for(so._subject("s", "u1"))) == 1
```

This tests that opening u2 finalizes u1 (emits u1's directive). If the `_close_locked` call in `_open_window` were deleted, u1's directive wouldn't be emitted, and the assertion would fail (0 directives instead of 1). Good.

But does it test that u1's directive is emitted BEFORE u2 accumulates? The test doesn't record any signals for u2. It just checks that u1's directive exists. If the finalization happened after u2's signals were recorded (e.g., if the code recorded u2's signals first and then finalized u1), the test would still pass (u1's directive would still exist). But the code structure makes this impossible - `_open_window` finalizes u1 before creating u2's window, and `_record` requires an open window. So u2's signals can't be recorded before u1 is finalized. The test doesn't explicitly test the ordering, but the code structure enforces it.

Actually, could a mutation break this ordering? If someone moved the `_close_locked(current)` call to after the new window creation, u1 would be finalized after u2's window is created. But u2's signals would still need to go through `_record`, which checks the window. The new window would be for u2, so u2's signals would be recorded against u2's window. u1's directive would still be emitted (just later). The test would still pass. But the ordering guarantee (A3) would be violated.

Hmm, but is this a realistic mutation? The code is:
```python
if current is not None and not current.closed and current.turn_id != turn_id:
    _close_locked(current)
if current is None or current.closed or current.turn_id != turn_id:
    _WINDOWS[session_id] = _Window(...)
```

If someone swapped these two if blocks, the new window would be created first, then the old one would be closed. But the old window is still in `_WINDOWS[session_id]`... wait, no. The second `if` replaces `_WINDOWS[session_id]` with the new window. So after the swap:
1. New window created (replaces old in `_WINDOWS`)
2. `_close_locked(current)` called on the old window (which is no longer in `_WINDOWS`)

The old window's directive would still be emitted. The test would still pass. But the ordering is wrong - u2's window exists before u1 is finalized. If a `_record` call came between steps 1 and 2 (impossible in single-threaded code, but possible with concurrent access), u2's signal might be recorded before u1's directive is emitted. But since all access is under `_LOCK` and single-threaded, this can't happen. So even with the swap, the behavior is correct in practice. The test is not mutation-blind here because the swap doesn't change observable behavior.

OK, let me think about what else might be missed...

**Let me look at the `_close_session` function again:**

```python
def _close_session(kwargs: dict) -> None:
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return
    with _LOCK:
        window = _WINDOWS.pop(session_id, None)
        if window is not None and not window.closed:
            _close_locked(window)
        _BUSES.pop(session_id, None)
```

What if `_close_locked` raises an exception? It has its own `try/except Exception`, so it shouldn't. But if it does (e.g., if `window.closed = True` raises, which is impossible for a slots class), the exception would propagate out of `_close_session`, through `observe_lifecycle`'s `except Exception`, and be caught. The `_BUSES.pop` wouldn't execute, so the bus would leak. But this is an extremely unlikely scenario (setting a slots attribute raising).

Actually, `window.closed = True` is the first line in `_close_locked` after the idempotency check. It's a simple attribute assignment on a `__slots__` class. It can't raise unless there's a memory corruption issue, which is outside our scope.

**Let me look at a potential issue with `on_session_reset`:**

`on_session_reset` is in the handled hooks set. When called, it triggers `_close_session`, which pops the window and bus. But "reset" might mean the session continues with a new task, not that it ends. If the session continues, the next `pre_llm_call` would open a new window (since the old one was popped). This is correct behavior - the reset finalizes the current turn and frees resources, and the next turn starts fresh.

But what if `on_session_reset` is called without a `session_id`? Then `_close_session` returns early (no session_id). The window and bus are not freed. This could be a leak if the host calls `on_session_reset` without a session_id.
