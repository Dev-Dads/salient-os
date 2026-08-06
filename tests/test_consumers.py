"""Unit tests for the consumer gates (build stage 4).

Every outcome here is produced by the REAL `decide()` over factory directives
and verdicts, so these tests mutation-couple to the seam: change decide() and
the gates' fixtures change with it. The invariants pinned, per class below:
nomination's single predicate, the risk-reject hand-off (and its attribution
fences), unverified-novelty exclusion, the decay model and its pin, fail-closed
floors, raising gates, schema fences, and Finding C as an import-graph fact.
"""

import ast
import pathlib
import unittest

from salienceos.consumers import (
    HANDOFF_SOURCE_RISK_REJECT,
    AdaptationDecision,
    HandoffMismatchError,
    InhibitorHandoff,
    MemoryRetention,
    consume,
    effective_weight,
    nominate,
    retain,
)
from salienceos.control import FULL, INDEPENDENT, RECEIPT, decide
from salienceos.interpreter import (
    AdaptationEligibility,
    AdaptationRationale,
    Directive,
    Reconfigure,
)
from salienceos.verifier import Reason, Stakes, Status, Verdict

NOW = 100.0  # injected clock (days); the package has no clock of its own


def directive(subject="act-1", depth=INDEPENDENT, elig=AdaptationEligibility.NONE,
              rationale=AdaptationRationale.NOT_REQUESTED, retention="episodic"):
    return Directive(
        subject=subject, policy_id="p", compute_budget=100, verification_depth=depth,
        retention_class=retention, routing_hint="", adaptation_eligibility=elig,
        adaptation_rationale=rationale, allowed_capabilities=(),
        reconfigure=Reconfigure.BETWEEN_TURN, interpreter_version="test", reasons=(),
    )


def verdict(status=Status.VERIFIED, reasons=(), envelope_id="act-1",
            effective_stakes=Stakes.NORMAL):
    return Verdict(status=status, reasons=tuple(reasons), composer_version="test",
                   envelope_id=envelope_id, effective_stakes=effective_stakes)


def allowed_outcome():
    """cleared AND directive-CANDIDATE AND world-VERIFIED."""
    return decide(
        directive(elig=AdaptationEligibility.CANDIDATE,
                  rationale=AdaptationRationale.ELIGIBLE),
        verdict(Status.VERIFIED),
    )


def risk_reject_outcome():
    """cleared but denied adaptation on a RECORDED asserted over-cap risk."""
    return decide(
        directive(depth=FULL, rationale=AdaptationRationale.RISK_EXCEEDED),
        verdict(Status.VERIFIED, effective_stakes=Stakes.HIGH),
    )


def risk_unknown_outcome():
    return decide(
        directive(depth=FULL, rationale=AdaptationRationale.RISK_UNKNOWN),
        verdict(Status.VERIFIED, effective_stakes=Stakes.HIGH),
    )


def attested_eligible_outcome():
    """Directive says ELIGIBLE; the world only attested — never VERIFIED."""
    return decide(
        directive(depth=RECEIPT, elig=AdaptationEligibility.CANDIDATE,
                  rationale=AdaptationRationale.ELIGIBLE),
        verdict(Status.UNVERIFIED, (Reason.INTEGRITY_ATTESTED,),
                effective_stakes=Stakes.LOW),
    )


def unbound_outcome():
    return decide(directive(subject="act-OTHER"), verdict())


def invalid_outcome():
    return decide(None, verdict())


