"""② The judgment view: a read-only view of what the Collaborator is attending to,
running, and proposing, plus HOST-authority controls (leash / proactivity / pause /
veto) that steer a job without typing. The controls are restrictive or the host's own
setting — never a way to grant the model new authority.
"""

import json
import tempfile
import unittest
from pathlib import Path

from collaborator.governance import DENIED, HELD, PAUSED, RAN, govern_action
from collaborator.loop import run_turn
from collaborator.model_client import ScriptedClient
from collaborator.propose import PROPOSED, VETOED, propose
from collaborator.session import Session
from collaborator.toolcall import ToolIntent
from collaborator.view import (
    JudgmentLedger,
    JudgmentView,
    approve,
    pause,
    resume,
    set_leash,
    set_proactivity,
    veto,
)


def _wi(path="a.txt", content="x"):
    return ToolIntent("write_file", {"path": path, "content": content}, "structured")


def _prop_resp(confidence=0.9, path="new.txt", content="hi"):
    return {"content": json.dumps(
        {"propose": True, "confidence": confidence, "rationale": "worth doing",
         "action": {"name": "write_file", "arguments": {"path": path, "content": content}}}),
        "tool_calls": None}


class Ledger(unittest.TestCase):
    def test_records_decisions_and_proposals(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            led = JudgmentLedger()
            led.record_decision(govern_action(s, _wi()))
            led.record_proposals(propose(s, ScriptedClient([_prop_resp()]), "ctx"))
            led.record_decision(None)  # tolerated, ignored
            self.assertEqual(len(led.decisions), 1)
            self.assertEqual(len(led.proposals), 1)


class Controls(unittest.TestCase):
    def test_set_leash_tightens_and_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            self.assertEqual(govern_action(s, _wi("a.txt")).status, RAN)  # default act-then-report
            self.assertTrue(set_leash(s, "write_file", "propose_first"))
            self.assertEqual(govern_action(s, _wi("b.txt")).status, HELD)  # now held

    def test_invalid_leash_rejected_no_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            self.assertFalse(set_leash(s, "write_file", "loose"))
            self.assertEqual(govern_action(s, _wi("c.txt")).status, RAN)  # unchanged

    def test_pause_holds_then_resume_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            pause(s)
            self.assertEqual(govern_action(s, _wi("a.txt")).status, PAUSED)
            self.assertFalse((Path(tmp) / "a.txt").exists())  # nothing ran
            resume(s)
            self.assertEqual(govern_action(s, _wi("b.txt")).status, RAN)

    def test_set_proactivity_valid_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            self.assertTrue(set_proactivity(s, "eager"))
            self.assertEqual(s.proactivity, "eager")
            self.assertFalse(set_proactivity(s, "aggressive"))
            self.assertEqual(s.proactivity, "eager")  # unchanged

    def test_veto_and_approve_via_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            led = JudgmentLedger()
            p1 = propose(s, ScriptedClient([_prop_resp(path="v.txt")]), "ctx")[0]
            led.record_proposal(p1)
            veto(s, led, p1)
            self.assertEqual(p1.status, VETOED)
            self.assertEqual(approve(s, led, p1).status, HELD)  # vetoed -> never runs
            self.assertFalse((Path(tmp) / "v.txt").exists())

            p2 = propose(s, ScriptedClient([_prop_resp(path="ok.txt")]), "ctx")[0]
            led.record_proposal(p2)
            self.assertEqual(approve(s, led, p2).status, RAN)  # approved -> runs
            self.assertTrue((Path(tmp) / "ok.txt").exists())


class Loop(unittest.TestCase):
    def test_paused_session_halts_run_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            pause(s)
            client = ScriptedClient([{"content": None,
                "tool_calls": [{"name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}]}])
            res = run_turn(s, client, "do it")
            self.assertEqual(res.stopped, "paused")
            self.assertEqual(res.decisions[0].status, PAUSED)
            self.assertFalse((Path(tmp) / "a.txt").exists())


class Snapshot(unittest.TestCase):
    def test_snapshot_reflects_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager",
                        capabilities=("fs.read:project", "fs.write:project", "shell.exec"))
            led = JudgmentLedger()
            led.record_decision(govern_action(s, _wi("a.txt")))
            led.record_proposal(propose(s, ScriptedClient([_prop_resp()]), "ctx")[0])
            snap = JudgmentView(s, led).snapshot()
            self.assertEqual(snap["proactivity"], "eager")
            self.assertIn("shell.exec", snap["capabilities"])
            self.assertEqual(snap["counts"]["ran"], 1)
            self.assertEqual(snap["counts"]["proposals_pending"], 1)
            self.assertEqual(snap["leashes"]["run_command"], "propose_first")

    def test_render_html_is_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            led = JudgmentLedger()
            led.record_decision(govern_action(s, _wi("a.txt")))
            out = JudgmentView(s, led).render_html()
            self.assertIn("Judgment View", out)
            self.assertNotIn("http://", out)   # no external assets
            self.assertNotIn("https://", out)
            self.assertNotIn("<script", out)   # no JS


class P01(unittest.TestCase):
    def test_controls_never_grant_capability(self):
        # Pause holds an ungranted action; after resume it is still DENIED — no control
        # ever added the capability. Controls change scrutiny/whether to proceed, not
        # what a capability permits.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)  # no shell.exec
            pause(s)
            rc = ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured")
            self.assertEqual(govern_action(s, rc).status, PAUSED)
            resume(s)
            self.assertEqual(govern_action(s, rc).status, DENIED)  # still ungranted


if __name__ == "__main__":
    unittest.main()
