"""End-to-end: subsystems publish salience -> interpret() -> directive escalates
the verifier -> governed outcome, driven through the REAL subprocess executor and
host-side observers. This is the whole control loop in one path.
"""

import sys
import tempfile
import unittest
from pathlib import Path

from salienceos.control import FULL, INDEPENDENT, govern, stakes_for
from salienceos.interpreter import (
    AdaptationEligibility,
    Facet,
    SalienceSignal,
    interpret,
    issue_policy,
)
from salienceos.verifier import Reason, Status, Verifier, issue_envelope, issue_receipt
from salienceos.verifier.contract import obligation_id, write_set_value
from salienceos.verifier.envelope import Stakes
from salienceos.verifier.evidence import WorldEvidence
from salienceos.verifier.observers import observe_action, run_supervised, snapshot_tree
from salienceos.verifier.signing import sha256_bytes

PK = b"policy-key"
EK = b"executor-key"
EXEC = "exec-1"
CONTENT = "hello world"


def interp_policy(subject, allow_adapt=False):
    # min_v=0..max_v=3 so risk salience can drive depth across the whole range.
    return issue_policy("p", subject, ("fs.write:project",), 10, 1000, 0, 3, "semantic",
                        allow_adapt, 2, 0.4, False, PK)


def sig(subject, facet, infl, conf=1.0):
    return SalienceSignal("scorer", subject, facet, infl, conf, ())


def run_write(ws, path, content, exit_code=0):
    script = ("import sys,pathlib;"
              "pathlib.Path(sys.argv[1]).write_text(sys.argv[2]);"
              "sys.exit(int(sys.argv[3]))")
    return run_supervised([sys.executable, "-c", script, path, content, str(exit_code)], cwd=ws)


def second_source(eid, content):
    """A genuinely independent second observation channel (distinct failure modes)."""
    h = sha256_bytes(content.encode())
    return [
        WorldEvidence(obligation_id(eid, "exit_status"), "exit_status", "0",
                      "audit_log", "host.audit", "p"),
        WorldEvidence(obligation_id(eid, "write_set"), "write_set", write_set_value(["out.txt"]),
                      "audit_log", "host.audit", "p"),
        WorldEvidence(obligation_id(eid, "artifact_hash", "out.txt"), "artifact_hash", h,
                      "mirror_read", "host.mirror", "p"),
    ]


