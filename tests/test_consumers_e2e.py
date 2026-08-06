"""End-to-end for build stage 4: salience -> interpret -> real subprocess
action -> govern -> consume. The crux is the DISAGREEMENT scenario (Finding C /
docx §4.4): the same high-salience high-risk event is RETAINED by the memory
channel (semantic class, pinned as an inhibitor, weight flat across a decade)
and HARD BLOCKED by the weight channel — two records, one event, opposite
answers. The RISK 0.1 twin makes the disagreement mutation-honest in both
directions. Everything flows through `consume()` — the seam is the only path
exercised here, so a reordering or dropped hand-off inside it goes red.
"""

import sys
import tempfile
import unittest
from pathlib import Path

from salienceos.consumers import consume, effective_weight
from salienceos.control import FULL, govern, stakes_for
from salienceos.interpreter import (
    AdaptationEligibility,
    AdaptationRationale,
    Facet,
    SalienceSignal,
    interpret,
    issue_policy,
)
from salienceos.verifier import Status, Verifier, issue_envelope, issue_receipt
from salienceos.verifier.contract import obligation_id, write_set_value
from salienceos.verifier.envelope import Stakes
from salienceos.verifier.evidence import WorldEvidence
from salienceos.verifier.observers import observe_action, run_supervised, snapshot_tree
from salienceos.verifier.signing import sha256_bytes

PK = b"policy-key"
EK = b"executor-key"
EXEC = "exec-1"
CONTENT = "hello world"
NOW = 1000.0


def interp_policy(subject):
    # Adaptation is allowed by policy so the RISK facet alone decides the
    # channel disagreement; cap 0.4 makes 0.9 an asserted over-cap risk.
    return issue_policy("p", subject, ("fs.write:project",), 10, 1000, 0, 3,
                        "semantic", True, 2, 0.4, False, PK)


def sig(subject, facet, infl, conf=1.0):
    return SalienceSignal("scorer", subject, facet, infl, conf, ())


def signals(subject, risk):
    return [
        sig(subject, Facet.MEMORY, 0.9),        # this matters: retain durably
        sig(subject, Facet.RISK, risk),         # the fork in the road
        sig(subject, Facet.ADAPTATION, 1.0),    # learning is requested
        sig(subject, Facet.VERIFICATION, 1.0),  # depth to FULL in both arms
    ]


def run_write(ws, path, content, exit_code=0):
    script = ("import sys,pathlib;"
              "pathlib.Path(sys.argv[1]).write_text(sys.argv[2]);"
              "sys.exit(int(sys.argv[3]))")
    return run_supervised([sys.executable, "-c", script, path, content, str(exit_code)],
                          cwd=ws)


def second_source(eid, content):
    h = sha256_bytes(content.encode())
    return [
        WorldEvidence(obligation_id(eid, "exit_status"), "exit_status", "0",
                      "audit_log", "host.audit", "p"),
        WorldEvidence(obligation_id(eid, "write_set"), "write_set",
                      write_set_value(["out.txt"]), "audit_log", "host.audit", "p"),
        WorldEvidence(obligation_id(eid, "artifact_hash", "out.txt"), "artifact_hash",
                      h, "mirror_read", "host.mirror", "p"),
    ]


def act(eid, directive):
    """One real action: subprocess write, honest receipt, host observers plus
    an independent second source (high stakes need two channels)."""
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        env = issue_envelope(eid, "file.write", {"path": "out.txt", "content": CONTENT},
                             "project_mutation", stakes_for(directive, Stakes.NORMAL),
                             "p", PK)
        pre = snapshot_tree(ws)
        res = run_write(ws, "out.txt", CONTENT)
        receipt = issue_receipt(f"r-{eid}", eid, res.returncode,
                                {"out.txt": sha256_bytes(CONTENT.encode())},
                                ("out.txt",), True, EXEC, EK)
        world = list(observe_action(env, ws, pre, res)) + second_source(eid, CONTENT)
        return govern(Verifier(PK, {EXEC: EK}), directive, env, receipt, world)


class DisagreementE2E(unittest.TestCase):
    def test_high_risk_retains_as_inhibitor_and_blocks_weights(self):
        subject = "act-hot"
        d = interpret(interp_policy(subject), signals(subject, risk=0.9), PK)

        # The interpreter's recorded decision: durable retention, full
        # verification, adaptation denied on an ASSERTED over-cap risk.
        self.assertEqual(d.retention_class, "semantic")
        self.assertEqual(d.verification_depth, FULL)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.NONE)
        self.assertIs(d.adaptation_rationale, AdaptationRationale.RISK_EXCEEDED)

        out = act(subject, d)
        self.assertTrue(out.cleared)               # the action itself verified
        self.assertIs(out.verdict.status, Status.VERIFIED)
        self.assertFalse(out.adaptation_allowed)   # but nothing may be learned

        decision, retention = consume(out, NOW)

        # Weight channel: HARD BLOCK, with the hand-off record itself.
        self.assertFalse(decision.nominated)
        self.assertIsNotNone(decision.handoff)
        self.assertEqual(decision.handoff.subject, subject)
        self.assertEqual(decision.handoff.source, "adaptation.risk_reject")

        # Memory channel: RETAIN — semantic class, pinned as an inhibitor.
        self.assertEqual(retention.retention_class, "semantic")
        self.assertTrue(retention.inhibitor)
        self.assertTrue(retention.cleared)
        # The pin: a DECADE of decay changes nothing.
        self.assertEqual(effective_weight(retention, NOW),
                         effective_weight(retention, NOW + 3650.0))

    def test_low_risk_twin_nominates_and_decays(self):
        # Identical except RISK 0.1: the channels now AGREE — mutation-honest
        # in both directions (forcing inhibitors on refusal, or blocking
        # nomination outright, reds one arm or the other).
        subject = "act-cool"
        d = interpret(interp_policy(subject), signals(subject, risk=0.1), PK)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.CANDIDATE)
        self.assertIs(d.adaptation_rationale, AdaptationRationale.ELIGIBLE)

        out = act(subject, d)
        self.assertTrue(out.cleared)
        self.assertTrue(out.adaptation_allowed)

        decision, retention = consume(out, NOW)
        self.assertTrue(decision.nominated)        # weight: nominated (offline review)
        self.assertIsNone(decision.handoff)
        self.assertFalse(retention.inhibitor)      # memory: normal retention...
        self.assertLess(effective_weight(retention, NOW + 180.0),
                        effective_weight(retention, NOW))  # ...that decays


if __name__ == "__main__":
    unittest.main()
