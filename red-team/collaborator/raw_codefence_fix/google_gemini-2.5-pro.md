# PR#34 codefence fail-closed panel: google/gemini-2.5-pro

_finish=stop seconds=46.0 cost=$0.0605 usage={'prompt_tokens': 9535, 'completion_tokens': 4862, 'total_tokens': 14397, 'cost': 0.06053875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.06053875, 'upstream_inference_prompt_cost': 0.01191875, 'upstream_inference_completions_cost': 0.04862}, 'completion_tokens_details': {'reasoning_tokens': 2754, 'image_tokens': 0, 'audio_tokens': 0}}_

An adversarial review and certification of the PR #34 delta follows.

### FINDINGS

---
**ID:** BUG-01
**TITLE:** Incomplete Fail-Closed: `names_code_root` Fails Open on Empty `PROTECTED_ROOTS`
**SEVERITY:** **HIGH**
**LOCATION:** `collaborator/codefence.py:104` (the `names_code_root` function)
**CONCRETE INPUT OR BYPASS:**
Patch `PROTECTED_ROOTS` to `()` and govern a proposer-authored `run_command` that names a code root.
```python
with patch.object(codefence, "PROTECTED_ROOTS", ()):
    s = _shell_session(tmp)
    # This should be DENIED because it names a code root.
    # Instead, names_code_root() returns "" and the check passes.
    # The action is HELD by the autonomy floor, not DENIED.
    d = govern_action(s, ToolIntent(
        "run_command", {"command": ["rm", "-rf", "/path/to/collaborator"]}, "proposed"))
    assert d.status == HELD  # Fails, is DENIED in normal operation
```
**WHY IT BREAKS A GUARANTEE:**
The PR's goal is to ensure that when `PROTECTED_ROOTS` is empty, governance guards fail closed. While the fix was correctly applied to `disjoint_from_code`, it was missed in `names_code_root`, which is the *other* consumer of `PROTECTED_ROOTS`.

`names_code_root` is used by `govern_action` to implement a hard `DENIED` for any *proposer-authored* `run_command` that literally names a code root (see `test_proposer_command_naming_code_root_is_hard_denied`). When `PROTECTED_ROOTS` is empty, `names_code_root`'s `for` loop does not execute and it returns `""` (no match). This silently disables the hard-deny, causing the guard to fail open. The action is then merely `HELD` by the autonomy floor instead of being outright `DENIED`. This violates the spirit and intent of the fail-closed hardening.

**WHETHER ANOTHER CHECK CATCHES IT:**
Yes, partially. The `code_protection_available() == False` floor still catches the action and downgrades it to `HELD` for human review. However, the specific `DENIED` guarantee for proposer-authored self-rewrites is defeated.

**FIX:**
Add the same fail-closed check to the top of `names_code_root`.
```python
# In collaborator/codefence.py, inside names_code_root()
def names_code_root(command) -> str:
    if not PROTECTED_ROOTS:
        raise WorkspaceOverlapsCodeError(
            "cannot locate the Collaborator's own code roots — refusing to check command "
            "(structural code protection would otherwise be a silent no-op)")
    ...
```

---
**ID:** NIT-01
**TITLE:** Error Shadowing: Empty Roots Check Preempts Unresolvable Workspace Check
**SEVERITY:** LOW
**LOCATION:** `collaborator/codefence.py:72`
**CONCRETE INPUT OR BYPASS:**
Call `disjoint_from_code` with an invalid workspace path when `PROTECTED_ROOTS` is empty.
```python
with patch.object(codefence, "PROTECTED_ROOTS", ()):
    # This raises "cannot locate the Collaborator's own code roots..."
    disjoint_from_code("/invalid/\0/path")
```
**WHY IT BREAKS A GUARANTEE:**
This is a minor regression in diagnostic quality, not a security failure. Previously, an invalid workspace path would raise an error about the workspace being unresolvable. Now, if `PROTECTED_ROOTS` is also empty, the new check fires first, raising a less specific error about code roots. The user is told about a system configuration problem (`PROTECTED_ROOTS`) when the immediate bug is in their input (`workspace`). This hides the true root cause of the user's immediate error.

**WHETHER ANOTHER CHECK CATCHES IT:**
No, the new check preempts the old one.

