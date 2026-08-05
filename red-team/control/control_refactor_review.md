# Control-seam refactor — focused validation review

Scope: the just-refactored security seam reconciling the interpreter `Directive`
with the verifier `Verdict`. Files read: `salienceos/control/govern.py`,
`salienceos/control/outcome.py`, `salienceos/verifier/pipeline.py`,
`salienceos/verifier/verdict.py`, `salienceos/verifier/envelope.py`,
`salienceos/verifier/composer.py`, plus supporting `contract.py`, `evidence.py`,
`interpreter/directive.py`, `interpreter/policy.py`, and both control test files.

Method: static trace of every clearance/adaptation path + 4 empirical mutation
experiments (each mutation applied, the target test run RED, then reverted) +
2 edge-case probes. Full suite: **129 passed, 1 skipped** before and after.

Verdict: **the refactor holds.** No false-clear or new desync path found. One
LOW robustness gap (asymmetric directive type-check) and one LOW hardening
suggestion (invert the RECEIPT guard to be future-proof). Everything else is
correct, and the new tests are honest killers.

---

## 1. Verifier core change is truly ADDITIVE — CONFIRMED

- **`composer.py` is untouched.** Every `Verdict(...)` it builds omits the two new
  fields, so they take defaults `envelope_id=""`, `effective_stakes=None`. The
  pure predicate's status/reasons logic is unchanged.
- **Stamping is pipeline-only and cannot change status/reasons.**
  `pipeline.py:96` does `replace(verdict, envelope_id=..., effective_stakes=...)`
  on the compose path — `dataclasses.replace` copies `status`, `reasons`,
  `details`, `composer_version` verbatim and only overrides the two provenance
  fields. The mismatch path (`pipeline.py:65-75`) constructs UNVERIFIED +
  `RECEIPT_ENVELOPE_MISMATCH` directly, also stamping both fields. Neither can
  promote a status.
- **New fields are default-valued**, so all prior `Verdict(...)` call sites keep
  working (e.g. `tests/test_gate_and_verdict.py:19` builds a Verdict with no
  provenance and still passes).
- **No prior test compares Verdicts by equality** (grepped: no `assertEqual(...Verdict)`,
  no `verdict ==`). The auto-generated `__eq__` now includes the two fields, but
  nothing relies on cross-comparing a stamped vs unstamped verdict. `__bool__`
  (raises) and `require_attested()` are unchanged.
- **M1 / fail-closed / sealed-gate all live in untouched code** (composer,
  receipt, evidence fences). Stamping cannot reach them.

GOOD: the (status, effective_stakes) pair a downstream gate now reads is
consistent *by construction* — both come out of the same `verify()` call, which
is precisely what closes the original desync class.

## 2. No NEW desync / false-clear in the 2-arg `decide` — CONFIRMED

- **One-source VERIFIED can never clear FULL.** A VERIFIED verdict only carries
  `effective_stakes ∈ {HIGH, CRITICAL}` when the composer actually saw two
  distinct world failure modes (`composer._required_sources`), so "one-source"
  means `effective_stakes ∈ {LOW, NORMAL}` → `achieved_level` returns INDEPENDENT,
  and `_stakes_floor(NORMAL)=INDEPENDENT`. `required = max(FULL, INDEPENDENT)=FULL`
  > INDEPENDENT = achieved ⇒ not cleared. (Test + mutation 2 confirm.)
- **Verdict for action B can't clear directive for A.**
  `bound = bool(directive.subject) and directive.subject == verdict.envelope_id`
  (`govern.py:145`). Different ids ⇒ `bound=False` ⇒ `cleared=False`. The
  `bool(subject)` guard additionally stops an empty-string `""=="" ` from binding
  an unstamped verdict. Mutation 1 (`bound=True`) turns both binding tests RED.
