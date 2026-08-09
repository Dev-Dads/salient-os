# net.post Tier-2 code panel: anthropic/claude-opus-4.1

_finish=stop seconds=140.9 cost=$0.7863 usage={'prompt_tokens': 43942, 'completion_tokens': 1695, 'total_tokens': 45637, 'cost': 0.786255, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.786255, 'upstream_inference_prompt_cost': 0.65913, 'upstream_inference_completions_cost': 0.127125}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of this shipped net.post (Tier-2 emission) implementation, focusing on breaking the security guarantees.

## SECURITY REVIEW FINDINGS

### 1. CRITICAL: Credential Injection Race Condition
**ID:** CRED-001  
**SEVERITY:** CRITICAL  
**LOCATION:** `collaborator/governance.py:346-351`  
**CONCRETE INPUT:** Session with `egress_credentials = {"api.example": "Bearer sk-1"}`, then between `govern_action()` hold and `approve()`, update to `{"api.example": "Bearer sk-2"}`  
**BYPASS:** The credential lookup happens at `execute_and_verify` time (line 349-351), not at hold time. A credential can be changed/added between hold and approval, so the human approves emission WITHOUT a credential but the system sends WITH one.  
**BREAKS GUARANTEE:** Human approved X, Y is sent (TOCTOU on credentials)  
**OTHER CHECKS:** No other check catches this - the re-gate only checks capability, not credential presence  
**FIX:** Freeze the credential (or its absence) in the held Decision and use that frozen value at approval

### 2. HIGH: Body Preview Boundary Truncation
**ID:** BODY-002  
**SEVERITY:** HIGH  
**LOCATION:** `collaborator/egress.py:366`  
**CONCRETE INPUT:** `body = "A" * 510 + "🔒"` (multi-byte UTF-8 character at boundary)  
**BYPASS:** `body_bytes[:_BODY_PREVIEW_BYTES].decode("utf-8", "replace")` can break mid-character, causing replacement character � in preview while actual sent body is intact  
**BREAKS GUARANTEE:** Preview shown to human differs from what's actually sent (though hash is correct)  
**OTHER CHECKS:** Body hash verification would catch actual tampering, but preview corruption isn't caught  
**FIX:** Use `body_bytes[:_BODY_PREVIEW_BYTES].decode("utf-8", "ignore")` or find character boundary

### 3. MEDIUM: Auto-Lift Prefix Attack
**ID:** AUTO-003  
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/governance.py:256-259`  
**CONCRETE INPUT:** Signed caps with `net.post.auto:api.example.com`, attempt to POST to `api.example.com.evil.com`  
**BYPASS:** None - the code correctly uses exact string match via `directive.grants_capability(egress.EGRESS_AUTO_PREFIX + emit_host)`  
**BREAKS GUARANTEE:** Would break auto-lift isolation if it worked  
**OTHER CHECKS:** The exact-match capability check prevents this  
**FIX:** Already correct - noting as a non-finding

### 4. MEDIUM: Leash Threading Inconsistency
**ID:** LEASH-004  
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/loop.py:119` vs `collaborator/governance.py:273`  
**CONCRETE INPUT:** Held decision with `leash="propose_first"`, but session's leash_overrides changed between hold and approve  
**BYPASS:** The `approve()` function passes `decision.leash` (line 119), but if `execute_and_verify` is called directly elsewhere without the leash param, it re-derives from current config  
**BREAKS GUARANTEE:** Audit trail could show wrong effective leash  
**OTHER CHECKS:** The leash is re-capped at execute_and_verify (line 273-275) as defense-in-depth  
**FIX:** Already mostly mitigated by explicit leash threading

### 5. LOW: Content-Type Default Inconsistency
**ID:** CTYPE-005  
**SEVERITY:** LOW  
**LOCATION:** `collaborator/tools.py:520` and `collaborator/egress.py:363`  
**CONCRETE INPUT:** `args = {"content_type": None}` or missing content_type  
**BYPASS:** None - both paths correctly default to "application/json"  
**BREAKS GUARANTEE:** None - consistent behavior  
**OTHER CHECKS:** N/A  
**FIX:** No fix needed - noting as correct

### 6. MEDIUM: Credential Lookup by Non-Canonical Host
**ID:** CRED-006  
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/governance.py:349-351`  
**CONCRETE INPUT:** URL with IDN: `https://bücher.example/api`, credentials keyed by `"xn--bcher-kva.example"`  
**BYPASS:** The credential lookup uses `egress.canonical_host(str(args.get("url")))` which correctly canonicalizes, so no bypass exists  
**BREAKS GUARANTEE:** Would inject wrong credential if it worked  
**OTHER CHECKS:** canonical_host ensures consistency  
**FIX:** Already correct - non-finding

### 7. LOW: Empty Body Handling
**ID:** BODY-007  
**SEVERITY:** LOW  
**LOCATION:** `collaborator/tools.py:517-518`  
**CONCRETE INPUT:** `args = {"body": None}`  
**BYPASS:** Converts None to empty string "", which is safe  
**BREAKS GUARANTEE:** None - empty POST is valid  
**OTHER CHECKS:** N/A  
**FIX:** No fix needed

### 8. CRITICAL: Execute Tool Direct Credential Access
**ID:** EXEC-008  
**SEVERITY:** CRITICAL  
**LOCATION:** `collaborator/tools.py:543-545`  
**CONCRETE INPUT:** Direct call to `execute_tool(tool, workspace, {"url": "...", "egress_auth": "Bearer evil"})`  
**BYPASS:** Wait - reviewing the code, `execute_tool` takes `egress_auth` as a keyword-only parameter, and `_exec_net_post` receives it correctly. The model cannot pass this through args.  
**BREAKS GUARANTEE:** None - parameter isolation is correct  
**OTHER CHECKS:** Keyword-only parameter prevents model access  
**FIX:** Already correct - non-finding

## STEELMAN

The implementation demonstrates strong security architecture with multiple independent defensive layers. The canonical host derivation is consistently applied for both capability checking and connection, preventing authorize-one/connect-another attacks. The credential injection path is properly isolated from model arguments through keyword-only parameters. The emission floor and auto-lift logic correctly prevents model self-origination of autonomous emissions while allowing operator-directed ones. Most critically, the re-gate at approval time prevents capability TOCTOU attacks.

## VERDICT

**MINOR_ISSUES** - The implementation is fundamentally sound with good defense-in-depth. The credential race condition (CRED-001) is the only critical issue where the human-approved state could differ from what's sent.

**Single highest-value fix:** Freeze the credential (or its absence) in the held Decision object at hold time, and use that frozen value at approval/execution rather than re-looking it up from session.egress_credentials. This ensures what was shown for approval matches what gets sent.
