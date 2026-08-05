# SalienceOS Control Seam — Correctness Review

Scope: `salienceos/control/{outcome,govern,__init__}.py` composed with the verifier
change (`envelope.py` `max_stakes`/`STAKES_ORDER`, `pipeline.py` `escalate_to`), read
against `composer.py`, `verdict.py`, `interpreter/policy.py`, plus the two consumers
`interpreter/{directive,interpreter}.py` and `verifier/contract.py`.

## Verdict

**All five invariants HOLD.** I found no input that produces a hard break of any of
the five. There are three defense-in-depth / boundary observations worth acting on;
none is a violation of the invariants as stated, but #1 is a genuine and non-obvious
risk. Ranked below.

Cross-check: `tests/test_control.py` + `tests/test_control_e2e.py` = 23 pass.

---

## Invariant-by-invariant

### 1. Salience can only ESCALATE verification — CLEAN
The only combinator is `max_stakes(envelope.stakes, escalate_to)`, used identically in
`Verifier.verify` (pipeline.py:88) and `govern` (govern.py:110).

`max_stakes(a, b)` returns `a` when `b is None`, `b` when `a is None`, else the
STAKES_ORDER-stronger. So for any `b` (escalate_to) and any valid Stakes `a`
(envelope.stakes), the result is `>= a`. `escalation_for` only ever yields `Stakes.HIGH`
or `None` (govern.py:33), both upward. There is **no** value of `escalate_to` (even a
malicious `Stakes.LOW`) that lowers the effective stakes below the signed floor — a
lower `b` is discarded by the `index(a) >= index(b)` branch. `verify()` and `govern()`
compute the same `effective_stakes` from the same frozen `envelope.stakes`, so no drift.
Confirmed by `test_salience_cannot_lower_envelope_floor` (depth=NONE, envelope=HIGH →
effective stays HIGH).

### 2. Fail-closed clearance — CLEAN
`cleared = bound and verdict.status is not Status.FAILED and achieved >= required`
(govern.py:80). All three deny-conditions are ANDed:
- subject/envelope mismatch → `bound=False` → not cleared (govern.py:71).
- FAILED verdict → not cleared; the `is not Status.FAILED` guard is load-bearing and
  present, so even `required=NONE` cannot clear a FAILED (achieved_level(FAILED)=NONE=0,
  0>=0 would otherwise pass — the guard is what blocks it). Confirmed by
  `test_none_requirement_clears_unless_failed` / `test_failed_never_clears`.
- `achieved < required` → not cleared.

No input yields `cleared=True` for a mismatch, a FAILED, or `achieved < required`.
(See Observation #1 for the separate, in-spec fact that `required` can be legitimately
low.)

### 3. Adaptation sealed gate — CLEAN
`adaptation_allowed = cleared and elig is CANDIDATE and verdict.status is Status.VERIFIED`
(govern.py:84-88). The explicit `is Status.VERIFIED` term is present and load-bearing:
- INTEGRITY_ATTESTED has `status == UNVERIFIED` → blocked, even though it can `clear` at
  `required<=RECEIPT`. Confirmed by `test_attested_but_not_verified_blocks_adaptation`.
- `cleared` alone is insufficient; a spoofed `elig=CANDIDATE` is insufficient.
No path reaches `adaptation_allowed=True` without a real `Status.VERIFIED`.

### 4. Verifier M1 unweakened by `escalate_to` — CLEAN
`escalate_to` appears in exactly one place in `verify()`:
`effective_stakes = max_stakes(envelope.stakes, escalate_to)` → `compose(...)`
(pipeline.py:88-89). Every other path (receipt/envelope binding check, contract build,
authenticity check, claim extraction, store appends) is untouched. When
`escalate_to=None`, `max_stakes(envelope.stakes, None) == envelope.stakes` exactly, so
compose is called with the identical stakes it received before the change — behavior is
byte-for-byte unchanged. When non-None, stakes can only rise, which only raises
`_required_sources` (composer.py:141-142, 174) from 1 to 2. M1 (VERIFIED requires an
executor-independent WORLD fact of a distinct failure mode, composer.py `_agreement_gap`)
is unaffected and can only be made stricter. Purely additive/upward.

