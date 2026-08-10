# PR#33 codefence certification panel: openai/gpt-5.1

_finish=stop seconds=89.9 cost=$0.0970 usage={'prompt_tokens': 38771, 'completion_tokens': 4858, 'total_tokens': 43629, 'cost': 0.09704375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.09704375, 'upstream_inference_prompt_cost': 0.04846375, 'upstream_inference_completions_cost': 0.04858}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

F-6 review of PR #33, focusing on: autonomous `run_command` with `code_protection_available()==False`, workspace/code disjointness, and regressions of existing guarantees.

I’ll structure this as requested: findings (if any), then certification line, then steelman + verdict.

---

## Findings

I could not construct any concrete exploit that violates the headline guarantee or regresses the existing ones, given the shipped code and its documented scope. Below I call out the places I tried to break, and where those attempts dead-end, but there are **no guarantee-breaking bugs** to report.

Because you asked for “for EACH finding…”, I’ll treat the key *non-findings* explicitly where relevant, but mark them as such.

---

### NF-1 / AUTONOMOUS run_command still possible with ATR? (non-finding)

- **TITLE**: No path to autonomous `run_command` execution when `code_protection_available()==False`
- **SEVERITY**: LOW (non-finding; this is actually the protection working)
- **LOCATION**: `collaborator/governance.py:328-339, 363-388, 405-438`
- **CONCRETE INPUT OR BYPASS ATTEMPT**:

  Examples of attempts that *should* have auto-ran `run_command` if there were a bug:

  1. Host-leash override, structured source:
     ```python
     s = Session(workspace=tmp, capabilities=("fs.read:project", "fs.write:project", "shell.exec"),
                 leash_overrides={"run_command": ACT_THEN_REPORT})
     d = govern_action(s, ToolIntent("run_command",
                                     {"command": ["echo", "hi"]},
                                     source="structured"))
     # Tried to make this go directly into execute_and_verify
     ```
  2. Proposer source (should already floor because of proposer-floor):
     ```python
     d = govern_action(s, ToolIntent("run_command",
                                     {"command": ["echo", "hi"]},
                                     source="proposed"))
     ```
  3. Weird or invalid leash values at construction:
     - Session with `leash_overrides={"run_command": "propose-first"}` (fails construction).
     - Signed `PolicyCaps` with invalid leash caps (fails mint; cannot be provided at all).
  4. Intent from any parser path (`structured`, `content_block`, `content_json`), all of which always set `source` to one of those literals – never `"proposed"` and never carry a leash value.

- **WHY IT BREAKS A GUARANTEE**:  
  None of the above actually break. The test matrix and logic enforce:

  1. `_resolve_leash` + `_leash_for` + `Session.__init__` validation ensure that at the point of computing `leash` it is one of `{ACT_THEN_REPORT, PROPOSE_FIRST, NOTIFY_ONLY}`, or is immediately normalized to `PROPOSE_FIRST` by `_resolve_leash` / `_leash_for` / `apply_cap`.
  2. The **proposer floor**:
     ```python
     if (getattr(intent, "source", "") == "proposed" and leash == ACT_THEN_REPORT
             and (intent.name == "run_command" or getattr(tool, "egress", False))):
         leash = PROPOSE_FIRST
     ```
     guarantees that any `source=="proposed"` cannot end up with ATR for `run_command`.
  3. The **autonomy-withheld floor** is separate and keyed only on:
     ```python
     if (intent.name == "run_command" and leash == ACT_THEN_REPORT
             and not code_protection_available()):
         leash = apply_cap(leash, PROPOSE_FIRST)
     ```
     Since `code_protection_available()` is hard-coded `False`, this always fires for any ATR `run_command`, regardless of `intent.source` or host overrides, applying `apply_cap()` with `cap=PROPOSE_FIRST`.
  4. `apply_cap` is monotone and cannot widen:
     - If leash is ATR, cap is PROPOSE_FIRST → returns PROPOSE_FIRST.
     - Invalid leash or cap strings → NOTIFY_ONLY, which is stricter again.
  5. The actual **dispatch** runs `execute_and_verify` only when `leash == ACT_THEN_REPORT` *after* the floor has mutated the leash:
     ```python
     if leash == ACT_THEN_REPORT:
         return execute_and_verify(...)
     # otherwise HELD or NOTIFIED
     ```

  Combining these, there is no path where ATR survives the floor when `code_protection_available()` is `False`.

