# PR#34 codefence fail-closed panel: qwen/qwen3-max

_finish=stop seconds=27.7 cost=$0.0115 usage={'prompt_tokens': 8456, 'completion_tokens': 1261, 'total_tokens': 9717, 'cost': 0.01151358, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.01151358, 'upstream_inference_prompt_cost': 0.00659568, 'upstream_inference_completions_cost': 0.0049179}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDINGS**

---

**ID**: F1  
**TITLE**: Empty `PROTECTED_ROOTS` correctly fails closed with no bypass  
**SEVERITY**: NONE (non-finding — blocked attack)  
**LOCATION**: `collaborator/codefence.py:75`  
**CONCRETE INPUT OR BYPASS**: `PROTECTED_ROOTS = ()` (as in test)  
**WHY IT BREAKS A GUARANTEE**: It does **not** break a guarantee. The guard now raises `WorkspaceOverlapsCodeError` on empty roots, fulfilling the fail-closed intent. No path exists where `PROTECTED_ROOTS` is empty and `disjoint_from_code` returns normally.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A — this **is** the primary check.  
**FIX**: None needed.

---

**ID**: F2  
**TITLE**: Partial `PROTECTED_ROOTS` (e.g., missing `salienceos`) is not addressed — but was never claimed to be  
**SEVERITY**: LOW (out of scope / non-goal)  
**LOCATION**: `collaborator/codefence.py:40–55` (`_resolved_roots`)  
**CONCRETE INPUT OR BYPASS**: `salienceos.__file__` unresolvable → `PROTECTED_ROOTS = (collaborator_root,)` only  
**WHY IT BREAKS A GUARANTEE**: It **does not** break the certified claim. PR #34 only claims to fix the **empty** case. The panel’s finding was specifically about **zero** roots. Partial protection still enforces disjointness for the available root(s), which is better than nothing and consistent with “fail closed on empty”.  
**WHETHER ANOTHER CHECK CATCHES IT**: No — but it’s **not required**. The F1 guarantee spans both packages, so partial coverage is a **residual risk**, but PR #34 never promised full coverage validation. This is a known limitation of `_resolved_roots`’s best-effort design (already accepted in #33).  
**FIX**: Out of scope. Could add a warning or runtime assertion if `len(PROTECTED_ROOTS) < 2`, but that’s a **belt-and-suspenders enhancement**, not a flaw in the shipped delta.

---

**ID**: F3  
**TITLE**: Exception type and message are safe, deterministic, and compose correctly  
**SEVERITY**: NONE (non-finding)  
**LOCATION**: `collaborator/codefence.py:78–81`  
**CONCRETE INPUT OR BYPASS**: N/A  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. Raising `WorkspaceOverlapsCodeError` (a `ValueError`) matches the existing error contract. `Session` already treats any exception from `disjoint_from_code` as a construction failure (see `session.py:60–62`). The message is static, contains no sensitive data, and clearly explains the failure mode.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A — this is correct by design.  
**FIX**: None.

---

**ID**: F4  
**TITLE**: No regression in normal operation or Session construction  
**SEVERITY**: NONE (non-finding — confirmed)  
**LOCATION**: `collaborator/session.py:60–62` + `codefence.py:75–81`  
**CONCRETE INPUT OR BYPASS**: Normal env with resolvable `__file__` → `PROTECTED_ROOTS` non-empty  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. The early raise only triggers when `not PROTECTED_ROOTS`, which is false in all normal environments (as noted: `collaborator/` always resolves). All existing disjointness logic runs unchanged otherwise. The test suite passes (537 green).  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A — behavior is identical for non-empty roots.  
**FIX**: None.

---

**ID**: F5  
**TITLE**: No alternative code paths consult `PROTECTED_ROOTS` without going through `disjoint_from_code`  
**SEVERITY**: NONE (non-finding — confirmed)  
**LOCATION**: `collaborator/codefence.py` (all uses of `PROTECTED_ROOTS`)  
**CONCRETE INPUT OR BYPASS**: N/A  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. The only safety-critical use of `PROTECTED_ROOTS` for **enforcement** is in `disjoint_from_code`. `names_code_root` is explicitly **porous DiD** (certified non-boundary in #33). `code_protection_available()` is hardcoded `False`. Thus, the only gate that could fail open on empty roots was `disjoint_from_code` — and it’s now fixed.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A — the fix targets the **only relevant path**.  
**FIX**: None.

---

**CERTIFICATION LINE**  
**CERTIFIED** — The claim holds: empty `PROTECTED_ROOTS` fails closed with no regression to the normal path or Session construction.

---

**STEELMAN**  
An adversary might argue that partial `PROTECTED_ROOTS` (e.g., missing `salienceos`) still leaves part of the F1 surface unprotected, and that PR #34’s fix is insufficient because it doesn’t validate completeness. However, the PR explicitly scopes to the **empty tuple** case identified by the panel, and the base system already assumes best-effort root resolution. The fix correctly addresses the only silent no-op vector.

---

**VERDICT**  
**SOUND** — The delta correctly implements a fail-closed guard for the empty `PROTECTED_ROOTS` case with no regressions or side effects. The highest-value fix shipped is the early raise itself, which eliminates the governance guard no-op.
