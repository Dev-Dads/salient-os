# Red-team (producer, pass=general): deepseek/deepseek-v4-pro

_finish=stop seconds=142.2 usage={'prompt_tokens': 16781, 'completion_tokens': 8974, 'total_tokens': 25755, 'cost': 0.0226606725, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0226606725, 'upstream_inference_prompt_cost': 0.0109496025, 'upstream_inference_completions_cost': 0.01171107}, 'completion_tokens_details': {'reasoning_tokens': 8329, 'image_tokens': 0, 'audio_tokens': 0}}_

**ID:** PR-H1-001  
**TITLE:** Uncaught `BaseException` from `get_hermes_home()` can crash the host  
**SEVERITY:** CRITICAL  
**LOCATION:** `hermes_cli/observability/salience_observer.py`, function `_bus_for` (the call `get_hermes_home()`)  

**CONCRETE TRIGGER:**  
1. A session with a valid `session_id` and `turn_id` is active.  
2. A `post_tool_call` or `api_request_error` hook fires, causing the observer to call `_record`.  
3. `_record` calls `_bus_for(session_id)` for the first time for that session.  
4. The host’s `get_hermes_home()` function raises a `BaseException` (e.g., `SystemExit` because `HERMES_HOME` is not set or the function is a CLI helper that calls `sys.exit()`).  

The exception propagates uncaught through `_record` → `observe_lifecycle` (which only catches `Exception`) → `_safe_observe` (only `Exception`) → `invoke_hook` (only `Exception`), ultimately terminating the agent process.

**WHY IT MATTERS:**  
Violates the top guarantee **PRODUCE-ONLY / NEVER BREAKS THE HOST**. A `BaseException` from a host API the observer calls escapes all three containment layers (`except Exception` in the observer, `_safe_observe`, and `invoke_hook`), crashing the agent. This is the exact class of defect the author already fixed for `get_config_value`; `get_hermes_home` is an analogous host API that may also call `sys.exit()`.

**SUGGESTED FIX:**  
In `_bus_for`, wrap the `get_hermes_home()` call to convert any `BaseException` into a normal `Exception`:

```python
try:
    home = get_hermes_home()
except BaseException as e:
    raise RuntimeError("salience observer: unable to determine hermes home") from e
```

The existing `except Exception` handlers in `_record` and `_close_locked` will then catch it, log a warning, and keep the observer dark without crashing the host.

---

**STEELMAN:**  
The observer’s double wrapping (`_safe_observe` + internal `try/except Exception`) is thorough for normal errors, and the author already identified and fixed the analogous `get_config_value` path, demonstrating awareness of the `BaseException` risk. The single `_LOCK` and careful window lifecycle management correctly prevent races and enforce fail-closed attribution.

**VERDICT:** **SERIOUS_FLAWS** — the unguarded `get_hermes_home()` call is a concrete path for a `BaseException` to crash the host, violating the primary guarantee; otherwise the implementation is sound.
