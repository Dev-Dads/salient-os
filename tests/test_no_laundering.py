"""The load-bearing invariant: salience influences; policy authorizes (P-01).

These are the interpreter's leak-locks — the analog of the verifier's CLAIM/WORLD
fence and sealed gate. Each test is written to FAIL if a future refactor let a
signal reach authority.
"""

import unittest

from salienceos.interpreter import (
    AdaptationEligibility,
    Facet,
    SalienceSignal,
    interpret,
    issue_policy,
)

KEY = b"policy-test-key"


def policy(caps=("fs.read:project",), allow_adapt=False, adapt_max_risk=0.4):
    return issue_policy("pol-1", "req-1", caps, 10, 1000, 0, 3, "semantic",
                        allow_adapt, 2, adapt_max_risk, False, KEY)


def sig(facet, influence, confidence=1.0, subsystem="x"):
    return SalienceSignal(subsystem, "req-1", facet, influence, confidence, ())


class CapabilitiesComeOnlyFromPolicy(unittest.TestCase):
    def test_allowed_capabilities_equal_policy_grant(self):
        caps = ("fs.read:project", "shell.exec:test")
        d = interpret(policy(caps=caps), [sig(Facet.ATTENTION, 0.5)], KEY)
        self.assertEqual(d.allowed_capabilities, caps)

    def test_maxed_out_signals_never_add_a_capability(self):
        base = interpret(policy(), [], KEY).allowed_capabilities
        # Every facet cranked to 1.0, including facets that name capability-like
        # strings — none of it can widen authority.
        flood = [
            sig(Facet.ATTENTION, 1.0), sig(Facet.VERIFICATION, 1.0),
            sig(Facet.MEMORY, 1.0), sig(Facet.RISK, 0.0), sig(Facet.ADAPTATION, 1.0),
            sig("shell.exec:root", 1.0), sig("fs.write:/", 1.0), sig("host_admin", 1.0),
        ]
        d = interpret(policy(), flood, KEY)
        self.assertEqual(d.allowed_capabilities, base)
        self.assertEqual(d.allowed_capabilities, ("fs.read:project",))

    def test_signal_cannot_grant_capability_not_in_policy(self):
        d = interpret(policy(caps=()), [sig("fs.write:/etc", 1.0)], KEY)
        self.assertEqual(d.allowed_capabilities, ())
        self.assertFalse(d.grants_capability("fs.write:/etc"))

    def test_signal_type_has_no_authority_field(self):
        # Structural: a SalienceSignal cannot even express a capability/scope.
        fields = set(SalienceSignal.__dataclass_fields__)
        for forbidden in ("capability", "capabilities", "scope", "grant", "authority", "allow"):
            self.assertNotIn(forbidden, fields)


class AdaptationNeedsPolicySwitch(unittest.TestCase):
    def test_no_adaptation_without_policy_allow(self):
        # allow_adapt=False; even the perfect adaptation case cannot become eligible.
        sigs = [sig(Facet.ADAPTATION, 1.0), sig(Facet.RISK, 0.0), sig(Facet.VERIFICATION, 1.0)]
        d = interpret(policy(allow_adapt=False), sigs, KEY)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.NONE)

    def test_eligibility_never_exceeds_candidate(self):
        sigs = [sig(Facet.ADAPTATION, 1.0), sig(Facet.RISK, 0.0), sig(Facet.VERIFICATION, 1.0)]
        d = interpret(policy(allow_adapt=True), sigs, KEY)
        # The enum has no "promoted"/"live" member; the strongest reachable state
        # is CANDIDATE (no live self-modification, ever).
        self.assertIn(d.adaptation_eligibility,
                      (AdaptationEligibility.NONE, AdaptationEligibility.CANDIDATE))
        self.assertEqual({e.name for e in AdaptationEligibility}, {"NONE", "CANDIDATE"})


if __name__ == "__main__":
    unittest.main()
