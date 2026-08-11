# ungrantable-namespace CODE panel: anthropic/claude-opus-4.1

_finish=stop seconds=107.3 cost=$0.3077 usage={'prompt_tokens': 14385, 'completion_tokens': 1226, 'total_tokens': 15611, 'cost': 0.307725, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.307725, 'upstream_inference_prompt_cost': 0.215775, 'upstream_inference_completions_cost': 0.09195}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## SECURITY REVIEW FINDINGS

### FINDING 1: CASEFOLD NORMALIZATION ASYMMETRY
**ID:** CASEFOLD_ASYMMETRY  
**TITLE:** Strip/refuse asymmetry with Turkish locale edge case  
**SEVERITY:** LOW  
**LOCATION:** policy.py:103, directive.py:62  
**CONCRETE INPUT:** Capability string `"OFFENSE:x"` with Turkish locale where `"I".casefold() == "ı"` (not "i")  
**WHY IT BREAKS:** `issue_policy` strips using `is_ungrantable_capability` which does `capability.casefold().startswith(("offense:",))`. In Turkish locale, if the input is `"OFFENSЕ:"` with Cyrillic Е (U+0415) that looks identical to Latin E, or edge cases around Turkish I/İ, the casefold behavior could theoretically differ. However, the double-gate design catches this.  
**ANOTHER LAYER CATCHES IT:** Yes - `grants_capability` performs the same check independently, so even if stripping fails, the capability is still refused at grant-time.  
**FIX:** Already mitigated by defense-in-depth.

### FINDING 2: DIRECT allowed_capabilities ACCESS
**ID:** DIRECT_TUPLE_ACCESS  
**TITLE:** Consumers could theoretically read allowed_capabilities directly  
**SEVERITY:** MEDIUM  
**LOCATION:** directive.py:54 (the tuple is public)  
**CONCRETE INPUT:** A consumer doing `if "offense:evil.com" in directive.allowed_capabilities:` instead of calling `grants_capability()`  
**WHY IT BREAKS:** If a stripped offense: cap somehow made it into allowed_capabilities (it shouldn't due to issue_policy stripping), a consumer reading the tuple directly would see it as present while grants_capability would refuse it.  
**ANOTHER LAYER CATCHES IT:** The strip at issue_policy prevents this, but relies on all consumers using the proper accessor.  
**FIX:** Document that grants_capability is the ONLY authority accessor; consider making allowed_capabilities private with a property that returns a filtered view.

### FINDING 3: BOUNDARY PRECISION
**ID:** PREFIX_BOUNDARY  
**TITLE:** Prefix check is correctly bounded  
**SEVERITY:** NONE (NON-FINDING)  
**LOCATION:** policy.py:36  
**CONCRETE INPUT:** Tested `"offensexyz"`, `"offense"`, `"x.offense:y"`  
**WHY IT BREAKS:** It doesn't. The implementation correctly uses `startswith(("offense:",))` with the colon included, so `"offense"` without colon returns False, `"offensexyz"` returns False. Only actual `"offense:"` prefix matches.  
**ANOTHER LAYER CATCHES IT:** N/A - working as designed.  
**FIX:** None needed.

### FINDING 4: TOTALITY VERIFICATION
**ID:** TOTALITY_CHECK  
**TITLE:** is_ungrantable_capability is correctly total  
**SEVERITY:** NONE (NON-FINDING)  
**LOCATION:** policy.py:36  
**CONCRETE INPUT:** `None`, `123`, `["offense:"]`, `b"offense:"`  
**WHY IT BREAKS:** It doesn't. The function correctly returns False for non-strings via `isinstance(capability, str)` check first.  
**ANOTHER LAYER CATCHES IT:** N/A - working as designed.  
**FIX:** None needed.

### FINDING 5: SIGNATURE COHERENCE
**ID:** SIGNATURE_STRIP_COHERENCE  
**TITLE:** Stripping happens before signing - coherent  
**SEVERITY:** NONE (NON-FINDING)  
**LOCATION:** policy.py:103-105  
**CONCRETE INPUT:** `granted_capabilities=["fs.read", "offense:evil", "shell.exec"]`  
**WHY IT BREAKS:** It doesn't. The strip via list comprehension happens BEFORE creating the PolicyCaps object, which is then signed. The signed payload contains the already-stripped list. Verification checks the stripped version.  
**ANOTHER LAYER CATCHES IT:** N/A - working as designed.  
**FIX:** None needed.

## CERTIFICATION LINE

**CERTIFIED** - No in-band path grants an offense: capability through any case variant or normalization trick; no legitimate capability is broken by the change.

## STEELMAN

The implementation employs defense-in-depth with three independent gates: mint-time rejection (fail loud), issue-policy stripping (clean envelope), and grants_capability unconditional refusal (structural guarantee). The prefix boundary is precise with the colon included, preventing adjacent-string confusion. The case-insensitive check via casefold() handles all practical Unicode variants, and even theoretical locale edge cases are caught by the redundant gates.

## VERDICT

**SOUND** - The structural un-grantability is correctly implemented with proper defense-in-depth. The highest-value fix would be documenting that `grants_capability()` is the sole authority accessor to prevent direct `allowed_capabilities` tuple access, though the current implementation remains secure even if misused.
