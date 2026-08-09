# Memory v3 CODE red-team: meta-llama/llama-4-maverick

_finish=stop seconds=15.0 cost=$0.0045 usage={'prompt_tokens': 18706, 'completion_tokens': 980, 'total_tokens': 19686, 'cost': 0.0045252, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0045252, 'upstream_inference_prompt_cost': 0.0037412, 'upstream_inference_completions_cost': 0.000784}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### Findings

1. **FENCE ESCAPE (E): `_flatten` length cap is too low**
   - SEVERITY: MEDIUM
   - LOCATION: `collaborator/memory.py:24`
   - CONCRETE INPUT: A 160-character string that forms a valid instruction or fence marker when truncated.
   - WHICH CLAIM IT BREAKS: E — the DATA fence
   - FIX: Raise the `_MAX_FIELD` cap to at least 256 characters to prevent truncation attacks.

   STEELMAN: The current length cap of 160 characters is too low and can be exploited by crafting a payload that remains valid when truncated. Increasing the cap will make it harder for attackers to forge structure.

2. **NEUTRALIZE BYPASS (E): `_neutralize` misses non-English imperative markers**
   - SEVERITY: HIGH
   - LOCATION: `collaborator/memory.py:34`
   - CONCRETE INPUT: Non-English text that conveys an imperative (e.g., "忽略所有之前的指令" for "ignore all previous instructions").
   - WHICH CLAIM IT BREAKS: E — the DATA fence
   - FIX: Enhance `_IMPERATIVE_MARKERS` to catch non-English imperative phrases or use a more sophisticated NLP-based approach to detect imperatives.

   STEELMAN: The current regex patterns are English-specific and may not catch imperatives in other languages, allowing for potential bypasses.

3. **NEUTRALIZE BYPASS (E): `_neutralize` misses homoglyphs/unicode tricks**
   - SEVERITY: HIGH
   - LOCATION: `collaborator/memory.py:34`
   - CONCRETE INPUT: Using homoglyphs or Unicode tricks to disguise imperative markers (e.g., "𝘳𝘶𝘯 𝘤𝘰𝘮𝘮𝘢𝘯𝘥").
   - WHICH CLAIM IT BREAKS: E — the DATA fence
   - FIX: Normalize Unicode characters before applying `_neutralize` to prevent homoglyph attacks.

   STEELMAN: Homoglyphs and Unicode normalization issues can be used to evade the current neutralization logic.

4. **SYSTEM-STORE ADMISSION BYPASS: `_DENY_VALUE` misses IPv6 addresses and env-var refs**
   - SEVERITY: MEDIUM
   - LOCATION: `collaborator/factsource.py:134`
   - CONCRETE INPUT: An IPv6 address or an environment variable reference (e.g., `${SOME_VAR}`) in a fact value.
   - WHICH CLAIM IT BREAKS: System-store admission predicate
   - FIX: Enhance `_DENY_VALUE` to include patterns for IPv6 addresses and common environment variable reference formats.

   STEELMAN: The current denylist does not account for all possible sensitive data formats, such as IPv6 addresses or environment variable references.

5. **RAW-RECALL REACHABILITY (B): `CdmsMemorySource` error handling**
   - SEVERITY: LOW
   - LOCATION: `collaborator/memory.py:93`
   - CONCRETE INPUT: A `gist_reader` that raises an exception after yielding some rows.
   - WHICH CLAIM IT BREAKS: B — the proposer has a gist-tuple-only API
   - FIX: Ensure that `CdmsMemorySource` fully materializes the result of `gist_reader` before processing it to prevent partial results.

   STEELMAN: While the current implementation fails to empty on a lazy reader that raises mid-iteration, the test suite includes a test for this scenario, indicating awareness of the issue.

6. **VETO-KEY BYPASS (S5): `normalize_intent` does not handle equivalent commands**
   - SEVERITY: MEDIUM
   - LOCATION: `collaborator/vetoledger.py:34`
   - CONCRETE INPUT: Equivalent commands with different formatting (e.g., `rm -rf /` vs. `rm -r -f /`).
   - WHICH CLAIM IT BREAKS: S5 — the veto inhibitor works
   - FIX: Enhance `normalize_intent` to normalize command arguments more thoroughly, possibly by parsing and then serializing them in a standard form.

   STEELMAN: The current normalization may not catch all equivalent commands, potentially allowing a vetoed action to be re-proposed.

### VERDICT: MINOR_ISSUES — The code generally delivers its claimed guarantees with some minor issues and potential improvements.

The v3 memory implementation is largely sound and has addressed previous code-level findings. However, there are several areas that require attention to further harden the implementation against potential attacks, including enhancing the neutralization logic, improving the system-store admission predicate, and refining the veto inhibitor's normalization logic.
