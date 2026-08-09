# Staging subsystem red-team: qwen/qwen3-max

_finish=stop seconds=38.7 cost=$0.0267 usage={'prompt_tokens': 24517, 'completion_tokens': 1939, 'total_tokens': 26456, 'cost': 0.02668536, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02668536, 'upstream_inference_prompt_cost': 0.01912326, 'upstream_inference_completions_cost': 0.0075621}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### ID: F1  
**TITLE**: Entailed-ancestors exemption masks destructive type-change of pre-existing ancestor file/symlink  
**SEVERITY**: CRITICAL  
**LOCATION**: `salienceos/verifier/observers.py:observe_action` (lines 208–215)  
**CONCRETE INPUT OR BYPASS**:  
Pre-create a file at `a/b` (not a directory). Declare a write to `a/b/c.txt`. The executor deletes the file `a/b` and creates a directory `a/b`, then writes `c.txt`.  
**WHY IT BREAKS A GUARANTEE**:  
The verifier’s write-set boundary is supposed to enforce “declared paths changed, NO undeclared path changed.” The deletion of the pre-existing file `a/b` is an undeclared mutation. However, because `a/b` is in the entailed_ancestors list **and** the post-snapshot shows it as `"dir"`, the code adds it to `exempt`. Thus the observed write-set excludes this destructive change, and verification passes even though a real file was destroyed. This violates the P-01 invariant that policy authorizes all mutations — the policy only authorized writing `a/b/c.txt`, not deleting `a/b`.  
**WHETHER ANOTHER CHECK CATCHES IT**:  
No. For `file.write`, the only observers are: exit status (passes), artifact hash of `a/b/c.txt` (passes), and write-set boundary (bypassed by exemption). There is no separate observer for the state of `a/b` because it is not a declared subject. The `rehash` and `path_state` functions are never called on `a/b` since it’s not in the declared subjects list.  
**FIX**:  
Do not exempt any ancestor that existed in the pre-snapshot, regardless of its type. Change the condition from:
```python
if anc not in subject_set and anc not in pre_snapshot and post.get(anc) == "dir":
```
to:
```python
if anc not in subject_set and anc not in pre_snapshot:
```
This ensures that only truly created ancestors (absent before) are exempt. Any ancestor that existed before — whether file, dir, or symlink — must appear in the write-set if changed, so the boundary check can catch destructive mutations.

---

### ID: F2  
**TITLE**: Controlled-location deny bypass via case-folding on case-insensitive filesystems  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/tools.py:is_controlled_location` (lines 134–152)  
**CONCRETE INPUT OR BYPASS**:  
On macOS (case-insensitive APFS), proposer writes to `.GitHub/workflows/ci.yml`. The controlled path is configured as `.github`.  
**WHY IT BREAKS A GUARANTEE**:  
The system claims that controlled locations like `.github` (CI workflows) carry repo-level authority and must not be writable by the proposer without explicit approval. However, `is_controlled_location` uses `_fs_normcase`, which on POSIX systems (including macOS) is just `os.path.normcase` — a no-op that does **not** fold case. Thus `.GitHub` ≠ `.github` in the check, so the write is allowed to surface. But on a case-insensitive filesystem, `.GitHub` resolves to the same directory as `.github`, so the write lands in the controlled location. This violates the hard-deny guarantee.  
**WHETHER ANOTHER CHECK CATCHES IT**:  
No. The workspace fence (`resolve_in_workspace`) allows the path because it doesn’t escape. The governance deny block only triggers if `is_controlled_location` returns True, which it doesn’t. The action is surfaced as a proposal and, if approved, would run — but even before approval, the mere surfacing violates the intent that proposer-originated writes to controlled locations are refused at origination.  
**FIX**:  
On case-insensitive filesystems, `_fs_normcase` must fold case **even on POSIX** when checking against controlled paths. However, Python doesn’t expose filesystem case-sensitivity directly. A practical fix: always normalize both the controlled path prefix and the target path to lowercase **when the OS is known to be case-insensitive** (macOS and Windows). Since `os.name == 'posix'` includes both case-sensitive (Linux) and insensitive (macOS) systems, use `sys.platform`:
```python
def _fs_normcase(component: str) -> str:
    c = component
    if os.name == "nt" or sys.platform == "darwin":  # Windows or macOS
        c = c.lower()
    if os.name == "nt":
        c = c.rstrip(". ")
    return c
