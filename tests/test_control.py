"""Unit tests for the control seam's pure gate `decide()` and its mappings.

The seam is where salience (directive depth) meets the world (verifier verdict).
These fixtures pin the reconciliation: required vs achieved level, the upward-only
escalation, and the two leak-locks — salience can only raise verification, and
nothing is learned from an unverified action. The verdict is self-describing
(`envelope_id` + `effective_stakes`), so `decide()` takes exactly (directive, verdict).
"""

import unittest

from salienceos.control import (
    FULL,
    INDEPENDENT,
    NONE,
    RECEIPT,
    achieved_level,
    decide,
    escalation_for,
    stakes_for,
)
from salienceos.control.govern import govern
from salienceos.interpreter import (
    AdaptationEligibility,
    AdaptationRationale,
    Directive,
    Reconfigure,
)
from salienceos.verifier import STAKES_ORDER, Reason, Stakes, Status, Verdict, max_stakes


def directive(subject="act-1", depth=INDEPENDENT, elig=AdaptationEligibility.NONE,
              rationale=None):
    # Default rationale coheres with eligibility (CANDIDATE <=> ELIGIBLE); tests
    # that need a specific denial reason pass it explicitly.
    if rationale is None:
        rationale = (AdaptationRationale.ELIGIBLE
                     if elig is AdaptationEligibility.CANDIDATE
                     else AdaptationRationale.NOT_REQUESTED)
    return Directive(
        subject=subject, policy_id="p", compute_budget=100, verification_depth=depth,
        retention_class="working", routing_hint="", adaptation_eligibility=elig,
        adaptation_rationale=rationale,
        allowed_capabilities=(), reconfigure=Reconfigure.BETWEEN_TURN,
        interpreter_version="test", reasons=(),
    )


def verdict(status, reasons=(), envelope_id="act-1", effective_stakes=Stakes.NORMAL):
    return Verdict(status=status, reasons=tuple(reasons), composer_version="test",
                   envelope_id=envelope_id, effective_stakes=effective_stakes)


# Common self-describing verdicts (envelope_id "act-1").
VERIFIED_ONE = verdict(Status.VERIFIED, effective_stakes=Stakes.NORMAL)      # one source
VERIFIED_TWO = verdict(Status.VERIFIED, effective_stakes=Stakes.HIGH)        # two sources
ATTESTED_LOW = verdict(Status.UNVERIFIED, (Reason.INTEGRITY_ATTESTED,), effective_stakes=Stakes.LOW)
UNVERIFIED = verdict(Status.UNVERIFIED, (Reason.INSUFFICIENT_CHANNELS,))
FAILED = verdict(Status.FAILED, (Reason.CONCLUSIVE_CONTRADICTION,))


class EscalationMapping(unittest.TestCase):
    def test_only_full_escalates(self):
        self.assertIs(escalation_for(FULL), Stakes.HIGH)
        self.assertIsNone(escalation_for(INDEPENDENT))
        self.assertIsNone(escalation_for(RECEIPT))
        self.assertIsNone(escalation_for(NONE))

    def test_stakes_for_never_lowers_the_floor(self):
        self.assertIs(stakes_for(directive(depth=FULL), Stakes.NORMAL), Stakes.HIGH)
        self.assertIs(stakes_for(directive(depth=FULL), Stakes.CRITICAL), Stakes.CRITICAL)
        self.assertIs(stakes_for(directive(depth=NONE), Stakes.HIGH), Stakes.HIGH)

    def test_max_stakes_is_upward_only(self):
        self.assertIs(max_stakes(Stakes.HIGH, Stakes.LOW), Stakes.HIGH)
        self.assertIs(max_stakes(Stakes.LOW, Stakes.HIGH), Stakes.HIGH)
        self.assertIs(max_stakes(Stakes.NORMAL, None), Stakes.NORMAL)
        self.assertIs(max_stakes(None, Stakes.CRITICAL), Stakes.CRITICAL)

    def test_max_stakes_ignores_malformed_input(self):
        # A stray string must not raise and must not lower a valid stakes.
        self.assertIs(max_stakes(Stakes.HIGH, "high"), Stakes.HIGH)
        self.assertIs(max_stakes("nonsense", Stakes.NORMAL), Stakes.NORMAL)
        self.assertIsNone(max_stakes("a", "b"))


