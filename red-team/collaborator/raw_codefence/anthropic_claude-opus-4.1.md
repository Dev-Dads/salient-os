# PR#33 codefence certification panel: anthropic/claude-opus-4.1

_finish=stop seconds=108.4 cost=$0.7778 usage={'prompt_tokens': 46376, 'completion_tokens': 1095, 'total_tokens': 47471, 'cost': 0.777765, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.777765, 'upstream_inference_prompt_cost': 0.69564, 'upstream_inference_completions_cost': 0.082125}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## SECURITY REVIEW FINDINGS

### FINDING-1: EMPTY PROTECTED_ROOTS FAIL-OPEN
**ID:** F6-001  
**TITLE:** Empty PROTECTED_ROOTS Makes disjoint_from_code() a No-Op  
**SEVERITY:** HIGH  
**LOCATION:** collaborator/codefence.py:77-79  
**CONCRETE INPUT:** If both `__file__` and `salienceos.__file__` are None/unresolvable at import, PROTECTED_ROOTS becomes empty tuple  
**WHY IT BREAKS:** With empty PROTECTED_ROOTS, disjoint_from_code() never raises (line 77-79 loop never executes), allowing ANY workspace including one containing the actual code. The "fail-open when PROTECTED_ROOTS is empty" mentioned in the prompt is REACHABLE if modules lack `__file__` attributes or resolve() fails  
**ANOTHER CHECK:** No - this is the sole workspace fence  
**FIX:** Make disjoint_from_code() raise if PROTECTED_ROOTS is empty rather than silently passing

### FINDING-2: RELATIVE PATH BYPASS IN disjoint_from_code()
**ID:** F6-002  
**TITLE:** Relative Workspace Path Can Bypass Disjointness Check  
**SEVERITY:** MEDIUM  
**LOCATION:** collaborator/codefence.py:73  
**CONCRETE INPUT:** `Session(workspace="../collaborator")` where cwd is inside the code root  
**WHY IT BREAKS:** If Path.resolve() fails on line 73 (OSError on broken symlink, permission denied, etc.), it raises WorkspaceOverlapsCodeError with "unresolvable" message but never checks disjointness. A caller catching ValueError (Session does) could retry with a different path. While Session.__init__ would fail, a direct disjoint_from_code() call with unresolvable path fails without checking overlap  
**ANOTHER CHECK:** Session construction would still fail on the ValueError, but the disjointness guarantee isn't enforced  
**FIX:** Consider unresolvable workspace as overlapping by default rather than raising a distinct error

### FINDING-3: LEASH OVERRIDE BYPASS VIA RUNTIME MUTATION
**ID:** F6-003  
**TITLE:** Runtime Leash Mutation After Session Construction  
**SEVERITY:** LOW  
**LOCATION:** collaborator/governance.py:196 (_leash_for function)  
**CONCRETE INPUT:** After Session construction, set `session.leash_overrides["run_command"] = "invalid"` then call govern_action()  
**WHY IT BREAKS:** Session validates leash_overrides at construction, but _leash_for() reads the mutable dict at runtime. An invalid leash fails closed to PROPOSE_FIRST (line 199), but this allows post-construction mutation that Session's validation doesn't catch  
**ANOTHER CHECK:** Yes - line 199 fails closed to PROPOSE_FIRST for invalid values  
**FIX:** Make leash_overrides immutable after construction or re-validate in _leash_for()

## CERTIFICATION

**CLAIM:** "With code_protection_available()==False, an AUTONOMOUS run_command (one that RANs/executes without a human hand) is IMPOSSIBLE"

**CERTIFIED** - The autonomy-withheld floor at governance.py:400-403 correctly floors every act_then_report run_command to propose_first when code_protection_available()==False, placed after the emission floor but before the dispatch. The dispatch at line 410 only auto-runs on exact ACT_THEN_REPORT, and the floor is monotonic (apply_cap only tightens).

## STEELMAN

The implementation creates multiple defensive layers: workspace disjointness structurally prevents fenced tools from reaching code, the proposer hard-deny blocks obvious self-rewrites at their source, and the autonomy floor ensures no shell runs without human oversight until OS-level protection is available. The design correctly identifies that code_protection_available()==False makes the system honest about its current limitations while maintaining safety through human gating.

## VERDICT

**MINOR_ISSUES** - The core claim is sound and the autonomy floor works correctly, but the empty PROTECTED_ROOTS fail-open path could allow workspace overlap in edge cases.

**Single highest-value fix:** Add a check in disjoint_from_code() to raise WorkspaceOverlapsCodeError if PROTECTED_ROOTS is empty, ensuring the fence never silently becomes a no-op: `if not PROTECTED_ROOTS: raise WorkspaceOverlapsCodeError("cannot establish code roots - refusing workspace for safety")`