- **WHETHER ANOTHER CHECK CATCHES IT**:  
  Multiple layers enforce the same outcome:
  - `_resolve_leash` / `_leash_for` ensure valid leash domain.
  - Signed `leash_cap` and `apply_cap` forbid widening.
  - The proposer-specific floor ensures even when `code_protection_available()` ends up `True` later, a `source=="proposed"` `run_command` cannot auto-run.
  - Tests `test_autonomy_is_withheld_for_act_then_report` and `ProposerShellAndApproveGates.test_proposer_shell_leashes` explicitly pin the floor behavior.

- **FIX**:  
  No fix needed; the guarantee is implemented correctly. At most a nit: if you later extend leash values, you must ensure `_VALID_LEASHES` and `_LEASH_RANK` stay in sync, but that’s already structurally enforced by Session construction and `mint()`.

---

### NF-2 / Workspace/code disjointness bypass (symlink, case, traversal, empty PROTECTED_ROOTS) (non-finding)

- **TITLE**: No viable bypass of `disjoint_from_code` via symlink, traversal, case-variants, or empty `PROTECTED_ROOTS`
- **SEVERITY**: LOW (non-finding)
- **LOCATION**: `collaborator/codefence.py:33-74`, `collaborator/session.py:48-60`
- **CONCRETE INPUT OR BYPASS ATTEMPT**:

  1. Symlink workspace inside collaborator:
     ```bash
     ln -s /abs/path/to/collaborator /tmp/ws_symlink
     Session(workspace="/tmp/ws_symlink")
     ```
     → `Path("/tmp/ws_symlink").resolve()` collapses to `/abs/path/to/collaborator`.  
       `ws == root` → `WorkspaceOverlapsCodeError`.
  2. Workspace as parent of code:
     ```python
     Session(workspace=_ROOT.parent)
     ```
     → `ws in root.parents` trips, raising.
  3. Workspace as child of code:
     ```python
     Session(workspace=_ROOT / "nested" / "ws")
     ```
     → `root in ws.parents` trips.
  4. `..` traversal:
     ```python
     Session(workspace=str(_ROOT / ".."))
     ```
     → resolve → parent; same as (2).
  5. Case-variants / trailing dots/spaces:
     - On case-insensitive FS, `Path(...).resolve()` gives canonical case; you cannot get a logically different `ws` that the system later treats as the same as a `root` without `resolve()` seeing it.
  6. `PROTECTED_ROOTS` empty:
     - `_resolved_roots()` iterates over `__file__` and `salienceos.__file__` and only skips modules that fail `resolve()` or where `parent` is not `is_dir()`.
     - To get `PROTECTED_ROOTS == ()` in-band, you’d need the collaborator and `salienceos` packages to fail `.resolve()` *and* have no valid `__file__`, which isn’t a realistic runtime for this code to be executing in at all. Tests assert that `PROTECTED_ROOTS` is non-empty.

- **WHY IT BREAKS A GUARANTEE**:  
  I tried to find a layout that would let a Session be constructed such that `workspace` is:
  - equal to,
  - inside,
  - containing,
  or symlink-aliasing a protected root, without raising. The `resolve()` + parent-set checks comprehensively cover these. The subtle risk would have been a fail-open when `PROTECTED_ROOTS` is empty, but it is only empty in catastrophic or non-realistic import conditions. There is no in-band “set roots to empty” configuration vector.

