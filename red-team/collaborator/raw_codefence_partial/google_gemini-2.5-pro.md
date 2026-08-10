# codefence completeness panel: google/gemini-2.5-pro

_finish=stop seconds=43.6 cost=$0.0604 usage={'prompt_tokens': 12170, 'completion_tokens': 4516, 'total_tokens': 16686, 'cost': 0.0603725, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0603725, 'upstream_inference_prompt_cost': 0.0152125, 'upstream_inference_completions_cost': 0.04516}, 'completion_tokens_details': {'reasoning_tokens': 3358, 'image_tokens': 0, 'audio_tokens': 0}}_

This is a strong delta that correctly identifies and closes a critical fail-open vulnerability (partial resolve). The design is robust, leveraging a single source of truth and slot-based logic to avoid the brittleness of prior approaches. The core completeness guarantee is sound. One minor issue was found in a defense-in-depth component.

***

### FINDINGS

**ID:** CF-01
**TITLE:** Porous Recognizer Can Run on Incomplete Roots, Weakening Defense-in-Depth
**SEVERITY:** LOW
**LOCATION:** `codefence.py:145` (function `names_code_root`)

**CONCRETE INPUT OR BYPASS:**
1.  Set up an environment where `salienceos` is not installed or its `__file__` is unresolvable, but `collaborator` is.
2.  At module load, `_RESOLVED_PACKAGES` becomes `frozenset({"collaborator"})` and `PROTECTED_ROOTS` contains only the path to the `collaborator` package.
3.  Call `codefence.names_code_root()` directly, before any `Session` is constructed:
    ```python
    # Assume salienceos is installed at /usr/lib/python3.9/site-packages/salienceos
    # but is not on the PYTHONPATH for this run.
    import collaborator.codefence
    
    # This will return "" because /usr/lib/python3.9/site-packages/salienceos
    # is NOT in the incomplete PROTECTED_ROOTS tuple.
    result = collaborator.codefence.names_code_root("rm -rf /usr/lib/python3.9/site-packages/salienceos")
    assert result == "" 
    ```

**WHY IT BREAKS A GUARANTEE:**
This breaks the claim that "no in-band consumer of incomplete roots exists". `names_code_root` is an in-band consumer of `PROTECTED_ROOTS` used in the governance path. Because it can be called as a standalone function before `Session` construction, it does not benefit from the `disjoint_from_code` completeness check. In a partial-resolve state, it will operate on an incomplete list of protected directories, making its already-porous check even weaker. For example, it would fail to flag a proposer command that literally names the missing `salienceos` directory, undermining its defense-in-depth role.

**WHETHER ANOTHER CHECK CATCHES IT:**
**Yes.** The primary safety guarantee is unaffected. Any attempt to construct a `Session` in this state would raise `WorkspaceOverlapsCodeError` via the `disjoint_from_code` check, failing closed and preventing any governed action from running. This finding only applies to a scenario where `names_code_root` is called directly, outside the normal `govern_action` flow that requires a valid `Session`.

**FIX:**
Add the completeness check to the top of `names_code_root`. This ensures it always operates on a complete set of roots or fails fast, just like `disjoint_from_code`.

```python
# In collaborator/codefence.py

def names_code_root(command) -> str:
    """POROUS, best-effort tag..."""
    missing = [pkg for pkg in _EXPECTED_PACKAGES if pkg not in _RESOLVED_PACKAGES]
    if missing:
        # To avoid adding a new exception type, we can return a special signal
        # or simply raise, as any subsequent Session creation would fail anyway.
        # Raising is cleaner and consistent with the fail-loud posture.
        raise RuntimeError(f"cannot perform check on incomplete code roots (missing: {missing})")

    if isinstance(command, (list, tuple)):
        # ... rest of the function is unchanged
```

***

### CERTIFICATION

**CERTIFICATION LINE:** **NOT-CERTIFIED.** The core completeness guarantee for `disjoint_from_code` is sound, but a secondary consumer (`names_code_root`) can operate on incomplete roots, violating the claim that no in-band consumer sees an incomplete state.

**STEELMAN OF THE AUTHOR'S DESIGN:**
The design is a significant security improvement that correctly closes a critical partial-resolve vulnerability. By creating a single source of truth in `_code_slots()` and deriving all related constants (`_EXPECTED_PACKAGES`, `PROTECTED_ROOTS`, `_RESOLVED_PACKAGES`) from it, the design elegantly eliminates the possibility of drift or desynchronization between the list of required packages and the list of protected directories. The move to slot-based completeness (`pkg not in _RESOLVED_PACKAGES`) is a masterstroke, simultaneously fixing the partial-resolve bug and eliminating a whole class of false-positive regressions related to directory naming, symlinks, or case sensitivity.

**VERDICT:** **MINOR_ISSUES.** The delta successfully hardens the core governance guard against fail-open on partial resolution, but a defense-in-depth component was overlooked and can run in a weakened state.

**Highest-Value Fix:** The single highest-value fix is to add the completeness check to `names_code_root()` to ensure all consumers of `PROTECTED_ROOTS` operate with the same guarantee, making the protection model consistent across all components.
