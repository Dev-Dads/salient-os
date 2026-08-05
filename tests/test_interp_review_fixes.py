"""Regressions for the internal review findings (correctness + design/test-honesty).

Each test is written to go RED if its fix were reverted — the mutation discipline
the verifier established. The headline is the adaptation verification-gate test:
before this, deleting the `v_depth >= adaptation_min_verification` clause left
every adaptation test green (a fixture that could not reach the wrong answer).
"""

import unittest

from salienceos.interpreter import (
    AdaptationEligibility,
    Facet,
    SalienceBus,
    SalienceSignal,
    VerificationDepth,
    additive_scorer,
    interpret,
    issue_policy,
    threshold_scorer,
    valid_signal,
)
from salienceos.interpreter.signal import MAX_PROVENANCE_REFS, MAX_TOKEN_LEN

KEY = b"policy-test-key"


def policy(subject="req-1", caps=("fs.read:project",), min_b=10, max_b=1000,
           min_v=0, max_v=3, max_ret="semantic", allow_adapt=True,
           adapt_min_v=2, adapt_max_risk=0.4, allow_immediate=False):
    return issue_policy("pol-1", subject, caps, min_b, max_b, min_v, max_v, max_ret,
                        allow_adapt, adapt_min_v, adapt_max_risk, allow_immediate, KEY)


def sig(facet, influence, confidence=1.0, subject="req-1", subsystem="x", prov=()):
    return SalienceSignal(subsystem, subject, facet, influence, confidence, tuple(prov))


class AdaptationVerificationGate(unittest.TestCase):
    """The clause that no prior test exercised (design review, section 5).

    Both cases use a COHERENT policy (adapt_min_v == max_v == 3) and differ ONLY
    in whether a VERIFICATION signal is present. Low risk (0.1) passes the risk
    gate in both; without the verification signal the applied depth is 0 < 3, so
    only the `v_depth >= adaptation_min_verification` clause can differ the
    outcome — deleting that clause makes the negative case go CANDIDATE (red)."""

    def test_insufficient_applied_depth_blocks_adaptation(self):
        pol = policy(min_v=0, max_v=3, adapt_min_v=3, adapt_max_risk=0.4)
        sigs = [sig(Facet.ADAPTATION, 1.0), sig(Facet.RISK, 0.1)]  # no verification push
        d = interpret(pol, sigs, KEY)
        self.assertLess(d.verification_depth, 3)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.NONE)

    def test_sufficient_applied_depth_allows_candidate(self):
        pol = policy(min_v=0, max_v=3, adapt_min_v=3, adapt_max_risk=0.4)
        sigs = [sig(Facet.ADAPTATION, 1.0), sig(Facet.RISK, 0.1), sig(Facet.VERIFICATION, 1.0)]
        d = interpret(pol, sigs, KEY)
        self.assertEqual(d.verification_depth, 3)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.CANDIDATE)


class VerificationSemantics(unittest.TestCase):
    """F1: verification rises from the policy floor with risk; unknown risk is
    maximal; an asserted low risk can never dip below the floor."""

    def test_unknown_risk_verifies_at_ceiling(self):
        d = interpret(policy(min_v=0, max_v=3), [sig(Facet.ATTENTION, 0.5)], KEY)
        self.assertEqual(d.verification_depth, int(VerificationDepth.FULL))

    def test_asserted_low_risk_floored_at_policy_min(self):
        # risk 0.0 would scale to 0, but the policy floor is 2.
        d = interpret(policy(min_v=2, max_v=3), [sig(Facet.RISK, 0.0)], KEY)
        self.assertEqual(d.verification_depth, 2)

    def test_monotonic_in_risk(self):
        depths = [
            interpret(policy(min_v=0, max_v=3), [sig(Facet.RISK, r)], KEY).verification_depth
            for r in (0.0, 0.34, 0.67, 1.0)
        ]
        self.assertEqual(depths, sorted(depths))
        self.assertEqual((depths[0], depths[-1]), (0, 3))


