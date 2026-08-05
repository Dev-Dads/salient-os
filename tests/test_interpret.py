"""Golden fixtures for the pure interpreter: (policy, signals) -> directive.

Pins the arbitration math, the stakes-free knob scaling, the fail-closed
defaults, and the hard-deny path. Sibling to tests/test_composer.py.
"""

import unittest

from salienceos.interpreter import (
    AdaptationEligibility,
    Directive,
    Facet,
    Reconfigure,
    SalienceSignal,
    VerificationDepth,
    interpret,
    issue_policy,
)

KEY = b"policy-test-key"


def policy(subject="req-1", caps=("fs.read:project",), min_b=10, max_b=1000,
           min_v=0, max_v=3, max_ret="semantic", allow_adapt=True,
           adapt_min_v=2, adapt_max_risk=0.4, allow_immediate=False):
    return issue_policy("pol-1", subject, caps, min_b, max_b, min_v, max_v, max_ret,
                        allow_adapt, adapt_min_v, adapt_max_risk, allow_immediate, KEY)


def sig(subsystem, facet, influence, confidence=1.0, subject="req-1", prov=()):
    return SalienceSignal(subsystem, subject, facet, influence, confidence, tuple(prov))


class KnobScaling(unittest.TestCase):
    def test_attention_scales_budget_within_caps(self):
        d = interpret(policy(), [sig("m", Facet.ATTENTION, 0.5)], KEY)
        self.assertEqual(d.compute_budget, 10 + round(0.5 * (1000 - 10)))
        self.assertGreaterEqual(d.compute_budget, 10)
        self.assertLessEqual(d.compute_budget, 1000)

    def test_confidence_weighted_aggregation(self):
        # Two attention signals; the confident one dominates the mean.
        sigs = [sig("a", Facet.ATTENTION, 1.0, confidence=0.9),
                sig("b", Facet.ATTENTION, 0.0, confidence=0.1)]
        d = interpret(policy(), sigs, KEY)
        expected_frac = (1.0 * 0.9 + 0.0 * 0.1) / (0.9 + 0.1)
        self.assertEqual(d.compute_budget, 10 + round(expected_frac * 990))

    def test_verification_scales_up_with_risk(self):
        d = interpret(policy(), [sig("r", Facet.RISK, 1.0)], KEY)
        self.assertEqual(d.verification_depth, int(VerificationDepth.FULL))

    def test_retention_capped_by_policy(self):
        # Max memory salience, but policy ceiling is "working".
        d = interpret(policy(max_ret="working"), [sig("m", Facet.MEMORY, 1.0)], KEY)
        self.assertEqual(d.retention_class, "working")

    def test_routing_hint_is_the_strongest_router(self):
        sigs = [sig("router-a", Facet.ROUTING, 0.3), sig("router-b", Facet.ROUTING, 0.9)]
        d = interpret(policy(), sigs, KEY)
        self.assertEqual(d.routing_hint, "router-b")

    def test_immediate_reconfigure_requires_policy_and_high_attention(self):
        hot = [sig("m", Facet.ATTENTION, 0.95)]
        self.assertIs(interpret(policy(allow_immediate=False), hot, KEY).reconfigure,
                      Reconfigure.BETWEEN_TURN)
        self.assertIs(interpret(policy(allow_immediate=True), hot, KEY).reconfigure,
                      Reconfigure.IMMEDIATE)
        # High attention but policy forbids immediate -> still deferred.
        self.assertIs(interpret(policy(allow_immediate=True),
                                [sig("m", Facet.ATTENTION, 0.5)], KEY).reconfigure,
                      Reconfigure.BETWEEN_TURN)


class FailClosedDefaults(unittest.TestCase):
    def test_no_signals_yields_min_budget_max_verification(self):
        d = interpret(policy(), [], KEY)
        self.assertEqual(d.compute_budget, 10)
        self.assertEqual(d.verification_depth, int(VerificationDepth.FULL))
        self.assertEqual(d.retention_class, "ephemeral")
        self.assertIn("no_subject_signals_failclosed_defaults", d.reasons)

    def test_signals_for_other_subject_are_ignored(self):
        d = interpret(policy(subject="req-1"),
                      [sig("m", Facet.ATTENTION, 1.0, subject="req-2")], KEY)
        self.assertEqual(d.compute_budget, 10)  # foreign signal did not raise budget

    def test_invalid_signals_are_dropped(self):
        bad = ["not a signal", sig("m", Facet.ATTENTION, 2.0), sig("m", Facet.ATTENTION, 0.5)]
        d = interpret(policy(), bad, KEY)
        self.assertTrue(any(r.startswith("dropped_invalid_signals=") for r in d.reasons))
        # Only the one valid 0.5 attention signal informs the budget.
        self.assertEqual(d.compute_budget, 10 + round(0.5 * 990))

    def test_bad_policy_key_hard_denies(self):
        d = interpret(policy(), [sig("m", Facet.ATTENTION, 1.0)], b"wrong-key")
        self.assertEqual(d.allowed_capabilities, ())
        self.assertEqual(d.compute_budget, 0)
        self.assertEqual(d.verification_depth, int(VerificationDepth.FULL))
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.NONE)
        self.assertIn("policy_unsigned_or_invalid", d.reasons)

    def test_none_policy_hard_denies(self):
        d = interpret(None, [sig("m", Facet.ATTENTION, 1.0)], KEY)
        self.assertEqual(d.allowed_capabilities, ())
        self.assertEqual(d.compute_budget, 0)

    def test_tampered_policy_hard_denies(self):
        good = policy()
        # widen the budget window after signing
        tampered = type(good)(**{**good.__dict__, "max_budget": 10_000})
        d = interpret(tampered, [sig("m", Facet.ATTENTION, 1.0)], KEY)
        self.assertEqual(d.allowed_capabilities, ())
        self.assertEqual(d.compute_budget, 0)


class AdaptationGate(unittest.TestCase):
    def test_candidate_requires_low_risk_and_enough_verification(self):
        sigs = [sig("evo", Facet.ADAPTATION, 1.0),
                sig("risk", Facet.RISK, 0.1),          # low risk enables
                sig("v", Facet.VERIFICATION, 1.0)]     # pushes depth to FULL >= 2
        d = interpret(policy(), sigs, KEY)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.CANDIDATE)

    def test_absent_risk_blocks_adaptation(self):
        sigs = [sig("evo", Facet.ADAPTATION, 1.0), sig("v", Facet.VERIFICATION, 1.0)]
        d = interpret(policy(), sigs, KEY)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.NONE)

    def test_high_risk_blocks_adaptation(self):
        sigs = [sig("evo", Facet.ADAPTATION, 1.0), sig("risk", Facet.RISK, 0.9),
                sig("v", Facet.VERIFICATION, 1.0)]
        d = interpret(policy(), sigs, KEY)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.NONE)


if __name__ == "__main__":
    unittest.main()
