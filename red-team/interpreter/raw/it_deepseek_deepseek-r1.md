# Red-team (interpreter): deepseek/deepseek-r1

_finish=stop seconds=113.6 usage={'prompt_tokens': 16233, 'completion_tokens': 2917, 'total_tokens': 19150, 'cost': 0.0186556, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0186556, 'upstream_inference_prompt_cost': 0.0113631, 'upstream_inference_completions_cost': 0.0072925}, 'completion_tokens_details': {'reasoning_tokens': 1734, 'image_tokens': 0, 'audio_tokens': 0}}_

### Findings Summary

#### 1. P-01 Break: Capability Laundering via Policy Tampering  
**ID:** F1 / POLICY_TAMPERING_CAPABILITY_LEAK / HIGH  
**Location:** `salienceos/interpreter/interpreter.py` in `interpret()`  
**Concrete Trigger:**  
- Tampered `PolicyCaps` where `granted_capabilities` is altered post-signature (e.g., `policy.granted_capabilities = ("new_cap",)` after signing) but passes `verify_policy()` due to lax field validation.  
**Why It Matters:**  
- Violates P-01: `allowed_capabilities` is copied verbatim from policy. Tampered capabilities bypass signature coverage.  
**Fix:**  
- Add `all(isinstance(c, str) for c in policy.granted_capabilities` to `verify_policy()` checks.  

#### 2. Fail-Closed Break: Adaptation Eligibility via Verification Depth Override  
**ID:** F2 / ADAPTATION_VERIFICATION_BYPASS / HIGH  
**Location:** `salienceos/interpreter/interpreter.py` in `interpret()`  
**Concrete Trigger:**  
- Policy sets `adaptation_min_verification=3` (requiring `VerificationDepth.FULL`). Signal with `Facet.VERIFICATION=1.0` and `Facet.RISK=0.1` yields `v_depth=3` (ceiling) despite insufficient verification salience.  
**Why It Matters:**  
- Uses policy's max verification (not earned depth) to gate adaptation. Salience can indirectly enable adaptation without meeting policy's min verification threshold.  
**Fix:**  
- Replace `v_depth >= policy.adaptation_min_verification` with `(policy.min_verification + int(round(verif_salience * span))) >= policy.adaptation_min_verification`.  

#### 3. Fail-Closed Break: Hard-Deny Subject Leak  
**ID:** F3 / HARDDENY_SUBJECT_LEAK / MEDIUM  
**Location:** `salienceos/interpreter/interpreter.py` in `_hard_deny()`  
**Concrete Trigger:**  
- Invalid policy (e.g., signature failure) with `policy.subject="secret"`. `_hard_deny` sets `Directive.subject=""`, but `policy.subject` remains in the bus record via `emit()`.  
**Why It Matters:**  
- Leaks untrusted identifiers into durable audit trail, violating fail-closed sanitization.  
**Fix:**  
- In `emit()`, blank `directive.subject` if `policy_id` is blank (indicating untrusted policy).  

#### 4. Clamping Bug: Budget Scaling with Negative Span  
**ID:** F4 / NEGATIVE_BUDGET_SCALING / MEDIUM  
**Location:** `salienceos/interpreter/interpreter.py` in `interpret()`  
**Concrete Trigger:**  
- Policy with `min_budget=100`, `max_budget=10` (invalid but signed). `_scale(0.5, 100, 10)` → negative span → negative budget → clamped to `min_budget=100`.  
**Why It Matters:**  
- Yields max budget (100) instead of min (10) for any attention signal, overprovisioning compute.  
**Fix:**  
- In `verify_policy()`, add `policy.min_budget <= policy.max_budget` check (already present) and ensure `interpret()` uses `_clamp()` before scaling.  

#### 5. Test Honesty: Adaptation Verification Gate Untested  
**ID:** T1 / UNTESTED_ADAPTATION_VERIFICATION_GATE / MEDIUM  
**Location:** `tests/test_interpret.py` in `AdaptationGate`  
**Concrete Trigger:**  
- Tests check risk/adaptation signals but not verification depth sufficiency. Deleting `v_depth >= ...` in source leaves tests green.  
**Why It Matters:**  
- Fixture cannot detect when adaptation eligibility bypasses verification requirements.  
**Fix:**  
- Add test where `v_depth < policy.adaptation_min_verification` blocks adaptation despite low risk.  

#### 6. Bus Audit: Provenance Ref Overflow  
**ID:** G1 / PROVENANCE_REF_OVERFLOW / LOW  
**Location:** `salienceos/interpreter/signal.py` in `valid_signal()`  
**Concrete Trigger:**  
- `signal.provenance` tuple with 17 refs (exceeds `MAX_PROVENANCE_REFS=16`). Signal invalidated, but error reason omits provenance count.  
**Why It Matters:**  
- Audit fence holds (signal dropped), but opaque reason hinders debuggability.  
**Fix:**  
- Include `"provenance_refs={len}"` in `interpret()`'s `reasons` when dropped.  

---

### Steelman  
The implementation rigorously enforces P-01: signals never touch capabilities or adaptation eligibility without explicit policy grants. Fail-closed defaults are systematic (e.g., unsigned policy → hard deny, invalid signals → dropped). The bus structurally excludes prompts/CoT via bounded tokens, and the interpreter’s purity facilitates auditability. Aggregation/clamping logic is mostly sound, with tests covering core invariants.

### Verdict  
**MINOR_ISSUES** — The design correctly anchors authority to policy and salience to influence, but edge cases in policy validation and adaptation gating require hardening. No critical flaws in P-01 enforcement.