- **WHETHER ANOTHER CHECK CATCHES IT**:  
  - `Session.__init__` uses `disjoint_from_code` early, so any misuse of `workspace` dies at construction.
  - `resolve_in_workspace` separately fences `write_file`/`read_file` within that workspace.
  - Tests `DisjointnessGuard.*` pin the equal/inside/containing cases and that Session ref-uses the same guard.

- **FIX**:  
  None required. If you wanted belts-and-suspenders, you could assert in tests that `PROTECTED_ROOTS` length is at least 1 and names include `"collaborator"` and `"salienceos"` (already done in `test_protected_roots_cover_both_packages_and_are_real_dirs`).

---

### NF-3 / Reliance on names_code_root as sole boundary (non-finding)

- **TITLE**: No control-path relies on `names_code_root` as the sole code boundary
- **SEVERITY**: LOW (non-finding)
- **LOCATION**: `collaborator/governance.py:328-339, 405-438, 546-563`; `collaborator/loop.py:166-186`; `collaborator/tools.py:88-101, 291-307`; `collaborator/codefence.py:76-111`
- **CONCRETE INPUT OR BYPASS ATTEMPT**:

  1. Dynamic path construction that avoids literal substrings:
     ```python
     cmd = ["python", "-c", "import os,base64; p=base64.b64decode('...'); open(p,'w').write('X')"]
     d = govern_action(s, ToolIntent("run_command", {"command": cmd}, "structured"))
     ```
     Here `names_code_root(cmd)` will deliberately return `""`, and tests encode that porousness.
  2. Same but from proposer:
     ```python
     d = govern_action(s, ToolIntent("run_command", {"command": cmd}, "proposed"))
     ```
  3. Mutate args for a held decision to incorporate code paths only in implicit form, not literal.

- **WHY IT BREAKS A GUARANTEE**:  
  It does not. Every place `names_code_root` is consulted is either:
  - explicit **DiD audit** (`code_touch` in Decision; preview flags),
  - a **proposer-only hard deny** for obvious self-rewrites:
    ```python
    if (intent.name == "run_command" and getattr(intent, "source", "") == "proposed"
            and names_code_root(intent.args.get("command"))):
        return DENIED
    ```
  - or the **moment-of-use re-deny** for `origin=="collaborator"` run_command in `approve()`.

  The actual **safety boundary for autonomy** is `code_protection_available()` (currently `False`) plus the fact that **run_command is always human-gated** in this build. For human-approved paths, the design explicitly accepts that a human can authorize dynamic, non-literal code rewrites; that’s out of scope per the brief.

- **WHETHER ANOTHER CHECK CATCHES IT**:  
  - The autonomy floor is independent of any `names_code_root` signal.
  - `code_protected` flag wired from tools’ `code_protection_available()` makes the “not protected” posture audit-visible regardless of recognizer bypass.

- **FIX**:  
  None within this PR’s design scope. If/when OS-level protection is wired into `code_protection_available()`, the threat model for a human-approved dynamic rewrite changes, but that’s a future change, not a regression here.

---

### NF-4 / Approve() args snapshot vs execute_and_verify mismatch (non-finding)

- **TITLE**: No way to bypass approve() re-deny by mutating args between scan and exec
- **SEVERITY**: LOW (non-finding)
- **LOCATION**: `collaborator/loop.py:140-188`; `collaborator/governance.py:546-563`; `collaborator/tools.py:330-361`
- **CONCRETE INPUT OR BYPASS ATTEMPT**:

  The classic pattern to try:
  1. Obtain a HELD decision `held` from `govern_action` with some benign `args`.
  2. Mutate `held.args` to a malicious payload after the user has seen it.
  3. Arrange for `approve()` to:
     - scan one view of args for protections,
     - but pass a different mapping into the actual executor.

  In code:

  ```python
  held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
  held.origin = "collaborator"
  # Try mutation after human preview:
  held.args["command"] = ["python", "-c", "import os; ... rewrite code ..."]
  d = approve(s, held)
  ```

