# Red-team (consumers): x-ai/grok-4.5

_finish=stop seconds=300.3 usage={'prompt_tokens': 34818, 'completion_tokens': 14099, 'total_tokens': 48917, 'cost': 0.1540124, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1540124, 'upstream_inference_prompt_cost': 0.0694184, 'upstream_inference_completions_cost': 0.084594}, 'completion_tokens_details': {'reasoning_tokens': 11992, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### 1 / Directive bus entries break the audit fence (payload unbounded + no schema on replay)
**SEVERITY:** HIGH  
**LOCATION:** `salienceos/interpreter/bus.py` — `SalienceBus.emit`, `SalienceBus._replay`, `SalienceBus.directives_for`

**CONCRETE TRIGGER (adversarial file, in scope):**
```python
# hash-correct directive line; extra keys sit INSIDE payload (top-level key-set still exact)
from salienceos.verifier.signing import digest
import json, tempfile, os
from salienceos.interpreter.bus import SalienceBus

payload = {
    "subject": "req-1",
    "policy_id": "p",
    "compute_budget": 0,
    "verification_depth": 3,
    "retention_class": "ephemeral",
    "routing_hint": "",
    "adaptation_eligibility": "none",
    "adaptation_rationale": "policy_disallowed",
    "allowed_capabilities": [],
    "reconfigure": "between_turn",
    "interpreter_version": "x",
    "reasons": [],
    "prompt": "H" * 50_000,          # prompt-sized
    "body": "B" * 50_000,
}
base = {"kind": "directive", "payload": payload, "prev": ""}
line = json.dumps({**base, "hash": digest(base)}, sort_keys=True) + "\n"
path = tempfile.mkdtemp() + "/bus.jsonl"
open(path, "w", encoding="utf-8").write(line)
bus = SalienceBus(path=path)  # opens successfully
assert "prompt" in bus.directives_for("req-1")[0]
assert len(bus.directives_for("req-1")[0]["prompt"]) == 50_000
```

**Also (hand-built `Directive` → `emit`, no signing key):**
```python
Directive(subject="H"*50_000, policy_id="p", compute_budget=0, verification_depth=0,
          retention_class="ephemeral", routing_hint="H"*50_000,
          adaptation_eligibility=AdaptationEligibility.NONE,
          adaptation_rationale=AdaptationRationale.POLICY_DISALLOWED,
          allowed_capabilities=("H"*50_000,), reconfigure=Reconfigure.BETWEEN_TURN,
          interpreter_version="x", reasons=("H"*50_000,))
# bus.emit(d) persists all of it — no MAX_TOKEN_LEN (unlike valid_signal)
```

**WHY IT MATTERS:** Signals are structurally body-free (`valid_signal` + replay re-validation). Directive entries are the other durable kind and get **neither** a payload allowlist on replay **nor** ref-length caps on `emit`. Top-level exact-key fencing (`test_smuggled_key`) does not cover smuggling **inside** `payload`. Prompt-sized content becomes durable and is served by `directives_for` — direct miss on attack surface (4) / Finding G.

**SUGGESTED FIX (minimal):** On `emit` and `_replay` for `kind=="directive"`: allowlist exact payload keys; require ref-shaped bounds on all strings (same `MAX_TOKEN_LEN`); reject unknown keys; keep fail-closed `ValueError` on replay.

---

### 2 / Test honesty: audit-fence tests stay green if directive bodies are accepted
**SEVERITY:** MEDIUM  
**LOCATION:** `tests/test_bus.py` — `ReplayOnOpen.test_smuggled_key`, `test_persisted_invalid_signal_refuses_to_open`; no directive payload counterpart

**CONCRETE TRIGGER:** Sabotage that leaves signal validation intact but accepts arbitrary directive `payload` dicts (current code). Entire `tests/test_bus.py` stays green; Finding G still fails in production via finding 1.

**WHY IT MATTERS:** The suite claims an audit fence and pins signal re-validation + top-level key-set, but never pins “directive payloads cannot carry prompt-sized / unknown fields.” That is exactly the gap above.

**SUGGESTED FIX:** Add replay/emit tests: unknown payload key → refuse; string field `len > MAX_TOKEN_LEN` → refuse; golden payload key set exact.

---

### 3 / Test honesty: “inhibitor only via hand-off” not pinned on the RISK_EXCEEDED path
**SEVERITY:** LOW  
**LOCATION:** `salienceos/consumers/memory.py` — `retain`; `tests/test_consumers.py` (gap)

**CONCRETE TRIGGER:** Sabotage:
```python
inhibitor = handoff is not None or (
    bound and d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED
)
```
Then `retain(risk_reject_outcome(), NOW)` (no hand-off) yields `inhibitor=True`. Existing tests stay green: e2e still pins via `consume`; attribution matrix still raises on *mismatched* hand-offs; `test_inhibitor_is_not_forced_by_mere_refusal` uses non-`RISK_EXCEEDED` refusal.

**WHY IT MATTERS:** Design claims memory pins **only** via explicit `InhibitorHandoff` (channel boundary). That sole-trigger property is not mutation-locked for the incident outcome.

**SUGGESTED FIX:** One line test: `self.assertFalse(retain(risk_reject_outcome(), NOW).inhibitor)`.

---

### 4 / `effective_weight` allows negative `reinforcement_sum` to null a pinned inhibitor
**SEVERITY:** LOW  
**LOCATION:** `salienceos/consumers/memory.py` — `effective_weight`

**CONCRETE TRIGGER:**
```python
dec, ret = consume(risk_reject_outcome(), 100.0)
assert ret.inhibitor
assert effective_weight(ret, 100.0 + 3650.0) == 1.0          # pin vs decay
assert effective_weight(ret, 100.0, reinforcement_sum=-1.0) == 0.0  # pin gone
```

**WHY IT MATTERS:** Inhibitors are exempt from decay by design; a caller-supplied negative reinforcement term is not decay but still drives retrieval weight to zero, undoing the pin in the only weight API.

**SUGGESTED FIX:** Reject non-finite or negative `reinforcement_sum`, or for `inhibitor` return at least `base_weight` (e.g. `max(0, reinforcement_sum)` added).

---

### 5 / `_valid_directive` does not type-check `adaptation_eligibility`
**SEVERITY:** LOW  
**LOCATION:** `salienceos/control/govern.py` — `_valid_directive`

**CONCRETE TRIGGER (hand-built directive → `decide`):**
```python
d = directive()  # from test factory
d = Directive(**{**d.__dict__,
                 "adaptation_eligibility": "candidate",  # str, not enum
                 "adaptation_rationale": AdaptationRationale.NOT_REQUESTED})
o = decide(d, VERIFIED_TWO)  # not denied as null_or_invalid_inputs
# coherence: (ELIGIBLE is False) == (elig is CANDIDATE is False) → passes
```

**WHY IT MATTERS:** Rationale is validated because it “rides through” to consumers; eligibility is half of the same pair and is only compared with `is CANDIDATE` later. No false nominate seen, but the boundary is asymmetric and accepts malformed directives that a strict seam should fail closed.

**SUGGESTED FIX:** Require `isinstance(directive.adaptation_eligibility, AdaptationEligibility)` (and ideally `Reconfigure`) in `_valid_directive`.

---

### Not reported (checked, no concrete in-scope break)
- **Nomination predicate / unverified novelty / no promote field:** holds; forged-`adaptation_allowed` mutation test is honest for `verdict.status` re-checks.
- **Inhibitor manufacture without asserted over-cap risk / misattribution:** priority chain + hand-off attribution matrix look sound; `RISK_UNKNOWN` does not hand off.
- **Capability leak through consumers:** no grant surface; schemas exclude caps/delete/promote fields.
- **Seam withhold:** unbound/invalid → empty subject, no hand-off, ephemeral floor; no crash-as-clear on well-typed deny paths.
- **ADR 0001 exclusions** (consistent rewrite, tail-truncation-across-reopen): not raised.
- **Priority UNDER_VERIFIED over RISK_EXCEEDED:** explicitly designed and test-pinned — not a review miss.

---

## STEELMAN

The consumer split is genuinely load-bearing: nomination is a single recorded bit, inhibitors are attribution-checked records rather than re-derived salience, and Finding C disagreement is exercised end-to-end through `consume()` with a low-risk twin. The seam’s self-describing withhold (`directive=None` / `subject=""`) plus rationale↔eligibility coherence checks close the obvious “act on the wrong action” and “crash the gate” holes. Bus replay is fail-closed for the signal path and for top-level hash/key/prev integrity within ADR 0001.

## VERDICT

**MINOR_ISSUES** — Authorization/disagreement gates look sound under in-scope attacks; the material miss is the **directive half of the bus audit fence** (and tests that would stay green without it), not a broken inhibitor or nomination predicate.
