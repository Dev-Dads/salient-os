# Red-team (control seam): qwen/qwen3-coder

_finish=stop seconds=56.3 usage={'prompt_tokens': 13000, 'completion_tokens': 937, 'total_tokens': 13937, 'cost': 0.0059555, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0059555, 'upstream_inference_prompt_cost': 0.00455, 'upstream_inference_completions_cost': 0.0014055}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## Red-Team Review: SalienceOS Control Seam

### Finding 1
**TITLE**: Salience Can Be Bypassed via Subject Mismatch Exploit  
**SEVERITY**: CRITICAL  
**LOCATION**: `salienceos/control/govern.py:decide()`  
**CONCRETE TRIGGER**: 
- Directive with `subject="malicious-action"` and high verification depth
- Envelope with `envelope_id="legitimate-action"` but same content
- Verdict returns `cleared=False` but reasons only show "subject mismatch"
- Caller can retry with different subject to bypass salience escalation

**WHY IT MATTERS**: The control seam fails to enforce that the same subject is used throughout the verification chain. An attacker can use a low-salience directive on a high-stakes envelope by simply mismatching the subject, effectively bypassing salience-based verification escalation.

**SUGGESTED FIX**: Add explicit verification that when subjects don't match, the function should fail closed with a security violation, not just record a reason.

### Finding 2
**TITLE**: Adaptation Gate Bypass via INTEGRITY_ATTESTED Status Confusion  
**SEVERITY**: HIGH  
**LOCATION**: `salienceos/control/govern.py:decide()`  
**CONCRETE TRIGGER**: 
- Directive with `adaptation_eligibility=CANDIDATE`  
- Verdict with `status=UNVERIFIED` and `reasons=[INTEGRITY_ATTESTED]`
- `cleared=True` (receipt-level verification satisfied)
- `adaptation_allowed=False` correctly, but...

Caller can manipulate to get `VERIFIED` status with minimal verification by exploiting the stakes mapping inconsistency.

**WHY IT MATTERS**: While the current code correctly blocks adaptation on `INTEGRITY_ATTESTED`, there's a potential path where partial verification can be upgraded to full verification through stakes manipulation.

**SUGGESTED FIX**: Strengthen the adaptation gate to require not just `Status.VERIFIED` but also that the verification was performed at the maximum possible stakes level.

### Finding 3
**TITLE**: Stakes Escalation Weakened by Direct `decide()` Calls  
**SEVERITY**: MEDIUM  
**LOCATION**: `salienceos/control/govern.py:decide()`  
**CONCRETE TRIGGER**: 
```python
# Direct call to decide() with manipulated effective_stakes
decide(
    directive=Directive(..., verification_depth=NONE),
    envelope_id="act-1",
    envelope_stakes=Stakes.CRITICAL,  # High policy floor
    verdict=VERIFIED,
    effective_stakes=Stakes.LOW  # Manipulated downward
)
```

**WHY IT MATTERS**: While `govern()` correctly computes effective_stakes, the public `decide()` function re-asserts the floor but doesn't prevent callers from passing artificially low effective_stakes that still pass the floor check.

**SUGGESTED FIX**: Add verification in `decide()` that `effective_stakes` was computed correctly from the escalation logic, not just that it meets the floor.

### Finding 4
**TITLE**: Incomplete Range Guarding in Required Level Calculation  
**SEVERITY**: MEDIUM  
**LOCATION**: `salienceos/control/govern.py:decide()`  
**CONCRETE TRIGGER**: 
- Directive with `verification_depth=5` (invalid value beyond FULL=3)
- Envelope with `stakes=Stakes.CRITICAL` 
- Required level calculation does `required = NONE if required < NONE else FULL if required > FULL else required`
- This normalizes `required=5` to `FULL=3`, potentially allowing insufficient verification

**WHY IT MATTERS**: The range guard normalizes invalid directive depths rather than rejecting them, which could allow bypassing intended verification requirements.

**SUGGESTED FIX**: Validate directive.verification_depth is within valid range (0-3) before computing required level, and fail closed if invalid.

### STEELMAN
The implementation demonstrates strong security design principles with multiple defensive checks, clear separation of concerns between interpreter and verifier, and explicit fail-closed behavior. The escalation logic correctly prevents lowering scrutiny and the adaptation gate properly requires world verification. The test suite provides good coverage of the core verification paths.

### VERDICT
**SERIOUS_FLAWS** - Critical subject mismatch vulnerability allows bypassing salience-based verification, with additional high-severity adaptation gate concerns and medium-severity weaknesses in stakes escalation enforcement.
