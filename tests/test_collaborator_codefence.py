"""F-6 "Harm A" — the Collaborator's own code as an un-rewritable rulebook (cheap cross-platform).

Pins the layers that stop a governed shell from rewriting the rules it runs under, WITHOUT
hard-fencing run_command (which would kill system maintenance):

* the code-root model (collaborator/ + salienceos/) + the workspace ⟂ code disjointness guard,
* the POROUS recognizer (names_code_root) — explicitly NOT a boundary (negative cases encode that),
* the proposer hard-deny + the approve-time re-deny,
* the run_command AUTONOMY-WITHHELD floor (an unfenced shell can never AUTO-run) + honest flag.

The STRUCTURAL guarantee for a human-APPROVED run_command (an OS-level read-only bind) is a deferred
follow-up; here code_protection_available() is False, so autonomy is withheld everywhere.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collaborator import codefence
from collaborator.governance import DENIED, HELD, RAN, govern_action
from collaborator.loop import approve
from collaborator.session import Session
from collaborator.toolcall import ToolIntent
from collaborator.tools import ACT_THEN_REPORT, PROPOSE_FIRST


def _shell_session(tmp, **kw):
    return Session(workspace=tmp, capabilities=("fs.read:project", "fs.write:project", "shell.exec"), **kw)


# The collaborator/ package dir — a real protected root, resolved at import.
_ROOT = codefence.PROTECTED_ROOTS[0]


class CodeRootModel(unittest.TestCase):
    def test_protected_roots_cover_both_packages_and_are_real_dirs(self):
        self.assertTrue(codefence.PROTECTED_ROOTS, "expected at least the collaborator/ root")
        names = {p.name for p in codefence.PROTECTED_ROOTS}
        self.assertIn("collaborator", names)
        self.assertIn("salienceos", names)  # the F1 guarantee spans the core too
        for p in codefence.PROTECTED_ROOTS:
            self.assertTrue(p.is_dir())
            self.assertTrue(p.is_absolute())

    def test_protection_is_unavailable_in_this_build(self):
        # OS-level prevention is deferred; while False the seam withholds run_command autonomy.
        self.assertFalse(codefence.code_protection_available())


class DisjointnessGuard(unittest.TestCase):
    def test_tempdir_is_disjoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            codefence.disjoint_from_code(tmp)  # must not raise

    def test_workspace_equal_to_code_root_is_refused(self):
        with self.assertRaises(codefence.WorkspaceOverlapsCodeError):
            codefence.disjoint_from_code(_ROOT)

    def test_workspace_inside_code_root_is_refused(self):
        with self.assertRaises(codefence.WorkspaceOverlapsCodeError):
            codefence.disjoint_from_code(_ROOT / "nested" / "ws")

    def test_workspace_containing_code_root_is_refused(self):
        # the repo root CONTAINS collaborator/ + salienceos/ — a shell there could reach the code
        with self.assertRaises(codefence.WorkspaceOverlapsCodeError):
            codefence.disjoint_from_code(_ROOT.parent)

    def test_session_construction_refuses_overlap(self):
        # a ValueError (WorkspaceOverlapsCodeError subclasses it) — fails LOUD at construction
        with self.assertRaises(ValueError):
            Session(workspace=str(_ROOT))

    def test_empty_protected_roots_fails_closed(self):
        # PR #33 certification panel (4/5): a governance guard must never silently no-op. If we
        # can't locate our own code roots, refuse EVERY workspace rather than fail open.
        with patch.object(codefence, "PROTECTED_ROOTS", ()):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(codefence.WorkspaceOverlapsCodeError):
                    codefence.disjoint_from_code(tmp)
                with self.assertRaises(ValueError):
                    Session(workspace=tmp)


class NamesCodeRootRecognizer(unittest.TestCase):
    """POROUS defence-in-depth — the negative cases ENCODE that it is not the boundary."""

    def test_matches_absolute_code_root_token_in_list(self):
        cmd = ["python", "-c", f"open('{(_ROOT / 'governance.py').as_posix()}','w')"]
        self.assertIn(str(_ROOT), codefence.names_code_root(cmd))

    def test_matches_in_string_command(self):
        cmd = f"echo x > {(_ROOT / 'tools.py').as_posix()}"
        self.assertTrue(codefence.names_code_root(cmd))

    def test_benign_command_not_matched(self):
        self.assertEqual(codefence.names_code_root(["echo", "hello"]), "")
        self.assertEqual(codefence.names_code_root(""), "")
        self.assertEqual(codefence.names_code_root(None), "")

    def test_porous_dynamic_construction_is_NOT_caught(self):
        # A shell that builds the path at runtime (a var, no literal root substring) sails past —
        # which is exactly why this is DiD and code_protection_available() (OS prevention) is the
        # real boundary. This test PINS the porousness so nobody mistakes the recognizer for a fence.
        self.assertEqual(codefence.names_code_root(["sh", "-c", 'd=/some/base; printf x > "$d/x"']), "")


class GovernSlot(unittest.TestCase):
    def test_proposer_command_naming_code_root_is_hard_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            d = govern_action(s, ToolIntent(
                "run_command", {"command": ["echo", str(_ROOT / "governance.py")]}, "proposed"))
            self.assertEqual(d.status, DENIED)
            self.assertIn("own code", d.reason)

    def test_user_directed_command_naming_code_root_is_not_denied_by_this_rule(self):
        # maintenance preserved: a user/host-directed command naming the code root is NOT hard-denied
        # (only the PROPOSER floor is); it is HELD by the autonomy floor and runs under a human hand.
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent(
                "run_command", {"command": ["echo", str(_ROOT / "governance.py")]}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertNotEqual(held.status, DENIED)
            d = approve(s, held)          # the human hand: echo runs (not re-denied for user origin)
            self.assertEqual(d.status, RAN)

    def test_autonomy_is_withheld_for_act_then_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp, leash_overrides={"run_command": ACT_THEN_REPORT})
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(held.status, HELD)              # floored — never auto-runs
            self.assertEqual(held.leash, PROPOSE_FIRST)
            self.assertIs(held.preview.get("code_protected"), False)  # honest posture in the preview

    def test_held_preview_surfaces_named_code_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent(
                "run_command", {"command": ["echo", str(_ROOT / "x.py")]}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertIn(str(_ROOT), held.preview.get("names_code_root", ""))

    def test_executed_run_command_flag_is_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            d = approve(s, held)
            self.assertEqual(d.status, RAN)
            self.assertIs(d.code_protected, False)               # never a silent claim of protection
            self.assertIn("code NOT protected", d.summary())

    def test_approve_re_denies_a_mutated_collaborator_command(self):
        # a held COLLABORATOR-origin run_command mutated after origination to name the code root is
        # refused at the moment of use (symmetric with the controlled-location re-deny), not consumed.
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(held.status, HELD)
            held.origin = "collaborator"                         # as if a proposer raised the hold
            held.args["command"] = ["echo", str(_ROOT / "governance.py")]  # mutated post-origination
            d = approve(s, held)
            self.assertEqual(d.status, DENIED)
            self.assertIn("code root", d.reason)
            self.assertFalse(held.consumed)                      # retryable, not burned


if __name__ == "__main__":
    sys.exit(unittest.main())
