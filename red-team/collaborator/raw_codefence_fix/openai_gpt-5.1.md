# PR#34 codefence fail-closed panel: openai/gpt-5.1

_finish=stop seconds=72.7 cost=$0.0408 usage={'prompt_tokens': 8459, 'completion_tokens': 3024, 'total_tokens': 11483, 'cost': 0.04081375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.04081375, 'upstream_inference_prompt_cost': 0.01057375, 'upstream_inference_completions_cost': 0.03024}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE: Empty PROTECTED_ROOTS no longer fails open  
SEVERITY: HIGH (positive security impact; previously-open bug is now closed)  
LOCATION: collaborator/codefence.py:72-80  

CONCRETE INPUT OR BYPASS  
- Environment: PROTECTED_ROOTS is empty at import time (e.g., both `__file__` and `salienceos.__file__` are unset or non-resolvable so `_resolved_roots()` returns `()`), or at runtime via monkeypatching in-process.  
- Call: `codefence.disjoint_from_code("/any/path")` or `Session(workspace="/any/path")`.  

WHY IT BREAKS A GUARANTEE  
- Prior to this PR, `disjoint_from_code` looped over `PROTECTED_ROOTS` without guarding the empty-tuple case. If `_resolved_roots()` returned `()`, the guard silently became a no-op: every workspace was accepted, even when the code roots were unknown.  
- That behavior violated the “governance guard must never silently no-op” requirement and the specific F-6 intent to structurally protect the collaborator’s own code.  
- The new `if not PROTECTED_ROOTS: raise WorkspaceOverlapsCodeError(...)` ensures that in the empty-roots state, *all* workspaces are rejected, so the guard never silently passes while doing no protection.  

WHETHER ANOTHER CHECK CATCHES IT  
- Before this PR: no other check in this module or in Session construction rejected the “empty PROTECTED_ROOTS” case; the guard was effectively disabled.  
- After this PR: the early raise is the primary line of defense for this case; there is no secondary independent check of PROTECTED_ROOTS emptiness elsewhere.  

FIX  
- Implemented and correct: the added early guard  
  ```python
  if not PROTECTED_ROOTS:
      raise WorkspaceOverlapsCodeError(...)
  ```  
  closes the previously fail-open path for empty roots.  
- I tried to find any path where PROTECTED_ROOTS could be empty yet `disjoint_from_code` would not raise; there is none. The `if not PROTECTED_ROOTS` check correctly handles an empty tuple and is executed before any other logic in `disjoint_from_code`.  
- CERTIFICATION: I cannot break this claim; empty PROTECTED_ROOTS now reliably yields a fail-closed error, not a silent no-op.  


ID 2 / TITLE: No regression in normal (non-empty) PROTECTED_ROOTS path  
SEVERITY: LOW (regression risk analysis, no bug found)  
LOCATION: collaborator/codefence.py:81-99; tests/test_collaborator_codefence.py:91-107  

CONCRETE INPUT OR BYPASS  
- Typical environment where `_resolved_roots()` succeeds for at least `collaborator/` (and usually `salienceos/`), so `PROTECTED_ROOTS` is a non-empty tuple of valid directories.  
- Example calls:  
  - `codefence.disjoint_from_code(tmpdir)` where `tmpdir` is outside all protected roots.  
  - `codefence.disjoint_from_code(_ROOT)` where `_ROOT` is one of the actual protected roots.  
  - `Session(workspace=tmpdir)` and `Session(workspace=str(_ROOT))`.  

WHY IT BREAKS A GUARANTEE  
- I attempted to show that the new early `if not PROTECTED_ROOTS` raise changed behavior on the normal path, e.g., by:  
  - mis-firing when PROTECTED_ROOTS is non-empty but somehow “falsey”;  
  - altering the type or message of exceptions observable by callers;  
  - shadowing the existing “unresolvable workspace” error or overlap error in normal operation.  
- None of these attempts succeeded:  
  - In Python, a non-empty tuple is always truthy, regardless of element validity, so the early `if not PROTECTED_ROOTS` cannot trigger when there is at least one element.  
  - The existing exception flow is preserved:  
    - Unresolvable workspace still raises `WorkspaceOverlapsCodeError("unresolvable workspace: ...")`.  
    - Overlap still raises `WorkspaceOverlapsCodeError("workspace ... overlaps ...")`.  
    - A disjoint, resolvable workspace still returns `None` (no exception).  
  - The tests `test_tempdir_is_disjoint`, `test_workspace_equal_to_code_root_is_refused`, `test_workspace_inside_code_root_is_refused`, `test_workspace_containing_code_root_is_refused`, and `test_session_construction_refuses_overlap` all pass and exercise the non-empty roots path.  

