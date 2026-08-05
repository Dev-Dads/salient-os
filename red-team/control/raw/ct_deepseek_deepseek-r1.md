# Red-team (control seam): deepseek/deepseek-r1

_finish=stop seconds=312.4 usage={'prompt_tokens': 14088, 'completion_tokens': 5960, 'total_tokens': 20048, 'cost': 0.0247616, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0247616, 'upstream_inference_prompt_cost': 0.0098616, 'upstream_inference_completions_cost': 0.0149}, 'completion_tokens_details': {'reasoning_tokens': 4922, 'image_tokens': 0, 'audio_tokens': 0}}_

After a thorough adversarial review of the control seam implementation, focusing on the three invariants and supporting requirements, I find no critical or high-severity defects. The implementation is robust and upholds the specified invariants under in-scope threats. Below are detailed findings:

### **FINDING 1: Potential Misclassification of INTEGRITY_ATTESTED as RECEIPT**  
**SEVERITY**: LOW  
**LOCATION**: `salienceos/control/govern.py` in `achieved_level()`  
**CONCRETE TRIGGER**:  
- `verdict.status = Status.UNVERIFIED`  
- `Reason.INTEGRITY_ATTESTED in verdict.reasons`  
- `Reason.INSUFFICIENT_CHANNELS in verdict.reasons` (co-occurring)  
**WHY IT MATTERS**:  
If a verdict has both `INTEGRITY_ATTESTED` and `INSUFFICIENT_CHANNELS` (e.g., due to partial corroboration at HIGH stakes), `achieved_level()` incorrectly returns `RECEIPT` (level 1) instead of `NONE` (level 0). This overstates the achieved verification level, though clearance remains fail-closed (since `required` is floored by envelope stakes).  
**SUGGESTED FIX**:  
Modify the condition to require `INTEGRITY_ATTESTED` as the sole reason:  
```python
if (verdict.status is Status.UNVERIFIED and 
    verdict.reasons == (Reason.INTEGRITY_ATTESTED,)):
    return RECEIPT
```

### **FINDING 2: Redundant Clamping in decide()**  
**SEVERITY**: LOW  
**LOCATION**: `salienceos/control/govern.py` in `decide()`  
**CONCRETE TRIGGER**:  
Any call to `decide()` where `required = max(depth, _stakes_floor)` is already within [0, 3].  
**WHY IT MATTERS**:  
The line `required = NONE if required < NONE else FULL if required > FULL else required` is redundant since `depth` is clamped by the interpreter (0-3) and `_stakes_floor` returns 1-3. This adds no value and obscures logic.  
**SUGGESTED FIX**:  
Remove the clamping line; rely on preconditions.  

### **FINDING 3: Inefficient Effective Stakes Recalculation**  
**SEVERITY**: LOW  
**LOCATION**: `salienceos/control/govern.py` in `decide()`  
**CONCRETE TRIGGER**:  
Any call to `decide()` where `effective_stakes` is already ≥ `envelope_stakes` (e.g., via `govern()`).  
**WHY IT MATTERS**:  
`effective_stakes = max_stakes(envelope_stakes, effective_stakes)` is idempotent but redundant when `govern()` already enforces upward escalation. This wastes cycles but doesn't compromise security.  
**SUGGESTED FIX**:  
Remove the line; add an assertion instead for defense-in-depth.  

---

### **STEELMAN**  
The implementation excels in:  
1. **Invariant 1 (Upward Escalation)**: The `max_stakes()` logic (used in `govern()`, `decide()`, and verifier) robustly ensures effective stakes never dip below the policy-signed floor. The `escalation_for()` design (only FULL escalates) prevents salience from lowering scrutiny.  
2. **Invariant 2 (Fail-Closed Clearance)**: `decide()` correctly denies clearance on subject mismatch, FAILED verdicts, or `achieved < required`. The envelope-stakes floor (`_stakes_floor()`) ensures the policy floor always bounds requirements.  
3. **Invariant 3 (Adaptation Gate)**: `adaptation_allowed` requires both candidate eligibility and `Status.VERIFIED`, preventing learning from unverified actions. The `achieved_level()` mapping correctly distinguishes VERIFIED (INDEPENDENT/FULL) from INTEGRITY_ATTESTED (RECEIPT).  

---

### **VERDICT**: **SOUND**  
The control seam implementation upholds all critical invariants under in-scope threats, with only minor non-exploitable issues. The design ensures salience only escalates verification, clearance fails closed, and adaptation requires verified world facts.
