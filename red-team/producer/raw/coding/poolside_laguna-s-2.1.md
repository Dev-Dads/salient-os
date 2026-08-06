# Red-team (producer, pass=coding): poolside/laguna-s-2.1

_finish=stop seconds=28.7 usage={'prompt_tokens': 17923, 'completion_tokens': 3009, 'total_tokens': 20932, 'cost': 0.00215469, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.00215469, 'upstream_inference_prompt_cost': 0.00161307, 'upstream_inference_completions_cost': 0.00054162}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

# Red-Team Review: Salience Observer (PR-H1)

## Findings

### 1. CRITICAL — SystemExit escapes `_safe_observe` via `observe_lifecycle` → `handles_hook` path

**LOCATION:** `hermes_cli/observability/__init__.py` → `handles_hook()`

**CONCRETE TRIGGER:**
1. `lifecycle.has_hook("post_tool_call")` is called by the emitter (`model_tools._emit_post_tool_call_hook`)
2. This calls `observability.handles_hook("post_tool_call")`
3. Which calls `salience_observer.handles_hook("post_tool_call")`
4. Which calls `salience_enabled()`
5. `salience_enabled()` calls `_config_flag("enabled", True)`
6. `_config_flag` calls `read_raw_config_readonly()` — if this raises `SystemExit` (e.g., a CLI-shaped config helper that `sys.exit()`s on a missing key), it propagates up through `handles_hook` → `lifecycle.has_hook` → the emitter's `try/except Exception` block
7. `SystemExit` is NOT caught by `except Exception` in `_safe_observe` (which only catches `Exception`), nor by the emitter's `except Exception`

**WHY IT MATTERS:** The emitter (`_emit_post_tool_call_hook`) wraps its `invoke_hook` call in `try/except Exception`, but `has_hook` is called *before* that try block. A `SystemExit` from `read_raw_config_readonly()` during `has_hook` would crash the host. The observer's own `observe_lifecycle` catches `SystemExit`, but `handles_hook` does not — and `handles_hook` is called on the hot path before any `_safe_observe` wrapper.

**SUGGESTED FIX:** Wrap the `salience_observer.handles_hook` call in `__init__.py`'s `handles_hook` with a `try/except (Exception, SystemExit)` block, or ensure `_config_flag` catches `SystemExit` from `read_raw_config_readonly`.

---

### 2. HIGH — `_close_locked` can raise `SystemExit` from `_operator_budget` → `_bus_for` → `get_hermes_home`

**LOCATION:** `salience_observer.py` → `_close_locked()` → `_operator_budget()` and `_bus_for()`

**CONCRETE TRIGGER:**
1. `_close_locked(window)` is called (either from `_close_session` or `_open_window` when a new turn starts)
2. `_operator_budget()` calls `read_raw_config_readonly()` — if this raises `SystemExit`, it propagates
3. Even if `_operator_budget` succeeds, `_bus_for(window.session_id)` calls `get_hermes_home()` — if this raises `SystemExit`, it propagates
4. Both calls happen inside `_close_locked`'s `try/except Exception` block, which does NOT catch `SystemExit`
5. `_close_locked` is called from `_close_session` (which catches `(Exception, SystemExit)`) and from `_open_window` (which is called from `observe_lifecycle`, also catching `(Exception, SystemExit)`)
6. BUT: `_close_locked` is also called directly from the test `test_close_locked_is_idempotent` — though that's a test issue, not production

Wait — re-reading: `_close_locked` is called from `_close_session` (under `_LOCK`, inside `observe_lifecycle`'s `except (Exception, SystemExit)`), and from `_open_window` (also under `_LOCK`, inside `observe_lifecycle`'s `except (Exception, SystemExit)`). So the `SystemExit` would be caught by `observe_lifecycle`'s outer handler.

Actually, let me re-check: `_close_locked` has its own `try/except Exception` which does NOT catch `SystemExit`. But it's called from within `observe_lifecycle`'s `try/except (Exception, SystemExit)`. So the `SystemExit` from `_operator_budget` or `_bus_for` would escape `_close_locked`'s handler but be caught by `observe_lifecycle`'s handler. So this is NOT a crash path.

