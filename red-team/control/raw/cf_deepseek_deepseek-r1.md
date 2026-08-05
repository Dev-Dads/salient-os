# Red-team (control seam): deepseek/deepseek-r1

_finish=stop seconds=302.0 usage={'prompt_tokens': 15433, 'completion_tokens': 7275, 'total_tokens': 22708, 'cost': 0.0289906, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 512, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0289906, 'upstream_inference_prompt_cost': 0.0108031, 'upstream_inference_completions_cost': 0.0181875}, 'completion_tokens_details': {'reasoning_tokens': 6832, 'image_tokens': 0, 'audio_tokens': 0}}_

### RED-TEAM REPORT

After rigorous adversarial review of the control seam implementation, focusing on the three invariants and supporting checks, no defects above LOW severity were found. The implementation demonstrates robust fail-closed behavior, upholds salience escalation invariants, and correctly gates adaptation. Below is a summary of findings, steelman defense, and final verdict.

---

#### FINDINGS SUMMARY  
**No critical/high/medium severity defects identified.** All potential issues were analyzed and found to be either:  
- Properly mitigated by existing fail-closed mechanisms  
- Covered by comprehensive tests  
- Out-of-scope (requiring policy signing key compromise)  
- Theoretically possible but lacking concrete exploit paths  

**LOW-SEVERITY OBSERVATIONS**  
1. **Error reporting desync in receipt mismatch**  
   - **SEVERITY**: LOW  
   - **LOCATION**: `salienceos/control/govern.py` in `decide()`  
   - **CONCRETE TRIGGER**:  
     - Directive subject = "A", verdict.envelope_id = "B" (mismatched actions)  
     - Verdict status = `UNVERIFIED` with `Reason.RECEIPT_ENVELOPE_MISMATCH`  
   - **WHY IT MATTERS**:  
     Outcome reasons may report "under_verified" instead of the root cause (action binding failure), complicating debugging.  
   - **SUGGESTED FIX**:  
     Prioritize verdict reasons in `decide()`'s outcome when present:  
     ```python  
     if verdict.reasons:  
         reasons.extend(verdict.reasons)  # Propagate verifier reasons  
     ```  

2. **Stakes floor defaults to FULL on malformed input**  
   - **SEVERITY**: LOW  
   - **LOCATION**: `salienceos/control/govern.py` in `_stakes_floor()`  
   - **CONCRETE TRIGGER**:  
     `effective_stakes = None` (e.g., from `max_stakes("invalid", "input")`)  
   - **WHY IT MATTERS**:  
     Defaulting to `FULL` is conservatively safe but may over-escalate requirements for corrupted inputs.  
   - **SUGGESTED FIX**:  
     Add explicit logging for malformed stakes; functional behavior remains sound.  

---

#### STEELMAN DEFENSE  
The implementation excels in three key areas:  
1. **Self-describing verdicts** eliminate parameter desynchronization risks by binding `envelope_id` and `effective_stakes` directly to the verdict. This forces all clearance decisions to derive from the verifier's output, preventing caller manipulation.  
2. **Upward-only escalation** is enforced via `max_stakes()` and `escalation_for()` logic. Tests (e.g., `test_salience_cannot_lower_envelope_floor`) prove salience cannot reduce scrutiny below the policy-signed floor.  
3. **Adaptation gating** is rigorously implemented: `adaptation_allowed` requires explicit `Status.VERIFIED` and candidate eligibility, blocking learning from attested/cleared-but-unverified actions.  

---

#### VERDICT  
**SOUND** – The control seam upholds all invariants under in-scope adversarial scenarios with no exploitable flaws. Minor improvements to error reporting are recommended but do not compromise security.