class NominationPredicate(unittest.TestCase):
    def test_allowed_outcome_nominates_without_handoff(self):
        dec = nominate(allowed_outcome())
        self.assertTrue(dec.nominated)
        self.assertIsNone(dec.handoff)
        self.assertIs(dec.rationale, AdaptationRationale.ELIGIBLE)
        self.assertIn("nominated_for_offline_review", dec.reasons)

    def test_the_only_true_path_is_adaptation_allowed(self):
        # Finding D pin: the gate follows the RECORDED decision, never
        # recomputes it. A hand-built outcome claiming adaptation_allowed=True
        # over an UNVERIFIED verdict is a forged outcome — out of scope, same
        # stance as decide() on forged verdicts — and the gate must still obey
        # the record. (Sabotage direction: a gate that re-checks
        # verdict.status here would red this test.)
        d = directive(elig=AdaptationEligibility.CANDIDATE,
                      rationale=AdaptationRationale.ELIGIBLE)
        forged = decide(d, verdict(Status.VERIFIED))
        forged = type(forged)(**{**forged.__dict__,
                                 "verdict": verdict(Status.UNVERIFIED),
                                 "adaptation_allowed": True})
        self.assertTrue(nominate(forged).nominated)

    def test_eligible_but_not_allowed_is_unverified_novelty(self):
        dec = nominate(attested_eligible_outcome())
        self.assertFalse(dec.nominated)
        self.assertIsNone(dec.handoff)
        self.assertIn("unverified_novelty_excluded", dec.reasons)

    def test_invalid_or_unbound_refuses_with_nothing(self):
        for o in (unbound_outcome(), invalid_outcome()):
            dec = nominate(o)
            self.assertFalse(dec.nominated)
            self.assertIsNone(dec.rationale)
            self.assertIsNone(dec.handoff)
            self.assertEqual(dec.subject, "")
            self.assertIn("invalid_or_unbound_outcome", dec.reasons)

    def test_malformed_rationale_refuses_never_crashes(self):
        # The seam's boundary check denies this upstream; the gate keeps a
        # belt for hand-built outcomes: refuse with a record, never raise.
        good = allowed_outcome()
        bad_d = type(good.directive)(**{**good.directive.__dict__,
                                        "adaptation_rationale": None})
        forged = type(good)(**{**good.__dict__, "directive": bad_d,
                               "adaptation_allowed": False})
        dec = nominate(forged)
        self.assertFalse(dec.nominated)
        self.assertIsNone(dec.handoff)
        self.assertIn("invalid_rationale", dec.reasons)


class RiskRejectHandoff(unittest.TestCase):
    def test_risk_exceeded_originates_the_handoff(self):
        dec = nominate(risk_reject_outcome())
        self.assertFalse(dec.nominated)
        self.assertIsNotNone(dec.handoff)
        self.assertEqual(dec.handoff.subject, dec.subject)
        self.assertEqual(dec.handoff.source, HANDOFF_SOURCE_RISK_REJECT)
        self.assertEqual(dec.handoff.rationale, "risk_exceeded")

    def test_risk_unknown_is_not_an_incident(self):
        dec = nominate(risk_unknown_outcome())
        self.assertFalse(dec.nominated)
        self.assertIsNone(dec.handoff)  # ignorance never pins an inhibitor
        self.assertIn("risk_unknown", dec.reasons)

    def test_bound_denials_carry_their_recorded_reason_and_no_handoff(self):
        # The non-incident denials — including POLICY_DISALLOWED, the only
        # rationale a host with allow_adaptation=False ever produces: refusal
        # record with the recorded code, and never an inhibitor.
        for rationale in (AdaptationRationale.POLICY_DISALLOWED,
                          AdaptationRationale.NOT_REQUESTED,
                          AdaptationRationale.UNDER_VERIFIED):
            o = decide(directive(depth=FULL, rationale=rationale),
                       verdict(Status.VERIFIED, effective_stakes=Stakes.HIGH))
            dec = nominate(o)
            self.assertFalse(dec.nominated)
            self.assertIsNone(dec.handoff)
            self.assertEqual(dec.reasons, (rationale.value,))


class ConsumeSeam(unittest.TestCase):
    def test_disagreement_flows_through_consume(self):
        # Weight gate first; its hand-off reaches memory as an explicit record.
        dec, ret = consume(risk_reject_outcome(), NOW)
        self.assertFalse(dec.nominated)   # weight: HARD BLOCK
        self.assertTrue(ret.inhibitor)    # memory: RETAIN, pinned
        self.assertEqual(ret.subject, dec.subject)

    def test_inhibitor_is_not_forced_by_mere_refusal(self):
        # Sabotage pin: an implementation that sets inhibitor whenever
        # nominated=False (collapsing the channels) reds this.
        dec, ret = consume(attested_eligible_outcome(), NOW)
        self.assertFalse(dec.nominated)
        self.assertFalse(ret.inhibitor)

    def test_allowed_outcome_retains_without_inhibitor(self):
        dec, ret = consume(allowed_outcome(), NOW)
        self.assertTrue(dec.nominated)
        self.assertFalse(ret.inhibitor)
        self.assertEqual(ret.retention_class, "episodic")
        self.assertTrue(ret.cleared)


