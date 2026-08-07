# Red-team (H3, pass=coding): qwen/qwen3-coder-plus

_finish=stop seconds=26.0 usage={'prompt_tokens': 28136, 'completion_tokens': 1286, 'total_tokens': 29422, 'cost': 0.0224679, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0224679, 'upstream_inference_prompt_cost': 0.0182884, 'upstream_inference_completions_cost': 0.0041795}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

# Red-team Analysis of PR-H3 Implementation

## FINDINGS

### CRITICAL: Cross-turn Budget Compounding Through `_budget_from_disk` State Pollution
**SEVERITY:** CRITICAL  
**LOCATION:** `hermes_cli/observability/salience_observer.py` `_budget_from_disk` function  
**CONCRETE TRIGGER:** 
1. Process A: Turn N applies budget 40 (busy turn with floor 25, headroom 15) → records directive with compute_budget=40
2. Process A: Turn N+1 applies budget 25 (quiet turn) → records directive with compute_budget=25  
3. Process B starts, calls `_budget_from_disk` → promotes the RECORDED budget 25 into `_LAST_DIRECTIVE` cache
4. Process B: Next turn finalizes with floor 25, but the cached directive from disk recovery has budget 25, which gets re-applied as the floor for the next turn
5. This creates a feedback loop where the governed value feeds back into the floor calculation

**WHY IT MATTERS:** The entire point of the ratchet fix was to ensure the floor always comes from the pristine operator budget. However, `_budget_from_disk` promotes a previously-governed value (which could be anywhere in the window) back into the in-memory cache, creating a pathway for cross-restart compounding.

**SUGGESTED FIX:** The recovered directive from disk should NOT be promoted into `_LAST_DIRECTIVE` cache. Instead, it should only be used for that single consumption, and the floor for the first turn of the resumed process should come from the pristine operator budget, not from the recovered value.

### HIGH: Arithmetic Edge Case in Influence Calculation Leading to Budget Overflow
**SEVERITY:** HIGH  
**LOCATION:** `hermes_cli/observability/salience_observer.py` `_close_locked` function, attention signal creation  
**CONCRETE TRIGGER:** When `window.events` is extremely large (e.g., 10^308) and `_ATTENTION_SATURATION_EVENTS` is 8, the division `window.events / _ATTENTION_SATURATION_EVENTS` produces infinity, which when multiplied by a large headroom and rounded could cause integer overflow in the interpreter's `_scale` function.

**WHY IT MATTERS:** While `_ATTENTION_SATURATION_EVENTS` limits influence to 1.0, the intermediate floating-point calculation could still overflow before the `min(1.0, ...)` clamping occurs, potentially causing the interpreter to crash or produce invalid budgets.

**SUGGESTED FIX:** Add explicit bounds checking before the division: `influence = min(1.0, min(window.events, 1e10) / _ATTENTION_SATURATION_EVENTS)` to prevent extreme values from entering the float domain.

### MEDIUM: Stale Cache After Config Change Could Lead to Floor Pollution
**SEVERITY:** MEDIUM  
**LOCATION:** `hermes_cli/observability/salience_observer.py` `_operator_budget` and `_compute_headroom` functions  
**CONCRETE TRIGGER:**
1. Process starts with operator budget 25, headroom 15 → caches floor=25
2. Config changes to operator budget 50 while process is running  
3. New turns still use cached floor=25 instead of updated 50
4. If a window closes during this period, it uses the stale floor, potentially creating a directive based on outdated operator intent

**WHY IT MATTERS:** The memoization prevents real-time config updates from taking effect, meaning the floor could be anchored to an outdated operator budget, violating the principle that the floor should reflect current operator intent.

**SUGGESTED FIX:** Remove memoization or add TTL/expiry mechanism for config-dependent caches, or document that config changes require restart for floor updates.

### MEDIUM: `_budget_from_disk` Could Apply Wrong Floor on Resume
**SEVERITY:** MEDIUM  
**LOCATION:** `hermes_cli/observability/salience_observer.py` `_budget_from_disk` function  
**CONCRETE TRIGGER:** When resuming a session, the recovered budget from disk represents the floor from the previous process, but the current turn might have a different operator budget. The recovered value gets cached and could be used as the floor for subsequent turns, creating inconsistency.

**WHY IT MATTERS:** The resume path should establish the current process's operator floor as the baseline, not perpetuate the previous process's floor. This could lead to budget drift across restarts.

**SUGGESTED FIX:** Ensure `_budget_from_disk` only provides the recovered value for immediate consumption, not for establishing the floor for future turns.

### LOW: Potential Integer Overflow in Headroom Scaling
**SEVERITY:** LOW  
**LOCATION:** `salienceos/interpreter/interpreter.py` `_scale` function  
**CONCRETE TRIGGER:** When `frac` is close to 1.0 and `(hi - lo)` is very large (close to `sys.maxsize`), the multiplication `frac * (hi - lo)` followed by `_round_half_up` could theoretically overflow for extremely large ranges.

**WHY IT MATTERS:** While `_MAX_HEADROOM` limits this somewhat, if the operator budget itself is extremely large, the range `(hi - lo)` could still be problematic.

**SUGGESTED FIX:** Add range validation in `_scale` to ensure `(hi - lo)` doesn't exceed reasonable bounds.

## STEELMAN

The implementation correctly addresses the main architectural challenges of the salience system. The separation of concerns between producer and consumer is well-designed, with proper fail-open mechanisms and audit trail integrity. The attention synthesis from activity counts provides a reasonable first approximation for compute budget adjustment, and the memoization strategies help performance while maintaining correctness in the common case.

## VERDICT

**SERIOUS_FLAWS** - Critical state pollution through the `_budget_from_disk` recovery mechanism creates a pathway for cross-restart budget compounding that defeats the entire purpose of the ratchet fix. The arithmetic edge cases in floating-point calculations also pose real risks. These issues need to be addressed before deployment.