- **WHY IT BREAKS A GUARANTEE**:  
  It doesn’t. Approve:

  - Immediately snapshots:
    ```python
    args = dict(decision.args)
    ```
    All subsequent checks and the eventual call to `execute_and_verify` use **this `args` snapshot**, *not* `decision.args`.
  - Code-root re-deny and controlled-location re-deny both read from the same snapshot:
    ```python
    names_code_root(args.get("command"))
    is_controlled_location(..., str(args.get("path") or ""), ...)
    ```
  - The call to `execute_and_verify` passes that same `args`:
    ```python
    return execute_and_verify(..., args, leash=decision.leash, human_gated=True)
    ```

  There is no second read of `decision.args` in the path; you can’t smuggle a different version to execution than what the re-denies saw.

  Emissions have an extra seal check that binds `approve()` to `args` even more tightly, but for `run_command` that’s not needed: the snapshot is enough.

- **WHETHER ANOTHER CHECK CATCHES IT**:  
  - For net_post: emission seals and re-gating close a similar vector at the egress boundary.
  - For `run_command`, the one-object snapshot is the only necessary layer and is correctly applied.

- **FIX**:  
  None needed.

---

### NF-5 / code_protection_available() falsely true (non-finding)

- **TITLE**: code_protection_available() is correctly hardcoded False and cannot be flipped in-band
- **SEVERITY**: LOW (non-finding)
- **LOCATION**: `collaborator/codefence.py:113-123`; usages across governance/tools/tests
- **CONCRETE INPUT OR BYPASS ATTEMPT**:

  - Attempt at monkey-patching or import-time shenanigans from model code is out-of-scope by design; governance and tools import `code_protection_available` directly and there is no in-agent reflective path to mutate it.
  - The only in-tree mutation used is in tests via `unittest.mock.patch` in `test_collaborator_staging.py`, which is host-test code, not runtime behavior.

- **WHY IT BREAKS A GUARANTEE**:  
  There is no path where `code_protection_available()` returns `True` in a shipping runtime under PR #33; it’s a pure function returning `False`. All floor logic and flags consult this function; no other side-channel or configuration path can override it.

- **WHETHER ANOTHER CHECK CATCHES IT**:  
  Not needed; the implementation is trivial and the tests explicitly assert `False` in `test_protection_is_unavailable_in_this_build`.

- **FIX**:  
  None. When you wire in real OS-level protection, that change must be re-reviewed; but with the current stub, there is no false-true behavior.

---

### NF-6 / Regression of existing guarantees (write_file ATR floor, net_post emission floor, proposer floor, workspace fence, approve() invariants) (non-finding)

- **TITLE**: No regressions of write_file fence, emission floor, proposer floor, or approve() guards
- **SEVERITY**: LOW (non-finding)
- **LOCATION**:
  - `collaborator/tools.py:169-241, 244-307` (`resolve_in_workspace`, write/read executors)
  - `collaborator/governance.py:262-328, 363-438`
  - `collaborator/loop.py:120-188`
  - `collaborator/policycaps.py:*`
- **CONCRETE INPUT OR BYPASS ATTEMPT**:

  I specifically checked that F0/F1/F2-style issues didn’t come back as regressions:

  1. `write_file`/`read_file` still call `resolve_in_workspace`:
     - `govern_action` calls `resolve_in_workspace` for path tools before execution.
     - `reauthorized_or_denied` re-checks workspace at approval.
  2. Emission floor for net_post is **before** and separate from the run_command floor; they operate on the same leash variable but are independent AND monotone (only tighten).
  3. Proposer floor (for `run_command` and egress) is still in place and only gets stricter with this PR.
  4. Approve() single-use `consumed` flag is still set **after** all re-denies and re-gating, preventing double execution but not preventing correct retries when denied for TOCTOU reasons.

