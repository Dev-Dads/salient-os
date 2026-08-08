"""Stage-4-live: a real risky+important action through the collaborator host trips
the already-built two-channel disagreement — weight gate HARD BLOCKS the skill while
the memory governor RETAINS it as a non-decaying inhibitor. The gate itself lives in
salienceos/consumers/ (built + tested); this proves it fires end to end on a real
governed action, and stays dormant when the host disallows adaptation."""

import sys
import tempfile
import unittest
from pathlib import Path

from collaborator.governance import govern_action
from collaborator.session import Session
from collaborator.toolcall import ToolIntent
from salienceos.consumers import effective_weight
from salienceos.interpreter import AdaptationRationale, Facet


def _risky_important_write(session):
    return govern_action(
        session,
        ToolIntent("write_file", {"path": "incident.txt", "content": "risky important change"}, "structured"),
        importance=0.9, risk=0.9,  # over the 0.4 adaptation risk cap -> RISK_EXCEEDED
    )


class DisagreementFiresLive(unittest.TestCase):
    def test_weight_blocks_and_memory_inhibits(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, allow_adaptation=True)
            d = _risky_important_write(s)
            self.assertTrue(d.disagreement, "the two channels must disagree on a risky+important action")
            # weight channel: refused to learn AND originated the inhibitor hand-off
            self.assertFalse(d.adaptation.nominated)
            self.assertIs(d.adaptation.rationale, AdaptationRationale.RISK_EXCEEDED)
            self.assertIsNotNone(d.adaptation.handoff)
            # memory channel: retained as an inhibitor (an incident/warning record)
            self.assertTrue(d.memory.inhibitor)
            # the action really happened (file written) even though it wasn't verified
            self.assertTrue((Path(tmp) / "incident.txt").exists())

    def test_inhibitor_never_decays(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, allow_adaptation=True)
            d = _risky_important_write(s)
            near = effective_weight(d.memory, 0.0)
            far = effective_weight(d.memory, 100_000.0)
            self.assertEqual(near, far)  # the pin: a warning that never fades

    def test_summary_reports_the_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, allow_adaptation=True)
            d = _risky_important_write(s)
            self.assertIn("channels disagree", d.summary())


class DormantOtherwise(unittest.TestCase):
    def test_low_risk_action_is_not_inhibited(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, allow_adaptation=True)
            d = govern_action(s, ToolIntent("write_file", {"path": "safe.txt", "content": "ok"}, "structured"),
                              importance=0.5, risk=0.0)
            self.assertFalse(d.disagreement)
            self.assertFalse(d.memory.inhibitor)

    def test_adaptation_off_produces_no_learning_records(self):
        # allow_adaptation defaults False -> host-dormant: no ADAPTATION signal,
        # no consume, no inhibitors at all.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            d = _risky_important_write(s)
            self.assertIsNone(d.adaptation)
            self.assertIsNone(d.memory)
            self.assertFalse(d.disagreement)


class FailSafeAndScope(unittest.TestCase):
    """The panel's two real findings: a consume() failure must SURFACE (never
    silently drop an inhibitor), and the ADAPTATION signal fires only where it can
    be honored."""

    def test_consume_failure_surfaces_learning_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, allow_adaptation=True)
            s.now_days = -1.0  # bypass the constructor guard -> the memory gate rejects it
            d = _risky_important_write(s)
            self.assertIsNotNone(d.learning_error)   # not silently swallowed
            self.assertFalse(d.disagreement)         # don't claim a disagreement we couldn't compute
            self.assertIn("LEARNING ERROR", d.summary())

    def test_session_rejects_bad_now_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                Session(workspace=tmp, now_days=-5.0)
            with self.assertRaises(ValueError):
                Session(workspace=tmp, now_days=float("nan"))

    def test_adaptation_signal_only_for_consumable_tools(self):
        # run_command is exit-mode (no GovernedOutcome to consume) -> it must NOT
        # emit an ADAPTATION signal it can't honor; write_file (artifact) does.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, capabilities=("fs.read:project", "fs.write:project", "shell.exec"),
                        allow_adaptation=True, leash_overrides={"run_command": "act_then_report"})
            d = govern_action(s, ToolIntent("run_command", {"command": [sys.executable, "-c", "pass"]}, "structured"),
                              importance=0.9, risk=0.9)
            facets = [sig.facet for sig in s.bus.signals_for(d.action_id)]
            self.assertNotIn(Facet.ADAPTATION, facets)
            self.assertIsNone(d.adaptation)
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, allow_adaptation=True)
            d = _risky_important_write(s)
            facets = [sig.facet for sig in s.bus.signals_for(d.action_id)]
            self.assertIn(Facet.ADAPTATION, facets)


if __name__ == "__main__":
    unittest.main()