class ControlLoopE2E(unittest.TestCase):
    def _act(self, eid, directive, content=CONTENT, exit_code=0, corrupt=None, extra_world=()):
        # Each action runs in its own fresh workspace so write-set diffs are clean.
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            env_stakes = stakes_for(directive, Stakes.NORMAL)
            env = issue_envelope(eid, "file.write", {"path": "out.txt", "content": content},
                                 "project_mutation", env_stakes, "p", PK)
            pre = snapshot_tree(ws)
            res = run_write(ws, "out.txt", content, exit_code)
            if corrupt is not None:
                corrupt(ws)
            receipt = issue_receipt(f"r-{eid}", eid, res.returncode,
                                    {"out.txt": sha256_bytes(content.encode())}, ("out.txt",),
                                    True, EXEC, EK)
            world = list(observe_action(env, ws, pre, res)) + list(extra_world)
            return govern(Verifier(PK, {EXEC: EK}), directive, env, receipt, world)

    def test_low_salience_clears_with_one_source(self):
        subject = "act-low"
        d = interpret(interp_policy(subject), [sig(subject, Facet.RISK, 0.0),
                                               sig(subject, Facet.ATTENTION, 0.2)], PK)
        self.assertLessEqual(d.verification_depth, INDEPENDENT)  # low risk -> low depth
        out = self._act(subject, d)
        self.assertTrue(out.cleared)
        self.assertIs(out.verdict.status, Status.VERIFIED)

    def test_high_salience_needs_two_sources(self):
        subject = "act-high"
        d = interpret(interp_policy(subject), [sig(subject, Facet.RISK, 1.0)], PK)
        self.assertEqual(d.verification_depth, FULL)  # high risk -> FULL

        one = self._act(subject + "-1", replace_subject(d, subject + "-1"))
        self.assertFalse(one.cleared)  # one host source can't satisfy two-source
        self.assertIs(one.effective_stakes, Stakes.HIGH)  # escalated by the directive

        eid2 = subject + "-2"
        two = self._act(eid2, replace_subject(d, eid2), extra_world=second_source(eid2, CONTENT))
        self.assertTrue(two.cleared)
        self.assertEqual(two.achieved_level, FULL)

    def test_real_attested_action_clears_at_receipt(self):
        # A low-salience action with an authentic receipt but NO independent world
        # evidence must clear at the LOW envelope's RECEIPT floor — exercised
        # through the REAL verifier (not a hand-built verdict), so the attested
        # reason set is genuine.
        subject = "act-attested"
        d = interpret(interp_policy(subject), [sig(subject, Facet.RISK, 0.0)], PK)
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t)
            env = issue_envelope(subject, "file.write", {"path": "out.txt", "content": CONTENT},
                                 "project_mutation", Stakes.LOW, "p", PK)
            res = run_write(ws, "out.txt", CONTENT)
            receipt = issue_receipt("r-att", subject, res.returncode,
                                    {"out.txt": sha256_bytes(CONTENT.encode())}, ("out.txt",),
                                    True, EXEC, EK)
            out = govern(Verifier(PK, {EXEC: EK}), d, env, receipt, [])  # empty world
        self.assertIs(out.verdict.status, Status.UNVERIFIED)
        self.assertIn(Reason.INTEGRITY_ATTESTED, out.verdict.reasons)
        self.assertEqual(out.achieved_level, 1)  # RECEIPT
        self.assertTrue(out.cleared)
        self.assertFalse(out.adaptation_allowed)  # attested, never VERIFIED

    def test_byte_flip_fails_and_denies_clearance(self):
        subject = "act-flip"
        d = interpret(interp_policy(subject), [sig(subject, Facet.RISK, 0.0)], PK)

        def flip(ws):
            p = ws / "out.txt"
            b = bytearray(p.read_bytes())
            b[0] ^= 0xFF
            p.write_bytes(bytes(b))

        out = self._act(subject, d, corrupt=flip)
        self.assertIs(out.verdict.status, Status.FAILED)
        self.assertFalse(out.cleared)
        self.assertIn("conclusive_failure", out.reasons)

    def test_salience_cannot_lower_envelope_floor(self):
        # Directive asks for NONE verification, but the envelope was signed HIGH.
        # The verifier must still run at HIGH — salience only escalates.
        subject = "act-floor"
        d = interpret(interp_policy(subject), [sig(subject, Facet.RISK, 0.0)], PK)
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t)
            env = issue_envelope(subject, "file.write", {"path": "out.txt", "content": CONTENT},
                                 "project_mutation", Stakes.HIGH, "p", PK)  # policy floor HIGH
            pre = snapshot_tree(ws)
            res = run_write(ws, "out.txt", CONTENT)
            receipt = issue_receipt("r-floor", subject, res.returncode,
                                    {"out.txt": sha256_bytes(CONTENT.encode())}, ("out.txt",),
                                    True, EXEC, EK)
            world = observe_action(env, ws, pre, res)  # only one source
            out = govern(Verifier(PK, {EXEC: EK}), d, env, receipt, world)
        self.assertIs(out.effective_stakes, Stakes.HIGH)  # NOT lowered to NORMAL
        self.assertIsNot(out.verdict.status, Status.VERIFIED)  # HIGH needs two sources
        self.assertFalse(out.cleared)  # HIGH envelope floor forces FULL; one source fails

    def test_escalation_raises_a_normal_envelope(self):
        # F1 killer: a FULL directive with an envelope signed only NORMAL. The
        # ESCALATION (not the envelope) must lift the verifier to HIGH — disabling
        # escalation would leave it at NORMAL and this assertion would fail.
        subject = "act-esc"
        d = interpret(interp_policy(subject), [sig(subject, Facet.RISK, 1.0)], PK)
        self.assertEqual(d.verification_depth, FULL)
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t)
            env = issue_envelope(subject, "file.write", {"path": "out.txt", "content": CONTENT},
                                 "project_mutation", Stakes.NORMAL, "p", PK)  # NOT HIGH
            pre = snapshot_tree(ws)
            res = run_write(ws, "out.txt", CONTENT)
            receipt = issue_receipt("r-esc", subject, res.returncode,
                                    {"out.txt": sha256_bytes(CONTENT.encode())}, ("out.txt",),
                                    True, EXEC, EK)
            world = observe_action(env, ws, pre, res)  # one source
            out = govern(Verifier(PK, {EXEC: EK}), d, env, receipt, world)
        self.assertIs(out.effective_stakes, Stakes.HIGH)          # escalation raised NORMAL -> HIGH
        self.assertIsNot(out.verdict.status, Status.VERIFIED)     # one source can't satisfy HIGH
        self.assertFalse(out.cleared)

    def test_escalate_to_raises_but_never_lowers(self):
        # Direct verifier-level proof of the escalate_to plumbing.
        subject = "act-esc2"
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t)
            env = issue_envelope(subject, "file.write", {"path": "out.txt", "content": CONTENT},
                                 "project_mutation", Stakes.NORMAL, "p", PK)
            pre = snapshot_tree(ws)
            res = run_write(ws, "out.txt", CONTENT)
            receipt = issue_receipt("r-esc2", subject, res.returncode,
                                    {"out.txt": sha256_bytes(CONTENT.encode())}, ("out.txt",),
                                    True, EXEC, EK)
            world = observe_action(env, ws, pre, res)  # one source
            # NORMAL, one source -> VERIFIED with no escalation...
            self.assertIs(Verifier(PK, {EXEC: EK}).verify(env, receipt, world).status,
                          Status.VERIFIED)
            # ...but escalated to HIGH the same evidence is insufficient (two-source).
            self.assertIsNot(
                Verifier(PK, {EXEC: EK}).verify(env, receipt, world, escalate_to=Stakes.HIGH).status,
                Status.VERIFIED)
        with tempfile.TemporaryDirectory() as t:
            ws = Path(t)
            envH = issue_envelope("act-esc3", "file.write", {"path": "out.txt", "content": CONTENT},
                                  "project_mutation", Stakes.HIGH, "p", PK)
            pre = snapshot_tree(ws)
            res = run_write(ws, "out.txt", CONTENT)
            receipt = issue_receipt("r-esc3", "act-esc3", res.returncode,
                                    {"out.txt": sha256_bytes(CONTENT.encode())}, ("out.txt",),
                                    True, EXEC, EK)
            world = observe_action(envH, ws, pre, res)  # one source
            # escalate_to=LOW must NOT lower a HIGH envelope: still needs two sources.
            self.assertIsNot(
                Verifier(PK, {EXEC: EK}).verify(envH, receipt, world, escalate_to=Stakes.LOW).status,
                Status.VERIFIED)

    def test_adaptation_only_when_eligible_and_verified(self):
        subject = "act-adapt"
        # allow_adaptation, low risk, adaptation + verification signals -> CANDIDATE.
        d = interpret(interp_policy(subject, allow_adapt=True),
                      [sig(subject, Facet.ADAPTATION, 1.0), sig(subject, Facet.RISK, 0.1),
                       sig(subject, Facet.VERIFICATION, 1.0)], PK)
        self.assertIs(d.adaptation_eligibility, AdaptationEligibility.CANDIDATE)
        # depth is FULL (verification signal 1.0) -> needs two sources to VERIFY.
        eid = subject
        out = self._act(eid, d, extra_world=second_source(eid, CONTENT))
        self.assertTrue(out.cleared)
        self.assertTrue(out.adaptation_allowed)


def replace_subject(directive, subject):
    # A directive is per-subject; re-key it to a fresh envelope id for a second run.
    return type(directive)(**{**directive.__dict__, "subject": subject})


if __name__ == "__main__":
    unittest.main()
