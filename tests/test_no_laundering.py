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
from salienceos.interpreter.directive import (
    AdaptationRationale,
    Directive,
    Reconfigure,
)
from salienceos.interpreter.policy import (
    RESERVED_UNGRANTABLE_PREFIXES,
    is_ungrantable_capability,
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


class ProhibitedNamespaceIsUngrantable(unittest.TestCase):
    """ADR 0004 (ADR 0003 revisit #4): the prohibited class (`offense:`) is un-grantable BY
    CONSTRUCTION in core — P-01's sibling. No signed policy carries it and no directive grants it,
    so an authorized-offense capability cannot be minted in band; Tier 3 stays locked by core."""

    def test_recognizer_matrix(self):
        for cap in ("offense:example.com", "offense:", "offense:1.2.3.4",
                    "OFFENSE:example.com", "Offense:x",     # case-insensitive
                    "ｏｆｆｅｎｓｅ：x"):  # full-width ｏｆｆｅｎｓｅ： (NFKC, gemini)
            self.assertTrue(is_ungrantable_capability(cap), cap)
        for cap in ("net.get:example.com", "fs.read:project", "shell.exec", "offense",
                    "offensive", "offense_shape", "", "not.offense:x"):
            self.assertFalse(is_ungrantable_capability(cap), cap)
        for junk in (None, 123, ("offense:x",), b"offense:x"):   # a non-str is not this namespace
            self.assertFalse(is_ungrantable_capability(junk), repr(junk))

    def test_issue_policy_strips_the_prohibited_namespace(self):
        # A signed envelope never carries an offense: cap — it is stripped before signing; the legit
        # capability alongside it survives and still grants.
        d = interpret(policy(caps=("fs.read:project", "offense:evil.com")),
                      [sig(Facet.ATTENTION, 1.0)], KEY)
        self.assertNotIn("offense:evil.com", d.allowed_capabilities)
        self.assertEqual(d.allowed_capabilities, ("fs.read:project",))
        self.assertFalse(d.grants_capability("offense:evil.com"))
        self.assertTrue(d.grants_capability("fs.read:project"))

    def test_grants_capability_refuses_offense_even_if_present_in_allowed(self):
        # THE load-bearing structural guarantee: even a HAND-BUILT / mis-wired directive that somehow
        # lists an offense: capability cannot grant it — grants_capability refuses the namespace
        # unconditionally, before the membership check. Nothing downstream of a directive can authorize
        # the prohibited class.
        d = Directive(
            subject="s", policy_id="p", compute_budget=10, verification_depth=3,
            retention_class="semantic", routing_hint="", adaptation_eligibility=AdaptationEligibility.NONE,
            adaptation_rationale=AdaptationRationale.NOT_REQUESTED,
            allowed_capabilities=("offense:evil.com", "fs.read:project"),
            reconfigure=Reconfigure.BETWEEN_TURN, interpreter_version="test",
        )
        self.assertIn("offense:evil.com", d.allowed_capabilities)   # it IS present in the tuple...
        self.assertFalse(d.grants_capability("offense:evil.com"))   # ...yet it is NOT granted (structural)
        self.assertTrue(d.grants_capability("fs.read:project"))     # the legit one is unaffected

    def test_reserved_prefix_tuple_is_the_single_source(self):
        # Pin the namespace so a future edit that empties it (fail-open) is caught.
        self.assertIn("offense:", RESERVED_UNGRANTABLE_PREFIXES)

    def test_issue_policy_is_total_on_malformed_capabilities(self):
        # gemini FRAG-01: a malformed granted_capabilities must fail closed (no capabilities), never
        # raise at this boundary. None and a bare str (which would iterate into characters) both -> ().
        from salienceos.interpreter import issue_policy
        for bad in (None, "fs.read:project"):
            p = issue_policy("p", "s", bad, 10, 1000, 0, 3, "semantic", False, 2, 0.4, False, KEY)
            self.assertEqual(p.granted_capabilities, ())


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
