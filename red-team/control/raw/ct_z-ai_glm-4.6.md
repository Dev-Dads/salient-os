# Red-team (control seam): z-ai/glm-4.6

_finish=stop seconds=374.2 usage={'prompt_tokens': 12970, 'completion_tokens': 5851, 'total_tokens': 18821, 'cost': 0.01581635, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.01581635, 'upstream_inference_prompt_cost': 0.0055771, 'upstream_inference_completions_cost': 0.01023925}, 'completion_tokens_details': {'reasoning_tokens': 4977, 'image_tokens': 0, 'audio_tokens': 0}}_

### Red-Team Findings

**ID:** F-001
**TITLE:** Overly Permissive `achieved_level` Calculation on Mixed-Reason Verdict
**SEVERITY:** HIGH
**LOCATION:** `salienceos/control/govern.py`, function `achieved_level`
**CONCRETE TRIGGER:**
1.  A `Directive` is created with `verification_depth = RECEIPT` (or `NONE`).
2.  An `ActionEnvelope` is created with `stakes = Stakes.LOW` (so the policy floor is `RECEIPT`).
3.  The verifier produces a `Verdict` with `status = Status.UNVERIFIED` and `reasons = (Reason.INTEGRITY_ATTESTED, Reason.INSUFFICIENT_CHANNELS)`. This can occur if the receipt is authentic but a separate check, such as channel distinctness for a higher-stakes obligation, fails.
4.  `decide()` is called with these inputs.
**WHY IT MATTERS:**
The `achieved_level` function will return `RECEIPT` because `Reason.INTEGRITY_ATTESTED` is present, ignoring the co-occurring `Reason.INSUFFICIENT_CHANNELS`. The `required` level is `RECEIPT`. Since `achieved >= required`, the `cleared` flag in `GovernedOutcome` will be set to `True`. This violates the **fail-closed clearance invariant (Invariant 2)**. An action that the verifier explicitly flagged with a hard failure reason (`INSUFFICIENT_CHANNELS`) is incorrectly cleared, allowing the system to proceed as if the action met the necessary verification bar when it did not.
**SUGGESTED FIX:**
The `achieved_level` function must be more conservative. It should only return `RECEIPT` for an `UNVERIFIED` verdict if `INTEGRITY_ATTESTED` is not accompanied by other failure reasons. This ensures any `UNVERIFIED` verdict with a "hard" failure results in an `achieved_level` of `NONE`, correctly failing closed.

```python
# In salienceos/control/govern.py

# Define the set of reasons that are compatible with a RECEIPT-level achievement.
ATTESTATION_ONLY_REASONS = frozenset({Reason.INTEGRITY_ATTESTED})

def achieved_level(verdict: Verdict, effective_stakes: Stakes) -> int:
    # ... (VERIFIED case remains the same)
    if verdict.status is Status.UNVERIFIED and Reason.INTEGRITY_ATTESTED in verdict.reasons:
        # Only treat as RECEIPT if there are no other, harder failure reasons.
        # This prevents a mixed-reason verdict (e.g., ATTESTED + INSUFFICIENT_CHANNELS)
        # from being improperly cleared.
        if set(verdict.reasons) <= ATTESTATION_ONLY_REASONS:
            return RECEIPT
    return NONE
```

---

### Steelman

The control seam is well-architected with a strong emphasis on defensive programming and clear invariants. The separation of the pure `decide()` function from the orchestrating `govern()` function is excellent for testability and reasoning. The implementation of the upward-only stakes escalation using `max_stakes` is robust and correctly applied in multiple places to defend against lowering verification scrutiny. The sealed gate for adaptation, which requires a strict `Status.VERIFIED` check, is a clear and correct enforcement of the learning-prevention invariant.

---

### VERDICT

**SERIOUS_FLAWS**

The implementation contains a high-severity flaw in the `achieved_level` function that violates the fail-closed clearance invariant. By allowing an `UNVERIFIED` verdict with mixed reasons (e.g., `INTEGRITY_ATTESTED` and `INSUFFICIENT_CHANNELS`) to be treated as a simple `RECEIPT`-level success, the seam can improperly clear actions that the verifier has flagged as failing critical checks. While the escalation and adaptation gate logic are sound, this flaw in the core clearance determination is a significant correctness and security issue.