WHETHER ANOTHER CHECK CATCHES IT  
- Session construction still sees only `ValueError` (of which `WorkspaceOverlapsCodeError` is a subclass), so caller expectations and type composition are unchanged.  
- The new early raise only triggers in the PROTECTED_ROOTS-empty case and thus does not shadow or alter the previous normal-path errors.  

FIX  
- No change required for the non-empty case; the delta is behaviorally inert when `PROTECTED_ROOTS` is non-empty.  
- CERTIFICATION: I cannot find a regression in the normal code path or in Session construction behavior attributable to this change.  


ID 3 / TITLE: Non-empty but invalid PROTECTED_ROOTS still fails open structurally (stated non-goal)  
SEVERITY: MEDIUM (structural weakness, not introduced by this PR; residual risk)  
LOCATION: collaborator/codefence.py:47-66 (PROTECTED_ROOTS construction); 81-99 (use)  

CONCRETE INPUT OR BYPASS  
- Scenario: a packaging / environment bug yields a non-empty `PROTECTED_ROOTS` that does not correspond to the actual on-disk locations of `collaborator/` or `salienceos/`. For example:  
  - Both modules are shadowed or imported from unusual loaders that set `__file__` to a path that resolves, but not to the real code directories.  
  - The filesystem layout later changes (e.g., in-place move or overlay) so the resolved path no longer points to live code, but the module is already imported and `PROTECTED_ROOTS` is fixed.  
- In such a case, `disjoint_from_code` enforces disjointness relative to these bogus roots and can happily accept a workspace that overlaps the *real* code location.  

WHY IT BREAKS A GUARANTEE  
- The core F-6 intent is “the workspace must be disjoint from the Collaborator’s own code root (collaborator/ + salienceos/).” That assumes PROTECTED_ROOTS accurately models the actual code root.  
- If PROTECTED_ROOTS is populated with paths that are directories but not the real code roots, then:  
  - The new empty-roots fail-closed guard never fires because the tuple is non-empty.  
  - The disjointness checks operate on the wrong directories and can return “disjoint” when the workspace can still reach actual governance code.  
- This is not new with PR #34 — it already existed in #33 — but the new “guard must never no-op” language could be misread as covering this case. It does not. This remains a potential fail-open if the import environment lies about `__file__`.  

WHETHER ANOTHER CHECK CATCHES IT  
- No check in this module detects “semantically wrong but resolvable” roots. `_resolved_roots` verifies only that the path is a directory and absolute; it does not confirm identity with the live module objects (e.g., by re-importing from path or checking contents).  
- No other part of Session construction validates that the workspace is disjoint from any “true” code location independent of PROTECTED_ROOTS.  

FIX  
- If this scenario is in-scope to harden, add a stronger invariant for `_resolved_roots`, for example:  
  - At import, verify that each supposed root contains an `__init__.py` and key module files expected for `collaborator` / `salienceos`, or  
  - Compare `Path(module.__file__).resolve().parent` to a second independent resolution (e.g., re-import from a canonical package spec or known installation root) and fail-closed if they diverge.  
- Alternatively, document this explicitly as out of scope (environment must provide honest `__file__` semantics), to avoid overclaiming the guard’s robustness.  
- This is a pre-existing structural assumption, not a regression, but worth calling out as residual risk.  


ID 4 / TITLE: Guard layer mismatch for names_code_root when PROTECTED_ROOTS is empty  
SEVERITY: LOW (belt-and-suspenders nit; not a boundary)  
LOCATION: collaborator/codefence.py:107-130; tests/test_collaborator_codefence.py:109-151  

CONCRETE INPUT OR BYPASS  
- Setup: `PROTECTED_ROOTS` is empty (e.g., import environment pathologically broken), causing `disjoint_from_code` to fail-closed and prevent Session construction, *if invoked*.  
- However, consider a hypothetical path (e.g., in future refactors or alternate call sites) that:  
  - Uses `names_code_root` as a signal or gate around `run_command`, but  
  - Does so in a context where no Session / `disjoint_from_code` call has yet run, or where the failure from `disjoint_from_code` is caught and ignored.  
