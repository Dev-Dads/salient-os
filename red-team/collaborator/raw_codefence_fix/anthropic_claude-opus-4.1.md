# PR#34 codefence fail-closed panel: anthropic/claude-opus-4.1

_finish=stop seconds=87.8 cost=$0.2260 usage={'prompt_tokens': 10142, 'completion_tokens': 985, 'total_tokens': 11127, 'cost': 0.226005, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.226005, 'upstream_inference_prompt_cost': 0.15213, 'upstream_inference_completions_cost': 0.073875}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## SECURITY REVIEW: PR #34 DELTA - FAIL-CLOSED EMPTY PROTECTED_ROOTS

### FINDINGS

**Finding 1: PARTIAL PROTECTED_ROOTS REMAINS FAIL-OPEN**
- **ID**: SEC-001
- **TITLE**: Single-package resolution failure leaves partial protection
- **SEVERITY**: MEDIUM
- **LOCATION**: collaborator/codefence.py:48-56 (_resolved_roots)
- **CONCRETE INPUT**: Environment where `salienceos.__file__` is None/unresolvable but `collaborator.__file__` resolves normally
- **WHY IT BREAKS A GUARANTEE**: If salienceos package path cannot be resolved but collaborator can, PROTECTED_ROOTS contains only `(collaborator_path,)`. The guard then protects collaborator/ but NOT salienceos/, allowing a shell to rewrite the verifier core. This violates the "F1 guarantee spans BOTH" claim.
- **WHETHER ANOTHER CHECK CATCHES IT**: No - the new guard only catches completely empty PROTECTED_ROOTS, not partial
- **FIX**: Also fail closed if len(PROTECTED_ROOTS) < 2, or explicitly verify both packages are present

**Finding 2: EXCEPTION TYPE CONSISTENCY**
- **ID**: SEC-002  
- **TITLE**: Early raise uses same exception type as later checks
- **SEVERITY**: LOW
- **LOCATION**: collaborator/codefence.py:75-82
- **CONCRETE INPUT**: Empty PROTECTED_ROOTS tuple
- **WHY IT BREAKS A GUARANTEE**: Does not break a guarantee - uses WorkspaceOverlapsCodeError consistently
- **WHETHER ANOTHER CHECK CATCHES IT**: N/A - non-issue
- **FIX**: None needed - exception type is appropriate

**Finding 3: DEGENERATE NON-EMPTY ROOTS NOT VALIDATED**
- **ID**: SEC-003
- **TITLE**: Invalid/deleted directories in PROTECTED_ROOTS pass emptiness check
- **SEVERITY**: LOW  
- **LOCATION**: collaborator/codefence.py:75 (emptiness check)
- **CONCRETE INPUT**: PROTECTED_ROOTS = (Path("/nonexistent"),) after module import but before disjoint_from_code call
- **WHY IT BREAKS A GUARANTEE**: A non-empty tuple of invalid paths would pass the `if not PROTECTED_ROOTS` check but provide no actual protection. However, _resolved_roots() already validates paths exist via is_dir() at import time.
- **WHETHER ANOTHER CHECK CATCHES IT**: Yes - _resolved_roots validates directories exist at import
- **FIX**: None critical, but could add runtime validation that roots still exist

### CERTIFICATION LINE

**CLAIM**: "When PROTECTED_ROOTS is empty, disjoint_from_code FAILS CLOSED (raises) — the guard can never silently no-op — with no regression to the normal (non-empty-roots) path or to Session construction."

**CERTIFICATION**: NOT-CERTIFIED - The guard correctly fails closed on completely empty PROTECTED_ROOTS, but the partial-protection case (Finding 1) remains a fail-open vulnerability where salienceos/ could be unprotected.

### STEELMAN

The PR #34 change correctly addresses the specific unanimous finding from the certification panel - when PROTECTED_ROOTS is completely empty, the guard now properly fails closed by raising WorkspaceOverlapsCodeError. The implementation is clean, uses the appropriate exception type that composes with Session's other construction checks, and includes a regression test. For the normal case where at least collaborator/__file__ resolves, there is no regression - the guard functions identically.

### VERDICT

**MINOR_ISSUES** - The empty-roots fail-closed guard works correctly as specified, but leaves the partial-protection vulnerability unaddressed where one package is protected but not the other.

**Single highest-value fix**: Modify the guard to also fail closed if PROTECTED_ROOTS doesn't contain BOTH expected packages:
```python
if not PROTECTED_ROOTS or len(PROTECTED_ROOTS) < 2:
    raise WorkspaceOverlapsCodeError(...)
```
Or more robustly, verify both package names are present in the resolved roots.
