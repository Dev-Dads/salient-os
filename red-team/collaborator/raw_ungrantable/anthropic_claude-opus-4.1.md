# ungrantable-namespace CODE panel: anthropic/claude-opus-4.1

_finish=stop seconds=97.0 cost=$0.3293 usage={'prompt_tokens': 15920, 'completion_tokens': 1207, 'total_tokens': 17127, 'cost': 0.329325, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.329325, 'upstream_inference_prompt_cost': 0.2388, 'upstream_inference_completions_cost': 0.090525}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## SECURITY REVIEW FINDINGS

### FINDING 1: Unicode Normalization Timing Window
**ID:** UNI-001  
**TITLE:** TOCTOU between normalization and membership check  
**SEVERITY:** LOW  
**LOCATION:** salienceos/interpreter/policy.py:43-44  
**CONCRETE INPUT:** Thread A calls `is_ungrantable_capability("offense:x")` while Thread B modifies `RESERVED_UNGRANTABLE_PREFIXES`  
**WHY IT BREAKS:** If `RESERVED_UNGRANTABLE_PREFIXES` were mutable (currently a tuple), there's a race between normalization and the startswith check. A capability could be checked against a different prefix set than intended.  
**ANOTHER LAYER CATCHES IT:** Yes - `grants_capability` in directive.py provides redundant checking, and the tuple is immutable in practice  
**FIX:** None needed - the tuple is immutable

### FINDING 2: Prefix Boundary Precision
**ID:** BOUND-001  
**TITLE:** No word boundary enforcement on prefix match  
**SEVERITY:** LOW (documented non-goal)  
**LOCATION:** salienceos/interpreter/policy.py:44  
**CONCRETE INPUT:** `"offensexyz:target"` is NOT blocked (no offense: prefix)  
**WHY IT BREAKS:** Capabilities like `"offense_shape:x"` or `"offenseXYZ:x"` that are semantically offense-related pass through  
**ANOTHER LAYER CATCHES IT:** N/A - this is explicitly out of scope per the requirements ("a capability that is offense-semantic but not offense:-prefixed")  
**FIX:** Not a bug - working as designed for prefix-only reservation

### FINDING 3: Direct allowed_capabilities Access Path
**ID:** PATH-001  
**TITLE:** No direct bypass of grants_capability found  
**SEVERITY:** NONE (non-finding)  
**LOCATION:** N/A  
**CONCRETE INPUT:** N/A  
**WHY IT BREAKS:** Searched for any code reading `directive.allowed_capabilities` directly for authority decisions - none found in provided code  
**ANOTHER LAYER CATCHES IT:** N/A  
**FIX:** None needed

### FINDING 4: Signature Coherence Verified
**ID:** SIG-001  
**TITLE:** Strip-before-sign is coherent with verification  
**SEVERITY:** NONE (non-finding)  
**LOCATION:** salienceos/interpreter/policy.py:110-111  
**CONCRETE INPUT:** `issue_policy(..., granted_capabilities=["offense:x", "fs.read:y"], ...)`  
**WHY IT BREAKS:** It doesn't - the stripped capabilities list is what gets signed, and verification checks the same signed payload. No mismatch.  
**ANOTHER LAYER CATCHES IT:** N/A  
**FIX:** None needed

### FINDING 5: Totality of is_ungrantable_capability
**ID:** TOT-001  
**TITLE:** Function is total - handles all edge cases without raising  
**SEVERITY:** NONE (non-finding)  
**LOCATION:** salienceos/interpreter/policy.py:41-44  
**CONCRETE INPUT:** `is_ungrantable_capability(None)`, `is_ungrantable_capability(123)`, `is_ungrantable_capability(b"offense:")`, massive strings  
**WHY IT BREAKS:** It doesn't - returns False for non-strings, handles all inputs gracefully  
**ANOTHER LAYER CATCHES IT:** N/A  
**FIX:** None needed

### FINDING 6: Turkish I and Case-folding Edge Cases
**ID:** CASE-001  
**TITLE:** Turkish locale casefold handled correctly  
**SEVERITY:** NONE (non-finding)  
**LOCATION:** salienceos/interpreter/policy.py:43  
**CONCRETE INPUT:** Turkish capital I with dot (İ) in "OFFENSE:" under Turkish locale  
**WHY IT BREAKS:** It doesn't - Python's casefold() is locale-independent and handles Turkish I correctly  
**ANOTHER LAYER CATCHES IT:** N/A  
**FIX:** None needed

## CERTIFICATION LINE

**CERTIFIED** - No in-band path grants an offense: capability through any layer (mint, issue_policy, grants_capability), and no legitimate capability is broken by the change.

## STEELMAN

The implementation achieves its stated goal with remarkable thoroughness - three independent layers (mint rejection, issue_policy stripping, grants_capability refusal) ensure the offense: namespace cannot be authorized even if two layers fail. The Unicode normalization (NFKC + casefold) properly handles confusables that could bypass a naive string check. The design correctly identifies that single-node ceremony provides no real trust boundary and honestly defers Tier-3 unlock until a genuine second trust domain exists.

## VERDICT

**SOUND** - The change correctly implements structural un-grantability of the offense: namespace with no paths to bypass and no impact on legitimate capabilities; no fixes needed.
