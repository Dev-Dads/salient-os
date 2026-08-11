# PR#38 sensitivepaths certification panel: anthropic/claude-opus-4.1

_finish=stop seconds=80.0 cost=$0.8308 usage={'prompt_tokens': 50555, 'completion_tokens': 967, 'total_tokens': 51522, 'cost': 0.83085, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.83085, 'upstream_inference_prompt_cost': 0.758325, 'upstream_inference_completions_cost': 0.072525}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## Security Review Findings

### FINDING-1
**ID:** SENS-001  
**TITLE:** Proposer Can Bypass Secret-Touch Deny via Non-'proposed' Source  
**SEVERITY:** HIGH  
**LOCATION:** collaborator/propose.py:189  
**CONCRETE INPUT:** A malicious proposer that somehow emits `ToolIntent(name="run_command", args={"command": ["cat", "~/.ssh/id_rsa"]}, source="structured")` instead of `source="proposed"`  
**WHY IT BREAKS:** The hard-deny in governance.py:381 keys on `intent.source == "proposed"`. If the proposer path could emit a different source, it bypasses the deny entirely.  
**ANOTHER CHECK:** No - this is the sole check for proposer-originated secret-touching commands  
**FIX:** The code correctly hardcodes `source="proposed"` in propose.py:189, making this bypass impossible. This is a non-finding upon inspection.

### FINDING-2  
**ID:** SENS-002  
**TITLE:** names_sensitive_path Fails Open on Non-String Command  
**SEVERITY:** LOW  
**LOCATION:** collaborator/sensitivepaths.py:79-80  
**CONCRETE INPUT:** `names_sensitive_path({"malicious": "dict"})` or `names_sensitive_path(object())`  
**WHY IT BREAKS:** Line 79 coerces via `str(command or "")` which could raise for objects with throwing `__str__`, but the except would need to be outside. However, the code actually handles this correctly - str() on most objects succeeds, returning a representation.  
**ANOTHER CHECK:** N/A - the function correctly returns "" for invalid inputs  
**FIX:** Current implementation is correct - fails closed by returning "".

### FINDING-3
**ID:** SENS-003  
**TITLE:** Case-Folding Creates False Positives on Case-Sensitive Linux  
**SEVERITY:** LOW  
**LOCATION:** collaborator/sensitivepaths.py:82  
**CONCRETE INPUT:** Command `["cat", "/home/user/ID_RSA"]` (uppercase file on Linux)  
**WHY IT BREAKS:** Linux filesystems are case-sensitive, but the recognizer lowercases everything, potentially flagging legitimate uppercase paths as sensitive when they're not actually the lowercase sensitive files.  
**ANOTHER CHECK:** This is audit-only and porous by design - not a security boundary  
**FIX:** Document this as an acceptable false-positive given the cross-platform nature and precision-over-recall goal.

## Certification

**CERTIFICATION:** CERTIFIED - A proposer-originated secret-touching run_command cannot autonomously run or bypass the DENY, and maintenance/user-directed commands are unaffected.

The implementation correctly ensures that:
1. The proposer path (propose.py:189) hardcodes `source="proposed"` making it impossible for a model-originated command to dodge the deny
2. The deny correctly fires only on `source=="proposed"` commands, preserving maintenance 
3. The re-deny at approval provides defense-in-depth
4. names_sensitive_path fails closed (returns "") on hostile inputs
5. No control path relies on names_sensitive_path as the sole boundary

## Steelman

The implementation provides a well-layered defense against proposer-originated secret exfiltration attempts. The hardcoded `source="proposed"` in propose.py makes the deny unforgeable by the model, while the explicit precision-over-recall design and documented exclusions (.env, .npmrc) prevent false positives that would degrade usability. The approve-time re-deny and the MINOR-B seal provide additional defense-in-depth against post-hold mutations.

## Verdict

**SOUND** - The implementation correctly achieves its stated goals with appropriate defense-in-depth layering.

The single highest-value fix would be to add a comment in propose.py:189 explicitly noting that `source="proposed"` is security-critical and must never be made dynamic or model-controllable, as it gates the secret-touch deny.