class AchievedLevel(unittest.TestCase):
    def test_verified_two_source_is_full(self):
        self.assertEqual(achieved_level(VERIFIED_ONE, Stakes.HIGH), FULL)
        self.assertEqual(achieved_level(VERIFIED_ONE, Stakes.CRITICAL), FULL)

    def test_verified_one_source_is_independent(self):
        self.assertEqual(achieved_level(VERIFIED_ONE, Stakes.NORMAL), INDEPENDENT)
        self.assertEqual(achieved_level(VERIFIED_ONE, Stakes.LOW), INDEPENDENT)

    def test_clean_attested_is_receipt(self):
        self.assertEqual(achieved_level(ATTESTED_LOW, Stakes.NORMAL), RECEIPT)

    def test_real_composer_attested_reason_set_is_receipt(self):
        # A REAL attested verdict carries a per-obligation NO_WORLD_FACT alongside
        # INTEGRITY_ATTESTED — these are attestation-compatible and must reach
        # RECEIPT (a denylist that treated NO_WORLD_FACT as a failure broke this).
        real = verdict(Status.UNVERIFIED, (Reason.NO_WORLD_FACT, Reason.INTEGRITY_ATTESTED))
        self.assertEqual(achieved_level(real, Stakes.LOW), RECEIPT)

    def test_attested_with_hard_failure_reason_is_none(self):
        # A hard-failure reason (outside the attestation-compatible set) alongside
        # attestation must never be laundered into a RECEIPT-level clear.
        mixed = verdict(Status.UNVERIFIED, (Reason.INTEGRITY_ATTESTED, Reason.INSUFFICIENT_CHANNELS))
        self.assertEqual(achieved_level(mixed, Stakes.NORMAL), NONE)

    def test_unverified_and_failed_are_none(self):
        self.assertEqual(achieved_level(UNVERIFIED, Stakes.HIGH), NONE)
        self.assertEqual(achieved_level(FAILED, Stakes.HIGH), NONE)


class DecideGate(unittest.TestCase):
    def test_cleared_when_achieved_meets_required(self):
        o = decide(directive(depth=INDEPENDENT), VERIFIED_ONE)
        self.assertTrue(o.cleared)
        self.assertEqual((o.required_level, o.achieved_level), (INDEPENDENT, INDEPENDENT))

    def test_under_verification_denies_clearance(self):
        # Directive wants FULL, but the verdict only reached one source.
        o = decide(directive(depth=FULL), VERIFIED_ONE)
        self.assertFalse(o.cleared)
        self.assertTrue(any(r.startswith("under_verified") for r in o.reasons))

    def test_full_cleared_with_two_source(self):
        o = decide(directive(depth=FULL), VERIFIED_TWO)
        self.assertTrue(o.cleared)
        self.assertEqual(o.achieved_level, FULL)

    def test_receipt_requirement_accepts_clean_attested(self):
        o = decide(directive(depth=RECEIPT), ATTESTED_LOW)
        self.assertTrue(o.cleared)

    def test_envelope_low_floor_requires_at_least_receipt(self):
        # Even a NONE-depth directive inherits the LOW envelope's RECEIPT floor,
        # so a bare UNVERIFIED cannot clear.
        low_unverified = verdict(Status.UNVERIFIED, (Reason.INSUFFICIENT_CHANNELS,),
                                 effective_stakes=Stakes.LOW)
        low_failed = verdict(Status.FAILED, (Reason.CONCLUSIVE_CONTRADICTION,),
                             effective_stakes=Stakes.LOW)
        self.assertTrue(decide(directive(depth=NONE), ATTESTED_LOW).cleared)
        self.assertFalse(decide(directive(depth=NONE), low_unverified).cleared)
        self.assertFalse(decide(directive(depth=NONE), low_failed).cleared)

    def test_failed_never_clears(self):
        o = decide(directive(depth=NONE), FAILED)
        self.assertFalse(o.cleared)
        self.assertIn("conclusive_failure", o.reasons)

    def test_subject_mismatch_denies(self):
        o = decide(directive(subject="other"), VERIFIED_ONE)  # verdict envelope_id is "act-1"
        self.assertFalse(o.cleared)
        self.assertTrue(any("action mismatch" in r for r in o.reasons))

    def test_null_inputs_fail_closed(self):
        self.assertFalse(decide(None, VERIFIED_ONE).cleared)
        self.assertFalse(decide(directive(), None).cleared)


