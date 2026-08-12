# provenance-store code panel: anthropic/claude-opus-4.1

_finish=stop seconds=115.0 cost=$0.5180 usage={'prompt_tokens': 27584, 'completion_tokens': 1390, 'total_tokens': 28974, 'cost': 0.51801, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.51801, 'upstream_inference_prompt_cost': 0.41376, 'upstream_inference_completions_cost': 0.10425}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of this durable provenance store implementation, examining each attack vector systematically.

## SECURITY REVIEW FINDINGS

### 1. CRITICAL: Workspace Symlink Bypass in `_assert_store_outside_workspace`
**SEVERITY:** CRITICAL  
**LOCATION:** `collaborator/session.py:364-375`  
**CONCRETE CASE:** If the workspace itself is a symlink pointing outside, then a store path that appears "inside" the symlink actually resolves outside, but the check at line 371 (`if sp == ws or ws in sp.parents`) will fail to detect this because both paths resolve to their real locations. However, the AGENT can later manipulate the symlink to point into a location where it has write access, making the previously "outside" store now accessible.

**ATTACK:** 
1. Workspace is `/tmp/ws` -> symlink to `/real/workspace`
2. Store is `/tmp/ws/../store.json` which resolves to `/real/store.json`
3. Check passes because `/real/store.json` is outside `/real/workspace`
4. Agent changes `/tmp/ws` symlink to point to `/tmp/agent-controlled/`
5. Now the store path effectively points to `/tmp/agent-controlled/../store.json` = `/tmp/store.json` which agent might access

**WHY IT FAILS:** The check happens at construction time with resolved paths, but the workspace path relationship can change if symlinks are modified.

**OTHER CONTROLS:** Partially caught - the agent runs in bwrap with workspace-only write access, so it cannot modify symlinks outside the workspace. However, if the workspace itself is a symlink, this remains a theoretical vector.

**FIX:** Also verify that the workspace path itself contains no symlinks in its path components.

### 2. HIGH: Missing Atomicity in save() Despite os.replace Claim
**SEVERITY:** HIGH  
**LOCATION:** `collaborator/provenancestore.py:74-76`  
**CONCRETE CASE:** The save() function writes to a `.tmp` file then uses `os.replace()`. However, if the process dies between lines 75-76 (after write_text but before os.replace), the `.tmp` file remains. A subsequent save() will overwrite this `.tmp` file, but a concurrent load() could read a partially written `.tmp` file if it mistakes it for the real store.

**WHY IT FAILS:** The comment claims "atomic-ish swap" but there's no cleanup of orphaned `.tmp` files, and load() doesn't check for or handle `.tmp` files specially.

**OTHER CONTROLS:** Load() only reads the actual store path, not `.tmp` files, so this is mitigated. The next save() will overwrite the orphaned `.tmp`.

**FIX:** Add cleanup of orphaned `.tmp` files at session start, or use a more robust atomic write pattern with proper temp file handling.

### 3. MEDIUM: Rollback Attack Possible Within Same Workspace
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/provenancestore.py:load/save` (no monotonic counter)  
**CONCRETE CASE:** An agent cannot directly write the store (outside workspace), but if a human-authorized process with full filesystem access runs and copies an OLD version of the store over the current one, recent taints are erased. The HMAC will still verify because it's the same workspace subject.

**WHY IT FAILS:** No freshness/monotonic counter in the signed payload means old versions of the same store remain valid indefinitely.

**OTHER CONTROLS:** This requires human-authorized filesystem access, which is documented as trusted. The threat model explicitly states "human maintenance run has full FS reach but is the TRUSTED hand."

**FIX:** Add a monotonic counter or timestamp to the signed payload and reject loads of older versions.

### 4. LOW: Unbounded Growth Via Accumulation
**SEVERITY:** LOW  
**LOCATION:** `collaborator/session.py:note_autonomous_authorship` (accumulation without pruning)  
**CONCRETE CASE:** An autonomous agent that creates many temporary files (e.g., `tmp_${i}.sh` for i=1..1000000) will cause the manifest to grow unboundedly. Each path stays in the set forever until a human write clears it.

**WHY IT FAILS:** No size limits or pruning mechanism for the authored set.

**OTHER CONTROLS:** This is documented as "accumulates (a dropped-then-deleted path stays until a human write_file / approved-run CLEARS it — a documented pruning follow-up)."

**FIX:** Implement size limits or periodic pruning of non-existent files.

### 5. LOW: Race Condition in Concurrent save() Operations
**SEVERITY:** LOW  
**LOCATION:** `collaborator/provenancestore.py:74-76`  
**CONCRETE CASE:** Two sessions sharing the same store file both call save() simultaneously. Both write to `.tmp` files, but only one os.replace() wins. The loser's changes are silently lost.

**WHY IT FAILS:** No file locking or coordination between concurrent sessions.

**OTHER CONTROLS:** The in-memory manifest remains authoritative for each running session, so this only affects cross-session durability.

**FIX:** Implement file locking or merge semantics for concurrent updates.

## STEELMAN

The implementation correctly achieves its primary security goal: the agent cannot forge a clean store due to HMAC protection with an unreachable key, and the workspace-outside check is generally robust. The fail-honest principle is well-implemented - any corruption or tampering results in degraded tracking rather than silent failure. The test coverage demonstrates the key security properties.

## VERDICT: MINOR_ISSUES

The store successfully prevents agent tampering and fails honestly on corruption, achieving its core security goals. The single highest-value fix is adding symlink resolution checks for the workspace path itself to completely close the theoretical symlink bypass vector.
