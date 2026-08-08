# Stage-4-live red-team: deepseek/deepseek-v4-pro

_finish=stop seconds=110.7 usage={'prompt_tokens': 11493, 'completion_tokens': 4690, 'total_tokens': 16183, 'cost': 0.005110086114, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.005110086114, 'upstream_inference_prompt_cost': 0.002813693274, 'upstream_inference_completions_cost': 0.00229639284}, 'completion_tokens_details': {'reasoning_tokens': 3505, 'image_tokens': 0, 'audio_tokens': 0}}_

**Finding 1**  
**ID:** F1  
**TITLE:** Fail‑open on learning‑bookkeeping exception silently drops inhibitor  
**SEVERITY:** HIGH  
**LOCATION:** `collaborator/governance.py`, `execute_and_verify()`, the `try/except` around `consume()` (lines ~230–237).  
**CONCRETE SCENARIO:**  
The memory governor’s `retain()` raises `HandoffMismatchError` (e.g., because of a subtle subject‑mismatch bug in the gate). The blanket `except Exception` catches it, sets `adaptation = memory = None` and `disagreement = False`. The action’s report shows no disagreement, and the inhibitor warning is never recorded. A future similar risky action could then be learned because the permanent warning was lost.  
**WHY IT MATTERS:**  
The inhibitor is a safety mechanism that prevents future learning of high‑risk skills. Swallowing the exception and reporting `disagreement=False` hides the failure and removes the protection. The comment says “learning bookkeeping must never break the action’s report”, but the action already ran; losing the inhibitor is a safety degradation, not a report‑breaking event.  
**SUGGESTED FIX:**  
At minimum, log the exception and do **not** set `disagreement = False` when the adaptation decision already contains a handoff. Better: let the exception propagate to the host so it can decide how to handle a failed safety record, or wrap it in a custom exception that the host can catch without aborting the action’s primary result.

---

**Finding 2**  
**ID:** F2  
**TITLE:** `now_days` not validated; negative value triggers fail‑open  
**SEVERITY:** LOW  
**LOCATION:** `collaborator/session.py`, `Session.__init__` (line `self.now_days = float(now_days)`) and `execute_and_verify()` exception handler.  
**CONCRETE SCENARIO:**  
A host accidentally passes `now_days=-1`. The memory governor’s `retain()` raises `TypeError` (“now_days must be a finite non‑negative number”). The exception is caught, inhibitor lost, disagreement set to `False`.  
**WHY IT MATTERS:**  
Same fail‑open pattern as F1, though the trigger is a host misconfiguration rather than a gate bug. The session should reject invalid `now_days` early.  
**SUGGESTED FIX:**  
Validate `now_days >= 0` in `Session.__init__` and raise a clear error.

---

**Finding 3**  
**ID:** F3  
**TITLE:** `disagreement` flag could be `True` while memory record is `None` after a partial failure  
**SEVERITY:** LOW  
**LOCATION:** `execute_and_verify()`, the `disagreement` computation line.  
**CONCRETE SCENARIO:**  
If `consume()` returns successfully but the memory record is somehow `None` (impossible with current gate, but a future change could introduce it), the expression `bool(memory.inhibitor)` would raise `AttributeError`, which is caught and sets `disagreement=False`. However, if the exception occurs *after* `adaptation` is set but before `memory` is assigned, the except block sets both to `None`. The current code is safe because `consume` returns a tuple, but the structure is fragile.  
**WHY IT MATTERS:**  
Minor robustness concern; no current exploit.  
**SUGGESTED FIX:**  
Assign the tuple unpacking outside the try block, or explicitly check `memory is not None` before accessing `.inhibitor`.

---

**Finding 4**  
**ID:** F4  
**TITLE:** Honesty of the claim – “no decay” evidence is sound, but retention class `ephemeral` may confuse  
**SEVERITY:** LOW  
**LOCATION:** Proof output (`stage4_live_proof_output.txt`) and `memory.py`.  
**CONCRETE SCENARIO:**  
The inhibitor’s `retention_class` is `'ephemeral'` (the floor class for unbound/invalid outcomes, or as set by the interpreter). The proof correctly shows that `effective_weight` returns 1.0 regardless of time because the inhibitor flag bypasses decay. However, a reader might misinterpret “ephemeral” as implying the warning is short‑lived.  
**WHY IT MATTERS:**  
The claim “NO DECAY” is true, but the class name could cause confusion. Not a functional flaw.  
**SUGGESTED FIX:**  
Add a comment in the proof or documentation clarifying that the retention class is irrelevant for inhibitors.

---

**STEELMAN**  
The wiring faithfully exercises the two‑channel gate: it emits the ADAPTATION signal only when the host allows, calls `consume()` with the real governed outcome, and derives the `disagreement` flag directly from the gate’s own records. The demo action runs without verification and still triggers the inhibitor hand‑off, exactly as designed. The only meaningful weakness is the exception‑swallowing in the learning path, which could silently drop a safety‑critical inhibitor.

**VERDICT:** **SERIOUS_FLAWS** — the fail‑open error handling on `consume()` can hide a lost inhibitor and report no disagreement, undermining the safety guarantee the wiring is meant to uphold.
