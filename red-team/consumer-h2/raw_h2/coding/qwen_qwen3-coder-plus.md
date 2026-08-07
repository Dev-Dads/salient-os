# Red-team (consumer, pass=coding): qwen/qwen3-coder-plus

_finish=stop seconds=22.1 usage={'prompt_tokens': 25404, 'completion_tokens': 888, 'total_tokens': 26292, 'cost': 0.0193986, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0193986, 'upstream_inference_prompt_cost': 0.0165126, 'upstream_inference_completions_cost': 0.002886}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### CRITICAL: Deadlock in finalize-on-read path
**LOCATION**: `salience_observer.py` `_resolve_bounded` → `_close_locked`  
**CONCRETE TRIGGER**: `bounded_iterations("s", 10)` called while another thread holds `_LOCK` in a different context, then `_close_locked` attempts to re-acquire the lock  
**WHY IT MATTERS**: The finalize-on-read path calls `_close_locked` from within `_resolve_bounded` which already holds `_LOCK`. Since `_close_locked` also tries to acquire `_LOCK`, this creates a guaranteed deadlock when finalize-on-read executes.  
**SUGGESTED FIX**: Remove the `with _LOCK:` wrapper from `_close_locked` since it's only called from contexts that already hold the lock.

### HIGH: SystemExit can escape consumer during disk operations
**LOCATION**: `salience_observer.py` `_budget_from_disk` function  
**CONCRETE TRIGGER**: `get_hermes_home()` or `path.exists()` or `mkdir` operations that sys.exit() due to filesystem permission issues or other system-level failures  
**WHY IT MATTERS**: The function calls host APIs (`get_hermes_home`, `Path().mkdir`, `path.exists`) that could raise SystemExit, but these are not wrapped in the exception handler that catches SystemExit - only the outer `bounded_iterations` catches it, creating a gap where SystemExit could propagate.  
**SUGGESTED FIX**: Wrap the entire body of `_budget_from_disk` in `try...except (Exception, SystemExit)`.

### MEDIUM: Failed close can leave inconsistent state with budget < 1
**LOCATION**: `salience_observer.py` `_close_locked` function  
**CONCRETE TRIGGER**: A directive is successfully emitted with `compute_budget=0` (hard-deny), then `_directive_budget` returns None, but `_LAST_DIRECTIVE` still contains the hard-deny directive  
**WHY IT MATTERS**: While the function properly handles exceptions by clearing `_LAST_DIRECTIVE`, it doesn't handle the case where the directive itself is deny-shaped but no exception occurred - the cached directive could still be deny-shaped and cause issues later.  
**SUGGESTED FIX**: Check the result of `_directive_budget(directive)` before caching and only cache if it's a valid budget.

### MEDIUM: TOCTOU race in `_budget_from_disk`
**LOCATION**: `salience_observer.py` `_budget_from_disk` function  
**CONCRETE TRIGGER**: Between checking `if session_id in _BUSES` and calling `_bus_for(session_id)`, another thread could create the bus, leading to a race condition where the verification happens twice or inconsistently  
**WHY IT MATTERS**: There's a time-of-check-to-time-of-use race where the condition `session_id in _BUSES` could change between the check and the subsequent operations.  
**SUGGESTED FIX**: Perform the check and bus creation atomically within the lock.

### LOW: Documentation overstates behavior-preserving nature
**LOCATION**: `salience_observer.py` module docstring and `bounded_iterations` docstring  
**CONCRETE TRIGGER**: Any configuration where the policy window is not pinned (though currently v0 makes this theoretical)  
**WHY IT MATTERS**: The documentation emphasizes that v0 is "behavior-preserving by construction" but doesn't clearly state that this is conditional on the specific policy configuration (pinned window + unmapped ATTENTION). If policy settings change, this guarantee could be broken.  
**SUGGESTED FIX**: Clarify that behavior-preserving property depends on the specific v0 policy configuration.

## STEELMAN

The implementation shows careful attention to safety with comprehensive error handling, proper fail-open semantics, and good use of locks for thread safety. The finalize-on-read pattern is cleverly designed to ensure turn N-1's directive governs turn N, and the restart recovery mechanism properly validates the audit chain.

## VERDICT

SERIOUS_FLAWS - The deadlock in the finalize-on-read path is a critical issue that would brick the agent whenever the consumer runs concurrently with other salience operations. The SystemExit escape in disk operations is also a significant reliability concern. These issues need immediate attention before deployment.
