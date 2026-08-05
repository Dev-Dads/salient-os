# Red-team (interpreter): x-ai/grok-4.5

_finish=stop seconds=186.6 usage={'prompt_tokens': 16598, 'completion_tokens': 10884, 'total_tokens': 27482, 'cost': 0.0982824, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0982824, 'upstream_inference_prompt_cost': 0.0329784, 'upstream_inference_completions_cost': 0.065304}, 'completion_tokens_details': {'reasoning_tokens': 8862, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / ZERO-WEIGHT RISK TREATED AS RISK=0 / HIGH
**LOCATION:** `salienceos/interpreter/interpreter.py` — `_aggregate`, then `interpret` (`risk = agg.get(Facet.RISK, 1.0)` and the adaptation gate)

**CONCRETE TRIGGER:**
```python
KEY = b"policy-test-key"
pol = issue_policy("pol-1", "req-1", ("fs.read:project",),
                   10, 1000, 2, 3, "semantic",
                   True, 2, 0.4, False, KEY)  # allow_adaptation=True, min_v=2
sigs = [
    SalienceSignal("evo", "req-1", Facet.ADAPTATION, 1.0, 1.0, ()),
    SalienceSignal("r",   "req-1", Facet.RISK,       1.0, 0.0, ()),  # conf=0
]
d = interpret(pol, sigs, KEY)
# d.verification_depth == 2  (floor), not 3 (ceiling)
# d.adaptation_eligibility is CANDIDATE
```
Without the zero-confidence RISK row, absent RISK defaults to `1.0` → verification ceiling and adaptation blocked (`1.0 <= 0.4` is false).

**WHY IT MATTERS:** `_aggregate` does `agg[facet] = 0.0` when `sum(confidence) == 0`, and **inserts the key**. Downstream `agg.get(Facet.RISK, 1.0)` therefore returns `0.0` (least caution) instead of the documented absent default `1.0` (maximum caution). That is a fail-closed inversion on the one facet where **low is permissive**: less verification, and the adaptation risk gate opens. A buggy/misfiring publisher emitting unusable RISK (conf=0) yields a **more permissive** directive than silence. P-01’s “salience alone” capability path is intact (`allow_adaptation` still required), but the mandate “any error or edge case → MORE cautious directive” is broken.

**SUGGESTED FIX (minimal):** If `weight == 0`, omit the facet from `agg` (treat as absent). Optionally also reject `confidence == 0` in `valid_signal` if zero-confidence publishes are never meaningful.

---

### F2 / ITERATOR ERRORS CRASH `interpret` / MEDIUM
**LOCATION:** `salienceos/interpreter/interpreter.py` — `interpret` (`signals = tuple(signals)`)

**CONCRETE TRIGGER:**
```python
def bad():
    yield SalienceSignal("m", "req-1", Facet.ATTENTION, 0.5, 1.0, ())
    raise RuntimeError("publisher bug")

interpret(policy(), bad(), KEY)  # propagates RuntimeError — no Directive
```
Only `TypeError` from a non-iterable is caught; any mid-stream publisher/bus error escapes.

**WHY IT MATTERS:** Mandate is pure fail-closed choke point: errors must become a cautious **directive**, not a crash. Orchestrators that assume `interpret` always returns a `Directive` lose the seam (Finding D).

**SUGGESTED FIX:** Broaden the guard:
```python
try:
    signals = tuple(signals)
except Exception:
    signals = ()
    reasons.append("signals_unreadable")
```
(or catch around the full body and return `_hard_deny` / fail-closed defaults).

---

### F3 / HASH CHAIN ACCEPTS A FULL REWRITE / MEDIUM
**LOCATION:** `salienceos/interpreter/bus.py` — `verify_chain`, `_append`

**CONCRETE TRIGGER:**
```python
bus = SalienceBus()
bus.publish(sig(...))  # real history
# Adversary with memory or file access:
new = {"kind": "directive", "payload": {...forged caps/eligibility...}, "prev": ""}
h = digest(new)
bus._entries = [{**new, "hash": h}]
bus._head = h
assert bus.verify_chain()  # True — forged history verifies
```
Same for rewriting the on-disk JSONL and recomputing `prev`/`hash` end-to-end. Tests only cover partial edits (payload tweak without rehash, swap without re-linking).

**WHY IT MATTERS:** Finding G sells “append-only” as a **checkable** property. A bare digest chain without a signed/anchored head only detects sloppy tampering; it does not stop a consistent alternate audit trail (fake directives, scrubbed signals). Durable record integrity is overstated.

**SUGGESTED FIX:** Sign each entry (or the head) under a bus/audit key; `verify_chain` checks signatures + links. At minimum, document that `verify_chain` is corruption-detection only, not authentic history.

---

### F4 / `emit` DOES NOT BIND DIRECTIVE TO `interpret` / LOW
**LOCATION:** `salienceos/interpreter/bus.py` — `emit`

**CONCRETE TRIGGER:** Construct any `Directive` (e.g. `allowed_capabilities=("host_admin",)`, `adaptation_eligibility=CANDIDATE`) and `bus.emit(d)`. It is hashed into the chain with no link to a prior `interpret()` result or policy signature.

**WHY IT MATTERS:** Enforcement remains in `interpret` (good), but the audit surface can record authority that never passed the choke point—undermines Finding G if consumers trust the bus as proof of authorization.

**SUGGESTED FIX:** Emit only from a helper that takes `(policy, signals, key)`, calls `interpret`, and stores policy signature / input signal hashes with the directive payload.

---

### F5 / BANKER’S ROUNDING AT HALF-ULP BOUNDARIES / LOW
**LOCATION:** `salienceos/interpreter/interpreter.py` — `_scale` (`int(round(...))`)

**CONCRETE TRIGGER:** `min_verification=0`, `max_verification=1`, `Facet.RISK` influence `0.5` → `round(0.5)==0` (Python half-to-even) → depth `0` not `1`. Same for budget spans of 1.

**WHY IT MATTERS:** Slightly less verification/compute than a “round half up” reading of the spec; not a P-01 break, but a clamping edge.

**SUGGESTED FIX:** Use an explicit cautious bias for verification (e.g. `ceil` toward more verification) and document budget rounding.

---

### F6 / TEST GAP ON ZERO-WEIGHT / ABSENT-FACET SEMANTICS / LOW (test honesty)
**LOCATION:** `tests/test_interpret.py`, `tests/test_interp_review_fixes.py` — no case with `confidence=0` on `Facet.RISK`

**CONCRETE TRIGGER:** Mutant that keeps `_aggregate`’s `else 0.0` branch stays green under the current suite; F1 would not be caught.

**WHY IT MATTERS:** Review’s own mutation discipline (“fixture that cannot reach the wrong answer”) is missing on the only facet whose default is non-zero.

**SUGGESTED FIX:** Add tests: (1) RISK conf=0 ≡ absent RISK for verification and adaptation; (2) full chain rewrite (expect fail once F3 is fixed).

---

**Not found (honest negative results):**
- No path for any facet/influence (including capability-shaped facet strings) to change `allowed_capabilities` vs `policy.granted_capabilities` when the policy verifies.
- No path to `CANDIDATE` with `allow_adaptation=False`.
- `signed_payload()` covers every authority-bearing field; tampered caps hard-deny in tests.
- NaN/inf influences fail `valid_signal`; non-bool policy switches rejected; oversized tokens rejected at bus/interpret.
- Structural audit fence on signals (bounded ref tokens) holds; prompts/CoT cannot validate as `SalienceSignal` fields.
- Capability / adaptation P-01 leak-locks in `test_no_laundering.py` are real and load-bearing.

Malicious policy signing key remains **OUT-OF-SCOPE** (unbounded policy subject strings in the durable directive record would require it).

---

## STEELMAN

The implementation really does keep authority on one side of the fence: `allowed_capabilities` is a pure policy pass-through, adaptation is conjunctively gated on the signed switch, and unknown facets cannot move knobs. Fail-closed hard-deny (empty caps, max verification, blanked untrusted ids), subject filtering, and structural token bounds on the bus match the design review’s choke-point and Finding G intent more carefully than most “policy objects” in agent stacks. The regression pack around adaptation’s verification ceiling and non-bool switches shows genuine mutation discipline on the paths that were fixed once.

## VERDICT

**SERIOUS_FLAWS** — No CRITICAL P-01 capability laundering, but F1 is a concrete fail-closed inversion (zero-confidence RISK → less verification and possible `CANDIDATE`) that a buggy salience publisher can trigger without the signing key.