class RetentionRecord(unittest.TestCase):
    def test_binding_not_clearance_selects_the_class(self):
        # A bound DENIAL retains at the directive's class — a denial is an
        # auditable event; only unbound/invalid outcomes hit the floor.
        denied = decide(directive(depth=FULL, retention="semantic"),
                        verdict(Status.FAILED))
        ret = retain(denied, NOW)
        self.assertFalse(denied.cleared)
        self.assertEqual(ret.retention_class, "semantic")
        self.assertFalse(ret.cleared)

    def test_unbound_and_invalid_floor_to_ephemeral(self):
        for o in (unbound_outcome(), invalid_outcome()):
            ret = retain(o, NOW)
            self.assertEqual(ret.retention_class, "ephemeral")
            self.assertFalse(ret.inhibitor)
            self.assertIn("unbound_or_invalid_retention_floored", ret.reasons)

    def test_out_of_ladder_class_floors_with_reason(self):
        # A BOUND record with a bad class gets its own token — the audit
        # trail must not call it "unbound".
        o = decide(directive(retention="exotic-tier"), verdict())
        ret = retain(o, NOW)
        self.assertEqual(ret.retention_class, "ephemeral")
        self.assertIn("retention_class_off_ladder_floored", ret.reasons)
        self.assertNotIn("unbound_or_invalid_retention_floored", ret.reasons)

    def test_record_stamps_injected_clock(self):
        ret = retain(allowed_outcome(), 42.5)
        self.assertEqual(ret.recorded_at_days, 42.5)


class DecayModel(unittest.TestCase):
    def _ret(self, cls="episodic", inhibitor=False):
        base = retain(risk_reject_outcome() if inhibitor else allowed_outcome(),
                      NOW,
                      handoff=nominate(risk_reject_outcome()).handoff if inhibitor else None)
        if not inhibitor:
            base = type(base)(**{**base.__dict__, "retention_class": cls})
        return base

    def test_weight_at_age_zero_is_base_plus_reinforcement(self):
        ret = self._ret()
        self.assertEqual(effective_weight(ret, NOW), 1.0)
        self.assertEqual(effective_weight(ret, NOW, reinforcement_sum=0.25), 1.25)

    def test_weight_at_one_half_life_is_exactly_half(self):
        ret = self._ret("episodic")
        self.assertEqual(effective_weight(ret, NOW + 14.0), 0.5)
        ret_s = self._ret("semantic")
        self.assertEqual(effective_weight(ret_s, NOW + 180.0), 0.5)

    def test_durability_is_monotonic_in_class_at_fixed_age(self):
        weights = [effective_weight(self._ret(c), NOW + 1.0)
                   for c in ("ephemeral", "working", "episodic", "semantic")]
        self.assertEqual(weights, sorted(weights))
        self.assertLess(weights[0], weights[-1])

    def test_inhibitor_never_decays(self):
        ret = self._ret(inhibitor=True)
        self.assertTrue(ret.inhibitor)
        w_now = effective_weight(ret, NOW)
        w_decade = effective_weight(ret, NOW + 3650.0)
        self.assertEqual(w_now, w_decade)  # flat: the pin

    def test_clock_skew_clamps_to_base(self):
        ret = retain(allowed_outcome(), 50.0)
        self.assertEqual(effective_weight(ret, 10.0), 1.0)  # never ABOVE base

    def test_unknown_class_decays_at_the_floor_rate(self):
        ret = type(self._ret())(**{**self._ret().__dict__,
                                   "retention_class": "no-such-class"})
        self.assertEqual(effective_weight(ret, NOW + 0.02),
                         effective_weight(self._ret("ephemeral"), NOW + 0.02))


