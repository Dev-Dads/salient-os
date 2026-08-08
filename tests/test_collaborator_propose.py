"""① The propose channel: the Collaborator originates a governed proposal for the host
to approve/veto. The tests pin the safety spine — **surfacing grants no authority** —
and the proactivity dial, the per-task leash, and fail-closed behaviour.
"""

import json
import tempfile
import unittest
from pathlib import Path

from collaborator.governance import DENIED, HELD, RAN, govern_action
from collaborator.model_client import ScriptedClient
from collaborator.propose import (
    APPROVED,
    PROPOSED,
    VETOED,
    approve_proposal,
    propose,
    veto_proposal,
)
from collaborator.session import Session
from collaborator.toolcall import ToolIntent


def _resp(propose=True, confidence=0.9, rationale="worth doing",
          name="write_file", args=None):
    payload = {"propose": propose}
    if propose:
        payload.update(confidence=confidence, rationale=rationale,
                       action={"name": name, "arguments": args if args is not None else {}})
    return {"content": json.dumps(payload), "tool_calls": None}


def _write_resp(confidence=0.9, path="todo.txt", content="draft\nplan\n"):
    return _resp(confidence=confidence, name="write_file",
                 args={"path": path, "content": content})


class _BoomClient:
    def complete(self, messages, tools=None):
        raise RuntimeError("model down")


class Dial(unittest.TestCase):
    def test_off_is_dormant(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="off")
            self.assertEqual(propose(s, ScriptedClient([_write_resp(0.99)]), "ctx"), [])

    def test_conservative_suppresses_low_surfaces_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            self.assertEqual(propose(s, ScriptedClient([_write_resp(0.5)]), "ctx"), [])
            got = propose(s, ScriptedClient([_write_resp(0.9)]), "ctx")
            self.assertEqual(len(got), 1)

    def test_eager_surfaces_mid_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            self.assertEqual(len(propose(s, ScriptedClient([_write_resp(0.5)]), "ctx")), 1)

    def test_missing_confidence_is_zero_not_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            r = {"content": json.dumps({"propose": True, "rationale": "x",
                 "action": {"name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}})}
            self.assertEqual(propose(s, ScriptedClient([r]), "ctx"), [])


class SurfacingGrantsNothing(unittest.TestCase):
    def test_ungranted_capability_proposal_is_never_surfaced(self):
        # P-01: a run_command proposal without shell.exec is DENIED at origination and
        # dropped — importance/confidence cannot buy it a surface (or a run).
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")  # no shell.exec
            got = propose(s, ScriptedClient(
                [_resp(confidence=0.99, name="run_command", args={"command": ["echo", "hi"]})]), "ctx")
            self.assertEqual(got, [])

    def test_escaping_path_proposal_is_never_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            got = propose(s, ScriptedClient([_write_resp(0.99, path="../evil.txt")]), "ctx")
            self.assertEqual(got, [])
            self.assertFalse((Path(tmp).parent / "evil.txt").exists())

    def test_surfaced_proposal_is_inert_until_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="t.txt")]), "ctx")
            self.assertEqual(got[0].decision.status, HELD)
            self.assertFalse((Path(tmp) / "t.txt").exists())  # nothing ran

    def test_approval_runs_through_full_governance(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="t.txt", content="a\nb\n")]), "ctx")
            d = approve_proposal(s, got[0])
            self.assertEqual(d.status, RAN)
            self.assertTrue(d.cleared)
            self.assertEqual((Path(tmp) / "t.txt").read_bytes(), b"a\nb\n")
            self.assertEqual(got[0].status, APPROVED)


class VetoAndDoubleRun(unittest.TestCase):
    def test_veto_runs_nothing_and_blocks_later_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="t.txt")]), "ctx")
            veto_proposal(s, got[0])
            self.assertEqual(got[0].status, VETOED)
            d = approve_proposal(s, got[0])          # must NOT run a vetoed proposal
            self.assertEqual(d.status, HELD)
            self.assertFalse((Path(tmp) / "t.txt").exists())

    def test_no_double_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="t.txt")]), "ctx")
            d1 = approve_proposal(s, got[0])
            self.assertEqual(d1.status, RAN)
            (Path(tmp) / "t.txt").unlink()           # tamper
            d2 = approve_proposal(s, got[0])         # already approved -> not run again
            self.assertEqual(d2.status, HELD)
            self.assertFalse((Path(tmp) / "t.txt").exists())