However, there's still a subtle issue: `_close_locked` catches `Exception` but not `SystemExit`, so if `_operator_budget` or `_bus_for` raises `SystemExit`, the window's `closed` flag is set to `True` (line `window.closed = True` happens before the try block), but the directive is NOT emitted. This means the window is marked closed but no directive is recorded — a silent failure that could violate A3 (turn N's directive must be emitted before N+1 accumulates).

**WHY IT MATTERS:** If `get_hermes_home()` or `read_raw_config_readonly()` raises `SystemExit` during finalization, the window is marked closed but the directive is silently lost. This violates A3 (directive must be emitted before next turn) and could cause the audit trail to be incomplete.

**SUGGESTED FIX:** Change `_close_locked`'s `except Exception` to `except (Exception, SystemExit)` to match `observe_lifecycle`'s handler, ensuring the window is not marked closed if finalization fails.

---

### 3. MEDIUM — `_record` silently drops signals on `publish` failure without logging the signal content

**LOCATION:** `salience_observer.py` → `_record()`

**CONCRETE TRIGGER:**
1. A signal is generated by `_map_tool_call` or `_map_api_error`
2. `_bus_for(session_id)` succeeds
3. `self_bus.publish(signal)` raises an exception (e.g., `TypeError` from `valid_signal` failing)
4. The exception is caught by `except Exception` and logged as "publish failed"
5. The signal is NOT appended to `window.signals`

**WHY IT MATTERS:** If `publish` fails (e.g., because a signal fails `valid_signal`), the signal is silently dropped from the window's in-memory list. This means when `_close_locked` calls `interpret(policy, tuple(window.signals), _POLICY_KEY)`, the failed signal is missing. This could cause the directive to be computed without all the signals that were "produced" — a subtle correctness issue. The audit fence is supposed to be structural, but a signal that fails `valid_signal` should be a bug, not a silent drop.

**SUGGESTED FIX:** Log the signal content (or at least its facet/subsystem_id) when `publish` fails, so the failure is visible and debuggable. Alternatively, append the signal to `window.signals` before attempting `publish`, so the in-memory record is complete even if the durable record is not.

---

### 4. MEDIUM — `_subject` can produce empty subject when session_id is empty but turn_id is valid

**LOCATION:** `salience_observer.py` → `_subject()`

**CONCRETE TRIGGER:**
1. `_open_window({"session_id": "", "task_id": "t", "turn_id": "u"})` is called
2. `_ids` returns `("", "u")`
3. `if not session_id or not turn_id: return` — this returns early, so no window is opened. This is correct.

Wait, this is actually handled correctly by the `if not session_id or not turn_id: return` guard in `_open_window`. So this is not a bug.

Let me look for another issue...

---

### 5. MEDIUM — `_map_tool_call` can produce a signal with empty provenance if both `tool_name` and `status` are empty

**LOCATION:** `salience_observer.py` → `_map_tool_call()`

**CONCRETE TRIGGER:**
1. `_map_tool_call({"tool_name": "", "status": ""}, subject)` is called
2. `tool_name = ""`, `status = ""`
3. `is_error = False` (no error_type, status not in error list)
4. `lowered = ""`
5. `provenance = _ref("tool:" + "", "status:" + "")` = `_ref("tool:", "status:")`
6. `_ref` filters out empty tokens: `token = "tool:"` (non-empty, kept), `token = "status:"` (non-empty, kept)
7. Returns `[_signal(subject, Facet.VERIFICATION, 0.7, ("tool:", "status:"))]`

Wait, `"tool:"` and `"status:"` are non-empty strings, so they pass the `if token:` check in `_ref`. So provenance is `("tool:", "status:")`. This is technically valid but semantically meaningless.

Actually, looking more carefully: `tool_name = str(kwargs.get("tool_name") or "")` — if `tool_name` is `None` or missing, it becomes `""`. Then `"tool:" + ""` = `"tool:"`. This is a non-empty string, so it passes `_ref`'s filter. The provenance token `"tool:"` is valid per `valid_signal` (it's a non-empty string ≤ 128 chars), but it's not very useful.