class MalformedInputsFailClosed(unittest.TestCase):
    """A fail-closed gate must DENY on malformed input, never raise (kimi round-2)."""

    def test_non_int_verification_depth_denies(self):
        for bad in ("FULL", None, 1.5, True):
            o = decide(directive(depth=bad), VERIFIED_ONE)  # must not raise
            self.assertFalse(o.cleared)
            self.assertIn("null_or_invalid_inputs", o.reasons)

    def test_unhashable_effective_stakes_denies(self):
        v = verdict(Status.VERIFIED, effective_stakes=[])  # unhashable
        o = decide(directive(depth=NONE), v)  # must not raise
        self.assertFalse(o.cleared)

    def test_govern_bad_directive_denies_without_running_verifier(self):
        # directive is invalid, so verify() must never be reached (verifier=None).
        o = govern(None, None, object(), object(), [])
        self.assertFalse(o.cleared)
        self.assertIn("invalid_directive", o.reasons)

    def test_mismatch_does_not_leak_other_stakes_floor(self):
        v_for_b = verdict(Status.VERIFIED, envelope_id="B", effective_stakes=Stakes.CRITICAL)
        o = decide(directive(subject="A", depth=NONE), v_for_b)
        self.assertFalse(o.cleared)
        self.assertEqual(o.required_level, FULL)  # not the CRITICAL verdict's floor

    def test_stakes_order_covers_every_stakes(self):
        # Guards the max_stakes footgun: a new Stakes member omitted from
        # STAKES_ORDER would rank -1 and could break upward-only escalation.
        self.assertEqual(set(STAKES_ORDER), set(Stakes))


class VerdictBindingCannotDesync(unittest.TestCase):
    """The findings that motivated self-describing verdicts: a caller cannot
    desync the action or the rigor from the verdict, because there are no free
    params — everything derives from the verdict."""

    def test_verdict_for_another_action_does_not_clear(self):
        # A VERIFIED verdict about action B cannot clear directive for action A.
        v_for_b = verdict(Status.VERIFIED, envelope_id="B", effective_stakes=Stakes.HIGH)
        o = decide(directive(subject="A", depth=NONE), v_for_b)
        self.assertFalse(o.cleared)

    def test_one_source_verified_cannot_clear_full(self):
        # A one-source VERIFIED (effective NORMAL) can never reach FULL, so it
        # cannot clear a FULL-required action however the caller frames it.
        o = decide(directive(depth=FULL), VERIFIED_ONE)
        self.assertEqual(o.achieved_level, INDEPENDENT)
        self.assertFalse(o.cleared)


class EnvelopeStakesFloor(unittest.TestCase):
    """The verdict's effective stakes is a clearance floor, independent of the
    salience-driven directive depth (OBS-1). Salience may raise it, never lower it."""

    def test_high_stakes_forces_full_even_with_low_depth(self):
        # Directive asks NONE, but the verdict ran at HIGH -> required FULL.
        high_unverified = verdict(Status.UNVERIFIED, (Reason.INSUFFICIENT_CHANNELS,),
                                  effective_stakes=Stakes.HIGH)
        o = decide(directive(depth=NONE), high_unverified)
        self.assertEqual(o.required_level, FULL)
        self.assertFalse(o.cleared)

    def test_high_stakes_cleared_only_by_two_source_verified(self):
        o = decide(directive(depth=NONE), VERIFIED_TWO)
        self.assertEqual(o.achieved_level, FULL)
        self.assertTrue(o.cleared)


class AdaptationSealedGate(unittest.TestCase):
    """Adaptation needs BOTH the directive's eligibility AND a real VERIFIED —
    the seam's learning gate. Nothing is learned from an unverified action."""

    def test_eligible_and_verified_allows_adaptation(self):
        o = decide(directive(depth=INDEPENDENT, elig=AdaptationEligibility.CANDIDATE), VERIFIED_ONE)
        self.assertTrue(o.adaptation_allowed)

    def test_not_eligible_blocks_adaptation(self):
        o = decide(directive(depth=INDEPENDENT, elig=AdaptationEligibility.NONE), VERIFIED_ONE)
        self.assertFalse(o.adaptation_allowed)

    def test_attested_but_not_verified_blocks_adaptation(self):
        # Cleared at RECEIPT (LOW), but never independently VERIFIED — no learning.
        o = decide(directive(depth=RECEIPT, elig=AdaptationEligibility.CANDIDATE), ATTESTED_LOW)
        self.assertTrue(o.cleared)
        self.assertFalse(o.adaptation_allowed)

    def test_failed_blocks_adaptation(self):
        o = decide(directive(depth=NONE, elig=AdaptationEligibility.CANDIDATE), FAILED)
        self.assertFalse(o.adaptation_allowed)