- **No residual free-param / clamp bug.** `decide` reads `effective_stakes` and
  the action id straight off the verdict; there are no `envelope_id` /
  `effective_stakes` parameters left. The only clamp is the range guard
  `required = NONE if required<NONE else FULL if required>FULL else required`
  (`govern.py:142`), which can only *tighten* (clamps a stray large depth down to
  FULL, a negative up to NONE) and never drops `required` below the stakes floor
  (floor ∈ {1,2,3}, and it is one operand of the `max`).
- **`cleared`** = `bound ∧ status≠FAILED ∧ achieved≥required`; `achieved` is
  nonzero only for a genuine VERIFIED (2/3) or a clean attestation (1). No
  spurious achievement source.
- **`adaptation_allowed`** = `cleared ∧ CANDIDATE ∧ status is VERIFIED` — an
  attested-only RECEIPT clear can never learn (test_attested_but_not_verified).

## 3. Fail-closed on null/malformed — MOSTLY; one LOW asymmetry

CONFIRMED closed:
- `verdict is None` or any non-Verdict → strict `type(verdict) is not Verdict`
  (`govern.py:126`) → fail-closed `GovernedOutcome(cleared=False,...)` with the
  `_NULL_VERDICT` placeholder.
- Verdict present but **unstamped** (`envelope_id=""`, `effective_stakes=None`) →
  double fail-closed: `bound=False` (empty subject) *and* `_stakes_floor(None)=FULL`
  vs `achieved=INDEPENDENT`. Probe: `cleared=False, required=3, achieved=2`.
- `directive is None` → fail-closed.

### FINDING F1 — LOW — asymmetric directive type check (`govern.py:126`, `decide`)
- **Trigger:** `decide("x", verdict)` or `decide({...}, verdict)` — a non-None
  object that is not a `Directive`. Probe result:
  `RAISED AttributeError - 'str'/'dict' object has no attribute 'verification_depth'`.
- **Why it matters:** the verdict side is strictly type-fenced
  (`type(verdict) is not Verdict`) but the directive side is only `is None`
  checked, so a malformed directive raises instead of returning the documented
  fail-closed outcome ("Fail-closed: null inputs ... deny clearance"). It is **not
  a false-clear** (the raise prevents any `cleared=True`), but it is an
  inconsistent contract and can crash a caller that expects a denial object
  rather than an exception. "Missing fields" was explicitly in scope for this pass.
- **Minimal fix:** mirror the verdict check —
  `if type(directive) is not Directive or type(verdict) is not Verdict:` (import
  `Directive` from `salienceos.interpreter`), returning the same fail-closed
  `GovernedOutcome`.

## 4. `achieved_level` guard correctness — CORRECT & COMPLETE

- **`_HARD_FAILURE_REASONS` is complete.** The `Reason` enum has 10 members;
  the frozenset lists all 9 except `INTEGRITY_ATTESTED` itself. So RECEIPT is
  reachable only for `status==UNVERIFIED ∧ INTEGRITY_ATTESTED present ∧ no other
  reason` — a clean attestation. No hard-failure reason can be laundered into a
  RECEIPT clear. Mutation 3 (drop the `and not any(...)` clause) turns
  `test_attested_with_hard_failure_reason_is_none` RED.
- **Nothing reaches RECEIPT/INDEPENDENT/FULL spuriously.** INDEPENDENT/FULL only
  via a real VERIFIED (composer-enforced M1); FULL split keyed on HIGH/CRITICAL.
- **VERIFIED branch is still correct with stakes-from-verdict.** `effective_stakes`
  is stamped by the same `verify()` that set `status=VERIFIED`, so a stamped
  `(VERIFIED, HIGH)` genuinely passed the two-source bar — no desync between the
  status and the rigor it is scored against.

### FINDING F2 — LOW (hardening) — RECEIPT guard is a denylist, not an allowlist (`govern.py:84-104`, `achieved_level`)
- **Trigger (latent):** if a future `Reason` member is added and someone forgets
  to add it to `_HARD_FAILURE_REASONS`, and it ever co-occurs with
  `INTEGRITY_ATTESTED`, `achieved_level` would return RECEIPT for it.
