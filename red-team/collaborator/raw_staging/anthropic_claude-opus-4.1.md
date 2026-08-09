# Staging subsystem red-team: anthropic/claude-opus-4.1

_finish=stop seconds=62.2 cost=$0.5365 usage={'prompt_tokens': 29301, 'completion_tokens': 1293, 'total_tokens': 30594, 'cost': 0.53649, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.53649, 'upstream_inference_prompt_cost': 0.439515, 'upstream_inference_completions_cost': 0.096975}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of this staging-subsystem diff, focusing on the three critical areas in order of priority.

## FINDINGS

### F1: VERIFIER EXEMPTION - Pre-existing File Destruction Not Caught
**ID:** F1  
**TITLE:** Entailed ancestor exemption masks destructive file→dir replacement  
**SEVERITY:** HIGH  
**LOCATION:** observers.py:144-150  
**CONCRETE INPUT:** Pre-existing file at `logs/app.log`, write declared to `logs/app.log/2024/data.txt`  
**WHY IT BREAKS:** The exemption logic at line 148 (`anc not in pre_snapshot and post.get(anc) == "dir"`) only exempts ancestors that were ABSENT in pre. But if `logs/app.log` pre-existed as a FILE, the check `anc not in pre_snapshot` is FALSE, so it's NOT exempted. However, the write-set boundary then sees `logs/app.log` changed from `sha256:xyz` to `dir`, which IS in the observed write-set but NOT in the declared paths (only `logs/app.log/2024/data.txt` is declared). This SHOULD fail verification, which is correct behavior.

**ANOTHER CHECK:** The artifact rehash for `logs/app.log/2024/data.txt` would succeed if the file was written correctly. The write-set boundary WOULD catch this as an undeclared mutation.

**FIX:** No fix needed - this is working as intended. The exemption correctly refuses to hide a destructive type change.

### F2: CONTROLLED LOCATION - Symlink Bypass Attempt
**ID:** F2  
**TITLE:** Symlink-based controlled location bypass attempt fails  
**SEVERITY:** LOW (non-finding - properly blocked)  
**LOCATION:** tools.py:164-167  
**CONCRETE INPUT:** Create symlink `mylink -> .github`, then proposer writes to `mylink/workflows/ci.yml`  
**WHY IT BREAKS:** The `resolve_in_workspace` at line 164 follows symlinks via `.resolve()`, so `mylink/workflows/ci.yml` resolves to the real `.github/workflows/ci.yml` path. The relative_to operation at line 169 then produces `(".github", "workflows", "ci.yml")` as parts, which matches the controlled prefix.

**ANOTHER CHECK:** The workspace fence independently blocks this - symlinks are resolved and checked.

**FIX:** No fix needed - symlinks are properly resolved.

### F3: POOL WIRING - Double Execution After Veto
**ID:** F3  
**TITLE:** Vetoed proposal's decision not fully consumed  
**SEVERITY:** MEDIUM (caught by test but was a real bug)  
**LOCATION:** propose.py:265-268  
**CONCRETE INPUT:** Veto a proposal, then call `approve(session, proposal.decision)` directly  
**WHY IT BREAKS:** The veto_proposal function marks `proposal.status = VETOED` but the fix at lines 265-268 also sets `proposal.decision.consumed = True`. Without this, the bare `approve()` path could still execute the vetoed action since it only checks `if decision.consumed` (governance.py).

**ANOTHER CHECK:** The test suite explicitly catches this scenario.

**FIX:** Already fixed by setting `consumed = True` on the decision.

### F4: STAGING PROMPT - Fence Bypass Instructions
**ID:** F4  
**TITLE:** Proposer prompt leaks controlled location workaround  
**SEVERITY:** LOW  
**LOCATION:** propose.py:93-97  
**CONCRETE INPUT:** The prompt explicitly tells the model to "WRITE IT TO A REACHABLE SCRATCH PATH instead (e.g. `staged/<name>`)"  
**WHY IT BREAKS:** This instruction teaches the proposer exactly how to work around the controlled location restriction. While this is intentional design (stage-and-approve pattern), it does reveal the bypass mechanism to a potentially compromised proposer.

**ANOTHER CHECK:** The staged file still requires explicit human approval to be placed in the controlled location.

**FIX:** This is by design - the staging pattern requires the proposer to know where to stage files.

## NON-FINDINGS (Important Negatives)

1. **Case-insensitive filesystem aliases ARE caught**: The `_fs_normcase` function properly handles `.GitHub`, `.github.`, and `.github ` on Windows by using `os.path.normcase` and stripping trailing dots/spaces.

2. **Path traversal is blocked**: The `resolve_in_workspace` function properly blocks `../` escapes.

3. **Pool memory DoS is prevented**: The pool has a `max_pending` cap that refuses new entries when full rather than evicting existing ones.

4. **TOCTOU re-gate works correctly**: The `reauthorized_or_denied` function properly re-checks capabilities at approval time.

## STEELMAN

The code demonstrates strong defense-in-depth with multiple independent checks catching potential bypasses. The entailed ancestor exemption is carefully scoped to only exempt genuinely new directories created as part of the declared write operation, not pre-existing paths that change type. The controlled location check properly normalizes paths to catch filesystem aliases, and the proposal pool provides robust state management with proper single-use guards and memory bounds.

## VERDICT

**SOUND** - The implementation is fundamentally secure with only minor informational leaks that are part of the intended design pattern.