class SelfDescribingOutcome(unittest.TestCase):
    """decide() stamps the bound directive + subject onto the outcome (the
    Verdict-stamping precedent one level up) and WITHHOLDS both on every
    unbound or invalid path — consumers key on `outcome.subject` and must
    never re-check binding themselves."""

    def test_bound_outcome_carries_the_identical_directive(self):
        d = directive(subject="act-1")
        o = decide(d, VERIFIED_TWO)
        self.assertEqual(o.subject, "act-1")
        # assertIs, not assertEqual: reverting the stamp to a default or a
        # copy must red this line.
        self.assertIs(o.directive, d)

    def test_unbound_outcome_withholds_directive_and_subject(self):
        o = decide(directive(subject="act-OTHER"), VERIFIED_TWO)
        self.assertFalse(o.cleared)
        self.assertIsNone(o.directive)
        self.assertEqual(o.subject, "")

    def test_blank_subject_directive_is_unbound(self):
        o = decide(directive(subject=""), VERIFIED_TWO)
        self.assertIsNone(o.directive)
        self.assertEqual(o.subject, "")

    def test_invalid_inputs_withhold(self):
        for o in (decide(None, VERIFIED_TWO), decide(directive(), "not-a-verdict")):
            self.assertIsNone(o.directive)
            self.assertEqual(o.subject, "")
            self.assertFalse(o.cleared)

    def test_malformed_rationale_is_denied_at_the_boundary(self):
        # The rationale rides through to the consumer gates, so the seam
        # validates it: a non-AdaptationRationale value is a malformed
        # directive and must DENY (a crash downstream is not a deny).
        # (Injected via __dict__, not the factory — the factory coherently
        # defaults a None rationale, which is exactly what we must bypass.)
        good = directive()
        for bad in (None, "risk_exceeded", 3):
            d = Directive(**{**good.__dict__, "adaptation_rationale": bad})
            o = decide(d, VERIFIED_TWO)
            self.assertFalse(o.cleared)
            self.assertIsNone(o.directive)
            self.assertEqual(o.subject, "")

    def test_non_string_subject_cannot_bind(self):
        # The binding key is attacker-supplied and drives an == against the
        # verdict's envelope_id. A non-str subject (always-equal object, or one
        # whose __bool__/__eq__ raises) must NOT bind to a verdict for another
        # action, and must never crash the gate (a crash is not a deny).
        class AlwaysEq:
            def __eq__(self, other):
                return True
            def __bool__(self):
                return True

        class Raises:
            def __eq__(self, other):
                raise RuntimeError("boom")
            def __bool__(self):
                raise RuntimeError("boom")

        good = directive(subject="act-1")
        v_other = verdict(Status.VERIFIED, envelope_id="act-innocent",
                          effective_stakes=Stakes.HIGH)
        for bad in (AlwaysEq(), Raises(), 3, b"act-1"):
            d = Directive(**{**good.__dict__, "subject": bad})
            o = decide(d, v_other)
            self.assertFalse(o.cleared)
            self.assertFalse(o.adaptation_allowed)
            self.assertIsNone(o.directive)
            self.assertEqual(o.subject, "")

    def test_malformed_eligibility_is_denied_at_the_boundary(self):
        # Both halves of the pair are validated symmetrically: a non-enum
        # eligibility is a malformed directive, denied the same way.
        good = directive()
        d = Directive(**{**good.__dict__, "adaptation_eligibility": "candidate"})
        o = decide(d, VERIFIED_TWO)
        self.assertFalse(o.cleared)
        self.assertIsNone(o.directive)

    def test_desynced_rationale_eligibility_pair_is_denied(self):
        # ELIGIBLE iff CANDIDATE — interpret() maintains the pair; a directive
        # that desyncs it is malformed (no free parameters to desync).
        incoherent = [
            directive(elig=AdaptationEligibility.CANDIDATE,
                      rationale=AdaptationRationale.RISK_EXCEEDED),
            directive(elig=AdaptationEligibility.NONE,
                      rationale=AdaptationRationale.ELIGIBLE),
        ]
        for d in incoherent:
            o = decide(d, VERIFIED_TWO)
            self.assertFalse(o.cleared)
            self.assertIsNone(o.directive)


if __name__ == "__main__":
    unittest.main()