**WHY IT MATTERS:** A signal with empty tool_name and status produces a provenance of `("tool:", "status:")`, which is technically valid but provides no useful attribution. This could happen if the emitter passes empty values.

**SUGGESTED FIX:** Consider filtering out provenance tokens that are just prefixes without actual content (e.g., `"tool:"` without a tool name). Or validate that `tool_name` is non-empty before producing a signal.

---

### 6. LOW — `_close_locked` catches `Exception` but not `SystemExit` from `issue_policy` or `interpret`

**LOCATION:** `salience_observer.py` → `_close_locked()`

**CONCRETE TRIGGER:**
1. `_close_locked(window)` is called
2. `issue_policy(...)` or `interpret(...)` raises `SystemExit`
3. The `except Exception` block does NOT catch `SystemExit`
4. The `SystemExit` propagates up to `observe_lifecycle`'s `except (Exception, SystemExit)` handler

**WHY IT MATTERS:** While `observe_lifecycle` does catch `SystemExit`, the inconsistency between `_close_locked`'s `except Exception` and `observe_lifecycle`'s `except (Exception, SystemExit)` is a code smell. If `_close_locked` were ever called from a different context (not through `observe_lifecycle`), a `SystemExit` would propagate uncaught.

**SUGGESTED FIX:** Change `_close_locked`'s `except Exception` to `except (Exception, SystemExit)` for consistency.

---

### 7. LOW — `_bus_for` can raise `OSError` from `directory.mkdir` that is not caught by `_record`'s inner `except Exception`

**LOCATION:** `salience_observer.py` → `_bus_for()` called from `_record()`

**CONCRETE TRIGGER:**
1. `_record` calls `_bus_for(session_id)` inside the `for signal in mapper(...)` loop
2. `_bus_for` calls `directory.mkdir(parents=True, exist_ok=True)`
3. If the directory creation fails (e.g., permission denied, disk full), `OSError` is raised
4. This `OSError` is caught by the inner `except Exception` in `_record` (line: `except Exception: logger.warning("salience observer: publish failed", exc_info=True)`)
5. The signal is silently dropped

**WHY IT MATTERS:** While the `OSError` is caught, it's logged as "publish failed" which is misleading — the actual failure was in bus creation, not publishing. This could confuse debugging.

**SUGGESTED FIX:** Separate the bus creation from the publish call, with distinct error handling and logging for each.

---

## Test Honesty Issues

### 8. LOW — `test_systemexit_from_host_api_is_contained` does not test the `handles_hook` path

**LOCATION:** `tests/hermes_cli/test_salience_observer.py` → `test_systemexit_from_host_api_is_contained`

**CONCRETE TRIGGER:** The test only calls `so.observe_lifecycle("post_tool_call", ...)` which goes through `observe_lifecycle`'s `except (Exception, SystemExit)` handler. It does NOT test the `handles_hook` → `salience_enabled` → `_config_flag` → `read_raw_config_readonly` path, which is the actual crash path identified in finding #1.

**WHY IT MATTERS:** The test gives false confidence that `SystemExit` is contained, but it doesn't test the path where `SystemExit` would actually escape — through `has_hook` → `handles_hook` → `salience_enabled` → `_config_flag` → `read_raw_config_readonly`.

**SUGGESTED FIX:** Add a test that calls `so.handles_hook("post_tool_call")` with `read_raw_config_readonly` raising `SystemExit`, and verify it doesn't propagate.

---

## Steelman

The observer is genuinely well-designed: it correctly uses `_LOCK` to serialize all registry access, implements fail-closed attribution with proper turn_id matching, hashes session IDs for the durable record, and catches `SystemExit` in its main dispatch path. The `_close_locked` idempotency guard, the session-close cleanup that frees both windows and buses, and the structural audit fence via `valid_signal` are all solid. The test suite covers the critical paths well, including the real-dispatch E2E test.

## Verdict

**SERIOUS_FLAWS** — The `SystemExit` escape through `handles_hook` is a real crash path that violates the PRODUCE-ONLY / NEVER-CRASH-THE-HOST guarantee. The `_close_locked` exception handling inconsistency and the misleading error logging in `_record` are secondary issues. The test suite has a gap in covering the `handles_hook` `SystemExit` path.