class PolicyTrustBoundary(unittest.TestCase):
    """F2/F4: verify_policy rejects incoherent-but-signed envelopes and never
    raises on a bad key."""

    def test_non_bool_switch_is_rejected(self):
        # allow_adaptation is a truthy string; signed, but incoherent -> hard deny.
        bad = issue_policy("pol-1", "req-1", ("fs.read:project",), 10, 1000, 0, 3,
                           "semantic", "true", 2, 0.4, False, KEY)
        d = interpret(bad, [sig(Facet.ADAPTATION, 1.0), sig(Facet.RISK, 0.0)], KEY)
        self.assertEqual(d.allowed_capabilities, ())
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.NONE)

    def test_non_bytes_key_fails_closed_not_raises(self):
        d = interpret(policy(), [sig(Facet.ATTENTION, 1.0)], 12345)
        self.assertEqual(d.allowed_capabilities, ())
        self.assertEqual(d.compute_budget, 0)


class FailClosedInputs(unittest.TestCase):
    """F3/F4: malformed signal containers degrade, not crash, and the audit
    count is never negative."""

    def test_none_signals_yields_directive(self):
        d = interpret(policy(), None, KEY)
        self.assertEqual(d.compute_budget, 10)  # fail-closed defaults, valid policy
        self.assertIn("signals_unreadable", d.reasons)

    def test_throwing_generator_yields_directive_not_crash(self):
        def poisoned():
            yield sig(Facet.ATTENTION, 0.5)
            raise RuntimeError("misfiring subsystem")
        d = interpret(policy(), poisoned(), KEY)  # must NOT raise
        self.assertIn("signals_unreadable", d.reasons)
        self.assertEqual(d.compute_budget, 10)  # fell back to no-signals defaults

    def test_generator_dropped_count_is_nonnegative(self):
        gen = (s for s in [sig(Facet.ATTENTION, 2.0), "nope", sig(Facet.ATTENTION, 0.5)])
        d = interpret(policy(), gen, KEY)
        drops = [r for r in d.reasons if r.startswith("dropped_invalid_signals=")]
        self.assertEqual(drops, ["dropped_invalid_signals=2"])

    def test_hard_deny_blanks_untrusted_identifiers(self):
        good = policy(subject="secret-subject")
        tampered = type(good)(**{**good.__dict__, "max_budget": 10_000})
        d = interpret(tampered, [], KEY)
        self.assertEqual(d.subject, "")
        self.assertEqual(d.policy_id, "")


class AuditFenceIsStructural(unittest.TestCase):
    """Finding G: bounded ref-shaped tokens — a body/CoT cannot validate."""

    def test_oversized_subject_is_invalid(self):
        blob = "x" * (MAX_TOKEN_LEN + 1)
        self.assertFalse(valid_signal(SalienceSignal("s", blob, "attention", 0.5, 1.0, ())))

    def test_oversized_provenance_ref_is_invalid(self):
        cot = "reasoning: " + "y" * MAX_TOKEN_LEN
        self.assertFalse(valid_signal(sig(Facet.ATTENTION, 0.5, prov=(cot,))))

    def test_too_many_provenance_refs_is_invalid(self):
        many = tuple(f"evt:{i}" for i in range(MAX_PROVENANCE_REFS + 1))
        self.assertFalse(valid_signal(sig(Facet.ATTENTION, 0.5, prov=many)))

    def test_bus_rejects_oversized_signal(self):
        blob = "z" * (MAX_TOKEN_LEN + 1)
        with self.assertRaises(TypeError):
            SalienceBus().publish(SalienceSignal("s", "req-1", "attention", 0.5, 1.0, (blob,)))


class BusChainVerification(unittest.TestCase):
    """Design G: the hash chain is checkable, not merely append-only by convention."""

    def _populate(self):
        bus = SalienceBus()
        bus.publish(sig(Facet.ATTENTION, 0.5, prov=("evt:1",)))
        bus.publish(sig(Facet.MEMORY, 0.3, subject="req-1"))
        d = interpret(policy(), [sig(Facet.ATTENTION, 0.7)], KEY)
        bus.emit(d)
        return bus

    def test_intact_chain_verifies(self):
        self.assertTrue(self._populate().verify_chain())

    def test_tampered_payload_fails(self):
        bus = self._populate()
        bus._entries[0]["payload"]["influence"] = 0.99  # simulate durable-record edit
        self.assertFalse(bus.verify_chain())

    def test_reordered_entries_fail(self):
        bus = self._populate()
        bus._entries[0], bus._entries[1] = bus._entries[1], bus._entries[0]
        self.assertFalse(bus.verify_chain())