class FailClosed(unittest.TestCase):
    def test_model_error_yields_no_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            self.assertEqual(propose(s, _BoomClient(), "ctx"), [])

    def test_malformed_and_declined_yield_no_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            self.assertEqual(propose(s, ScriptedClient([{"content": "not json"}]), "ctx"), [])
            self.assertEqual(propose(s, ScriptedClient(
                [{"content": json.dumps({"propose": False})}]), "ctx"), [])


class PerTaskLeash(unittest.TestCase):
    def test_leash_override_forces_held(self):
        # write_file's tool default is act_then_report; a per-task propose_first holds it.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            d = govern_action(s, ToolIntent("write_file", {"path": "a.txt", "content": "x"},
                                            "structured"), leash="propose_first")
            self.assertEqual(d.status, HELD)
            self.assertFalse((Path(tmp) / "a.txt").exists())

    def test_invalid_leash_fails_closed_to_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            d = govern_action(s, ToolIntent("write_file", {"path": "b.txt", "content": "x"},
                                            "structured"), leash="garbage")
            self.assertEqual(d.status, HELD)  # never runs unleashed on a bad value
            self.assertFalse((Path(tmp) / "b.txt").exists())

    def test_omitted_leash_preserves_default_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            d = govern_action(s, ToolIntent("write_file", {"path": "c.txt", "content": "x"},
                                            "structured"))
            self.assertEqual(d.status, RAN)  # act_then_report default unchanged


class ReGateAndProvenance(unittest.TestCase):
    """The panel's real findings pinned: approval re-checks authority against the CURRENT
    session (TOCTOU), confidence never feeds salience, and provenance is recorded."""

    def test_approval_re_gates_capability_toctou(self):
        # A proposal surfaces while fs.write:project is granted; the capability is then
        # revoked; approving it must DENY (not run on the stale origination directive).
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")  # has fs.write:project
            got = propose(s, ScriptedClient([_write_resp(0.9, path="t.txt")]), "ctx")
            self.assertEqual(got[0].decision.status, HELD)
            s.capabilities = ("fs.read:project",)  # revoke fs.write:project after surfacing
            d = approve_proposal(s, got[0])
            self.assertEqual(d.status, DENIED)
            self.assertFalse((Path(tmp) / "t.txt").exists())

    def test_confidence_does_not_feed_salience(self):
        # Two proposals, very different confidence, both surfaced under EAGER. The governed
        # directive's verification depth must be identical — confidence is NOT importance.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            lo = propose(s, ScriptedClient([_write_resp(0.5, path="a.txt")]), "ctx")[0]
            hi = propose(s, ScriptedClient([_write_resp(0.99, path="b.txt")]), "ctx")[0]
            self.assertEqual(lo.decision.directive.verification_depth,
                             hi.decision.directive.verification_depth)

    def test_origin_provenance_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="t.txt")]), "ctx")
            self.assertEqual(got[0].decision.origin, "collaborator")
            ran = approve_proposal(s, got[0])
            self.assertEqual(ran.origin, "collaborator")
            direct = govern_action(
                s, ToolIntent("write_file", {"path": "d.txt", "content": "x"}, "structured"))
            self.assertEqual(direct.origin, "direct")  # a user-turn action is "direct"

    def test_leash_is_keyword_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            with self.assertRaises(TypeError):  # leash can't be threaded positionally
                govern_action(s, ToolIntent("write_file", {"path": "a.txt", "content": "x"},
                                            "structured"), 0.3, 0.1, "propose_first")


class SessionConfig(unittest.TestCase):
    def test_bad_proactivity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                Session(workspace=tmp, proactivity="aggressive")


if __name__ == "__main__":
    unittest.main()
