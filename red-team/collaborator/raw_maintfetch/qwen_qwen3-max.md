# maintfetch CODE panel: qwen/qwen3-max

_finish=stop seconds=28.7 cost=$0.0655 usage={'prompt_tokens': 32190, 'completion_tokens': 1961, 'total_tokens': 34151, 'cost': 0.0655122, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0655122, 'upstream_inference_prompt_cost': 0.0502164, 'upstream_inference_completions_cost': 0.0152958}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### ID-1 / STREAMING FAIL-CLOSED CEILING OFF-BY-ONE
**SEVERITY:** HIGH  
**FILE:FUNCTION:** `collaborator/egress.py:fetch_to_file`  
**CONCRETE BUG OR ATTACK:** The check `if total + len(chunk) > max_bytes` uses `>` which means an artifact exactly equal to `max_bytes` will be accepted, but an artifact of `max_bytes+1` will be rejected. However, if the final chunk pushes the total to exactly `max_bytes`, it's accepted. This is actually correct behavior (not off-by-one). The real issue is that when over-cap is detected, the function returns without writing the current chunk, but any previously written chunks remain in the sink. The executor correctly deletes the partial file on non-ok records, so this is handled properly.

**WHETHER ANOTHER CONTROL CATCHES IT:** YES - The executor `_exec_maint_fetch` calls `_unlink_quiet(target)` when `rec.ok` is False, ensuring partial files are deleted.

**FIX:** No fix needed - the current implementation is correct.

### ID-2 / NON-2XX ERROR PAGE STAGING
**SEVERITY:** CRITICAL  
**FILE:FUNCTION:** `collaborator/egress.py:fetch_to_file`  
**CONCRETE BUG OR ATTACK:** For non-2xx responses, the code reads `DEFAULT_MAX_RESPONSE + 1` bytes to drain the response, then returns a refused record. However, if the error page is small (≤ `DEFAULT_MAX_RESPONSE`), those bytes could theoretically be written to the sink before the refusal is processed. Looking at the code: the non-2xx check happens before any streaming begins, so no bytes are written to the sink for non-2xx responses.

**WHETHER ANOTHER CONTROL CATCHES IT:** The logic is correct - non-2xx responses are handled before the streaming loop begins, so no bytes are written to the sink.

**FIX:** No fix needed.

### ID-3 / SINK.WRITE() OSERROR HANDLING
**SEVERITY:** HIGH  
**FILE:FUNCTION:** `collaborator/egress.py:fetch_to_file` and `collaborator/tools.py:_exec_maint_fetch`  
**CONCRETE BUG OR ATTACK:** If `sink.write()` raises an OSError (e.g., disk full), it's caught in the except block and returns a refused record. However, the partial file may already exist with some bytes written. The executor's exception handler calls `_unlink_quiet(target)` to clean up.

**WHETHER ANOTHER CONTROL CATCHES IT:** YES - The executor has a specific OSError handler that unlinks the target file before returning a failure.

**FIX:** No fix needed - properly handled.

### ID-4 / NET.MAINT AUTHORITY NAMESPACE ISOLATION  
**SEVERITY:** LOW  
**FILE:FUNCTION:** `collaborator/egress.py:required_capability`  
**CONCRETE BUG OR ATTACK:** The "MAINT" pseudo-method is used only for capability derivation and doesn't conflict with real HTTP methods since it's never passed to the actual HTTP client (which uses "GET"). The separation between net.get, net.post, and net.maint namespaces is properly implemented.

**WHETHER ANOTHER CONTROL CATCHES IT:** The design is sound - "MAINT" is purely internal to capability derivation.

**FIX:** No fix needed.

### ID-5 / CANONICAL_HOST CONSISTENCY  
**SEVERITY:** LOW  
**FILE:FUNCTION:** `collaborator/egress.py:required_capability` and connection logic  
**CONCRETE BUG OR ATTACK:** The same `canonical_host()` function is used for both capability derivation and connection, ensuring authorize==connect. Single derivation site prevents TOCTOU.

**WHETHER ANOTHER CONTROL CATCHES IT:** The implementation is correct by design.

**FIX:** No fix needed.

### ID-6 / URL+DEST SEAL INTEGRITY  
**SEVERITY:** MEDIUM  
**FILE:FUNCTION:** `collaborator/tools.py:held_action_seal` and `freeze_args`  
**CONCRETE BUG OR ATTACK:** The seal uses length-prefixed encoding which is injective, preventing field boundary attacks. `freeze_args` coerces url/dest to str, preventing drifting `__str__` methods. The tool identity is bound into the seal, preventing cross-tool replay.

**WHETHER ANOTHER CONTROL CATCHES IT:** The implementation appears sound.

**FIX:** No fix needed.

