# maintfetch CODE panel: anthropic/claude-opus-4.1

_finish=stop seconds=99.2 cost=$0.6740 usage={'prompt_tokens': 38451, 'completion_tokens': 1297, 'total_tokens': 39748, 'cost': 0.67404, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.67404, 'upstream_inference_prompt_cost': 0.576765, 'upstream_inference_completions_cost': 0.097275}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of the maint_fetch implementation, examining each attack vector systematically.

## FINDINGS

### 1. **ID: STREAM-1 / OVER-CAP BOUNDARY CHECK / SEVERITY: HIGH / FILE:egress.py:fetch_to_file**

**CONCRETE BUG**: Lines 486-491 check `if total + len(chunk) > max_bytes` but this is a strict inequality. An artifact of EXACTLY `max_bytes` passes through, but if the next read returns more data, it's refused AFTER already writing `max_bytes`. This is correct behavior but the error message "artifact exceeds cap" is misleading when exactly at the boundary.

**ATTACK**: Send exactly `max_bytes` followed by more data → The full `max_bytes` gets written, then refused with "exceeds cap" message, potentially confusing operators about whether the file is complete.

**CAUGHT BY**: The executor's `_unlink_quiet(target)` on non-ok (line 579) properly deletes the file. The contract holds.

**FIX**: None required - behavior is correct. Consider clarifying error message for exactly-at-limit case.

### 2. **ID: AUTH-1 / NAMESPACE SEPARATION / SEVERITY: LOW / FILE:egress.py:required_capability**

**CONCRETE BUG**: None found. Lines 155-159 properly separate the namespaces. The "MAINT" pseudo-method at line 155 is a capability-KIND string, not an HTTP method, and cannot collide with real HTTP methods since `required_capability` is only called with "GET", "POST", or "MAINT" by the governance layer.

**ATTACK**: None viable. The single derivation site (line 155-159) is used consistently at both hold and approve time.

**CAUGHT BY**: N/A - no bug exists.

**FIX**: None required.

### 3. **ID: SEAL-1 / SEAL VERIFICATION / SEVERITY: LOW / FILE:tools.py:held_action_seal**

**CONCRETE BUG**: None found. Lines 224-226 properly seal maint_fetch with url+dest. The tool name is bound into the seal (line 231-233), preventing cross-tool replay. Length-prefixing (line 232) makes the encoding injective.

**ATTACK**: None viable. The seal properly binds the exact url+dest pair.

**CAUGHT BY**: N/A - no bug exists.

**FIX**: None required.

### 4. **ID: FENCE-1 / WORKSPACE ESCAPE / SEVERITY: MEDIUM / FILE:tools.py:_exec_maint_fetch**

**CONCRETE BUG**: Line 567 creates parent directory with `os.makedirs(os.path.dirname(str(target)) or str(workspace), exist_ok=True)`. The `or str(workspace)` fallback happens when dirname returns empty string (for files in workspace root), which is correct.

**ATTACK**: Attempting `dest="../../../etc/passwd"` → `resolve_in_workspace` raises `WorkspaceError` at line 566, caught by governance which denies.

**CAUGHT BY**: `resolve_in_workspace` properly validates before any filesystem operations.

**FIX**: None required.

### 5. **ID: BYTES-1 / MAX_BYTES THREADING / SEVERITY: LOW / FILE:governance.py + session.py**

**CONCRETE BUG**: None found. The `maint_fetch_max_bytes` validation in session.py lines 152-160 properly rejects non-positive integers and booleans. Line 793 in governance.py correctly threads it as `maint_max_bytes`.

**ATTACK**: Model trying to pass max_bytes in args → Ignored, only the host value is used.

**CAUGHT BY**: The value is never read from args, only from session configuration.

**FIX**: None required.

### 6. **ID: SINK-1 / SINK WRITE FAILURE / SEVERITY: MEDIUM / FILE:egress.py:fetch_to_file**

**CONCRETE BUG**: Line 494 `sink.write(chunk)` can raise OSError (disk full). This is caught at line 500-502, returning a refused record.

**ATTACK**: Fill disk during streaming → OSError caught, non-ok record returned, partial file deleted by executor.

**CAUGHT BY**: The except clause at lines 500-502 properly catches OSError. The executor's `_unlink_quiet` at line 579 deletes the partial.

**FIX**: None required - properly handled.

## STEELMAN

The maint_fetch implementation demonstrates robust security engineering. The streaming fail-closed ceiling is properly implemented with no off-by-one errors. The net.maint namespace separation is watertight - a read or emit grant never confers maintenance fetch authority. The workspace fence is solid with proper path resolution before any filesystem operations. The max_bytes value threading ensures the model cannot influence its own limits. All error paths properly clean up partial artifacts. The seal mechanism correctly binds the approved url+dest pair, preventing TOCTOU attacks.

## VERDICT: SOUND

The code is security-sound with no critical vulnerabilities. The implementation correctly enforces all stated security properties: fail-closed streaming with proper cleanup, namespace separation, workspace fencing, and seal verification. The single highest-value change would be adding a comment at line 486 clarifying that the boundary check is intentionally strict (>) rather than (>=) to ensure fail-closed behavior.