- **Why it matters:** correctness currently depends on hand-maintaining an
  exhaustive denylist of every non-attested reason. It is complete *today*, but
  the invariant "attested is the *only* thing present" is expressed indirectly.
- **Minimal fix (optional):** express it as an allowlist —
  `... and set(verdict.reasons) == {Reason.INTEGRITY_ATTESTED}`. This is
  future-proof against new reasons and lets `_HARD_FAILURE_REASONS` retire. (If
  kept as-is, add a test asserting every `Reason` except `INTEGRITY_ATTESTED` is
  in the frozenset, so the enum and the set can't drift.)

## 5. Test honesty — CONFIRMED (empirical mutation)

Each invariant reverted, target test observed RED, then reverted:

| Mutation | Test(s) that went RED |
|---|---|
| `bound = True` | `VerdictBindingCannotDesync.test_verdict_for_another_action_does_not_clear`, `DecideGate.test_subject_mismatch_denies` |
| `required = directive.verification_depth` (drop stakes floor) | `EnvelopeStakesFloor.test_high_stakes_forces_full_even_with_low_depth`, `DecideGate.test_envelope_low_floor_requires_at_least_receipt` |
| drop `and not any(... _HARD_FAILURE_REASONS ...)` | `AchievedLevel.test_attested_with_hard_failure_reason_is_none` |
| `escalate_to=None` in `govern` | `test_escalation_raises_a_normal_envelope` (the F1 killer) |

- **null-input tests** catch guard removal via a raised AttributeError (unittest
  ERROR = failing) — adequate.
- **Honest-but-worth-noting:** under the escalation mutation,
  `test_high_salience_needs_two_sources` still PASSES — by design, because that
  test signs the envelope HIGH via `stakes_for`, so escalation isn't the only
  lift. The dedicated killer `test_escalation_raises_a_normal_envelope` uses a
  NORMAL envelope so escalation is load-bearing, and it caught the mutation on
  its `effective_stakes is Stakes.HIGH` / `status is not VERIFIED` assertions
  (its `assertFalse(cleared)` alone would *not* have distinguished it, since
  depth=FULL already forces required=FULL — but the other two assertions do).
- No fixture found that cannot reach the wrong answer. Positive-path tests
  (`test_high_stakes_cleared_only_by_two_source_verified`,
  `test_high_salience_needs_two_sources`) are correctly paired with negatives.

## Genuinely good things (calibration)

- **Self-describing verdict via `replace()` on BOTH pipeline paths** — closes the
  entire desync class at the source; the gate no longer trusts free params.
- **Strict `type(verdict) is not Verdict`** (not `isinstance`) — a subclass can't
  smuggle past the fence.
- **`bool(directive.subject)` bind guard** — empty-string ids don't bind, so an
  unstamped verdict cannot ride a directive with an empty subject.
- **Range guard can only tighten** `required`; never loosens below the stakes floor.
- **`max_stakes` fail-safe on non-Stakes input** (rank −1: ignored, never lowers a
  valid stakes; two malformed → None) — tested directly.
- **Documented non-monotonicity of `achieved_level`** is in the safe direction
  (partial corroboration can only make clearance harder).
- **Defense-in-depth `_HARD_FAILURE_REASONS`** even though the composer already
  guarantees attested never co-occurs with a hard-failure reason.
- The mutation-tested invariants are **real killers**, verified empirically here.

## Summary of findings

| ID | Severity | Location | Issue |
|----|----------|----------|-------|
| F1 | LOW | `govern.py:126` `decide` | Non-None, non-Directive directive raises AttributeError instead of a fail-closed outcome (asymmetric with the strict verdict check). Not a false-clear. |
| F2 | LOW (hardening) | `govern.py:84-104` `achieved_level` / `_HARD_FAILURE_REASONS` | RECEIPT guard is a hand-maintained denylist; complete today but drift-prone. Prefer allowlist `set(reasons)=={INTEGRITY_ATTESTED}` or add an enum-coverage test. |

No MEDIUM/HIGH findings. The two invariants the prior red-team targeted
(desyncable free params; clamp false-clear) are both closed.
