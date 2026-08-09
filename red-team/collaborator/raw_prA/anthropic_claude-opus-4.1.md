# PR-A host-emit code panel: anthropic/claude-opus-4.1

_finish=stop seconds=91.4 cost=$0.9999 usage={'prompt_tokens': 60721, 'completion_tokens': 1188, 'total_tokens': 61909, 'cost': 0.999915, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.999915, 'upstream_inference_prompt_cost': 0.910815, 'upstream_inference_completions_cost': 0.0891}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll perform a thorough adversarial review of this PR-A delta implementing host-side autonomous emission with the "require both" authority rule.

## FINDINGS

### 1. F-8: UNVALIDATED MODEL CONTENT IN AUTONOMOUS EMIT
**ID**: F-8  
**SEVERITY**: HIGH  
**LOCATION**: collaborator/loop.py:207-214 (emit function)  
**CONCRETE INPUT**: Host wrapper calls `emit(session, model_output['url'], model_output['body'], autonomous=True)`  
**WHY IT BREAKS A GUARANTEE**: The emit() docstring warns against this (lines 207-211) but the CODE doesn't prevent it. If a host wrapper pipes model-derived content into emit() with autonomous=True, it creates an unauditable exfil channel to the granted host, defeating the F1 guarantee that only the operator directs autonomy.  
**ANOTHER CHECK CATCHES IT**: No - this is a caller-side vulnerability with no runtime defense.  
**FIX**: Add runtime validation that autonomous=True requires body to be bytes (proving host pre-encoded it) or add a required `host_controlled=True` parameter.

### 2. MINOR-B: EMISSION SEAL DOESN'T INCLUDE METHOD
**ID**: MINOR-B  
**SEVERITY**: LOW  
**LOCATION**: collaborator/egress.py:305-323 (emission_seal function)  
**CONCRETE INPUT**: A future PR adding PUT/PATCH support could have different behavior for same URL/body but different methods  
**WHY IT BREAKS A GUARANTEE**: The seal binds the "consequential identity" but omits the HTTP method. While currently only POST is supported, this is a future-proofing issue.  
**ANOTHER CHECK CATCHES IT**: Yes - currently only net_post exists with hardcoded POST method.  
**FIX**: Include method in the seal calculation.

### 3. F-2c: LOUD HINT CONDITION INCOMPLETE
**ID**: F-2c  
**SEVERITY**: LOW  
**LOCATION**: collaborator/governance.py:383  
**CONCRETE INPUT**: Session with auto_host=True but intent.source="proposed"  
**WHY IT BREAKS A GUARANTEE**: The loud hint checks `host_directed_atr` (which requires source=="host") but a proposer-originated intent with auto_host=True won't get the diagnostic hint.  
**ANOTHER CHECK CATCHES IT**: Yes - proposer can't author emissions (propose.py:163-167).  
**FIX**: Consider checking just `leash == ACT_THEN_REPORT` instead of `host_directed_atr` for the hint condition.

### 4. VERIFIED: CROSS-SUBJECT BINDING CORRECT
**ID**: N/A (Non-finding)  
**LOCATION**: collaborator/loop.py:145-150, governance.py:109-110  
**TEST**: Cross-session approval with different workspace subjects  
**RESULT**: Correctly refused with "cross-session approval refused" - the origin_subject binding works as designed.

### 5. VERIFIED: SNAPSHOT PREVENTS TOCTOU
**ID**: N/A (Non-finding)  
**LOCATION**: collaborator/loop.py:135-136  
**TEST**: Mutable/proxy args that change between reads  
**RESULT**: The `args = dict(decision.args)` snapshot correctly freezes the payload for all downstream operations.

### 6. VERIFIED: REQUIRE-BOTH LOGIC SOUND
**ID**: N/A (Non-finding)  
**LOCATION**: collaborator/governance.py:354-363  
**TEST**: All combinations of conditions for auto-lift  
**RESULT**: The four-condition AND correctly implements "require both" - no way to get autonomy without both the per-host auto grant AND the net_post act_then_report leash-cap.

### 7. VERIFIED: MODEL CANNOT SELF-ORIGINATE
**ID**: N/A (Non-finding)  
**LOCATION**: Multiple barriers: parser can't produce source='host', run_turn never passes keyword leash  
**TEST**: Model-emitted net_post with full grants  
**RESULT**: Correctly held at propose_first - F1 guarantee maintained.

## STEELMAN

The PR successfully implements a robust two-signal requirement for autonomous emission that prevents model self-origination through multiple independent barriers. The emission seal with length-prefixed framing prevents collision attacks, and the cross-session binding prevents credential confusion. The only significant issue is the lack of runtime validation against piping model content through emit() with autonomous=True, which the docstring warns about but doesn't enforce.

## VERDICT

**MINOR_ISSUES** - The implementation is fundamentally sound with strong defense-in-depth against the primary threat (model self-originating autonomous emissions). The F-8 issue of unvalidated model content in autonomous emit() is concerning but requires explicit host misconfiguration. The single highest-value fix is adding runtime validation that autonomous=True requires demonstrably host-controlled payloads, not just documenting the requirement.
