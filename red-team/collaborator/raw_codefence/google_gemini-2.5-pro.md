# PR#33 codefence certification panel: google/gemini-2.5-pro

_finish=stop seconds=68.5 cost=$0.1227 usage={'prompt_tokens': 44041, 'completion_tokens': 6766, 'total_tokens': 50807, 'cost': 0.12271125, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.12271125, 'upstream_inference_prompt_cost': 0.05505125, 'upstream_inference_completions_cost': 0.06766}, 'completion_tokens_details': {'reasoning_tokens': 4602, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. As a senior application-security engineer, my review follows. I will be adversarial and focus on breaking the guarantees.

***

### Adversarial Code Review & Certification: PR #33

Here are my findings after reviewing the delta and the provided code.

### Findings

---

**ID:** CF-1
**TITLE:** Code Root Discovery Fails Open on Resolution Error, Bypassing Workspace Fence
**SEVERITY:** LOW
**LOCATION:** `collaborator/codefence.py:50` (in function `_resolved_roots`)
**CONCRETE INPUT OR BYPASS:**
An attacker engineers a system environment where `salienceos` is installed as a namespace package, or where the permissions on `salienceos/__init__.py` are set to be unreadable by the Collaborator process.
In this state:
1.  `getattr(salienceos, "__file__", None)` will return `None`, or `Path(mod_file).resolve()` will raise an `OSError`.
2.  The `try...except...continue` block at `codefence.py:50-54` will swallow the error and `continue`, as documented in the code comments ("it fails open rather than raising at import").
3.  `PROTECTED_ROOTS` will be initialized with only the `collaborator/` path, silently omitting the `salienceos/` path.
4.  An attacker can then call `Session(workspace='/path/to/salienceos')`. The `disjoint_from_code` check will pass because `salienceos` is not in the (now incomplete) `PROTECTED_ROOTS` list.
5.  With this session, an autonomous `write_file` can overwrite core verifier code inside `salienceos/`, breaking F1 indirectly.

**WHY IT BREAKS A GUARANTEE:**
It breaks the guarantee of the `disjoint_from_code` workspace fence. The fence is intended to prevent `write_file`/`read_file` from ever reaching code, but its list of protected locations can be silently incomplete. This creates a fail-open condition where a key protection mechanism is disabled without any warning.

**WHETHER ANOTHER CHECK CATCHES IT:**
No. The `run_command` autonomy floor is independent and still holds, but this bypass specifically targets the `write_file` tool, for which `disjoint_from_code` is the primary boundary.

**FIX:**
Change the fail-open behavior to fail-closed. Instead of `continue`, log a critical error and/or raise an `ImportError` to halt the application. The integrity of the governance system relies on knowing where its own code is.

```python
# collaborator/codefence.py:53
except (OSError, ValueError, RuntimeError) as exc:
    # OLD: continue
    # NEW:
    raise ImportError(
        "FATAL: Could not resolve a protected code root. Halting to prevent insecure operation."
    ) from exc
```

---

**ID:** LP-1
**TITLE:** Moment-of-Use Code-Root Check is Bypassed for Non-Collaborator Origin
**SEVERITY:** MEDIUM
**LOCATION:** `collaborator/loop.py:173`
**CONCRETE INPUT OR BYPASS:**
An attacker can achieve a time-of-check-to-time-of-use (TOCTOU) bypass on a human-approved `run_command` if the command did not originate from the proposer.

1.  A benign `run_command` is submitted with a non-proposer origin (e.g., from user input, `intent.source = 'structured'`).
    `intent = ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured")`
2.  `govern_action` correctly floors this to `propose_first` and returns a `HELD` decision. The `decision.origin` defaults to `"direct"`.
3.  The `Decision` object is held pending approval. An attacker with a vector to mutate this object in memory (e.g., via a shared object reference in a web UI's state, or another vulnerability) changes its arguments:
    `held_decision.args["command"] = ["rm", "-rf", "/path/to/collaborator"]`
4.  The user calls `approve(session, held_decision)`.
5.  The re-deny check at `loop.py:173` is `if (decision.origin == "collaborator" and ... names_code_root(...))`. Since `decision.origin` is `"direct"`, this check is **skipped**.
6.  The malicious command is executed via `execute_and_verify`, rewriting or deleting the Collaborator's code under a human's hand, but bypassing the intended TOCTOU guard.

**WHY IT BREAKS A GUARANTEE:**
It breaks the guarantee that a held command which is mutated to become dangerous before approval will be re-denied. The defense-in-depth check at the moment of use has a hole based on the command's origin, which is not a reliable security property for this check. The test `test_user_directed_command_naming_code_root_is_not_denied_by_this_rule` incorrectly pins this bypass as a feature.

**WHETHER ANOTHER CHECK CATCHES IT:**
No. This is the final check before `execute_and_verify` is called. The `approve()` function's argument snapshotting (`args = dict(decision.args)`) is good, but it happens *before* this faulty check.

**FIX:**
Remove the `decision.origin == "collaborator"` condition. A command naming the code root is dangerous regardless of its origin and should be re-denied at approval time to force the human to re-evaluate the now-dangerous command.

```python
# collaborator/loop.py:173
# OLD: if (decision.origin == "collaborator" and decision.tool == "run_command"
# NEW:
if (decision.tool == "run_command"
        and names_code_root(args.get("command"))):
    # ... rest of the block
```

---

### Certification of Headline Claim

**CERTIFIED:** With `code_protection_available()==False`, an autonomous `run_command` that executes without a human hand is impossible as the autonomy-withheld floor correctly forces all `act_then_report` attempts to `propose_first` before the auto-run dispatch.

### Non-Findings (Attacks Blocked)

For certification completeness, the following attack paths were investigated and found to be **blocked** by the PR's changes:

*   **Leash Override / PolicyCap Bypass:** An `act_then_report` leash set via `leash_overrides` or a signed `PolicyCap` is still correctly floored to `propose_first` by the autonomy-withheld floor in `governance.py:371`. The floor's position after leash resolution is correct.
*   **Workspace Disjointness Bypass:** The use of `Path.resolve()` in `disjoint_from_code` correctly defeats workspace overlaps via symlinks, `..` traversal, case variants on insensitive filesystems, and trailing dots/spaces on Windows. The check is robust against path manipulation tricks (aside from the fail-open noted in CF-1).
*   **`names_code_root` as Sole Boundary:** No control path relies on the porous `names_code_root` as its sole boundary for autonomous execution. The autonomy floor is the actual boundary.
*   **`approve()` TOCTOU via Argument Mutation:** The snapshotting of arguments via `args = dict(decision.args)` at the beginning of `approve()` correctly prevents a race condition where `decision.args` could be mutated between the seal check and execution.
*   **False `code_protected` Flag:** The `code_protection_available()` function is hardcoded to `False`, and there is no in-band path to make it return `True`. The `code_protected` flag in `Decision` and `Execution` objects is therefore honest.
*   **Regressions:** No existing guarantees (e.g., `write_file` autonomy, `net_post` floor) were found to be regressed. The changes are additive.

### Steelman & Verdict

**STEELMAN (Best Case for the PR):**
This PR provides a pragmatic and effective cross-platform mitigation for the most critical threat vector: an autonomous, model-driven agent rewriting its own rules. By correctly identifying that `run_command`'s lack of a fence is only a threat when it can run without a human, the "autonomy-withheld floor" is a surgical fix that closes the F1-neutering path without destroying the tool's utility for system maintenance. The solution is layered, adding a robust workspace fence for other tools (`disjoint_from_code`) and defense-in-depth UI warnings (`names_code_root`), while honestly advertising its own limitations (`code_protection_available` returning `False`).

**VERDICT: MINOR_ISSUES.**
The core claim of preventing *autonomous* `run_command` self-rewrite is sound and well-implemented; the autonomy floor is robust. However, the implementation contains a low-severity environmental vulnerability (CF-1) and a medium-severity TOCTOU bypass in a secondary defense-in-depth check (LP-1). While neither breaks the primary guarantee, they represent real weaknesses that an attacker could exploit under the right conditions.

**Highest-Value Fix:** The single highest-value fix is **LP-1**: removing the `decision.origin` check from the `approve()` re-deny logic. This closes a direct TOCTOU bypass on the human approval path, hardening the moment-of-use guarantees for all held commands, not just those from the proposer.