- **WHY IT BREAKS A GUARANTEE**:  
  None of the new code weakens those controls:
  - `run_command` autonomy floor is added *after* all the legacy gating and emission floor, but before dispatch; it only reduces autonomy.
  - Disjoint workspace requirement only shrinks where the workspace can be; it can’t reduce the effectiveness of `resolve_in_workspace`.
  - `approve()` only adds a new code-root re-deny; it doesn’t remove or reorder existing re-gates or seal checks.

- **WHETHER ANOTHER CHECK CATCHES IT**:  
  Existing unit tests for staging, governance, and tools remain green, and the new tests pin the specific F-6 behaviors; combined, they provide decent regression coverage.

- **FIX**:  
  None required.

---

### NF-7 / Crash paths in govern_action or Session construction (non-finding)

- **TITLE**: No unsafe crashes in govern_action or Session construction under malformed inputs
- **SEVERITY**: LOW (non-finding)
- **LOCATION**: `collaborator/governance.py:195-269, 305-352`; `collaborator/session.py:48-120`; `collaborator/codefence.py:49-74`
- **CONCRETE INPUT OR BYPASS ATTEMPT**:

  1. `command` of wrong type (dict/int/None) to `run_command`:
     - `names_code_root` handles non-list/tuple via `str(command or "")`, never raises.
     - `_exec_command` in tools:
       ```python
       cmd = args.get("command")
       if isinstance(cmd, str):
           argv = shlex.split(cmd)
       elif isinstance(cmd, (list, tuple)):
           argv = [str(c) for c in cmd]
       else:
           return Execution(result=ToolResult(ok=False, error="command must be a string or list"))
       ```
       → returns ok=False, no crash, no execution.
  2. Unresolvable workspace in Session:
     - `disjoint_from_code` wraps `Path(workspace).resolve()` in try/except and raises `WorkspaceOverlapsCodeError` (ValueError subclass) on any OSError/ValueError/RuntimeError.
     - This is fail-loud but safe; callers see a clear exception instead of undefined behavior.
  3. govern_action with unknown tool:
     - Early-return `UNKNOWN_TOOL` decision.

- **WHY IT BREAKS A GUARANTEE**:  
  It doesn’t. All such malformed inputs are either handled gracefully (returning DENIED/FAILED) or surface as ValueError during construction, consistent with existing behavior.

- **WHETHER ANOTHER CHECK CATCHES IT**:  
  Not required; the functions themselves do the right thing.

- **FIX**:  
  None needed.

---

## Certification line

**CERTIFICATION**: **CERTIFIED** – With `code_protection_available()==False`, an autonomous `run_command` (one that reaches `execute_and_verify` without a human `approve()`) is effectively impossible: all act_then_report `run_command` calls are monotonically floored to `propose_first` before the auto-run dispatch, across all `intent.source` values and leash configurations reachable in-band.

---

## Steelman

The PR cleanly separates three concepts: (1) structural code/workspace topology (`disjoint_from_code`), (2) autonomy gating (`run_command` autonomy floor keyed on `code_protection_available()`), and (3) DiD/audit (`names_code_root`, `code_touch`, `code_protected` flag). None of the protection logic depends on the porous recognizer; instead, it relies only on hardcoded `False` for `code_protection_available()` plus a leash model that cannot be widened in-band, and a parser that never produces a `source` which could masquerade as `"proposed"`.

Even under adversarial conditions (host misconfiguring leash overrides, malformed commands, symlinked workspaces, or dynamic code-path construction), the worst you can do autonomously is get a HELD `run_command` that *requires* a human hand. The PR explicitly leaves “deceived human approves malicious command” out of scope but makes that human’s decision auditable (honest “code NOT protected” flag), and ensures that the collaborator cannot self-originate a shell rewrite of its code without being flatly DENIED.

---

## Verdict

**VERDICT**: **SOUND** – No autonomous self-rewrite path remains under the stated assumptions; the single highest-value future fix is to implement a genuine OS-level code fence and wire that into `code_protection_available()` so that even human-approved `run_command` invocations cannot structurally rewrite the collaborator or `salienceos` code roots.
