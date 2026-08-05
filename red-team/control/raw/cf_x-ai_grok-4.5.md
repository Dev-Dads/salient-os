# Red-team (control seam): x-ai/grok-4.5

_finish=stop seconds=310.5 usage={'prompt_tokens': 15600, 'completion_tokens': 16092, 'total_tokens': 31692, 'cost': 0.1014224, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 15488, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1014224, 'upstream_inference_prompt_cost': 0.0048704, 'upstream_inference_completions_cost': 0.096552}, 'completion_tokens_details': {'reasoning_tokens': 15266, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

**Nothing above LOW survived honest adversarial effort.** The three seam invariants hold under concrete misuse (mismatched directive/verdict, low-depth directive + high envelope, attestation-only, FAILED, forged-shaped inputs that still type-check as `Verdict`/`Directive`). Prior fixes (self-describing verdict, upward `max_stakes`, stakes floor in `decide`, attestation allowlist) close the obvious bypasses.

### LOW (hardening only — no false clear / false adapt / lowered scrutiny)

**L1 / `govern` not fail-closed on bad inputs**  
**SEVERITY:** LOW  
**LOCATION:** `salienceos/control/govern.py` — `govern`  
**CONCRETE TRIGGER:** `govern(verifier, None, envelope, receipt, [])` or a non-`Directive` object → `AttributeError` on `directive.verification_depth` before `decide()`.  
**WHY IT MATTERS:** `decide()` denies nulls; `govern()` throws. Not a clearance bypass (exception ≠ `cleared=True`), but a buggy caller that catches broadly could skip the gate.  
**SUGGESTED FIX:** Guard like `decide()` and return a denied `GovernedOutcome`, or call `decide` only after validating `type(directive) is Directive` and envelope binding.

**L2 / `decide` TypeError on non-comparable `verification_depth`**  
**SEVERITY:** LOW  
**LOCATION:** `salienceos/control/govern.py` — `decide`  
**CONCRETE TRIGGER:**  
`Directive(..., verification_depth=None, ...)` (dataclass does not enforce `int`) + any `Verdict` → `max(None, _stakes_floor(...))` raises `TypeError`.  
**WHY IT MATTERS:** Same class as L1: crash, not false allow. Corrupted/buggy interpreter output should deny.  
**SUGGESTED FIX:** Before `max`, require `isinstance(directive.verification_depth, int) and not isinstance(..., bool)` else deny with `required_level=FULL`, `cleared=False`.

**L3 / `max_stakes` + incomplete `STAKES_ORDER` footgun (not live today)**  
**SEVERITY:** LOW  
**LOCATION:** `salienceos/verifier/envelope.py` — `max_stakes`  
**CONCRETE TRIGGER:** No current `Stakes` member is outside `STAKES_ORDER`. *If* a new member were added to `Stakes` but omitted from `STAKES_ORDER`, `max_stakes(NEW, Stakes.LOW)` returns `LOW` (rank −1 loses).  
**WHY IT MATTERS:** Would break upward-only escalation for that future value. All four live stakes are in the tuple, so no concrete exploit now.  
**SUGGESTED FIX:** Build order from `Stakes` or assert `set(STAKES_ORDER) == set(Stakes)`.

---

## STEELMAN

The seam really does keep the two vocabularies honest: `verify(..., escalate_to=)` + `max_stakes` makes scrutiny upward-only and a no-op at `None`; `decide` re-floors required level by `_stakes_floor(effective_stakes)` so a low-depth directive cannot clear a high-stakes run; adaptation is double-locked (`cleared` ∧ `CANDIDATE` ∧ `status is VERIFIED`). Self-describing verdicts remove the old desync knobs, and the attestation path is allowlist-hard so stray hard-failure reasons cannot launder into RECEIPT.

## VERDICT

**SOUND** — Under the stated adversary and invariants, no concrete false-clear, false-adapt, or scrutiny-lowering path remains; only fail-closed hardening nits (L1–L3).
