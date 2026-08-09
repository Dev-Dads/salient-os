"""The staging subsystem: hard-deny-and-stage for controlled locations + the proposal pool.

Two guarantees are pinned here:

* **Controlled-location hard-deny** — a self-originated PROPOSER write into a controlled
  location (default ``.github/**`` — CI/hooks, repo-level authority) is refused so the
  proposer stages to scratch instead. The deny is keyed on the proposer ORIGIN, so a
  user-directed / approved placement is deliberately unaffected (the Collaborator still
  executes an approved action there — "the proposer proposes; the Collaborator executes").

* **The proposal stage pool** — a surfaced-but-undecided proposal is never lost: it stays
  PENDING and findable until explicitly approved/vetoed, so nothing falls through the cracks.
"""

import json
import tempfile
import unittest
from pathlib import Path

from collaborator.governance import HELD, RAN, govern_action
from collaborator.model_client import ScriptedClient
from collaborator.propose import approve_proposal, propose, veto_proposal
from collaborator.proposalpool import ProposalPool
from collaborator.session import Session
from collaborator.toolcall import ToolIntent
from collaborator.tools import is_controlled_location


def _write_resp(confidence=0.9, path="todo.txt", content="draft\n"):
    return {"content": json.dumps(
        {"propose": True, "confidence": confidence, "rationale": "worth doing",
         "action": {"name": "write_file", "arguments": {"path": path, "content": content}}}),
        "tool_calls": None}


class ControlledLocationDeny(unittest.TestCase):
    def test_proposer_write_into_dot_github_is_denied_and_not_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            got = propose(s, ScriptedClient([_write_resp(0.9, path=".github/workflows/ci.yml")]), "ctx")
            self.assertEqual(got, [])  # refused at origination -> never surfaced
            self.assertFalse((Path(tmp) / ".github").exists())  # nothing created
            self.assertEqual(s.proposal_pool.pending_count(), 0)  # a denied proposal is not pooled

    def test_proposer_write_into_scratch_is_surfaced(self):
        # The stage path the proposer is meant to take instead: reachable scratch is HELD.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="staged/ci.yml")]), "ctx")
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].decision.status, HELD)

    def test_user_directed_write_into_controlled_location_is_NOT_denied(self):
        # The deny is proposer-scoped: a user-directed action (source != "proposed") places
        # into the controlled location and runs — the Collaborator executes an approved act.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            d = govern_action(s, ToolIntent(
                "write_file", {"path": ".github/workflows/ci.yml", "content": "on: push\n"},
                "structured"))
            self.assertEqual(d.status, RAN)
            self.assertTrue(d.cleared)
            self.assertEqual((Path(tmp) / ".github" / "workflows" / "ci.yml").read_text(), "on: push\n")

    def test_controlled_paths_config_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager", controlled_paths=("deploy",))
            denied = propose(s, ScriptedClient([_write_resp(0.9, path="deploy/prod.yml")]), "ctx")
            self.assertEqual(denied, [])
            # .github is no longer controlled under this config -> a scratch-like write surfaces
            ok = propose(s, ScriptedClient([_write_resp(0.9, path=".github/notes.md")]), "ctx")
            self.assertEqual(len(ok), 1)

    def test_empty_controlled_paths_denies_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager", controlled_paths=())
            got = propose(s, ScriptedClient([_write_resp(0.9, path=".github/workflows/ci.yml")]), "ctx")
            self.assertEqual(len(got), 1)  # nothing is controlled -> surfaces (still HELD)


class IsControlledLocation(unittest.TestCase):
    def test_root_anchored_prefix_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(is_controlled_location(tmp, ".github/workflows/ci.yml", (".github",)))
            self.assertTrue(is_controlled_location(tmp, ".github", (".github",)))
            self.assertTrue(is_controlled_location(tmp, "deploy/prod/x.yml", ("deploy/prod",)))

    def test_nested_lookalike_and_unrelated_are_not_controlled(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_controlled_location(tmp, "src/.github/x.yml", (".github",)))
            self.assertFalse(is_controlled_location(tmp, "README.md", (".github",)))
            self.assertFalse(is_controlled_location(tmp, "deploy/dev/x.yml", ("deploy/prod",)))

    def test_empty_config_and_escaping_path_are_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_controlled_location(tmp, ".github/x", ()))
            self.assertFalse(is_controlled_location(tmp, "../evil", (".github",)))  # escape -> False


class StagePool(unittest.TestCase):
    def test_surfaced_proposal_is_pooled_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="a.txt")]), "ctx")
            self.assertEqual(len(s.proposal_pool.pending()), 1)
            self.assertIs(s.proposal_pool.pending()[0], got[0])  # same object, by reference

    def test_approval_moves_proposal_out_of_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="a.txt")]), "ctx")
            approve_proposal(s, got[0])
            self.assertEqual(s.proposal_pool.pending(), [])          # resolved in place
            self.assertEqual(len(s.proposal_pool.resolved()), 1)

    def test_veto_moves_proposal_out_of_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            got = propose(s, ScriptedClient([_write_resp(0.9, path="a.txt")]), "ctx")
            veto_proposal(s, got[0])
            self.assertEqual(s.proposal_pool.pending(), [])
            self.assertEqual(len(s.proposal_pool.resolved()), 1)

    def test_undecided_proposals_persist_across_turns(self):
        # The whole point: two proposals, neither approved nor vetoed -> BOTH stay pending.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            propose(s, ScriptedClient([_write_resp(0.9, path="a.txt")]), "ctx")
            propose(s, ScriptedClient([_write_resp(0.9, path="b.txt")]), "ctx")
            self.assertEqual(s.proposal_pool.pending_count(), 2)

    def test_declined_proposal_is_not_pooled(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="conservative")
            self.assertEqual(propose(s, ScriptedClient([_write_resp(0.4, path="a.txt")]), "ctx"), [])
            self.assertEqual(s.proposal_pool.pending_count(), 0)

    def test_snapshot_is_json_serializable_and_informative(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, proactivity="eager")
            propose(s, ScriptedClient([_write_resp(0.9, path="a.txt")]), "ctx")
            snap = s.proposal_pool.snapshot()
            json.dumps(snap)  # must not raise
            self.assertEqual(snap[0]["tool"], "write_file")
            self.assertEqual(snap[0]["status"], "proposed")
            self.assertIn("summary", snap[0])

    def test_prune_resolved_keeps_pending(self):
        pool = ProposalPool()

        class _P:
            def __init__(self, pid, status):
                self.proposal_id, self.status = pid, status

        pool.add(_P("p1", "proposed"))
        pool.add(_P("p2", "approved"))
        pool.add(_P("p3", "vetoed"))
        self.assertEqual(pool.prune_resolved(), 2)
        self.assertEqual(pool.pending_count(), 1)
        self.assertIsNotNone(pool.get("p1"))

    def test_add_is_idempotent(self):
        pool = ProposalPool()

        class _P:
            proposal_id = "p1"
            status = "proposed"

        p = _P()
        pool.add(p)
        pool.add(p)
        self.assertEqual(len(pool), 1)


if __name__ == "__main__":
    unittest.main()