### ID-7 / WORKSPACE FENCE ESCAPE VIA SYMLINKS  
**SEVERITY:** CRITICAL  
**FILE:FUNCTION:** `collaborator/tools.py:resolve_in_workspace`  
**CONCRETE BUG OR ATTACK:** The function uses `Path.resolve()` which follows symlinks. If the workspace contains a symlink pointing outside the workspace, a dest like "symlink/../target" could escape. For example, if workspace contains `evil -> /etc`, then `dest="evil/passwd"` would resolve to `/etc/passwd`.

**WHETHER ANOTHER CONTROL CATCHES IT:** NO - This is a genuine vulnerability. The current check `if target != root and root not in target.parents` is insufficient when symlinks are involved because `resolve()` follows them before the containment check.

**FIX:** Use `Path.resolve(strict=False)` with additional symlink checks, or implement a custom resolver that doesn't follow symlinks outside the workspace boundary. Alternatively, use `os.path.realpath()` with careful validation.

### ID-8 / PARENT DIRECTORY CREATION ESCAPE  
**SEVERITY:** HIGH  
**FILE:FUNCTION:** `collaborator/tools.py:_exec_maint_fetch`  
**CONCRETE BUG OR ATTACK:** The line `os.makedirs(os.path.dirname(str(target)) or str(workspace), exist_ok=True)` could create directories outside the workspace if `target` somehow escapes (though `resolve_in_workspace` should prevent this). However, if the workspace fence is bypassed, this could create arbitrary directories.

**WHETHER ANOTHER CONTROL CATCHES IT:** Partially - the `resolve_in_workspace` should prevent escaped targets, but if that has vulnerabilities (like the symlink issue above), this becomes exploitable.

**FIX:** Ensure parent directory creation only happens within the validated workspace boundary.

### ID-9 / OVERWRITE OF EXISTING SYMLINKS  
**SEVERITY:** HIGH  
**FILE:FUNCTION:** `collaborator/tools.py:_exec_maint_fetch`  
**CONCRETE BUG OR ATTACK:** If there's an existing symlink in the workspace pointing outside, `open(target, "wb")` will follow it and write outside the workspace. The `resolve_in_workspace` check happens once at the beginning, but doesn't account for race conditions where a symlink is created after resolution but before opening.

**WHETHER ANOTHER CONTROL CATCHES IT:** NO - This is a time-of-check-time-of-use (TOCTOU) vulnerability.

**FIX:** Use `O_NOFOLLOW` flag when opening files, or re-validate the path immediately before opening using `os.open()` with appropriate flags.

### ID-10 / MAX_BYTES VALIDATION  
**SEVERITY:** LOW  
**FILE:FUNCTION:** `collaborator/session.py:Session.__init__`  
**CONCRETE BUG OR ATTACK:** The validation properly rejects non-int values, booleans, and non-positive integers. The model cannot influence this value as it's threaded from session config.

**WHETHER ANOTHER CONTROL CATCHES IT:** The validation is comprehensive.

**FIX:** No fix needed.

### ID-11 / HUMAN GATING ENFORCEMENT  
**SEVERITY:** LOW  
**FILE:FUNCTION:** `collaborator/tools.py` tool definition  
**CONCRETE BUG OR ATTACK:** The tool is correctly set to `mutating=False` and `default_leash=PROPOSE_FIRST` with no auto-lift path, ensuring human gating.

**WHETHER ANOTHER CONTROL CATCHES IT:** The configuration is correct.

**FIX:** No fix needed.

### ID-12 / FETCH_TO_FILE CONTRACT FIDELITY  
**SEVERITY:** LOW  
**FILE:FUNCTION:** `collaborator/egress.py:fetch_to_file`  
**CONCRETE BUG OR ATTACK:** The function faithfully mirrors `fetch()`'s safety contract including redirect fail-closed, `is_safe_public_ip`, HTTPS-only, and request target cleanliness. No drift detected.

**WHETHER ANOTHER CONTROL CATCHES IT:** The implementation is consistent.

**FIX:** No fix needed.

## STEELMAN

The maint_fetch implementation demonstrates strong security engineering with multiple defense-in-depth layers. The separate net.maint capability namespace properly isolates maintenance fetches from regular reads and emissions. The streaming implementation correctly handles size limits and cleanup of partial files. The approved==executed sealing mechanism effectively prevents post-approval parameter substitution. The human-gating by default aligns with the principle of least privilege for maintenance operations.

## VERDICT

**SERIOUS_FLAWS** - The workspace fence has critical vulnerabilities that could allow file system escape via symlinks and TOCTOU races. The single highest-value change is to fix `resolve_in_workspace` to properly handle symlinks and add `O_NOFOLLOW` semantics to file creation to prevent following malicious symlinks that point outside the workspace boundary.
