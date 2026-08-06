# Red-team (producer, pass=coding): qwen/qwen3-coder-plus

_finish=stop seconds=15.8 usage={'prompt_tokens': 16959, 'completion_tokens': 949, 'total_tokens': 17908, 'cost': 0.0141076, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0141076, 'upstream_inference_prompt_cost': 0.01102335, 'upstream_inference_completions_cost': 0.00308425}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### CRITICAL

**ID-1** / **BaseException Escape Through Bus Creation** / **CRITICAL** / `hermes_cli/observability/salience_observer.py` `_bus_for()` / A `KeyboardInterrupt` during the `get_hermes_home()` call or `directory.mkdir()` operation would bypass the `except Exception` handler in `_record()` and crash the host, since `KeyboardInterrupt` inherits from `BaseException` not `Exception`. The containment in `_observe_lifecycle` only catches `(Exception, SystemExit)` but not `KeyboardInterrupt`.

**WHY IT MATTERS**: A user pressing Ctrl+C during directory creation could crash the host process, violating the PRODUCE-ONLY / NEVER-CRASH-THE-HOST guarantee.

**SUGGESTED FIX**: Change the exception handling in `_record()` to catch `BaseException` instead of `(Exception, SystemExit)`.

### HIGH

**ID-2** / **Cross-Session Signal Attribution** / **HIGH** / `hermes_cli/observability/salience_observer.py` `_record()` / If `_open_window()` is called with session_id="s1", then `_close_session()` is called for "s1", but then `_record()` is called with session_id="s2" before another `_open_window()` occurs, the check `if window is None` would pass and potentially use a stale window reference if there's a race condition or unexpected state.

**WHY IT MATTERS**: This violates FAIL-CLOSED ATTRIBUTION by potentially recording a signal with no proper window or cross-session contamination.

**SUGGESTED FIX**: Add explicit session_id validation in `_record()` to ensure the window belongs to the correct session.

### MEDIUM

**ID-3** / **Unbounded Memory Growth in _ref Helper** / **MEDIUM** / `hermes_cli/observability/salience_observer.py` `_ref()` / The function creates a list that grows without bound before slicing to 16 elements. If many very short strings are passed, the intermediate list could grow significantly before truncation.

**WHY IT MATTERS**: This violates CONCURRENCY/RESOURCE by creating potential for unbounded temporary memory allocation on the hot path.

**SUGGESTED FIX**: Limit the input processing early: `for part in parts[:16]:` before processing.

**ID-4** / **Policy Key Regeneration Timing Issue** / **MEDIUM** / `hermes_cli/observability/salience_observer.py` module level `_POLICY_KEY` / The policy key is generated once per process at import time, but if the observer is disabled and re-enabled, or if there are multiple processes, this could lead to inconsistent interpretation of signals.

**WHY IT MATTERS**: This affects the integrity of the issue/interpret round-trip mechanism described in the comments.

**SUGGESTED FIX**: Regenerate the key when the observer becomes active or add process ID to ensure uniqueness.

### LOW

**ID-5** / **Missing Validation in _map_api_error** / **LOW** / `hermes_cli/observability/salience_observer.py` `_map_api_error()` / The `provider` parameter is directly concatenated without checking length, though it gets filtered through `_ref()` which should bound it.

**WHY IT MATTERS**: Minor audit fence concern, though likely contained by `_ref()`.

**SUGGESTED FIX**: Add explicit length check on provider parameter.

**ID-6** / **Test Coverage Gap** / **LOW** / `tests/hermes_cli/test_salience_observer.py` / No test covers the specific scenario where `retryable` is `None` in `_map_api_error`.

**WHY IT MATTERS**: Test honesty - the default influence value (0.5 for True, 0.8 for False) when None isn't tested.

**SUGGESTED FIX**: Add test case for `retryable=None`.

## STEELMAN

The implementation shows strong defensive programming with comprehensive exception handling at multiple levels, proper fail-closed design with clear attribution requirements, and careful attention to audit boundaries through bounded token lengths and hashed identities. The threading safety with a global lock and the separation of concerns between signal production and consumption demonstrate good architectural thinking.

## VERDICT

**MINOR_ISSUES** - While several issues were identified, most are medium or low severity. The critical issue regarding KeyboardInterrupt escape is real and needs fixing, but the overall architecture demonstrates solid adherence to the core safety principles. The observer maintains its produce-only nature and contains most failure modes appropriately.
