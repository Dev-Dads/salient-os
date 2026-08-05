# Red-team (interpreter): z-ai/glm-4.6

_finish=stop seconds=231.3 usage={'prompt_tokens': 15293, 'completion_tokens': 4555, 'total_tokens': 19848, 'cost': 0.01454724, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.01454724, 'upstream_inference_prompt_cost': 0.00657599, 'upstream_inference_completions_cost': 0.00797125}, 'completion_tokens_details': {'reasoning_tokens': 2837, 'image_tokens': 0, 'audio_tokens': 0}}_

### Red-Team Findings

#### ID: P-01-1  
**TITLE:** Policy `adaptation_min_verification` Can Exceed `max_verification`  
**SEVERITY:** HIGH  
**LOCATION:** `salienceos/interpreter/policy.py` -> `verify_policy`  
**CONCRETE TRIGGER:**  
```python
policy = issue_policy(
    "pol-1", "req-1", ("fs.read:project",), 10, 1000,
    min_verification=0, max_verification=1,  # Ceiling is 1
    max_retention="semantic", allow_adaptation=True,
    adaptation_min_verification=2,  # Exceeds max_verification (1)
    adaptation_max_risk=0.4, allow_immediate_reconfigure=False,
    KEY
)
assert verify_policy(policy, KEY)  # Passes verification
```  
**WHY IT MATTERS:**  
Violates P-01 indirectly by creating an incoherent policy where adaptation eligibility is impossible (since `v_depth` ≤ `max_verification` < `adaptation_min_verification`). While fail-closed (adaptation blocked), it allows a policy to silently disable adaptation without explicit `allow_adaptation=False`, which could mislead operators. The interpreter should reject incoherent policies to avoid ambiguous directives.  
**SUGGESTED FIX:**  
Add to `verify_policy`:  
```python
and policy.min_verification <= policy.adaptation_min_verification <= policy.max_verification
```

---

#### ID: FC-2  
**TITLE:** `adaptation_max_risk` Not Checked for NaN/Infinity  
**SEVERITY:** HIGH  
**LOCATION:** `salienceos/interpreter/policy.py` -> `verify_policy`  
**CONCRETE TRIGGER:**  
```python
policy = issue_policy(
    "pol-1", "req-1", ("fs.read:project",), 10, 1000,
    0, 3, "semantic", True, 2, float('nan'), False, KEY
)
assert verify_policy(policy, KEY)  # Passes verification
```  
**WHY IT MATTERS:**  
`_is_num` accepts NaN/infinity, but `agg.get(Facet.RISK, 1.0) <= policy.adaptation_max_risk` in `interpret.py` always returns `False` for NaN, causing adaptation to be blocked even when risk is low. This is fail-closed but violates the principle that errors should yield explicit hard-deny directives, not silent logic quirks.  
**SUGGESTED FIX:**  
Replace `_is_num` with:  
```python
def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not (x != x) and x != float('inf') and x != float('-inf')
```

---

#### ID: P-01-3  
**TITLE:** Signal Facet Strings Can Override Policy Constants  
**SEVERITY:** MEDIUM  
**LOCATION:** `salienceos/interpreter/interpreter.py` -> `_aggregate`  
**CONCRETE TRIGGER:**  
```python
# Signal with facet matching a policy constant name
policy = issue_policy("pol-1", "req-1", (), 10, 1000, 0, 3, "semantic", False, 2, 0.4, False, KEY)
signal = SalienceSignal("attacker", "req-1", "allow_adaptation", 1.0, 1.0, ())
d = interpret(policy, [signal], KEY)
assert d.adaptation_eligibility == AdaptationEligibility.NONE  # Blocked by policy.allow_adaptation=False
```  
**WHY IT MATTERS:**  
While P-01 holds (capabilities/adaptation still gated by policy), allowing arbitrary facet strings to influence knobs (e.g., `agg.get("allow_adaptation", 0.0)`) creates confusion. A future refactor could mistakenly use such a facet to bypass a gate. The interpreter should ignore unknown facets to enforce the "known facets move knobs" principle.  
**SUGGESTED FIX:**  
In `_aggregate`, filter by known facets:  
```python
KNOWN_FACETS = {Facet.ATTENTION, Facet.VERIFICATION, Facet.RISK, Facet.MEMORY, Facet.ROUTING, Facet.ADAPTATION}
for s in signals:
    if s.facet in KNOWN_FACETS:
        by_facet.setdefault(s.facet, []).append(s)
```

