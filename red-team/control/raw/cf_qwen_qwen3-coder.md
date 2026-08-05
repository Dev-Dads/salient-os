# Red-team (control seam): qwen/qwen3-coder

_finish=stop seconds=9.8 usage={'prompt_tokens': 14173, 'completion_tokens': 864, 'total_tokens': 15037, 'cost': 0.00467326, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 14171, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.00467326, 'upstream_inference_prompt_cost': 0.00311806, 'upstream_inference_completions_cost': 0.0015552}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## Red-Team Review: SalienceOS Control Seam

### Finding 1
**ID**: SAL-2024-001  
**TITLE**: Salience can be bypassed via malformed directive depth  
**SEVERITY**: HIGH  
**LOCATION**: `salienceos/control/govern.py:decide()`  
**CONCRETE TRIGGER**: Pass a Directive with `verification_depth=999` and a verdict with `effective_stakes=Stakes.LOW`  
**WHY IT MATTERS**: The range guard in `decide()` clamps invalid depths to `FULL`, but doesn't prevent escalation bypass. A caller could set an arbitrarily high depth that gets clamped, potentially bypassing intended salience escalation logic.  
**SUGGESTED FIX**: Add validation in `decide()` to reject directives with out-of-bounds verification_depth values rather than clamping them.

### Finding 2
**ID**: SAL-2024-002  
**TITLE**: Adaptation eligibility check uses loose equality  
**SEVERITY**: MEDIUM  
**LOCATION**: `salienceos/control/govern.py:decide()`  
**CONCRETE TRIGGER**: Create an `AdaptationEligibility` enum value that equals `CANDIDATE` but isn't the same object instance  
**WHY IT MATTERS**: The adaptation check uses `is` comparison for status but `==` for eligibility, creating inconsistency in how enum values are compared. This could lead to unexpected behavior if enum values are constructed dynamically.  
**SUGGESTED FIX**: Use `is` comparison consistently for enum comparisons: `directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE`

### Finding 3
**ID**: SAL-2024-003  
**TITLE**: Verifier escalation doesn't validate input stakes  
**SEVERITY**: LOW  
**LOCATION**: `salienceos/verifier/pipeline.py:verify()`  
**CONCRETE TRIGGER**: Pass a non-Stakes enum value as `escalate_to` parameter  
**WHY IT MATTERS**: While `max_stakes()` handles invalid inputs safely, the verifier doesn't validate that `escalate_to` is a valid Stakes value, potentially masking bugs in callers.  
**SUGGESTED FIX**: Add type/validation check for `escalate_to` parameter in `verify()` method

### Finding 4
**ID**: SAL-2024-004  
**TITLE**: Achieved level calculation has inconsistent failure mode handling  
**SEVERITY**: LOW  
**LOCATION**: `salienceos/control/govern.py:achieved_level()`  
**CONCRETE TRIGGER**: A verdict with `Status.UNVERIFIED`, `Reason.INTEGRITY_ATTESTED` and a mix of attestation-compatible and non-compatible reasons  
**WHY IT MATTERS**: The attestation check uses set inclusion (`<=`) which may allow mixed reason sets to be treated as clean attestations when they shouldn't be.  
**SUGGESTED FIX**: Use set equality (`==`) instead of subset check to ensure only attestation-compatible reasons are present

## Steelman

The implementation demonstrates strong security engineering principles with multiple defensive layers. The self-describing verdict pattern effectively prevents desync attacks, and the fail-closed architecture ensures safety even under unexpected inputs. The separation of concerns between interpreter (salience) and verifier (world facts) maintains the critical invariant that policy authorization and real-world verification remain distinct.

The code shows careful attention to edge cases, with comprehensive test coverage that validates both the letter and spirit of the security invariants. The use of immutable data structures and pure functions in decision points creates an audit trail that's essential for a security-critical system.

## Verdict

**MINOR_ISSUES** - The control seam implementation is fundamentally sound with only minor issues that don't compromise core security invariants. The three main invariants (salience escalation, fail-closed clearance, and adaptation gating) are properly enforced. The identified issues are edge cases that could be addressed for defense-in-depth but don't represent exploitable vulnerabilities under the specified threat model.