**FIX:**
Resolve the workspace *before* checking if `PROTECTED_ROOTS` is empty. This prioritizes validating user input over checking system state, providing better diagnostics.
```python
# In collaborator/codefence.py, inside disjoint_from_code()
def disjoint_from_code(workspace) -> None:
    try:
        ws = Path(workspace).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise WorkspaceOverlapsCodeError(f"unresolvable workspace: {workspace!r}") from exc

    if not PROTECTED_ROOTS:
        raise WorkspaceOverlapsCodeError(...) # The new check moves here

    for root in PROTECTED_ROOTS:
        ...
```

---
**ID:** RES-01
**TITLE:** Residual Risk: Partial `PROTECTED_ROOTS` Fails Open for Unresolved Packages
**SEVERITY:** MEDIUM
**LOCATION:** `collaborator/codefence.py:50` (the loop in `_resolved_roots`)
**CONCRETE INPUT OR BYPASS:**
In an environment where `collaborator/__file__` resolves but `salienceos.__file__` is `None` or its parent directory does not exist, `PROTECTED_ROOTS` will be non-empty (e.g., `(Path('/path/to/collaborator'),)`). The new check `if not PROTECTED_ROOTS` will pass. A `Session` can then be constructed with `workspace='/path/to/salienceos'`, and `disjoint_from_code` will not raise an error, as `salienceos` is not in `PROTECTED_ROOTS`.
**WHY IT BREAKS A GUARANTEE:**
This is a stated non-goal of the PR (which only fixes the *empty* case), but it's a significant residual risk worth flagging. The docstring for `_resolved_roots` states "The F1 guarantee spans BOTH, so both are protected." If one root is missing, this guarantee is silently violated. The guard is only partially effective, failing open for the unprotected package. The PR fixes the total failure case but not the partial failure case.

**WHETHER ANOTHER CHECK CATCHES IT:**
No. The non-empty `PROTECTED_ROOTS` tuple bypasses the new guard, and the subsequent loop only checks the roots that were successfully resolved.

**FIX:**
Strengthen `_resolved_roots` to assert that a minimum set of expected roots was found.
```python
# In collaborator/codefence.py, at the end of _resolved_roots()
    if len(roots) < 2: # Or check for specific package names
        # Log a warning or raise an error, making the partial failure loud.
        # Raising would be the most fail-closed approach.
        raise ImportError(
            "Failed to resolve all expected code roots (collaborator, salienceos). "
            "Code protection is incomplete.")
    return tuple(roots)
```

---
### NON-FINDINGS (Blocked Attacks)

1.  **Invalid/Nonexistent Roots:** The attack of `PROTECTED_ROOTS` being a non-empty tuple of invalid paths (e.g., `(Path('/nonexistent'),)`) is blocked. The `_resolved_roots` function validates `p.is_dir()` before adding a path to the list, so a non-existent or non-directory path will never be included in `PROTECTED_ROOTS` in the first place.
2.  **Raise Safety:** The `raise` statement itself is safe. It uses a hardcoded string, so there is no risk of information disclosure or injection vulnerabilities in the exception message. It is deterministic and does not risk a crash.
3.  **Normal Path Regression:** The change is inside an `if not PROTECTED_ROOTS:` block. It has zero effect on the normal execution path where `PROTECTED_ROOTS` is a non-empty tuple of valid paths. The added test confirms this.

---
### CERTIFICATION

**CERTIFICATION LINE:** **NOT-CERTIFIED.** The claim that "the guard can never silently no-op" is false, as the fix was not applied to all consumers of `PROTECTED_ROOTS`, leaving the `names_code_root` guard to fail open.

**STEELMAN:** The change correctly fixes the most severe failure mode identified by the certification panel: a total, silent bypass of the `disjoint_from_code` guard. It does so with a minimal, targeted, and easily understood one-line check, adhering to the principle of failing closed. The fix is accompanied by a clear regression test that pins the new, safer behavior, directly addressing the panel's top finding without over-engineering a solution or altering unrelated logic.

**VERDICT:** **SERIOUS_FLAWS.** While the change correctly hardens `disjoint_from_code`, it introduces an inconsistency by leaving `names_code_root` vulnerable to the exact same fail-open condition, which in turn silently defeats a documented `DENIED` guarantee.

**HIGHEST-VALUE FIX:** Apply the same fail-closed check to `names_code_root` to ensure all guards that depend on `PROTECTED_ROOTS` are hardened consistently.