---

#### ID: FC-4  
**TITLE:** `SalienceBus.emit` Accepts Arbitrary Directives Without Validation  
**SEVERITY:** MEDIUM  
**LOCATION:** `salienceos/interpreter/bus.py` -> `emit`  
**CONCRETE TRIGGER:**  
```python
bus = SalienceBus()
# Manually crafted directive with invalid retention_class
from salienceos.interpreter.directive import Directive, Reconfigure, AdaptationEligibility
invalid_dir = Directive(
    subject="req-1", policy_id="pol-1", compute_budget=10,
    verification_depth=3, retention_class="invalid_class",  # Not in RETENTION_ORDER
    routing_hint="", adaptation_eligibility=AdaptationEligibility.NONE,
    allowed_capabilities=(), reconfigure=Reconfigure.BETWEEN_TURN,
    interpreter_version="0.1.0"
)
bus.emit(invalid_dir)  # Succeeds, corrupting audit trail
```  
**WHY IT MATTERS:**  
The bus is the audit surface; accepting invalid directives without validation breaks the "append-only hash-chained" contract if malformed entries are recorded. While enforcement is in the interpreter, the bus should validate to ensure the audit trail is coherent.  
**SUGGESTED FIX:**  
Add validation in `emit`:  
```python
if directive.retention_class not in RETENTION_ORDER:
    raise ValueError("Invalid retention_class in directive")
```

---

#### ID: AGG-5  
**TITLE:** Confidence Weighting in `_aggregate` Can Divide by Zero  
**SEVERITY:** LOW  
**LOCATION:** `salienceos/interpreter/interpreter.py` -> `_aggregate`  
**CONCRETE TRIGGER:**  
```python
signals = [SalienceSignal("x", "req-1", Facet.ATTENTION, 0.5, 0.0, ())]  # confidence=0.0
agg = _aggregate(signals)
assert agg[Facet.ATTENTION] == 0.0  # Due to weight=0.0
```  
**WHY IT MATTERS:**  
The code handles `weight == 0` by defaulting to 0.0, but the division `sum / weight` is guarded. If `weight` were zero due to floating-point underflow (e.g., many tiny confidences), it could raise `ZeroDivisionError`. This is unlikely but violates the "pure and fail-closed" mandate.  
**SUGGESTED FIX:**  
Explicitly check:  
```python
if weight == 0.0:
    agg[facet] = 0.0
else:
    agg[facet] = sum(s.influence * s.confidence for s in group) / weight
```

---

#### ID: TEST-6  
**TITLE:** Test `test_maxed_out_signals_never_add_a_capability` Uses Unknown Facets  
**SEVERITY:** LOW  
**LOCATION:** `tests/test_no_laundering.py` -> `CapabilitiesComeOnlyFromPolicy.test_maxed_out_signals_never_add_a_capability`  
**CONCRETE TRIGGER:**  
The test passes facets like `"shell.exec:root"` which are not in `Facet`. If `_aggregate` were changed to ignore unknown facets (per P-01-3 fix), this test would still pass, but it wouldn’t verify that unknown facets are ignored.  
**WHY IT MATTERS:**  
Test honesty: the fixture cannot reach the wrong answer if unknown facets are ignored, but it doesn’t assert they are. A mutant that removes unknown-facet filtering would stay green.  
**SUGGESTED FIX:**  
Add an assertion:  
```python
# After calling interpret
self.assertEqual(d.reasons, ("dropped_invalid_signals=3",))  # 3 unknown facets dropped
```

---

### STEELMAN
The implementation rigorously enforces P-01: capabilities are always copied verbatim from the signed policy, and adaptation eligibility requires explicit policy switches plus verification/risk checks. The fail-closed defaults are sound—invalid policies or signals yield hard-deny directives with empty capabilities and maximal verification. The audit trail is structurally body-free via token length limits, and the hash-chain verification is robust.

### VERDICT
**MINOR_ISSUES**. The interpreter correctly isolates authority to the signed policy and defaults to cautious behavior on errors. The findings are high-severity only in edge cases (NaN in risk, incoherent adaptation thresholds) that do not break P-01 but could cause silent misconfigurations. With the suggested fixes, the implementation would be sound.