- In that context, `names_code_root` simply iterates over an empty `PROTECTED_ROOTS` and returns `""` for all commands, silently dropping its DiD signal while the structural guard has already failed-closed.  

WHY IT BREAKS A GUARANTEE  
- The current code and tests make clear that `names_code_root` is *not* a boundary; its porousness is explicitly pinned, so this is not a guarantee-breaking bug for F-6.  
- However, there is a layer mismatch: `disjoint_from_code` treats empty roots as a fatal fail-closed condition, whereas `names_code_root` treats them as “no matches” and silently no-ops.  
- If any future logic were to incorrectly treat `names_code_root != ""` as a safety boundary (e.g., “if it doesn’t name the code root, it’s safe”), it would be dangerously misled in exactly the “empty roots” condition that is now treated as pathological at the construction layer.  

WHETHER ANOTHER CHECK CATCHES IT  
- Under the current design, Session construction always calls `disjoint_from_code`, and the test `test_empty_protected_roots_fails_closed` asserts that Session creation fails when PROTECTED_ROOTS is empty. That path is safe.  
- The lingering risk is conceptual / future-facing: nothing in `names_code_root` itself enforces or signals that “empty PROTECTED_ROOTS is a fail-closed condition,” so misuse by a future caller is possible. No second check prevents that.  

FIX  
- Option 1 (minimal and explicit): add a short note in the `names_code_root` docstring clarifying that if PROTECTED_ROOTS is empty, this function is operating in a clearly invalid environment and its output must not be used for any safety decision.  
- Option 2 (stronger): mirror the fail-closed behavior and raise a clear error if `not PROTECTED_ROOTS`, ensuring that the “no-op in pathological envs” cannot occur even at the DiD layer; this is conservative but somewhat at odds with the function’s current “best-effort tag” design.  
- Given its DiD status, this is a belt-and-suspenders nit, not a current exploit path.  


ID 5 / TITLE: Error message content is deterministic and non-leaky  
SEVERITY: LOW (sanity check, no issue found)  
LOCATION: collaborator/codefence.py:72-80, 87-99  

CONCRETE INPUT OR BYPASS  
- Inputs:  
  - Empty PROTECTED_ROOTS plus arbitrary `workspace` argument to `disjoint_from_code`.  
  - Unresolvable workspace path with non-empty PROTECTED_ROOTS.  

WHY IT BREAKS A GUARANTEE  
- I attempted to show that the new error message for the empty-roots case could either:  
  - leak sensitive host details, or  
  - be nondeterministic in a way that complicates governance, logging, or tests.  
- The empty-roots message is static English text with no path interpolation; it does not include filesystem details or any data derived from user input beyond the fact of failure. The unresolvable-workspace message still interpolates only the `workspace!r`, as before. Both are deterministic given the call inputs. No info leak or instability was found.  

WHETHER ANOTHER CHECK CATCHES IT  
- Not applicable; this is a positive sanity check.  

FIX  
- No change needed. The new raise is syntactically and semantically safe.  


CERTIFICATION LINE  
CERTIFIED — When PROTECTED_ROOTS is empty, disjoint_from_code now reliably raises (failing closed) and I do not find any regression in the normal non-empty-roots path or in Session construction behavior attributable to this PR.  


STEELMAN  
The PR addresses the precise fail-open condition identified by the prior panel — the empty PROTECTED_ROOTS case — with a minimal, targeted guard that cleanly composes with existing ValueError-based Session construction checks. It is written in an obviously fail-closed style, has an explicit regression test pinning the behavior, and leaves the non-empty path unchanged. The remaining structural assumptions (e.g., correctness of `__file__`-based roots) are pre-existing and outside the scope of this delta but should be acknowledged as residual risk.  


VERDICT  
VERDICT: MINOR_ISSUES — The change soundly closes the empty-PROTECTED_ROOTS fail-open and does not regress normal behavior; the main residual risk is that “non-empty but wrong roots” are still assumed honest, which is not new here but remains the highest-value structural gap.  
Single highest-value fix: strengthen or explicitly document the trust assumptions around PROTECTED_ROOTS (e.g., by validating roots more robustly against the actual module layout or clearly stating that an honest import environment is required).