class ScorerHeterogeneity(unittest.TestCase):
    """Thin-contract: two subsystems, different scoring shapes, same signal."""

    def test_additive_scorer_emits_clamped_weighted_sum(self):
        s = additive_scorer("mem", "req-1", Facet.MEMORY,
                            features={"recurrence": 1.0, "novelty": 1.0},
                            weights={"recurrence": 0.5, "novelty": 0.3},
                            confidence=0.8, provenance=("evt:1",))
        self.assertTrue(valid_signal(s))
        self.assertAlmostEqual(s.influence, 0.8)

    def test_threshold_scorer_is_a_hard_step(self):
        hot = threshold_scorer("risk", "req-1", Facet.RISK, value=0.9, threshold=0.7, confidence=1.0)
        cold = threshold_scorer("risk", "req-1", Facet.RISK, value=0.5, threshold=0.7, confidence=1.0)
        self.assertEqual((hot.influence, cold.influence), (1.0, 0.0))
        self.assertTrue(valid_signal(hot) and valid_signal(cold))

    def test_both_shapes_produce_the_same_thin_type(self):
        a = additive_scorer("m", "req-1", Facet.MEMORY, {"x": 1.0}, {"x": 0.5}, 1.0)
        t = threshold_scorer("r", "req-1", Facet.RISK, 1.0, 0.5, 1.0)
        self.assertIs(type(a), type(t))
        self.assertEqual(set(type(a).__dataclass_fields__), set(type(t).__dataclass_fields__))


class PanelRedTeamFixes(unittest.TestCase):
    """Regressions for the five-model panel findings that reproduced."""

    def test_zero_confidence_risk_is_treated_as_absent(self):
        # The headline: a zero-confidence RISK signal must NOT lower verification
        # or open the adaptation risk gate below the absent-RISK (cautious) default.
        p = policy(min_v=2, max_v=3, adapt_min_v=2, adapt_max_risk=0.4)
        with_zero = interpret(p, [sig(Facet.ADAPTATION, 1.0), sig(Facet.RISK, 1.0, confidence=0.0)], KEY)
        absent = interpret(p, [sig(Facet.ADAPTATION, 1.0)], KEY)
        self.assertEqual(with_zero.verification_depth, absent.verification_depth)
        self.assertIs(with_zero.adaptation_eligibility, absent.adaptation_eligibility)
        self.assertEqual(with_zero.verification_depth, 3)  # ceiling, cautious

    def test_zero_confidence_attention_yields_min_budget(self):
        d = interpret(policy(), [sig(Facet.ATTENTION, 1.0, confidence=0.0)], KEY)
        self.assertEqual(d.compute_budget, 10)  # no information -> floor

    def test_unknown_facet_moves_no_knob(self):
        # Capability-shaped and unknown facets are valid signals but inert.
        base = interpret(policy(), [], KEY)
        flooded = interpret(policy(), [sig("fs.write:/etc", 1.0), sig("host_admin", 1.0),
                                      sig("totally_unknown", 1.0)], KEY)
        self.assertEqual(
            (flooded.compute_budget, flooded.verification_depth, flooded.retention_class,
             flooded.adaptation_eligibility, flooded.allowed_capabilities),
            (base.compute_budget, base.verification_depth, base.retention_class,
             base.adaptation_eligibility, base.allowed_capabilities),
        )

    def test_incoherent_adaptation_threshold_is_rejected(self):
        bad = issue_policy("pol-1", "req-1", ("fs.read:project",), 10, 1000, 0, 1,
                           "semantic", True, 2, 0.4, False, KEY)  # adapt_min_v=2 > max_v=1
        d = interpret(bad, [sig(Facet.ATTENTION, 1.0)], KEY)
        self.assertEqual(d.allowed_capabilities, ())  # hard deny (incoherent policy)

    def test_emit_rejects_non_directive(self):
        for bad in ("directive", 42, None, object()):
            with self.assertRaises(TypeError):
                SalienceBus().emit(bad)

    def test_verification_rounds_half_up(self):
        # span 1, risk exactly 0.5 -> half rounds UP to 1 (more scrutiny), not 0.
        # adapt_min_v=1 keeps the policy coherent (<= max_v).
        d = interpret(policy(min_v=0, max_v=1, adapt_min_v=1), [sig(Facet.RISK, 0.5)], KEY)
        self.assertEqual(d.verification_depth, 1)

    def test_empty_provenance_ref_is_invalid(self):
        self.assertFalse(valid_signal(sig(Facet.ATTENTION, 0.5, prov=("",))))


if __name__ == "__main__":
    unittest.main()
