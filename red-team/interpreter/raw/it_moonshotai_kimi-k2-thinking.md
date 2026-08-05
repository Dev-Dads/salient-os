# Red-team (interpreter): moonshotai/kimi-k2-thinking

_finish=stop seconds=354.9 usage={'prompt_tokens': 15342, 'completion_tokens': 12849, 'total_tokens': 28191, 'cost': 0.0413277, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0413277, 'upstream_inference_prompt_cost': 0.0092052, 'upstream_inference_completions_cost': 0.0321225}, 'completion_tokens_details': {'reasoning_tokens': 11729, 'image_tokens': 0, 'audio_tokens': 0}}_

**Finding 1: Exception in Signal Iterable Propagates as Crash (Fail-Closed Break)**

- **ID**: INTERP-001
- **TITLE**: Uncaught exception during signal materialization breaks fail-closed guarantee
- **SEVERITY**: HIGH
- **LOCATION**: `salienceos/interpreter/interpreter.py`, `interpret()`, line `signals = tuple(signals)`
- **CONCRETE TRIGGER**: 
  ```python
  def poisoned_gen():
      yield SalienceSignal("s", "req-1", "attention", 0.5, 1.0, ())
      raise ValueError("misfiring subsystem")
  
  interpret(valid_policy, poisoned_gen(), policy_key)  # Raises ValueError instead of Directive
  ```
- **WHY IT MATTERS**: Per the fail-closed mandate, any error or edge case must yield the cautious directive (hard deny). Propagating an exception crashes the choke point, halting all processing instead of degrading to empty capabilities / max verification. A misfiring subsystem that publishes a throwing generator can disable the interpreter.
- **SUGGESTED FIX**: Wrap the materialization in a broad exception handler:
  ```python
  try:
      signals = tuple(signals)
  except Exception:
      signals = ()
      reasons.append("signals_materialization_failed")
  ```

---

**Finding 2: Missing Test for Zero-Confidence Aggregation (Test Honesty)**

- **ID**: INTERP-002
- **TITLE**: Zero-confidence signal group lacks coverage
- **SEVERITY**: LOW
- **LOCATION**: `tests/test_interpret.py` (absence) and `salienceos/interpreter/interpreter.py`, `_aggregate()`
- **CONCRETE TRIGGER**: 
  ```python
  sigs = [
      SalienceSignal("s", "req-1", "attention", 1.0, 0.0, ()),  # confidence 0
      SalienceSignal("s", "req-1", "attention", 1.0, 0.0, ())   # confidence 0
  ]
  # Current code returns 0.0 for attention facet (weight=0 branch)
  # No test asserts this behavior; a mutant that removed the `if weight > 0` guard 
  # would cause ZeroDivisionError but no test would turn red.
  ```
- **WHY IT MATTERS**: While the code correctly handles the `weight == 0` case (returning 0.0, which is fail-closed), the absence of a test means a regression introducing a division-by-zero would not be caught by the current suite.
- **SUGGESTED FIX**: Add to `test_interp_review_fixes.py`:
  ```python
  def test_zero_confidence_signals_yield_zero_influence(self):
      sigs = [sig(Facet.ATTENTION, 1.0, confidence=0.0), sig(Facet.ATTENTION, 1.0, confidence=0.0)]
      d = interpret(policy(), sigs, KEY)
      self.assertEqual(d.compute_budget, policy().min_budget)  # 0 influence -> min budget
  ```

---

**Finding 3: Missing Test for Empty Provenance Token (Audit Fence)**

- **ID**: INTERP-003
- **TITLE**: Empty string in provenance is rejected but not tested
- **SEVERITY**: LOW
- **LOCATION**: `salienceos/interpreter/signal.py`, `_ref_token()` and `tests/test_bus.py`
- **CONCRETE TRIGGER**: 
  ```python
  s = SalienceSignal("s", "req-1", "attention", 0.5, 1.0, ("",))  # empty ref
  valid_signal(s)  # Returns False due to 0 < len(x) check
  ```
- **WHY IT MATTERS**: The audit fence relies on non-empty ref-shaped tokens. While the code correctly rejects empty strings (preventing null provenance entries), this structural check lacks a regression test.
- **SUGGESTED FIX**: Add to `test_interp_review_fixes.py`:
  ```python
  def test_empty_provenance_ref_is_invalid(self):
      self.assertFalse(valid_signal(sig(Facet.ATTENTION, 0.5, prov=("",))))
  ```

---

**STEELMAN**

The interpreter is architecturally sound: it enforces P-01 by construction with no code path from signals to `allowed_capabilities`, and it gates `adaptation_eligibility` strictly behind the policy boolean. It is genuinely fail-closed for all validated inputs, defaulting to minimal budgets, maximal verification, and empty capabilities when trust is absent, and it structurally prevents prompts/bodies from entering the durable bus record via bounded token validation. The test suite demonstrates strong mutation discipline, particularly with the added `AdaptationVerificationGate` tests that pin the previously unexercised verification-depth clause.

---

**VERDICT**

**SERIOUS_FLAWS**

The implementation correctly realizes the P-01 invariant and the thin bus contract, but the unhandled exception during signal materialization (Finding 1) is a material fail-closed break: a misfiring subsystem can crash the central choke point rather than being absorbed into a cautious hard-deny directive. Once wrapped to catch materialization failures, the implementation would merit **SOUND**.