### 5. achieved_level mapping — CLEAN
`achieved_level` (govern.py:44-56):
- VERIFIED & effective_stakes ∈ {HIGH, CRITICAL} → FULL(3). ✓
- VERIFIED & else (LOW/NORMAL) → INDEPENDENT(2). ✓ (VERIFIED with an invalid/None stakes
  is unreachable — compose returns UNVERIFIED on `not isinstance(stakes, Stakes)`.)
- UNVERIFIED & INTEGRITY_ATTESTED ∈ reasons → RECEIPT(1). ✓ (FAILED never carries
  INTEGRITY_ATTESTED — compose emits it only on the UNVERIFIED unmet path.)
- everything else, incl. FAILED and bare UNVERIFIED → NONE(0). ✓ "FAILED is not a level"
  is honored: it maps to 0 AND is independently blocked by the FAILED guard in `decide`.
Matches the spec table exactly. Confirmed by the `AchievedLevel` test class.

---

## Observations (ranked; not invariant breaks)

### OBS-1 (Medium) — Clearance floor is decoupled from `envelope.stakes`
`decide()` gates clearance on `achieved >= required`, where `required =
directive.verification_depth`. Nothing derives a clearance floor from `envelope.stakes`.
The envelope's stakes governs how hard the verifier *runs* (and the achieved mapping),
but imposes **no** floor on the *clearance bar*. `stakes_for` protects the envelope side
at authorization; there is no analogous protection on the clearance side.

Consequence: a high-stakes action the verifier ran at HIGH and could **not** corroborate
still clears if the directive's depth is low.

Concrete triggering input:
```
directive           = Directive(subject="act-1", verification_depth=0 (NONE),
                                 adaptation_eligibility=NONE, ...)
envelope_stakes     = Stakes.CRITICAL
effective_stakes    = Stakes.HIGH        # verifier genuinely ran two-source
verdict             = Verdict(UNVERIFIED, reasons=(Reason.INSUFFICIENT_CHANNELS,))
                      # one world source at HIGH -> could not corroborate
decide(directive, "act-1", Stakes.CRITICAL, verdict, Stakes.HIGH)
   -> achieved = NONE(0);  required = NONE(0);  achieved >= required
   -> cleared = True
```
This is exactly the shape built (but not asserted on) by
`test_salience_cannot_lower_envelope_floor` (depth=NONE + envelope HIGH); that test
checks the stakes floor and non-VERIFIED status but does **not** assert `cleared=False`.

Why it is not an invariant-2 break: invariant 2 defines "verified-enough" as
`achieved >= required`; with `required=NONE` the action is trivially enough, so the code
is faithful to the stated contract. The risk is that the *two signed quantities*
(`envelope.stakes` from the verifier's policy domain and `directive.verification_depth`
from the interpreter's policy domain) can disagree, and the seam trusts the issuer to
keep them consistent. `adaptation_allowed` stays False here (needs VERIFIED), so learning
is still safe — the exposure is purely the `cleared` surface.

Recommendation: add a defense-in-depth clearance floor from `effective_stakes`
(e.g. an envelope signed HIGH/CRITICAL requires `achieved >= INDEPENDENT`, i.e. an actual
VERIFIED, before `cleared`), or assert at issuance that
`directive.verification_depth >= depth implied by envelope.stakes`.

### OBS-2 (Low) — `decide()` trusts caller-supplied `effective_stakes`
`achieved_level` reads the `effective_stakes` *argument*; `decide()` cannot cross-check
it against the verdict. `govern()` supplies it correctly
(`max_stakes(envelope.stakes, escalate)`), so the seam is internally consistent. But
`decide` is a public export (control/__init__.py). A direct caller passing
`effective_stakes=Stakes.HIGH` alongside a verdict actually composed at LOW would get
`achieved=FULL` for a one-source VERIFIED — a mislabel. Not reachable via `govern`.
Recommendation: either fold the stakes computation into `decide` (derive it from the
envelope + escalation internally) so it cannot be misreported, or keep `govern` the sole
public entry point.

### OBS-3 (Low/Info) — Empty-string subject binding
Binding is `directive.subject == envelope_id` (govern.py:71). The interpreter's
`_hard_deny` blanks `subject=""`. An envelope issued with `envelope_id=""` would bind to
a hard-deny directive. Harmless in practice (hard-deny sets `verification_depth=FULL` and
`allowed_capabilities=()`, so it demands the strongest verification and grants nothing),
but empty-string equality as a binding is a smell. Recommendation: reject empty
`subject`/`envelope_id` explicitly in `decide`.
