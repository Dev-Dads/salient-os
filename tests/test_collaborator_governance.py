"""The governance seam: capability gate = authority (salience can't grant it),
leash = second axis, verified execution (hands can't lie), workspace fence,
fail-closed. Runs the REAL salienceos flow per action."""

import sys
import tempfile
import unittest
from pathlib import Path

from collaborator.governance import DENIED, FAILED, HELD, NOTIFIED, RAN, govern_action
from collaborator.loop import approve
from collaborator.session import Session
from collaborator.toolcall import ToolIntent


def _session(tmp, caps=("fs.read:project", "fs.write:project"), **kw):
    return Session(workspace=tmp, capabilities=caps, **kw)


class CapabilityGate(unittest.TestCase):
    def test_write_runs_and_verifies_when_granted(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            d = govern_action(s, ToolIntent("write_file", {"path": "out.txt", "content": "hello"}, "structured"))
            self.assertEqual(d.status, RAN)
            self.assertTrue(d.cleared)
            self.assertEqual((Path(tmp) / "out.txt").read_text(), "hello")
            # binding key: the outcome is bound to this action (subject == envelope_id).
            self.assertEqual(d.outcome.subject, d.action_id)

    def test_run_command_denied_without_shell_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)  # no shell.exec
            d = govern_action(s, ToolIntent("run_command", {"command": [sys.executable, "-c", "pass"]}, "structured"))
            self.assertEqual(d.status, DENIED)
            self.assertIn("shell.exec", d.reason)

    def test_high_importance_cannot_grant_capability(self):
        # P-01: salience is influence only — max importance must NOT open shell.exec.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"),
                              importance=1.0, risk=1.0)
            self.assertEqual(d.status, DENIED)


class Leash(unittest.TestCase):
    def test_run_command_held_by_propose_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, caps=("fs.read:project", "fs.write:project", "shell.exec"))
            d = govern_action(s, ToolIntent("run_command", {"command": [sys.executable, "-c", "print('hi')"]}, "structured"))
            self.assertEqual(d.status, HELD)
            self.assertIsNotNone(d.preview)

    def test_approve_runs_held_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, caps=("fs.read:project", "fs.write:project", "shell.exec"))
            held = govern_action(s, ToolIntent("run_command", {"command": [sys.executable, "-c", "print('hi')"]}, "structured"))
            self.assertEqual(held.status, HELD)
            ran = approve(s, held)
            self.assertEqual(ran.status, RAN)
            self.assertTrue(ran.cleared)

    def test_notify_only_does_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, leash_overrides={"write_file": "notify_only"})
            d = govern_action(s, ToolIntent("write_file", {"path": "out.txt", "content": "x"}, "structured"))
            self.assertEqual(d.status, NOTIFIED)
            self.assertFalse((Path(tmp) / "out.txt").exists())  # not run


class WorkspaceFence(unittest.TestCase):
    def test_escape_is_denied_and_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            d = govern_action(s, ToolIntent("write_file", {"path": "../escape.txt", "content": "x"}, "structured"))
            self.assertEqual(d.status, DENIED)
            self.assertFalse((Path(tmp).parent / "escape.txt").exists())

    def test_absolute_path_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            evil = str(Path(tmp).parent / "abs_escape.txt")
            d = govern_action(s, ToolIntent("write_file", {"path": evil, "content": "x"}, "structured"))
            self.assertEqual(d.status, DENIED)


class HonestFailure(unittest.TestCase):
    def test_nonzero_exit_reports_failed_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, caps=("fs.read:project", "fs.write:project", "shell.exec"),
                         leash_overrides={"run_command": "act_then_report"})
            d = govern_action(s, ToolIntent("run_command", {"command": [sys.executable, "-c", "import sys;sys.exit(3)"]}, "structured"))
            self.assertEqual(d.status, FAILED)  # real exit code drives the honest result
            self.assertFalse(d.cleared)

    def test_read_missing_file_reports_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            d = govern_action(s, ToolIntent("read_file", {"path": "nope.txt"}, "structured"))
            self.assertEqual(d.status, FAILED)


class FailClosed(unittest.TestCase):
    def test_governance_error_denies_never_runs(self):
        # A broken policy key makes issue_policy/interpret raise; the action must be
        # DENIED (never run), not executed to keep going.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            s.policy_key = "not-bytes"  # signing needs bytes -> raises inside the seam
            d = govern_action(s, ToolIntent("write_file", {"path": "out.txt", "content": "x"}, "structured"))
            self.assertEqual(d.status, DENIED)
            self.assertFalse((Path(tmp) / "out.txt").exists())

    def test_unknown_tool_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            d = govern_action(s, ToolIntent("delete_everything", {}, "structured"))
            self.assertNotIn(d.status, (RAN,))


class AdaptationOffByDefault(unittest.TestCase):
    def test_cleared_write_does_not_allow_adaptation(self):
        # allow_adaptation defaults False -> even a cleared action never permits
        # learning. (Stage-4-live flips this on to exercise the disagreement.)
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            d = govern_action(s, ToolIntent("write_file", {"path": "out.txt", "content": "hi"}, "structured"))
            self.assertEqual(d.status, RAN)
            self.assertFalse(d.outcome.adaptation_allowed)


if __name__ == "__main__":
    unittest.main()