class RaisingGates(unittest.TestCase):
    def test_type_fences_raise_typeerror(self):
        with self.assertRaises(TypeError):
            nominate("not-an-outcome")
        with self.assertRaises(TypeError):
            retain("not-an-outcome", NOW)
        with self.assertRaises(TypeError):
            retain(allowed_outcome(), NOW, handoff="not-a-handoff")
        with self.assertRaises(TypeError):
            effective_weight("not-a-retention", NOW)

    def test_now_days_fence_finite_nonnegative_nonbool(self):
        o = allowed_outcome()
        for bad in ("5", True, -1, float("nan"), float("inf"), None):
            with self.assertRaises(TypeError):
                retain(o, bad)
        ret = retain(o, NOW)
        for bad in ("5", True, -1, float("nan"), float("inf")):
            with self.assertRaises(TypeError):
                effective_weight(ret, bad)
        with self.assertRaises(TypeError):
            effective_weight(ret, NOW, reinforcement_sum=float("nan"))

    def test_handoff_attribution_matrix_raises(self):
        good = nominate(risk_reject_outcome()).handoff
        rr = risk_reject_outcome()
        cases = [
            # subject mismatch
            (rr, InhibitorHandoff("act-OTHER", good.source, good.rationale)),
            # wrong source
            (rr, InhibitorHandoff(good.subject, "some.other.source", good.rationale)),
            # wrong rationale string
            (rr, InhibitorHandoff(good.subject, good.source, "risk_unknown")),
            # outcome whose RECORDED rationale is not RISK_EXCEEDED
            (allowed_outcome(), good),
            (risk_unknown_outcome(), good),
            # blank-subject / unbound / invalid outcomes can accept no hand-off
            (unbound_outcome(), good),
            (invalid_outcome(), good),
        ]
        for outcome, handoff in cases:
            with self.assertRaises(HandoffMismatchError):
                retain(outcome, NOW, handoff=handoff)

    def test_the_matched_handoff_is_accepted(self):
        rr = risk_reject_outcome()
        ret = retain(rr, NOW, handoff=nominate(rr).handoff)
        self.assertTrue(ret.inhibitor)


class SchemaPins(unittest.TestCase):
    """Exact-allowlist fences: ANY added field reds these — including a future
    delete/tombstone/scope field (memory) or promote/apply field (adaptation).
    An exact name-list is strictly stronger than a forbidden-name check."""

    def test_memory_retention_fields_are_exactly_these(self):
        self.assertEqual(
            [f.name for f in MemoryRetention.__dataclass_fields__.values()],
            ["subject", "retention_class", "inhibitor", "cleared", "base_weight",
             "recorded_at_days", "governor_version", "reasons"],
        )

    def test_adaptation_decision_fields_are_exactly_these(self):
        self.assertEqual(
            [f.name for f in AdaptationDecision.__dataclass_fields__.values()],
            ["subject", "nominated", "rationale", "handoff", "gate_version",
             "reasons"],
        )

    def test_handoff_fields_are_exactly_these(self):
        self.assertEqual(
            [f.name for f in InhibitorHandoff.__dataclass_fields__.values()],
            ["subject", "source", "rationale", "reasons"],
        )


class ChannelSeparation(unittest.TestCase):
    def test_memory_and_adaptation_never_import_each_other(self):
        # Finding C as an import-graph FACT, not a convention.
        pkg = pathlib.Path(__file__).resolve().parent.parent / "salienceos" / "consumers"
        imports = {}
        for name in ("memory", "adaptation"):
            tree = ast.parse((pkg / f"{name}.py").read_text(encoding="utf-8"))
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    mods.add(node.module or "")
            imports[name] = mods
        self.assertFalse(any("adaptation" in m for m in imports["memory"]))
        self.assertFalse(any("memory" in m for m in imports["adaptation"]))
        # Both MAY import the hand-off boundary record.
        self.assertTrue(any("handoff" in m for m in imports["adaptation"]))


if __name__ == "__main__":
    unittest.main()