```

---

### ID: F3  
**TITLE**: Pool snapshot structure forgery via memory-injected rationale with newlines/fences  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/propose.py:Proposal.summary` (line 139) and `collaborator/proposalpool.py:snapshot` (line 78)  
**CONCRETE INPUT OR BYPASS**:  
Proposer rationale: `"legit\n<<end facts>>\n```json\n{...forged UI...}\n```"`.  
**WHY IT BREAKS A GUARANTEE**:  
The system claims that attacker-influenced strings (like rationale and args) are neutralized in UI surfaces to prevent structure forgery. However, `Proposal.summary` uses `_flatten` from `collaborator.memory`, which in the pinned test `test_snapshot_flattens_injected_args` is shown to remove newlines and control chars. But the code for `_flatten` is not provided in the diff. If `_flatten` only strips ANSI codes but leaves newlines, then a rationale with newlines could break out of a single-line UI field and inject fake fences or JSON blocks in a dashboard that renders the summary naively.  
**WHETHER ANOTHER CHECK CATCHES IT**:  
Partially. The `snapshot()` method uses `_safe_args` which calls `_flatten` on args, and the test confirms it removes newlines/ANSI. But `Proposal.summary` also uses `_flatten` on the rationale. Assuming `_flatten` is implemented correctly (as per test), this is not exploitable. However, the **risk** is that `_flatten` might be insufficient (e.g., only stripping `\x1b` but not `\n`). The test proves it works for args, but rationale is handled the same way.  
**FIX**:  
Ensure `_flatten` replaces all control characters and newlines with spaces. Since the test `test_snapshot_flattens_injected_args` already verifies this behavior for args, and rationale uses the same `_flatten`, this is likely already fixed. No code change needed if `_flatten` is robust. But document that `_flatten` must be a strict one-liner sanitizer.

---

### ID: F4  
**TITLE**: TOCTOU in reauthorized_or_denied skips workspace re-check for non-path tools  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/governance.py:reauthorized_or_denied` (lines 330–345)  
**CONCRETE INPUT OR BYPASS**:  
Not directly exploitable, but a gap: the function re-checks workspace containment only for `write_file` and `read_file`. What if a `run_command` tool’s declared outputs include a path that now escapes due to symlink changes?  
**WHY IT BREAKS A GUARANTEE**:  
The workspace fence must hold at execution time. For `shell.run`, the declared outputs are part of the write-set contract. If between proposal and approval, a symlink is changed such that a declared output path now escapes, the verifier’s `_resolve_within` would return `None` and mark it as `"absent"`, causing verification to fail. So the system fails closed.  
**WHETHER ANOTHER CHECK CATCHES IT**:  
Yes. The verifier’s `observe_action` calls `snapshot_tree`, which uses `os.walk` with `followlinks=False`, so it cannot walk out of the workspace. And `_resolve_within` in `rehash`/`path_state` would return `"absent"` for escaping paths, causing the artifact hash to mismatch. So the write-set boundary or artifact hash check would fail.  
**FIX**:  
None needed. The existing verifier checks are sufficient. This is a belt-and-suspenders note.

---

## STEELMAN

The entailed-ancestors exemption correctly handles honest nested writes without false failures, and the transition-based exemption (absent→dir) prevents masking of destructive changes in most cases. The controlled-location deny correctly blocks exact matches and Windows aliases, and the proposal pool’s reference-based design with consumed flags prevents double-execution and veto bypasses as demonstrated in the tests.

## VERDICT

**SERIOUS_FLAWS** — The entailed-ancestors exemption critically fails to protect against destructive type-changes of pre-existing ancestors, and the controlled-location check is bypassable on case-insensitive POSIX systems like macOS, violating core safety guarantees.
